#include "workspace/file_engine.hpp"

#include <dirent.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <linux/openat2.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <limits>
#include <mutex>
#include <optional>
#include <random>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace spatial {
FileIoResult wait_file_io_task(std::future<FileIoResult>& future,
                               const std::function<void()>& observer,
                               bool effectful) noexcept {
  bool observer_failed=false;
  while(future.wait_for(std::chrono::milliseconds(10))!=std::future_status::ready) {
    if(observer && !observer_failed) try { observer(); } catch(...) { observer_failed=true; }
  }
  FileIoResult result;
  try { result=future.get(); }
  catch(...) { result.value=-1; result.error=EIO; result.effect_may_have_occurred=effectful; }
  if(observer && !observer_failed) try { observer(); } catch(...) { observer_failed=true; }
  if(observer_failed) {
    if(result.owns_descriptor && result.value>=0) { close(result.value); result.owns_descriptor=false; }
    result.value=-1; result.error=EIO;
    result.effect_may_have_occurred=result.effect_may_have_occurred||effectful;
  }
  return result;
}
namespace {
std::uint64_t generation(const struct stat&);
}
namespace file_engine_detail {
bool sync_directory(int descriptor) noexcept { return ::fsync(descriptor) == 0; }
static int pin_leaf(int directory, const char* basename) noexcept {
  open_how how{};
  how.flags = O_PATH | O_NOFOLLOW | O_CLOEXEC;
  how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV;
  return static_cast<int>(syscall(SYS_openat2, directory, basename, &how, sizeof(how)));
}
int open_regular_file(int directory, const char* basename,
                      std::uint64_t expected_generation) noexcept {
  const int pinned = pin_leaf(directory, basename);
  if (pinned < 0) return -1;
  struct stat before {};
  if (fstat(pinned, &before) < 0) {
    const int error = errno;
    close(pinned);
    errno = error;
    return -1;
  }
  if (!S_ISREG(before.st_mode) ||
      (expected_generation != 0 && generation(before) != expected_generation)) {
    close(pinned);
    errno = S_ISREG(before.st_mode) ? ESTALE : EINVAL;
    return -1;
  }
  // This trusted proc link names our pinned inode, never the mutable basename.
  char path[64]{};
  std::snprintf(path, sizeof(path), "/proc/self/fd/%d", pinned);
  const int opened = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
  int error = errno;
  struct stat after {};
  if (opened >= 0 && (fstat(opened, &after) < 0 || !S_ISREG(after.st_mode) ||
                      after.st_dev != before.st_dev || after.st_ino != before.st_ino)) {
    close(opened);
    close(pinned);
    errno = ESTALE;
    return -1;
  }
  close(pinned);
  errno = error;
  return opened;
}
}  // namespace file_engine_detail
namespace {
namespace pb = workspace::v1;
namespace authz = spatial::authority::v1;
constexpr std::size_t kMaxName = 255;
constexpr std::size_t kMaxContent = 1024 * 1024;
constexpr std::size_t kMaxEntries = 256;
struct Failure {
  pb::FileActionError::Code code;
  const char* message;
};
[[noreturn]] void fail(pb::FileActionError::Code c, const char* m) { throw Failure{c, m}; }
void need(bool ok, const char* message) {
  if (!ok) fail(pb::FileActionError::INVALID_ARGUMENT, message);
}
struct Fd {
  int v = -1;
  Fd() = default;
  explicit Fd(int n) : v(n) {}
  ~Fd() {
    if (v >= 0) close(v);
  }
  Fd(Fd&& x) noexcept : v(x.v) { x.v = -1; }
  Fd& operator=(Fd&& x) noexcept {
    if (this != &x) {
      if (v >= 0) close(v);
      v = x.v;
      x.v = -1;
    }
    return *this;
  }
  Fd(const Fd&) = delete;
  Fd& operator=(const Fd&) = delete;
};
std::string random_bytes(std::size_t n) {
  std::string out(n, '\0');
  std::size_t done = 0;
  while (done < n) {
    const auto r = getrandom(out.data() + done, n - done, 0);
    if (r < 0 && errno == EINTR) continue;
    if (r <= 0) throw std::runtime_error("file engine entropy unavailable");
    done += static_cast<std::size_t>(r);
  }
  return out;
}
std::string hex(std::string_view in) {
  constexpr char h[] = "0123456789abcdef";
  std::string out;
  out.reserve(in.size() * 2);
  for (unsigned char c : in) {
    out += h[c >> 4];
    out += h[c & 15];
  }
  return out;
}
std::uint64_t directory_generation(const struct stat& s) {
  std::uint64_t x = static_cast<std::uint64_t>(s.st_ino) * 0x9e3779b185ebca87ULL;
  x ^= static_cast<std::uint64_t>(s.st_dev) + (x << 6) + (x >> 2);
  return x ? x : 1;
}
bool same_published_object(const struct stat& a, const struct stat& b) {
  if (a.st_dev != b.st_dev || a.st_ino != b.st_ino || a.st_mode != b.st_mode) return false;
  if (S_ISDIR(a.st_mode)) return true;
  return a.st_nlink == b.st_nlink && a.st_size == b.st_size &&
         a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec;
}
std::uint64_t generation(const struct stat& s) {
  std::uint64_t x = static_cast<std::uint64_t>(s.st_ino) * 0x9e3779b185ebca87ULL;
  x ^= static_cast<std::uint64_t>(s.st_dev) + (x << 6) + (x >> 2);
  x ^= static_cast<std::uint64_t>(s.st_ctim.tv_sec) + (x << 6) + (x >> 2);
  x ^= static_cast<std::uint64_t>(s.st_ctim.tv_nsec) + (x << 6) + (x >> 2);
  x ^= static_cast<std::uint64_t>(s.st_size) + (x << 6) + (x >> 2);
  return x ? x : 1;
}
void valid_name(std::string_view n) {
  need(!n.empty() && n.size() <= kMaxName, "invalid POSIX basename length");
  need(n.find('\0') == n.npos && n.find('/') == n.npos && n != "." && n != ".." &&
           n != ".spatial-trash",
       "invalid or reserved POSIX basename");
}
int open_child_dir(int parent, const std::string& n) {
  valid_name(n);
  open_how how{};
  how.flags = O_RDONLY | O_DIRECTORY | O_CLOEXEC;
  how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV;
  const int fd = static_cast<int>(syscall(SYS_openat2, parent, n.c_str(), &how, sizeof(how)));
  if (fd < 0) {
    if (errno == ELOOP || errno == EXDEV)
      fail(pb::FileActionError::INVALID_ARGUMENT, "symlink or path escape rejected");
    fail(pb::FileActionError::NOT_FOUND, "directory is unavailable");
  }
  return fd;
}
void stat_child(int dir, const std::string& n, struct stat& st) {
  valid_name(n);
  Fd pinned(file_engine_detail::pin_leaf(dir, n.c_str()));
  if (pinned.v < 0) {
    if (errno == EXDEV || errno == ELOOP)
      fail(pb::FileActionError::INVALID_ARGUMENT, "leaf mount or symlink crossing rejected");
    fail(errno == ENOENT ? pb::FileActionError::NOT_FOUND : pb::FileActionError::UNAVAILABLE,
         "file is unavailable");
  }
  if (fstat(pinned.v, &st) < 0)
    fail(pb::FileActionError::UNAVAILABLE, "pinned file identity unavailable");
}
void expect_generation(int dir, const std::string& n, std::uint64_t expected,
                       struct stat* out = nullptr) {
  struct stat st {};
  stat_child(dir, n, st);
  if (S_ISLNK(st.st_mode) || generation(st) != expected)
    fail(pb::FileActionError::CONFLICT, "file changed since it was observed");
  if (out) *out = st;
}
void authorize(FileAuthority& auth, const pb::FileActionRequest& req, authz::Right right,
               const std::string& workspace, const std::vector<std::string>& scope) {
  pb::ScopedAuthorityCheckRequest c;
  c.set_workspace(workspace);
  *c.mutable_principal() = req.principal();
  c.set_right(right);
  for (const auto& x : scope) c.add_components(x);
  pb::ScopedAuthorityCheckReply d;
  try {
    d = auth.check_fresh(c);
  } catch (...) {
    fail(pb::FileActionError::UNAVAILABLE, "fresh workspace authority check failed");
  }
  if (!d.allowed()) fail(pb::FileActionError::UNAUTHORIZED, "file operation is not authorized");
}
}  // namespace
struct FileEngine::Impl {
  using Clock = std::chrono::steady_clock;
  struct Handle {
    std::vector<std::string> path;
    std::uint64_t gen = 0;
    bool directory = false;
    Clock::time_point last_use{};
  };
  struct Cursor {
    DIR* stream = nullptr;
    std::string directory_handle;
    std::uint64_t directory_generation = 0;
    Clock::time_point last_use{};
    Cursor() = default;
    Cursor(DIR* d, std::string h, std::uint64_t g, Clock::time_point tick)
        : stream(d), directory_handle(std::move(h)), directory_generation(g), last_use(tick) {}
    ~Cursor() {
      if (stream) closedir(stream);
    }
    Cursor(Cursor&& c) noexcept
        : stream(c.stream),
          directory_handle(std::move(c.directory_handle)),
          directory_generation(c.directory_generation),
          last_use(c.last_use) {
      c.stream = nullptr;
    }
    Cursor& operator=(Cursor&& c) noexcept {
      if (this != &c) {
        if (stream) closedir(stream);
        stream = c.stream;
        directory_handle = std::move(c.directory_handle);
        directory_generation = c.directory_generation;
        last_use = c.last_use;
        c.stream = nullptr;
      }
      return *this;
    }
    Cursor(const Cursor&) = delete;
    Cursor& operator=(const Cursor&) = delete;
  };
  Fd root;
  std::string workspace;
  std::vector<std::string> scope;
  FileAuthority& authority;
  FileEffectExecutor* effects;
  FileEngine::MonotonicNow now;
  std::unordered_map<std::string, Handle> handles;
  std::unordered_map<std::string, Cursor> cursors;
  std::mutex mutex;
  Impl(int fd, std::string w, std::vector<std::string> s, FileAuthority& a,
       FileEngine::MonotonicNow clock, FileEffectExecutor* executor)
      : root(openat(fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)),
        workspace(std::move(w)),
        scope(std::move(s)),
        authority(a),
        effects(executor),
        now(std::move(clock)) {
    struct stat st {};
    struct stat supplied {};
    if (root.v < 0 || fstat(fd, &supplied) < 0 || fstat(root.v, &st) < 0 || !S_ISDIR(st.st_mode) ||
        supplied.st_dev != st.st_dev || supplied.st_ino != st.st_ino || !now || workspace.empty() ||
        workspace.size() > 256 || workspace.find('\0') != std::string::npos || scope.empty() ||
        scope.size() > 32)
      throw std::invalid_argument("invalid authorized project root or scope");
    for (const auto& component : scope)
      if (component.empty() || component.size() > 256)
        throw std::invalid_argument("invalid opaque cabinet authority scope");
    handles.emplace(std::string{}, Handle{{}, directory_generation(st), true, now()});
  }
  Handle get(const std::string& id, bool directory = false) {
    const auto it = handles.find(id);
    if (it == handles.end()) fail(pb::FileActionError::STALE_HANDLE, "file handle is stale");
    const auto tick = now();
    if (!id.empty() && tick - it->second.last_use > std::chrono::minutes(10)) {
      handles.erase(it);
      fail(pb::FileActionError::STALE_HANDLE, "file handle expired");
    }
    it->second.last_use = tick;
    auto h = it->second;
    if (directory && !h.directory)
      fail(pb::FileActionError::INVALID_ARGUMENT, "directory handle required");
    // Resolve at the operation boundary. Do not validate one descriptor and then
    // reopen the same mutable path without checking the descriptor actually used.
    return h;
  }
  std::string add_handle(std::vector<std::string> path, std::uint64_t gen, bool dir) {
    const auto tick = now();
    if (handles.size() >= 16384) {
      for (auto it = handles.begin(); it != handles.end();) {
        if (!it->first.empty() && tick - it->second.last_use > std::chrono::minutes(10))
          it = handles.erase(it);
        else
          ++it;
      }
    }
    if (handles.size() >= 16384)
      fail(pb::FileActionError::EXHAUSTED, "file handle table reached its bound");
    std::string id;
    do {
      id = random_bytes(16);
    } while (handles.contains(id));
    handles.emplace(id, Handle{std::move(path), gen, dir, tick});
    return id;
  }
  struct Reservation {
    Impl& engine;
    std::string id;
    bool committed = false;
    Reservation(Impl& e, std::string token) : engine(e), id(std::move(token)) {}
    Reservation(const Reservation&) = delete;
    Reservation& operator=(const Reservation&) = delete;
    ~Reservation() {
      if (!committed) engine.handles.erase(id);
    }
    void finish(std::uint64_t gen) {
      engine.finish_handle(id, gen);
      committed = true;
    }
  };
  Reservation reserve_handle(std::vector<std::string> path, bool dir) {
    return Reservation(*this, add_handle(std::move(path), 0, dir));
  }
  void finish_handle(const std::string& id, std::uint64_t gen) {
    auto it = handles.find(id);
    if (it == handles.end())
      fail(pb::FileActionError::UNCERTAIN, "published file lacks its reserved handle");
    it->second.gen = gen;
  }
  Fd directory(const Handle& h) {
    Fd d(openat(root.v, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    if (d.v < 0) fail(pb::FileActionError::UNAVAILABLE, "project root unavailable");
    for (const auto& c : h.path) d = Fd(open_child_dir(d.v, c));
    struct stat st {};
    if (fstat(d.v, &st) < 0 || !S_ISDIR(st.st_mode) ||
        (h.gen != 0 && directory_generation(st) != h.gen))
      fail(pb::FileActionError::STALE_HANDLE, "directory handle is stale");
    return d;
  }
  template<class F> FileIoResult io_result_step(const pb::FileActionRequest& request,
      authz::Right right, bool effectful, bool& occurred, F&& function) {
    FileEffectExecutor::Step step=std::forward<F>(function);
    auto result=effects?effects->run_step(request,right,effectful,std::move(step)):step();
    if(result.effect_may_have_occurred)occurred=true;
    return result;
  }
  template<class F> FileIoResult io_step(const pb::FileActionRequest& request,
      authz::Right right, bool effectful, bool& occurred, F&& function, bool owns_descriptor=false) {
    FileEffectExecutor::Step step=[fn=std::forward<F>(function),effectful,owns_descriptor]() mutable {
      FileIoResult result; errno=0; result.value=fn(); result.error=result.value<0?errno:0;
      result.owns_descriptor=owns_descriptor && result.value>=0;
      result.effect_may_have_occurred=effectful && result.value>=0; return result;
    };
    auto result=effects?effects->run_step(request,right,effectful,std::move(step)):step();
    if(result.effect_may_have_occurred)occurred=true;
    return result;
  }
  Fd trash_dir(bool create, bool* effects = nullptr,
      const pb::FileActionRequest* request = nullptr, authz::Right right = authz::FILE_TRASH) {
    if (create) {
      int made=-1;
      if(request && effects) { auto step=io_step(*request,right,true,*effects,[&]{return mkdirat(root.v,".spatial-trash",0700);}); made=step.value; if(made<0)errno=step.error; }
      else made=mkdirat(root.v,".spatial-trash",0700);
      if (made == 0) {
        if (effects) *effects = true;
        FileIoResult root_sync;
        if(request&&effects) root_sync=io_step(*request,right,true,*effects,[&]{return fsync(root.v);});
        else { root_sync.value=file_engine_detail::sync_directory(root.v)?0:-1; root_sync.error=root_sync.value<0?errno:0; }
        if(root_sync.value<0) fail(pb::FileActionError::UNCERTAIN,"trash directory created but root durability is uncertain");
      } else if (errno != EEXIST) fail(pb::FileActionError::UNAVAILABLE,"workspace trash unavailable");
    }
    open_how how{};
    how.flags = O_RDONLY | O_DIRECTORY | O_CLOEXEC;
    how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV;
    const int fd =
        static_cast<int>(syscall(SYS_openat2, root.v, ".spatial-trash", &how, sizeof(how)));
    if (fd < 0) fail(pb::FileActionError::UNAVAILABLE, "workspace trash unavailable");
    struct stat st {};
    if (fstat(fd, &st) < 0 || !S_ISDIR(st.st_mode) || (st.st_mode & 077) != 0) {
      close(fd);
      fail(pb::FileActionError::UNAVAILABLE, "workspace trash has unsafe permissions");
    }
    return Fd(fd);
  }
  pb::FileActionReply run(const pb::FileActionRequest& r, bool& effects) {
    if (r.ByteSizeLong() > 1100000)
      fail(pb::FileActionError::EXHAUSTED, "file action exceeds the message bound");
    pb::FileActionReply out;
    out.set_protocol_version(1);
    out.set_operation_id(r.operation_id());
    if (r.protocol_version() != 1 || r.operation_id().size() != 16 ||
        r.workspace_id() != workspace || !r.has_principal() ||
        r.principal().issuer_id().size() != 32 || r.principal().actor_id().size() != 32 ||
        r.authority_components_size() != static_cast<int>(scope.size()))
      fail(pb::FileActionError::INVALID_ARGUMENT, "malformed file operation envelope");
    for (int i = 0; i < r.authority_components_size(); ++i)
      if (r.authority_components(i) != scope[i])
        fail(pb::FileActionError::INVALID_ARGUMENT, "workspace scope does not match cabinet");
    if (r.has_browse()) {
      authorize(authority, r, authz::FILE_BROWSE, workspace, scope);
      const auto tick = now();
      for (auto it = cursors.begin(); it != cursors.end();) {
        if (tick - it->second.last_use > std::chrono::minutes(1))
          it = cursors.erase(it);
        else
          ++it;
      }
      const auto cap =
          r.browse().page_size() ? r.browse().page_size() : static_cast<unsigned>(kMaxEntries);
      if (cap > kMaxEntries || r.browse().cursor().size() > 16)
        fail(pb::FileActionError::INVALID_ARGUMENT, "browse cursor or page exceeds bound");
      const auto directory_handle = r.browse().directory_handle();
      const auto h = get(directory_handle, true);
      auto d = directory(h);
      struct stat current {};
      if (fstat(d.v, &current) < 0)
        fail(pb::FileActionError::UNAVAILABLE, "cannot inspect directory");
      Cursor cursor;
      if (r.browse().cursor().empty()) {
        Fd copy(dup(d.v));
        if (copy.v < 0) fail(pb::FileActionError::UNAVAILABLE, "cannot open directory cursor");
        DIR* stream = fdopendir(copy.v);
        if (!stream) fail(pb::FileActionError::UNAVAILABLE, "cannot enumerate directory");
        copy.v = -1;
        cursor = Cursor(stream, directory_handle, generation(current), tick);
      } else {
        const auto found = cursors.find(r.browse().cursor());
        if (found == cursors.end())
          fail(pb::FileActionError::STALE_HANDLE, "browse cursor is stale");
        if (found->second.directory_handle != directory_handle ||
            found->second.directory_generation != generation(current))
          fail(pb::FileActionError::STALE_HANDLE, "directory changed during browse");
        cursor = std::move(found->second);
        cursors.erase(found);
      }
      auto next = [&]() -> std::optional<std::pair<std::string, struct stat>> {
        while (true) {
          auto listing=io_result_step(r,authz::FILE_BROWSE,false,effects,[&]{
            FileIoResult result; errno=0; auto* ent=readdir(cursor.stream);
            if(!ent){result.value=0;result.error=errno;return result;}
            result.value=1;result.bytes.assign(ent->d_name);return result;
          });
          if(listing.value<0) {
            if(listing.error==EACCES || listing.error==EPERM) fail(pb::FileActionError::UNAUTHORIZED,"directory access was revoked");
            fail(pb::FileActionError::UNAVAILABLE,"directory enumeration failed");
          }
          if(listing.value==0){if(listing.error)fail(pb::FileActionError::UNAVAILABLE,"directory enumeration failed");return std::nullopt;}
          const auto& name=listing.bytes;
          if (name == "." || name == ".." || name == ".spatial-trash") continue;
          struct stat st {};
          Fd pinned(file_engine_detail::pin_leaf(d.v, name.c_str()));
          if (pinned.v < 0) {
            if (errno == EXDEV || errno == ENOENT) continue;
            fail(pb::FileActionError::UNAVAILABLE, "cannot pin directory entry");
          }
          if (fstat(pinned.v, &st) < 0)
            fail(pb::FileActionError::UNAVAILABLE, "cannot inspect pinned directory entry");
          return std::make_pair(std::move(name), st);
        }
      };
      auto* result = out.mutable_directory();
      result->set_handle(directory_handle);
      unsigned emitted = 0;
      while (emitted < cap) {
        auto item = next();
        if (!item) break;
        const auto& [name, st] = *item;
        auto* e = result->add_entries();
        e->set_name(name);
        e->set_size(st.st_size < 0 ? 0 : static_cast<std::uint64_t>(st.st_size));
        e->set_generation(S_ISDIR(st.st_mode) ? directory_generation(st) : generation(st));
        e->set_is_directory(S_ISDIR(st.st_mode));
        if (S_ISDIR(st.st_mode) || S_ISREG(st.st_mode)) {
          auto path = h.path;
          path.push_back(name);
          e->set_handle(add_handle(std::move(path),
                                   S_ISDIR(st.st_mode) ? directory_generation(st) : generation(st),
                                   S_ISDIR(st.st_mode)));
        }
        ++emitted;
      }
      const auto before_probe = telldir(cursor.stream);
      if (before_probe < 0)
        fail(pb::FileActionError::UNAVAILABLE, "cannot checkpoint directory cursor");
      const auto probe = next();
      const bool more = probe.has_value();
      if (more) seekdir(cursor.stream, before_probe);
      if (more) {
        if (cursors.size() >= 256) {
          auto oldest = std::min_element(
              cursors.begin(), cursors.end(),
              [](const auto& a, const auto& b) { return a.second.last_use < b.second.last_use; });
          if (oldest != cursors.end()) cursors.erase(oldest);
        }
        std::string token;
        do {
          token = random_bytes(16);
        } while (cursors.contains(token));
        cursor.last_use = now();
        cursors.emplace(token, std::move(cursor));
        result->set_next_cursor(token);
      }
      return out;
    }
    if (r.has_read()) {
      authorize(authority, r, authz::FILE_READ, workspace, scope);
      const auto h = get(r.read().file_handle());
      if (h.directory) fail(pb::FileActionError::INVALID_ARGUMENT, "file handle required");
      if (r.read().maximum_bytes() > kMaxContent ||
          r.read().offset() > UINT64_MAX - r.read().maximum_bytes() ||
          r.read().offset() > static_cast<std::uint64_t>(std::numeric_limits<off_t>::max()))
        fail(pb::FileActionError::INVALID_ARGUMENT, "read exceeds bound");
      auto p = h.path;
      p.pop_back();
      auto d = directory(Handle{p, 0, true});
      auto opened=io_step(r,authz::FILE_READ,false,effects,[&]{return file_engine_detail::open_regular_file(d.v,h.path.back().c_str(),h.gen);},true);
      Fd f(opened.value); if(f.v<0)errno=opened.error;
      if (f.v < 0) {
        if (errno == EINVAL || errno == ELOOP || errno == EXDEV)
          fail(pb::FileActionError::INVALID_ARGUMENT, "only confined regular files can be read");
        fail(errno == ENOENT || errno == ESTALE ? pb::FileActionError::STALE_HANDLE
                                                : pb::FileActionError::UNAVAILABLE,
             "cannot open pinned regular file");
      }
      struct stat st {};
      if (fstat(f.v, &st) < 0 || generation(st) != h.gen)
        fail(pb::FileActionError::STALE_HANDLE, "file changed during read");
      if (!S_ISREG(st.st_mode))
        fail(pb::FileActionError::INVALID_ARGUMENT, "only regular files can be read");
      auto* v = out.mutable_content();
      const auto n = std::min<std::uint64_t>(
          r.read().maximum_bytes(), st.st_size > static_cast<off_t>(r.read().offset())
                                        ? static_cast<std::uint64_t>(st.st_size - r.read().offset())
                                        : 0);
      auto read_result=io_result_step(r,authz::FILE_READ,false,effects,[&]{
        FileIoResult result; result.bytes.resize(static_cast<std::size_t>(n)); errno=0;
        result.value=static_cast<int>(pread(f.v,result.bytes.data(),result.bytes.size(),static_cast<off_t>(r.read().offset())));
        result.error=result.value<0?errno:0;
        if(result.value>=0)result.bytes.resize(static_cast<std::size_t>(result.value));
        return result;
      });
      if(read_result.value<0)fail(pb::FileActionError::UNAVAILABLE,"file read failed");
      auto bytes=std::move(read_result.bytes);
      struct stat after {};
      if (fstat(f.v, &after) < 0 || generation(after) != h.gen)
        fail(pb::FileActionError::CONFLICT, "file changed during read");
      v->set_data(bytes);
      v->set_total_size(st.st_size);
      v->set_generation(h.gen);
      return out;
    }
    if (r.has_create()) {
      authorize(authority, r, authz::FILE_CREATE, workspace, scope);
      if (r.create().content().size() > kMaxContent)
        fail(pb::FileActionError::EXHAUSTED, "file content exceeds bound");
      const auto h = get(r.create().directory_handle(), true);
      valid_name(r.create().basename());
      auto d = directory(h);
      auto path = h.path;
      path.push_back(r.create().basename());
      auto reserved = reserve_handle(path, false);
      auto* obj = out.mutable_object();
      obj->set_handle(reserved.id);
      auto opened=io_step(r,authz::FILE_CREATE,true,effects,[&]{return openat(d.v,r.create().basename().c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);},true);
      Fd f(opened.value); if(f.v<0)errno=opened.error;
      if (f.v < 0)
        fail(errno == EEXIST ? pb::FileActionError::CONFLICT : pb::FileActionError::UNAVAILABLE,
             "file create failed");
      effects = true;
      const auto& b = r.create().content();
      std::size_t at = 0;
      while (at < b.size()) {
        auto io=io_step(r,authz::FILE_CREATE,true,effects,[&]{return static_cast<int>(write(f.v,b.data()+at,b.size()-at));});
        const auto n=io.value; if(n<0)errno=io.error;
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) fail(pb::FileActionError::UNCERTAIN, "file create may be partial");
        at += static_cast<std::size_t>(n);
      }
      auto file_sync=io_step(r,authz::FILE_CREATE,true,effects,[&]{return fsync(f.v);});
      auto dir_sync=io_step(r,authz::FILE_CREATE,true,effects,[&]{return file_sync.value==0?fsync(d.v):-1;});
      if(file_sync.value<0 || dir_sync.value<0) fail(pb::FileActionError::UNCERTAIN,"file create durability is uncertain");
      struct stat st {};
      if (fstat(f.v, &st) < 0)
        fail(pb::FileActionError::UNCERTAIN, "created file identity unavailable");
      struct stat named {};
      if (fstatat(d.v, r.create().basename().c_str(), &named, AT_SYMLINK_NOFOLLOW) < 0 ||
          !same_published_object(st, named))
        fail(pb::FileActionError::UNCERTAIN, "created file name changed before acknowledgment");
      reserved.finish(generation(st));
      obj->set_generation(generation(st));
      obj->set_size(st.st_size);
      return out;
    }
    if (r.has_edit()) {
      authorize(authority, r, authz::FILE_EDIT, workspace, scope);
      if (r.edit().content().size() > kMaxContent)
        fail(pb::FileActionError::EXHAUSTED, "file content exceeds bound");
      const auto h = get(r.edit().file_handle());
      if (h.directory || r.edit().expected_generation() != h.gen)
        fail(pb::FileActionError::CONFLICT, "edit generation mismatch");
      auto p = h.path;
      p.pop_back();
      auto d = directory(Handle{p, 0, true});
      auto reserved = reserve_handle(h.path, false);
      auto* o = out.mutable_object();
      o->set_handle(reserved.id);
      struct stat original {};
      expect_generation(d.v, h.path.back(), h.gen, &original);
      if (!S_ISREG(original.st_mode))
        fail(pb::FileActionError::INVALID_ARGUMENT, "only regular files can be edited");
      const auto tmp = ".spatial-edit-" + hex(r.operation_id());
      const auto recovery = ".spatial-conflict-" + hex(r.operation_id());
      auto opened=io_step(r,authz::FILE_EDIT,true,effects,[&]{return openat(d.v,tmp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);},true);
      Fd f(opened.value); if(f.v<0)errno=opened.error;
      if (f.v < 0) fail(pb::FileActionError::UNAVAILABLE, "cannot stage edit");
      effects = true;
      const auto& b = r.edit().content();
      std::size_t at = 0;
      while (at < b.size()) {
        auto io=io_step(r,authz::FILE_EDIT,true,effects,[&]{return static_cast<int>(write(f.v,b.data()+at,b.size()-at));});
        const auto n=io.value; if(n<0)errno=io.error;
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0)
          fail(pb::FileActionError::UNCERTAIN,
               "edit staging may be partial; staging file retained");
        at += static_cast<std::size_t>(n);
      }
      auto mode=io_step(r,authz::FILE_EDIT,true,effects,[&]{return fchmod(f.v,original.st_mode&07777);});
      auto staged_sync=io_step(r,authz::FILE_EDIT,true,effects,[&]{return mode.value==0?fsync(f.v):-1;});
      if(mode.value<0 || staged_sync.value<0) fail(pb::FileActionError::UNCERTAIN,"edit staging metadata or durability is uncertain");
      struct stat staged {};
      if (fstat(f.v, &staged) < 0)
        fail(pb::FileActionError::UNCERTAIN, "staged file identity unavailable");
      auto exchange=io_step(r,authz::FILE_EDIT,true,effects,[&]{return renameat2(d.v,tmp.c_str(),d.v,h.path.back().c_str(),RENAME_EXCHANGE);});
      if(exchange.value<0){errno=exchange.error;fail(errno==ENOENT?pb::FileActionError::CONFLICT:pb::FileActionError::UNCERTAIN,"edit exchange failed; staging identity retained");}
      struct stat displaced {};
      struct stat published {};
      if (fstatat(d.v, tmp.c_str(), &displaced, AT_SYMLINK_NOFOLLOW) < 0 ||
          fstatat(d.v, h.path.back().c_str(), &published, AT_SYMLINK_NOFOLLOW) < 0)
        fail(pb::FileActionError::UNCERTAIN, "edit exchange outcome requires recovery");
      const bool exact = displaced.st_dev == original.st_dev &&
                         displaced.st_ino == original.st_ino &&
                         same_published_object(original, displaced) &&
                         published.st_dev == staged.st_dev && published.st_ino == staged.st_ino;
      if (!exact) {
        if (published.st_dev == staged.st_dev && published.st_ino == staged.st_ino &&
            io_step(r,authz::FILE_EDIT,true,effects,[&]{return renameat2(d.v,tmp.c_str(),d.v,h.path.back().c_str(),RENAME_EXCHANGE);}).value == 0) {
          if (io_step(r,authz::FILE_EDIT,true,effects,[&]{return renameat2(d.v,tmp.c_str(),d.v,recovery.c_str(),RENAME_NOREPLACE);}).value < 0)
            fail(pb::FileActionError::UNCERTAIN,
                 "conflicting edit was rolled back; staged data remains under its temporary name");
          auto recovery_sync=io_step(r,authz::FILE_EDIT,true,effects,[&]{return fsync(d.v);});
          if (recovery_sync.value < 0)
            fail(pb::FileActionError::UNCERTAIN, "conflict recovery durability is uncertain");
          fail(pb::FileActionError::CONFLICT,
               "concurrent edit preserved in workspace conflict file");
        }
        fail(pb::FileActionError::UNCERTAIN,
             "concurrent replacement detected; both directory entries retained for recovery");
      }
      auto cleanup=io_step(r,authz::FILE_EDIT,true,effects,[&]{return unlinkat(d.v,tmp.c_str(),0);});
      auto durable=io_step(r,authz::FILE_EDIT,true,effects,[&]{return cleanup.value==0?fsync(d.v):-1;});
      if(cleanup.value<0 || durable.value<0) fail(pb::FileActionError::UNCERTAIN,"edit published but displaced file cleanup is uncertain");
      struct stat st {};
      if (fstatat(d.v, h.path.back().c_str(), &st, AT_SYMLINK_NOFOLLOW) < 0)
        fail(pb::FileActionError::UNCERTAIN, "edited file identity unavailable");
      if (!same_published_object(staged, st))
        fail(pb::FileActionError::UNCERTAIN, "edited file changed before acknowledgment");
      reserved.finish(generation(st));
      o->set_generation(generation(st));
      o->set_size(st.st_size);
      return out;
    }
    if (r.has_rename() || r.has_move()) {
      const bool moving = r.has_move();
      authorize(authority, r, moving ? authz::FILE_MOVE : authz::FILE_RENAME, workspace, scope);
      const auto& a = moving ? r.move().file_handle() : r.rename().file_handle();
      const auto expected =
          moving ? r.move().expected_generation() : r.rename().expected_generation();
      const auto h = get(a);
      if (h.path.empty())
        fail(pb::FileActionError::INVALID_ARGUMENT, "project root cannot be renamed or moved");
      if (h.gen != expected) fail(pb::FileActionError::CONFLICT, "move generation mismatch");
      auto srcpath = h.path;
      const auto old = srcpath.back();
      srcpath.pop_back();
      Fd src = directory(Handle{srcpath, 0, true});
      const auto& dn = moving ? r.move().new_basename() : r.rename().new_basename();
      valid_name(dn);
      const auto desth =
          moving ? get(r.move().destination_directory_handle(), true) : Handle{srcpath, 0, true};
      Fd dst = directory(desth);
      struct stat st {};
      stat_child(src.v, old, st);
      if (S_ISLNK(st.st_mode) || (h.directory ? directory_generation(st) : generation(st)) != h.gen)
        fail(pb::FileActionError::CONFLICT, "move source changed since it was observed");
      if (h.directory) {
        if (desth.path.size() >= h.path.size() &&
            std::equal(h.path.begin(), h.path.end(), desth.path.begin()))
          fail(pb::FileActionError::INVALID_ARGUMENT, "directory cannot be moved into itself");
      }
      auto path = desth.path;
      path.push_back(dn);
      auto reserved = reserve_handle(path, h.directory);
      auto* o = out.mutable_object();
      o->set_handle(reserved.id);
      auto moved=io_step(r,moving?authz::FILE_MOVE:authz::FILE_RENAME,true,effects,[&]{return renameat2(src.v,old.c_str(),dst.v,dn.c_str(),RENAME_NOREPLACE);});
      if(moved.value<0){errno=moved.error;fail(errno==EEXIST?pb::FileActionError::CONFLICT:pb::FileActionError::UNAVAILABLE,"move destination exists or cannot be published");}
      effects = true;
      auto src_sync=io_step(r,moving?authz::FILE_MOVE:authz::FILE_RENAME,true,effects,[&]{return fsync(src.v);});
      auto dst_sync=io_step(r,moving?authz::FILE_MOVE:authz::FILE_RENAME,true,effects,[&]{return src_sync.value==0 && src.v!=dst.v?fsync(dst.v):0;});
      if(src_sync.value<0 || dst_sync.value<0) fail(pb::FileActionError::UNCERTAIN,"move published but directory durability is uncertain");
      struct stat now {};
      if (fstatat(dst.v, dn.c_str(), &now, AT_SYMLINK_NOFOLLOW) < 0)
        fail(pb::FileActionError::UNCERTAIN, "moved file identity unavailable");
      if (!same_published_object(st, now))
        fail(pb::FileActionError::UNCERTAIN,
             "move selected a replaced source; inspect the operation result");
      const auto new_generation =
          S_ISDIR(now.st_mode) ? directory_generation(now) : generation(now);
      reserved.finish(new_generation);
      o->set_generation(new_generation);
      o->set_size(now.st_size);
      return out;
    }
    if (r.has_trash()) {
      authorize(authority, r, authz::FILE_TRASH, workspace, scope);
      const auto h = get(r.trash().file_handle());
      if (h.path.empty())
        fail(pb::FileActionError::INVALID_ARGUMENT, "project root cannot be trashed");
      if (h.gen != r.trash().expected_generation())
        fail(pb::FileActionError::CONFLICT, "trash generation mismatch");
      auto p = h.path;
      p.pop_back();
      Fd d = directory(Handle{p, 0, true});
      struct stat hs {};
      stat_child(d.v, h.path.back(), hs);
      if (S_ISLNK(hs.st_mode) || (h.directory ? directory_generation(hs) : generation(hs)) != h.gen)
        fail(pb::FileActionError::CONFLICT, "trash object changed since it was observed");
      const auto id = r.operation_id();
      const auto stored = hex(id);
      auto* rec = out.mutable_trash();
      rec->set_handle(id);
      auto t = trash_dir(true, &effects, &r, authz::FILE_TRASH);
      auto trashed=io_step(r,authz::FILE_TRASH,true,effects,[&]{return renameat2(d.v,h.path.back().c_str(),t.v,stored.c_str(),RENAME_NOREPLACE);});
      if(trashed.value<0){errno=trashed.error;fail(pb::FileActionError::UNCERTAIN,"trash publication is uncertain");}
      effects = true;
      auto source_sync=io_step(r,authz::FILE_TRASH,true,effects,[&]{return fsync(d.v);});
      auto trash_sync=io_step(r,authz::FILE_TRASH,true,effects,[&]{return source_sync.value==0?fsync(t.v):-1;});
      if(source_sync.value<0 || trash_sync.value<0) fail(pb::FileActionError::UNCERTAIN,"trash published but directory durability is uncertain");
      struct stat st {};
      if (fstatat(t.v, stored.c_str(), &st, AT_SYMLINK_NOFOLLOW) < 0)
        fail(pb::FileActionError::UNCERTAIN, "trashed object identity unavailable");
      if (!same_published_object(hs, st))
        fail(pb::FileActionError::UNCERTAIN,
             "trash moved a concurrently changed source; inspect the operation result");
      rec->set_generation(generation(st));
      return out;
    }
    if (r.has_restore()) {
      authorize(authority, r, authz::FILE_RESTORE, workspace, scope);
      const auto& id = r.restore().trash_handle();
      if (id.size() != 16 || r.restore().expected_generation() == 0)
        fail(pb::FileActionError::INVALID_ARGUMENT, "invalid trash identity");
      valid_name(r.restore().destination_basename());
      auto t = trash_dir(false);
      const auto stored = hex(id);
      struct stat ts {};
      expect_generation(t.v, stored, r.restore().expected_generation(), &ts);
      const auto dest = get(r.restore().destination_directory_handle(), true);
      auto d = directory(dest);
      auto path = dest.path;
      path.push_back(r.restore().destination_basename());
      auto reserved = reserve_handle(path, S_ISDIR(ts.st_mode));
      auto* o = out.mutable_object();
      o->set_handle(reserved.id);
      auto restored=io_step(r,authz::FILE_RESTORE,true,effects,[&]{return renameat2(t.v,stored.c_str(),d.v,r.restore().destination_basename().c_str(),RENAME_NOREPLACE);});
      if(restored.value<0){errno=restored.error;fail(errno==EEXIST?pb::FileActionError::CONFLICT:pb::FileActionError::UNCERTAIN,"restore destination exists or publication is uncertain");}
      effects = true;
      auto trash_sync=io_step(r,authz::FILE_RESTORE,true,effects,[&]{return fsync(t.v);});
      auto destination_sync=io_step(r,authz::FILE_RESTORE,true,effects,[&]{return trash_sync.value==0?fsync(d.v):-1;});
      if(trash_sync.value<0 || destination_sync.value<0) fail(pb::FileActionError::UNCERTAIN,"restore published but directory durability is uncertain");
      struct stat st {};
      if (fstatat(d.v, r.restore().destination_basename().c_str(), &st, AT_SYMLINK_NOFOLLOW) < 0)
        fail(pb::FileActionError::UNCERTAIN, "restored object identity unavailable");
      if (!same_published_object(ts, st))
        fail(pb::FileActionError::UNCERTAIN, "restore selected a replaced trash object");
      const auto restored_generation =
          S_ISDIR(st.st_mode) ? directory_generation(st) : generation(st);
      reserved.finish(restored_generation);
      o->set_generation(restored_generation);
      o->set_size(st.st_size);
      return out;
    }
    if (r.has_trash_inspect()) {
      authorize(authority, r, authz::FILE_BROWSE, workspace, scope);
      const auto& id = r.trash_inspect().trash_handle();
      if (id.size() != 16) fail(pb::FileActionError::INVALID_ARGUMENT, "invalid trash identity");
      auto t = trash_dir(false);
      struct stat st {};
      stat_child(t.v, hex(id), st);
      if (S_ISLNK(st.st_mode)) fail(pb::FileActionError::STALE_HANDLE, "trash object changed");
      auto* entry = out.mutable_trash_entry();
      entry->set_handle(id);
      entry->set_generation(generation(st));
      entry->set_size(st.st_size < 0 ? 0 : static_cast<std::uint64_t>(st.st_size));
      return out;
    }
    if (r.has_permanent_delete()) {
      authorize(authority, r, authz::FILE_PERMANENT_DELETE, workspace, scope);
      const auto& id = r.permanent_delete().trash_handle();
      if (id.size() != 16) fail(pb::FileActionError::INVALID_ARGUMENT, "invalid trash identity");
      auto t = trash_dir(false);
      const auto stored = hex(id);
      struct stat before {};
      expect_generation(t.v, stored, r.permanent_delete().expected_generation(), &before);
      const auto quarantine = ".spatial-delete-" + hex(r.operation_id());
      out.mutable_object()->set_generation(r.permanent_delete().expected_generation());
      auto isolated_move=io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return renameat2(t.v,stored.c_str(),t.v,quarantine.c_str(),RENAME_NOREPLACE);});
      if(isolated_move.value<0) fail(pb::FileActionError::UNCERTAIN,"permanent-delete quarantine failed");
      effects = true;
      struct stat isolated {};
      if (fstatat(t.v, quarantine.c_str(), &isolated, AT_SYMLINK_NOFOLLOW) < 0)
        fail(pb::FileActionError::UNCERTAIN, "permanent-delete quarantine identity is uncertain");
      if (!same_published_object(before, isolated)) {
        auto restored=io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return renameat2(t.v,quarantine.c_str(),t.v,stored.c_str(),RENAME_NOREPLACE);});
        if(restored.value<0) fail(pb::FileActionError::UNCERTAIN,"changed trash object remains in quarantine");
        auto durable=io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return fsync(t.v);});
        if(durable.value<0) fail(pb::FileActionError::UNCERTAIN,"quarantine restoration durability is uncertain");
        fail(pb::FileActionError::CONFLICT, "trash object changed before permanent deletion");
      }
      auto removed=io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return unlinkat(t.v,quarantine.c_str(),S_ISDIR(isolated.st_mode)?AT_REMOVEDIR:0);});
      if (removed.value < 0) {
        const auto deletion_error = removed.error;
        if (io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return renameat2(t.v,quarantine.c_str(),t.v,stored.c_str(),RENAME_NOREPLACE);}).value < 0)
          fail(pb::FileActionError::UNCERTAIN, "undeleted trash object remains in quarantine");
        if(io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return fsync(t.v);}).value<0)
          fail(pb::FileActionError::UNCERTAIN,"undeleted trash restoration durability is uncertain");
        fail(deletion_error == ENOTEMPTY ? pb::FileActionError::CONFLICT
                                         : pb::FileActionError::UNAVAILABLE,
             "permanent deletion failed; trash object restored");
      }
      if(io_step(r,authz::FILE_PERMANENT_DELETE,true,effects,[&]{return fsync(t.v);}).value<0)
        fail(pb::FileActionError::UNCERTAIN,"permanent deletion durability is uncertain");
      return out;
    }
    if (r.has_copy_job()) {
      authorize(authority, r, authz::FILE_COPY, workspace, scope);
      fail(pb::FileActionError::UNIMPLEMENTED,
           "copy requires the asynchronous broker process-scope job adapter");
    }
    fail(pb::FileActionError::INVALID_ARGUMENT, "no file action selected");
  }
};
FileEngine::FileEngine(int root, std::string workspace, std::vector<std::string> scope,
                       FileAuthority& authority, MonotonicNow now, FileEffectExecutor* effects)
    : impl_(std::make_unique<Impl>(root, std::move(workspace), std::move(scope), authority,
                                   std::move(now), effects)) {}
FileEngine::~FileEngine() = default;
pb::FileActionReply FileEngine::handle(const pb::FileActionRequest& r) {
  std::lock_guard lock(impl_->mutex);
  // Allocate the error envelope before any filesystem effect. Catch paths reuse
  // these buffers, including when the triggering failure is an allocation failure.
  pb::FileActionReply failure;
  failure.set_protocol_version(1);
  failure.set_operation_id(r.operation_id().size() == 16 ? r.operation_id() : std::string{});
  auto* error = failure.mutable_error();
  error->mutable_detail()->reserve(256);
  error->set_recovery_operation_id(failure.operation_id());
  bool effects = false;
  try {
    return impl_->run(r, effects);
  } catch (const Failure& e) {
    error->set_code(e.code);
    error->set_detail(e.message);
    if (!effects && e.code != pb::FileActionError::UNCERTAIN) error->clear_recovery_operation_id();
    return failure;
  } catch (const std::bad_alloc&) {
    error->set_code(effects ? pb::FileActionError::UNCERTAIN : pb::FileActionError::EXHAUSTED);
    error->set_detail(effects ? "filesystem effect occurred before allocation failure"
                              : "file engine memory limit reached");
    if (!effects) error->clear_recovery_operation_id();
    return failure;
  } catch (...) {
    error->set_code(effects ? pb::FileActionError::UNCERTAIN : pb::FileActionError::UNAVAILABLE);
    error->set_detail(effects ? "filesystem outcome requires recovery"
                              : "file engine failed closed");
    if (!effects) error->clear_recovery_operation_id();
    return failure;
  }
}
}  // namespace spatial
