#include "workspace/file_engine.hpp"
#include "workspace/file_action_policy.hpp"
#include "workspace/copy_worker.hpp"
#include "workspace/resources.hpp"

#include <dirent.h>
#include <memory>
#include <fcntl.h>
#include <linux/btrfs.h>
#include <linux/fs.h>
#include <linux/magic.h>
#include <linux/openat2.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <cstring>
#include <limits>
#include <mutex>
#include <optional>
#include <random>
#include <stdexcept>
#include <string_view>
#include <thread>
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
bool can_skip_recovered_scope_cleanup(bool same_owner_root,
                                      std::string_view recorded_boot_id,
                                      std::string_view current_boot_id) noexcept {
  if (same_owner_root) return false;
  if (recorded_boot_id.empty() || current_boot_id.empty()) return false;
  return recorded_boot_id != current_boot_id;
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
constexpr char kCopyStagePrefix[] = ".spatial-copy-staging-";
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
std::uint64_t unix_millis() {
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count());
}
void set_copy_identity(pb::FileCopyFilesystemIdentity* output,const struct stat& info,
                       bool directory,std::uint64_t gen=0) {
  output->set_device(static_cast<std::uint64_t>(info.st_dev));
  output->set_inode(static_cast<std::uint64_t>(info.st_ino));
  output->set_generation(gen);
  output->set_type(directory?pb::FileCopyFilesystemIdentity::DIRECTORY:
                           pb::FileCopyFilesystemIdentity::REGULAR_FILE);
}
std::uint64_t directory_generation(const struct stat& s) {
  std::uint64_t x = static_cast<std::uint64_t>(s.st_ino) * 0x9e3779b185ebca87ULL;
  x ^= static_cast<std::uint64_t>(s.st_dev) + (x << 6) + (x >> 2);
  return x ? x : 1;
}
std::uint64_t subvolume_identity(int fd) {
  struct statfs filesystem {};
  if (fstatfs(fd, &filesystem) < 0)
    fail(pb::FileActionError::UNAVAILABLE, "cannot inspect project filesystem");
  if (static_cast<unsigned long>(filesystem.f_type) != BTRFS_SUPER_MAGIC)
    return 0;
  btrfs_ioctl_get_subvol_info_args info {};
  if (ioctl(fd, BTRFS_IOC_GET_SUBVOL_INFO, &info) < 0 || info.treeid == 0)
    fail(pb::FileActionError::UNAVAILABLE, "cannot verify project subvolume identity");
  return info.treeid;
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
           n != ".spatial-trash" && n != ".spatial-copy-staging" &&
           !n.starts_with(kCopyStagePrefix),
       "invalid or reserved POSIX basename");
}
int open_copy_stage_root(int parent, const std::string& name) {
  open_how how{};
  how.flags = O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK;
  how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV;
  return static_cast<int>(syscall(SYS_openat2, parent, name.c_str(), &how, sizeof(how)));
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
bool remove_staged_entry(int parent, const char* name, dev_t device, unsigned depth, std::uint32_t& visited) noexcept {
  if (depth > 128 || ++visited > 200000) return false;
  struct stat st{};
  if (fstatat(parent, name, &st, AT_SYMLINK_NOFOLLOW) < 0) return errno == ENOENT;
  if (st.st_dev != device || S_ISLNK(st.st_mode) || (S_ISREG(st.st_mode) && st.st_nlink != 1))
    return false;
  if (S_ISDIR(st.st_mode)) {
    open_how how{}; how.flags=O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW;
    how.resolve=RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS|RESOLVE_NO_XDEV;
    Fd child(static_cast<int>(syscall(SYS_openat2,parent,name,&how,sizeof(how))));
    struct stat opened{};
    if(child.v<0 || fstat(child.v,&opened)<0 || opened.st_dev!=device ||
       opened.st_dev!=st.st_dev || opened.st_ino!=st.st_ino) return false;
    Fd copy(openat(child.v,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC));
    if(copy.v<0)return false;
    DIR* stream=fdopendir(copy.v); if(!stream)return false; copy.v=-1;
    for(;;) {
      errno=0; auto* entry=readdir(stream);
      if(!entry) { const bool ok=errno==0; closedir(stream); if(!ok)return false; break; }
      if(!strcmp(entry->d_name,".")||!strcmp(entry->d_name,".."))continue;
      if(!remove_staged_entry(child.v,entry->d_name,device,depth+1,visited)){closedir(stream);return false;}
    }
    struct stat current{};
    if(fstatat(parent,name,&current,AT_SYMLINK_NOFOLLOW)<0 || current.st_dev!=st.st_dev ||
       current.st_ino!=st.st_ino || unlinkat(parent,name,AT_REMOVEDIR)<0)return false;
    return true;
  }
  if(!S_ISREG(st.st_mode))return false;
  struct stat current{};
  return fstatat(parent,name,&current,AT_SYMLINK_NOFOLLOW)==0 &&
      current.st_dev==st.st_dev && current.st_ino==st.st_ino && unlinkat(parent,name,0)==0;
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
  Fd staging_parent;
  Fd staging_root;
  std::string staging_root_name;
  struct stat staging_root_identity{};
  std::uint64_t project_subvolume = 0;
  std::string workspace;
  std::vector<std::string> scope;
  FileAuthority& authority;
  FileEffectExecutor* effects;
  FileEngine::MonotonicNow now;
  WorkspaceResources* resources;
  FileOperationJournal* journal;
  FileOperationJournal::Observer journal_observer;
  const PinnedFileOperationJournal* cleanup_pin=nullptr;
  struct CopyJob {
    std::string id, principal, source_id, destination_id, source_name, destination_name, staging_name;
    Handle source_handle, destination_handle;
    struct stat source_identity{}, destination_identity{}, staging_root_identity{}, staging_identity{};
    Fd source_parent, destination_parent, staging_directory;
    std::unique_ptr<CopyTreeJob> worker;
    pb::FileCopyJobReceipt terminal;
    FileOperationJournal::CopyJobRecord durable;
    bool finished = false;
    std::uint64_t maximum_bytes=0;std::uint32_t maximum_entries=0;
    std::uint64_t bytes_progress=0;std::uint32_t entries_progress=0;
    Clock::time_point deadline{};Clock::time_point last_use{};
  };
  std::unordered_map<std::string, Handle> handles;
  bool unsafe_legacy_copy_namespace = false;
  std::unordered_map<std::string, std::unique_ptr<CopyJob>> copy_jobs;
  std::unordered_map<std::string, std::pair<dev_t, ino_t>> retained_uncertain_stages;
  std::unordered_map<std::string, Cursor> cursors;
  std::mutex mutex;
  Impl(int fd, std::string w, std::vector<std::string> s, FileAuthority& a,
       FileEngine::MonotonicNow clock, FileEffectExecutor* executor, WorkspaceResources* worker_resources,
       int stage_parent_fd, std::string stage_root_name,
       FileOperationJournal* copy_journal, FileOperationJournal::Observer copy_journal_observer,
        const PinnedFileOperationJournal* current_cleanup_pin)
      : root(openat(fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)),
        staging_parent(stage_parent_fd >= 0
                           ? openat(stage_parent_fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)
                           : -1),
        staging_root_name(stage_root_name.empty() ? ".spatial-copy-staging" : std::move(stage_root_name)),
        workspace(std::move(w)),
        scope(std::move(s)),
        authority(a),
        effects(executor),
        now(std::move(clock)),
        resources(worker_resources),
        journal(copy_journal),
        journal_observer(std::move(copy_journal_observer)),
         cleanup_pin(current_cleanup_pin) {
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
    project_subvolume = subvolume_identity(root.v);
    if (stage_parent_fd < 0)
      staging_parent = Fd(fcntl(root.v, F_DUPFD_CLOEXEC, 0));
    struct stat stage_parent_info{};
    if (staging_parent.v < 0 || fstat(staging_parent.v, &stage_parent_info) < 0 ||
        !S_ISDIR(stage_parent_info.st_mode) || staging_root_name.empty() ||
        staging_root_name.find('/') != std::string::npos ||
        staging_root_name.find('\0') != std::string::npos)
      throw std::invalid_argument("broker-private copy staging parent is invalid");
    bool legacy_test_stage_created=false;
    if(staging_root_name==".spatial-copy-staging" && stage_parent_fd<0) {
      if(mkdirat(staging_parent.v,staging_root_name.c_str(),0700)==0) legacy_test_stage_created=true;
      else if(errno!=EEXIST) throw std::invalid_argument("cannot create legacy test staging root");
    }
    staging_root = Fd(open_copy_stage_root(staging_parent.v, staging_root_name));
    struct stat stage {};
    if (staging_root.v < 0 || fstat(staging_root.v, &stage) < 0 || !S_ISDIR(stage.st_mode) ||
        stage.st_dev != st.st_dev || stage_parent_info.st_dev != st.st_dev ||
        stage.st_uid != geteuid() || (stage.st_mode & 0777) != 0700 ||
        (project_subvolume != 0 && subvolume_identity(staging_root.v) != project_subvolume))
      throw std::invalid_argument("broker-private sibling staging root has an unsafe identity or subvolume");
    staging_root_identity = stage;
    if(journal) recover_copy_jobs();
    if(legacy_test_stage_created && fsync(staging_parent.v)<0)
      throw std::invalid_argument("legacy test staging root is not durable");
    Fd initial_entries(openat(staging_root.v, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    if (initial_entries.v < 0) throw std::invalid_argument("cannot inspect copy staging recovery state");
    DIR* initial_stream = fdopendir(initial_entries.v);
    if (!initial_stream) throw std::invalid_argument("cannot inspect copy staging recovery state");
    initial_entries.v = -1;
    bool stage_empty = true;
    for (;;) {
      errno = 0; auto* entry = readdir(initial_stream);
      if (!entry) { if (errno != 0) stage_empty = false; break; }
      if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
      const auto retained=retained_uncertain_stages.find(entry->d_name);
      struct stat named{};
      if(retained==retained_uncertain_stages.end() ||
         fstatat(staging_root.v,entry->d_name,&named,AT_SYMLINK_NOFOLLOW)<0 ||
         !S_ISDIR(named.st_mode) || named.st_dev!=retained->second.first ||
         named.st_ino!=retained->second.second) stage_empty=false;
    }
    closedir(initial_stream);
    if (!stage_empty)
      throw std::invalid_argument("unreconciled copy staging entries prevent cabinet file actions");
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
    if (file_policy::is_registered_root_admin_path(h.path))
      fail(pb::FileActionError::STALE_HANDLE, "registered root administration is outside cabinet files");
    if (directory && !h.directory)
      fail(pb::FileActionError::INVALID_ARGUMENT, "directory handle required");
    // Resolve at the operation boundary. Do not validate one descriptor and then
    // reopen the same mutable path without checking the descriptor actually used.
    return h;
  }
  std::string add_handle(std::vector<std::string> path, std::uint64_t gen, bool dir) {
    if (file_policy::is_registered_root_admin_path(path))
      fail(pb::FileActionError::INVALID_ARGUMENT, "registered root administration is outside cabinet files");
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
  void recover_copy_jobs() {
    if(!journal) return;
    const auto records=journal->recovery_copy_jobs(workspace,journal_observer);
    auto parents=journal->retained_git_worker_parents(journal_observer);
    const auto active_parent=journal->find_git_worker_parent(journal_observer);
    if(active_parent.state!=GitWorkerParentRecord::State::absent) parents.push_back(active_parent);
    if(cleanup_pin) {
      for(const auto& record:records) {
        if(record.worktree_id()!=scope.front()) continue;
        const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& candidate) {
          return candidate.state==GitWorkerParentRecord::State::bound &&
              candidate.root_device==record.broker_root_device() && candidate.root_inode==record.broker_root_inode() &&
              candidate.boot_id==record.broker_root_boot_id() &&
              candidate.identity_bytes==record.process_scope_parent().SerializeAsString();
        });
        const auto disposition=journal->validated_scope_cleanup_outcome(record,
            parent==parents.end()?nullptr:&*parent,*cleanup_pin,journal_observer);
        if(disposition && disposition->kind()!=pb::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED)
          throw std::runtime_error("Copy worktree quarantined by durable scope cleanup conflict");
      }
    }
    const auto unsafe_legacy_record = [](const auto& record) {
      std::vector<std::string> source(record.source_path_components().begin(),
                                      record.source_path_components().end());
      std::vector<std::string> destination(record.destination_directory_components().begin(),
                                           record.destination_directory_components().end());
      destination.push_back(record.destination_basename());
      return file_policy::is_registered_root_admin_path(source) ||
             file_policy::is_registered_root_admin_path(destination);
    };
    if (std::any_of(records.begin(), records.end(), [&](const auto& record) {
          return record.worktree_id() == scope.front() && unsafe_legacy_record(record);
        })) {
      unsafe_legacy_copy_namespace = true;
      for (const auto& record : records) {
        if (record.worktree_id() != scope.front() || !unsafe_legacy_record(record) ||
            record.staging_name().empty() || !record.has_staging()) continue;
        struct stat named{};
        if (fstatat(staging_root.v, record.staging_name().c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0) {
          if (!S_ISDIR(named.st_mode) ||
              static_cast<std::uint64_t>(named.st_dev) != record.staging().device() ||
              static_cast<std::uint64_t>(named.st_ino) != record.staging().inode())
            throw std::runtime_error("Unsafe legacy copy stage identity changed; evidence retained");
          retained_uncertain_stages.emplace(record.staging_name(),
                                            std::make_pair(named.st_dev, named.st_ino));
        } else if (errno != ENOENT) {
          throw std::runtime_error("Cannot inspect unsafe legacy copy stage; evidence retained");
        }
      }
      return;
    }
    const bool has_uncertain=std::any_of(records.begin(),records.end(),[](const auto& record) {
      return record.state()==pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN;
    });
    if(!resources) {
      if(has_uncertain) {
        for(const auto& record:records) {
          if(record.state()!=pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN || record.worktree_id()!=scope.front()) continue;
          struct stat named{};
          if(fstatat(staging_root.v,record.staging_name().c_str(),&named,AT_SYMLINK_NOFOLLOW)<0) {
            if(errno==ENOENT) continue;
            throw std::runtime_error("Cannot inspect uncertain copy staging evidence");
          }
          if(!S_ISDIR(named.st_mode) || static_cast<std::uint64_t>(named.st_dev)!=record.staging().device() ||
             static_cast<std::uint64_t>(named.st_ino)!=record.staging().inode())
            throw std::runtime_error("Uncertain copy staging identity changed");
          retained_uncertain_stages.emplace(record.staging_name(),std::make_pair(named.st_dev,named.st_ino));
        }
      }
      return;
    }
    struct stat root_stat{},stage_root_stat{};
    if(fstat(root.v,&root_stat)<0 || fstat(staging_root.v,&stage_root_stat)<0)
      throw std::runtime_error("Cannot inspect copy recovery roots");
    const auto current_broker=resources->broker_root_identity();
    std::ifstream boot_file("/proc/sys/kernel/random/boot_id");
    std::string current_boot;std::getline(boot_file,current_boot);
    for(auto record:records) {
      if(record.worktree_id()!=scope.front()) continue;
      if(record.worktree_root_device()!=static_cast<std::uint64_t>(root_stat.st_dev) ||
         record.worktree_root_inode()!=static_cast<std::uint64_t>(root_stat.st_ino) ||
         record.staging_root().device()!=static_cast<std::uint64_t>(stage_root_stat.st_dev) ||
         record.staging_root().inode()!=static_cast<std::uint64_t>(stage_root_stat.st_ino))
        throw std::runtime_error("Copy recovery root identity differs from its durable record");
      const auto parent=std::find_if(parents.begin(),parents.end(),[&](const auto& candidate) {
        return candidate.state==GitWorkerParentRecord::State::bound &&
            candidate.root_device==record.broker_root_device() && candidate.root_inode==record.broker_root_inode() &&
            candidate.boot_id==record.broker_root_boot_id() &&
            candidate.identity_bytes==record.process_scope_parent().SerializeAsString();
      });
      std::optional<pb::FileCopyScopeCleanupOutcome> disposition;
      if(cleanup_pin) disposition=journal->validated_scope_cleanup_outcome(record,
          parent==parents.end()?nullptr:&*parent,*cleanup_pin,journal_observer);
      else if(parent==parents.end()) throw std::runtime_error("Copy recovery parent generation is missing");
      if(disposition && disposition->kind()!=pb::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED)
        throw std::runtime_error("Copy worktree quarantined by durable scope cleanup conflict");
      if(parent==parents.end() && (!disposition ||
          disposition->kind()!=pb::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED))
        throw std::runtime_error("Copy recovery parent generation is missing");
      const std::filesystem::path prior_root=parent==parents.end()?std::filesystem::path{}:
          std::filesystem::path(parent->root_path);
      if(parent!=parents.end() && prior_root.parent_path()!=resources->broker_root_path().parent_path())
        throw std::runtime_error("Copy recovery root is outside the delegated parent");
      const auto parent_path=prior_root/"spatial-git-workers";
      const bool start_intent=record.state()==pb::FILE_COPY_JOB_JOURNAL_START_INTENT;
      bool published=false;
      bool publication_durable=false;
      Fd destination;
      if(record.has_prepared_result()) {
        destination=Fd(openat(root.v,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW));
        for(const auto& component:record.destination_directory_components())
          destination=Fd(open_child_dir(destination.v,component));
        struct stat destination_parent{};
        if(destination.v<0 || fstat(destination.v,&destination_parent)<0 ||
           destination_parent.st_dev!=record.destination_directory().device() ||
           destination_parent.st_ino!=record.destination_directory().inode())
          throw std::runtime_error("Copy recovery destination identity changed");
        struct stat published_stat{};
        if(fstatat(destination.v,record.destination_basename().c_str(),&published_stat,AT_SYMLINK_NOFOLLOW)==0) {
          const auto& expected=record.prepared_result().output();
          published=static_cast<std::uint64_t>(published_stat.st_dev)==expected.device() &&
              static_cast<std::uint64_t>(published_stat.st_ino)==expected.inode() &&
              (S_ISDIR(published_stat.st_mode)==(expected.type()==pb::FileCopyFilesystemIdentity::DIRECTORY));
        } else if(errno!=ENOENT) {
          throw std::runtime_error("Cannot inspect copy recovery publication target");
        }
      }

      const bool same_owner_root=record.broker_root_device()==current_broker.first &&
          record.broker_root_inode()==current_broker.second && record.broker_root_boot_id()==current_boot;
      const bool prior_boot_drained_scope=
          file_engine_detail::can_skip_recovered_scope_cleanup(
              same_owner_root,record.broker_root_boot_id(),current_boot);
      if(!published && (record.state()==pb::FILE_COPY_JOB_JOURNAL_QUEUED ||
                        record.state()==pb::FILE_COPY_JOB_JOURNAL_RUNNING)) {
        journal->request_copy_job_cancel(record,journal_observer);
      }
      if(!prior_boot_drained_scope) {
        try {
          if(record.has_process_scope()) {
            bool must_reopen=!record.has_drained_process_scope();
            if(!must_reopen) {
              struct stat named{};
              const auto path=parent_path/record.process_scope().scope_name();
              if(lstat(path.c_str(),&named)==0) {
                if(static_cast<std::uint64_t>(named.st_dev)!=record.process_scope().device() ||
                   static_cast<std::uint64_t>(named.st_ino)!=record.process_scope().inode())
                  throw std::runtime_error("Acknowledged copy scope name was replaced");
                must_reopen=true;
              } else if(errno!=ENOENT) {
                throw std::runtime_error("Cannot inspect acknowledged copy scope");
              }
            }
            if(must_reopen) {
              const bool stopped=supervisor::ProcessScope::stop_recovered(parent_path,
                  record.process_scope_parent().device(),record.process_scope_parent().inode(),record.process_scope(),
                  [&](const auto& identity) {
                    if(record.state()==pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN)return;
                    *record.mutable_drained_process_scope()=identity;
                    record.set_updated_at_unix_ms(std::max(record.updated_at_unix_ms(),unix_millis()));
                    journal->update_copy_job(record,journal_observer);
                  });
              if(!stopped && !record.has_drained_process_scope())
                throw std::runtime_error("Recorded copy scope is absent without durable drain proof");
            }
          } else if(!disposition ||
                    disposition->kind()!=pb::FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED) {
            supervisor::ProcessScope::remove_reserved_empty(parent_path,
                record.process_scope_parent(),record.reserved_process_scope_name());
          }
        } catch(...) {
          if(record.state()!=pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN) {
            record.set_state(pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN);
            auto* receipt=record.mutable_terminal_receipt();receipt->set_job_id(record.job_id());
            receipt->set_state(pb::FileCopyJobReceipt::UNCERTAIN);
            receipt->set_detail("Exact worker drain could not be proven; staged evidence retained");
            record.set_updated_at_unix_ms(std::max(record.updated_at_unix_ms(),unix_millis()));
            journal->update_copy_job(record,journal_observer);
          }
          throw std::runtime_error("Copy worktree quarantined because exact worker drain is unconfirmed");
        }
      }

      // Seeing the prepared inode after rename does not prove its directory
      // entry survived a crash. Keep the exact destination directory pinned.
      if(published && record.state()==pb::FILE_COPY_JOB_JOURNAL_RUNNING) {
#if defined(SPATIAL_TEST_COPY_RECOVERY_FAULTS)
        const char* fault=std::getenv("SPATIAL_TEST_COPY_RECOVERY_DESTINATION_SYNC_FAIL");
        publication_durable=!(fault && std::string_view(fault)=="1") && fsync(destination.v)==0;
#else
        publication_durable=fsync(destination.v)==0;
#endif
      }
      if(record.state()==pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN) {
        struct stat named{};
        if(fstatat(staging_root.v,record.staging_name().c_str(),&named,AT_SYMLINK_NOFOLLOW)==0) {
          if(!S_ISDIR(named.st_mode) || static_cast<std::uint64_t>(named.st_dev)!=record.staging().device() ||
             static_cast<std::uint64_t>(named.st_ino)!=record.staging().inode())
            throw std::runtime_error("Uncertain copy staging identity changed");
          retained_uncertain_stages.emplace(record.staging_name(),std::make_pair(named.st_dev,named.st_ino));
        } else if(errno!=ENOENT) {
          throw std::runtime_error("Cannot inspect uncertain copy staging evidence");
        }
        continue;
      }

      struct stat named_stage{};
      const bool stage_exists=fstatat(staging_root.v,record.staging_name().c_str(),
                                      &named_stage,AT_SYMLINK_NOFOLLOW)==0;
      if(!stage_exists && errno!=ENOENT)
        throw std::runtime_error("Cannot inspect recorded copy stage");
      bool staged_output_intact=false;
      if(stage_exists) {
        if(!S_ISDIR(named_stage.st_mode))
          throw std::runtime_error("Recorded copy stage is not a directory");
        if(!start_intent &&
           (static_cast<std::uint64_t>(named_stage.st_dev)!=record.staging().device() ||
            static_cast<std::uint64_t>(named_stage.st_ino)!=record.staging().inode()))
          throw std::runtime_error("Recorded copy stage identity changed");
        Fd stage(open_child_dir(staging_root.v,record.staging_name()));
        struct stat stage_open{};
        if(stage.v<0 || fstat(stage.v,&stage_open)<0 || stage_open.st_dev!=named_stage.st_dev ||
           stage_open.st_ino!=named_stage.st_ino)
          throw std::runtime_error("Cannot pin exact recorded copy stage");
        if(start_intent) {
          Fd entries_fd(openat(stage.v,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC));
          DIR* entries=entries_fd.v>=0?fdopendir(entries_fd.v):nullptr;
          if(!entries)throw std::runtime_error("Cannot inspect unbound copy stage");
          entries_fd.v=-1;bool empty=true;
          for(;;) {
            errno=0;auto* entry=readdir(entries);
            if(!entry){const auto error=errno;closedir(entries);if(error)throw std::runtime_error("Cannot read unbound copy stage");break;}
            if(strcmp(entry->d_name,".") && strcmp(entry->d_name,".."))empty=false;
          }
          if(!empty)throw std::runtime_error("Unbound copy stage contains unexpected data");
        } else if(record.has_prepared_result()) {
          struct stat staged_output{};
          if(fstatat(stage.v,record.prepared_result().staging_output_name().c_str(),
                     &staged_output,AT_SYMLINK_NOFOLLOW)==0) {
            const auto& expected=record.prepared_result().output();
            staged_output_intact=static_cast<std::uint64_t>(staged_output.st_dev)==expected.device() &&
                static_cast<std::uint64_t>(staged_output.st_ino)==expected.inode();
          } else if(errno!=ENOENT) {
            throw std::runtime_error("Cannot inspect prepared copy output");
          }
        }

        const bool unresolved_prepared_output=record.has_prepared_result() &&
            !published && !staged_output_intact;
        if(!unresolved_prepared_output && (!published || publication_durable)) {
          Fd entries_fd(openat(stage.v,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC));
          DIR* entries=entries_fd.v>=0?fdopendir(entries_fd.v):nullptr;
          if(!entries) throw std::runtime_error("Cannot enumerate recorded copy stage");
          entries_fd.v=-1;std::uint32_t visited=0;
          for(;;) {
            errno=0;auto* entry=readdir(entries);
            if(!entry){const auto error=errno;closedir(entries);if(error)throw std::runtime_error("Cannot read recorded copy stage");break;}
            if(!strcmp(entry->d_name,".")||!strcmp(entry->d_name,".."))continue;
            if(!remove_staged_entry(stage.v,entry->d_name,named_stage.st_dev,0,visited)) {
              closedir(entries);throw std::runtime_error("Cannot safely clean recorded copy stage");
            }
          }
          struct stat again{};
          if(fstatat(staging_root.v,record.staging_name().c_str(),&again,AT_SYMLINK_NOFOLLOW)<0 ||
             again.st_dev!=named_stage.st_dev || again.st_ino!=named_stage.st_ino ||
             unlinkat(staging_root.v,record.staging_name().c_str(),AT_REMOVEDIR)<0 ||
             fsync(staging_root.v)<0)
            throw std::runtime_error("Cannot remove recorded copy stage");
        }
      }

      pb::FileCopyJobReceipt terminal;terminal.set_job_id(record.job_id());
      if(published && publication_durable && record.state()==pb::FILE_COPY_JOB_JOURNAL_RUNNING) {
        terminal.set_state(pb::FileCopyJobReceipt_State_COMPLETE);
        terminal.set_bytes_copied(record.prepared_result().bytes_copied());
        terminal.set_entries_copied(record.prepared_result().entries_copied());
        terminal.set_content_digest(record.prepared_result().content_sha256());
        terminal.set_detail("recovery verified the prepared inode at the publication target");
        record.set_state(pb::FILE_COPY_JOB_JOURNAL_COMPLETE);
      } else if(!published &&
                (!record.has_prepared_result() || staged_output_intact || start_intent)) {
        terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
        terminal.set_bytes_copied(record.bytes_progress());
        terminal.set_entries_copied(record.entries_progress());
        terminal.set_detail("recovery stopped the exact worker and discarded private staging before publication");
        record.set_state(pb::FILE_COPY_JOB_JOURNAL_CANCELLED);
      } else {
        terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
        terminal.set_bytes_copied(record.bytes_progress());
        terminal.set_entries_copied(record.entries_progress());
        terminal.set_detail("recovery preserved staged evidence because publication could not be proven");
        record.set_state(pb::FILE_COPY_JOB_JOURNAL_UNCERTAIN);
        if(stage_exists)
          retained_uncertain_stages.emplace(record.staging_name(),std::make_pair(named_stage.st_dev,named_stage.st_ino));
      }
      record.set_bytes_progress(terminal.bytes_copied());
      record.set_entries_progress(terminal.entries_copied());
      *record.mutable_terminal_receipt()=terminal;
      record.set_updated_at_unix_ms(std::max(record.updated_at_unix_ms(),unix_millis()));
      journal->update_copy_job(record,journal_observer);
    }
  }
  void request_copy_cancel(CopyJob& job) {
    if(journal) journal->request_copy_job_cancel(job.durable,journal_observer);
  }
  void persist_copy_job(CopyJob& job) {
    if(!journal) return;
    job.durable.set_updated_at_unix_ms(std::max(job.durable.updated_at_unix_ms(),unix_millis()));
    journal->update_copy_job(job.durable,journal_observer);
  }
  void persist_copy_terminal(CopyJob& job) {
    using namespace pb;
    if(job.terminal.state()!=FileCopyJobReceipt_State_COMPLETE) {
      job.terminal.set_bytes_copied(job.bytes_progress);
      job.terminal.set_entries_copied(job.entries_progress);
    }
    if(!journal) return;
    switch(job.terminal.state()) {
      case FileCopyJobReceipt_State_COMPLETE: job.durable.set_state(FILE_COPY_JOB_JOURNAL_COMPLETE); break;
      case FileCopyJobReceipt_State_CANCELLED: job.durable.set_state(FILE_COPY_JOB_JOURNAL_CANCELLED); break;
      case FileCopyJobReceipt_State_CONFLICT: job.durable.set_state(FILE_COPY_JOB_JOURNAL_CONFLICT); break;
      case FileCopyJobReceipt_State_UNCERTAIN: job.durable.set_state(FILE_COPY_JOB_JOURNAL_UNCERTAIN); break;
      case FileCopyJobReceipt_State_FAILED: job.durable.set_state(FILE_COPY_JOB_JOURNAL_FAILED); break;
      default: throw std::logic_error("copy job terminal receipt has no terminal state");
    }
    job.durable.set_bytes_progress(job.terminal.bytes_copied());
    job.durable.set_entries_progress(job.terminal.entries_copied());
    *job.durable.mutable_terminal_receipt()=job.terminal;
    job.durable.set_updated_at_unix_ms(std::max(job.durable.updated_at_unix_ms(),unix_millis()));
    journal->update_copy_job(job.durable,journal_observer);
  }
  void persist_copy_terminal_noexcept(CopyJob& job) noexcept {
    try { persist_copy_terminal(job); }
    catch(...) {
      job.terminal.Clear();job.terminal.set_job_id(job.id);
      job.terminal.set_state(pb::FileCopyJobReceipt::UNCERTAIN);
      job.terminal.set_detail("Terminal journal commit is unconfirmed");
      std::fputs("copy terminal journal commit unconfirmed; durable recovery required\n",stderr);
    }
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
  FileIoResult remove_copy_stage(CopyJob& job) noexcept {
      FileIoResult result; struct stat named_root{}, root_open{}, named{}, opened{};
      if(fstat(staging_root.v,&root_open)<0 ||
         root_open.st_dev!=job.staging_root_identity.st_dev ||
         root_open.st_ino!=job.staging_root_identity.st_ino ||
         fstatat(staging_parent.v,staging_root_name.c_str(),&named_root,AT_SYMLINK_NOFOLLOW)<0 ||
         named_root.st_dev!=job.staging_root_identity.st_dev ||
         named_root.st_ino!=job.staging_root_identity.st_ino || !S_ISDIR(named_root.st_mode)) {
        result.value=-1; result.error=ESTALE; return result;
      }
      Fd named_root_open(open_copy_stage_root(staging_parent.v, staging_root_name));
      struct stat named_root_open_identity{};
      if(named_root_open.v<0 || fstat(named_root_open.v,&named_root_open_identity)<0 ||
         named_root_open_identity.st_dev!=job.staging_root_identity.st_dev ||
         named_root_open_identity.st_ino!=job.staging_root_identity.st_ino) {
        result.value=-1; result.error=ESTALE; return result;
      }
      if(fstatat(staging_root.v,job.staging_name.c_str(),&named,AT_SYMLINK_NOFOLLOW)<0 ||
         named.st_dev!=job.staging_identity.st_dev || named.st_ino!=job.staging_identity.st_ino ||
         !S_ISDIR(named.st_mode)) {
        result.value=-1; result.error=ESTALE; return result;
      }
      if(fstat(job.staging_directory.v,&opened)<0 || opened.st_dev!=named.st_dev ||
         opened.st_ino!=named.st_ino) {
        result.value=-1; result.error=ESTALE; return result;
      }
      Fd entries_fd(openat(job.staging_directory.v,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC));
      if(entries_fd.v<0) { result.value=-1; result.error=errno; return result; }
      DIR* entries=fdopendir(entries_fd.v);
      if(!entries) { result.value=-1; result.error=errno; return result; }
      entries_fd.v=-1; bool changed=false, ok=true; std::uint32_t visited=0;
      for(;;) {
        errno=0; auto* entry=readdir(entries);
        if(!entry) { if(errno)ok=false; break; }
        if(!strcmp(entry->d_name,".")||!strcmp(entry->d_name,".."))continue;
        changed=true;
        if(!remove_staged_entry(job.staging_directory.v,entry->d_name,named.st_dev,0,visited)){ok=false;break;}
      }
      const int read_error=errno; closedir(entries);
      if(!ok) { result.value=-1; result.error=read_error?read_error:ESTALE; result.effect_may_have_occurred=changed; return result; }
      changed=true;
      if(unlinkat(staging_root.v,job.staging_name.c_str(),AT_REMOVEDIR)<0 ||
         fsync(staging_root.v)<0) {
        result.value=-1; result.error=errno; result.effect_may_have_occurred=changed; return result;
      }
      result.value=0; result.effect_may_have_occurred=changed; return result;
  }
  void cleanup_copy_stage(CopyJob& job, const pb::FileActionRequest& request, bool& effects) {
    auto removed=io_result_step(request,authz::FILE_COPY,true,effects,[&]{return remove_copy_stage(job);});
    if(removed.value<0) fail(pb::FileActionError::UNCERTAIN,"copy staging cleanup cannot be proven");
  }
  void cleanup_copy_stage_internal(CopyJob& job) {
    const auto removed=remove_copy_stage(job);
    if(removed.value<0) fail(pb::FileActionError::UNCERTAIN,"revoked copy staging cleanup cannot be proven");
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
    if (unsafe_legacy_copy_namespace)
      fail(pb::FileActionError::UNCERTAIN, "unsafe legacy copy recovery is quarantined; evidence retained");
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
          if (name == "." || name == ".." || name == ".spatial-trash" ||
              (h.path.empty() && name == ".git") ||
              (name == ".spatial-copy-staging" || name.starts_with(kCopyStagePrefix) || name.starts_with(".spatial-copy-stage-"))) continue;
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
      const auto& request = r.copy_job();
      const std::string principal = r.principal().SerializeAsString();
      if (request.command() == pb::FileCopyJob_Command_START) {
        authorize(authority, r, authz::FILE_COPY, workspace, scope);
        if (!request.job_id().empty() || request.maximum_bytes() == 0 ||
            request.maximum_bytes() > 4ULL * 1024 * 1024 * 1024 ||
            request.maximum_entries() == 0 || request.maximum_entries() > 200000)
          fail(pb::FileActionError::INVALID_ARGUMENT, "invalid cabinet copy start request");
        for (auto it=copy_jobs.begin();it!=copy_jobs.end();) {
          if (it->second->finished && now()-it->second->last_use>std::chrono::minutes(10))
            it=copy_jobs.erase(it);
          else ++it;
        }
        if (copy_jobs.size() >= 64) {
          auto oldest=copy_jobs.end();
          for(auto it=copy_jobs.begin();it!=copy_jobs.end();++it)
            if(it->second->finished&&(oldest==copy_jobs.end()||it->second->last_use<oldest->second->last_use))oldest=it;
          if(oldest!=copy_jobs.end())copy_jobs.erase(oldest);
        }
        if (copy_jobs.size() >= 64)
          fail(pb::FileActionError::EXHAUSTED, "cabinet copy job table is full");
        std::size_t active = 0;
        for (const auto& [id, job] : copy_jobs) { (void)id; if (!job->finished) ++active; }
        if (active >= 16)
          fail(pb::FileActionError::EXHAUSTED, "cabinet copy active-job limit reached");
        const auto source = get(request.source_handle());
        const auto destination = get(request.destination_directory_handle(), true);
        if (source.path.empty())
          fail(pb::FileActionError::INVALID_ARGUMENT, "copy destination would be inside the project-root source");
        valid_name(request.destination_basename());
        auto destination_path = destination.path;
        destination_path.push_back(request.destination_basename());
        if (file_policy::is_registered_root_admin_path(destination_path))
          fail(pb::FileActionError::INVALID_ARGUMENT,
               "registered root administration is outside cabinet files");
        const auto source_name = source.path.back();
        auto source_parent_path = source.path;
        source_parent_path.pop_back();
        auto source_parent = directory(Handle{source_parent_path, 0, true});
        auto destination_parent = directory(destination);
        struct stat source_stat{}, destination_stat{};
        stat_child(source_parent.v, source_name, source_stat);
        const auto source_generation = source.directory ? directory_generation(source_stat) : generation(source_stat);
        if (source_generation != source.gen || (!S_ISREG(source_stat.st_mode) && !S_ISDIR(source_stat.st_mode)) ||
            (source.directory != S_ISDIR(source_stat.st_mode)))
          fail(pb::FileActionError::STALE_HANDLE, "copy source handle identity changed");
        if (fstat(destination_parent.v, &destination_stat) < 0 ||
            directory_generation(destination_stat) != destination.gen)
          fail(pb::FileActionError::STALE_HANDLE, "copy destination handle identity changed");
        if (source.directory) {
          Fd source_directory(open_child_dir(source_parent.v, source_name));
          if (source_directory.v < 0 ||
              cabinet_copy_destination_inside_source(source_directory.v, destination_parent.v))
            fail(pb::FileActionError::INVALID_ARGUMENT,
                 "copy destination parent cannot be inside the copied directory");
        }
        struct stat existing{};
        if (fstatat(destination_parent.v, request.destination_basename().c_str(), &existing, AT_SYMLINK_NOFOLLOW) == 0)
          fail(pb::FileActionError::CONFLICT, "copy destination already exists");
        if (errno != ENOENT)
          fail(pb::FileActionError::UNAVAILABLE, "copy destination cannot be inspected");
        if (!resources)
          fail(pb::FileActionError::UNIMPLEMENTED, "copy worker resources are not configured");
        auto job = std::make_unique<CopyJob>();
        job->maximum_bytes=request.maximum_bytes();job->maximum_entries=request.maximum_entries();
        job->id = random_bytes(16); job->principal = principal;
        job->source_id = request.source_handle(); job->destination_id = request.destination_directory_handle();
        job->source_name = source_name; job->destination_name = request.destination_basename();
        job->source_handle = source; job->destination_handle = destination;
        job->source_identity = source_stat; job->destination_identity = destination_stat;
        job->source_parent = std::move(source_parent); job->destination_parent = std::move(destination_parent);
        job->last_use = now();
        job->staging_root_identity = staging_root_identity;
        job->staging_name = ".spatial-copy-stage-" + hex(job->id);
        if(journal) {
          if(!resources) fail(pb::FileActionError::UNAVAILABLE,"copy journal requires worker resources");
          struct stat root_stat{},stage_stat{};
          if(fstat(root.v,&root_stat)<0 || fstat(staging_root.v,&stage_stat)<0)
            fail(pb::FileActionError::UNAVAILABLE,"copy journal root identity is unavailable");
          const auto& parent_identity=resources->git_worker_parent_identity();
          const auto broker_identity=resources->broker_root_identity();
          auto& record=job->durable;
          record.set_format_version(2);record.set_workspace_id(workspace);record.set_job_id(job->id);
          record.set_initiating_operation_id(r.operation_id());
          record.set_initiating_request_sha256(file_policy::request_digest(r));
          record.set_principal_issuer_id(r.principal().issuer_id());
          record.set_principal_actor_id(r.principal().actor_id());
          record.set_worktree_id(scope.front());
          record.set_worktree_root_device(static_cast<std::uint64_t>(root_stat.st_dev));
          record.set_worktree_root_inode(static_cast<std::uint64_t>(root_stat.st_ino));
          record.set_exact_right(authz::FILE_COPY);
          for(const auto& part:source.path)record.add_source_path_components(part);
          set_copy_identity(record.mutable_source(),source_stat,source.directory,source_generation);
          for(const auto& part:destination.path)record.add_destination_directory_components(part);
          set_copy_identity(record.mutable_destination_directory(),destination_stat,true,destination.gen);
          record.set_destination_basename(request.destination_basename());
          record.set_staging_name(job->staging_name);
          set_copy_identity(record.mutable_staging_root(),stage_stat,true);
          const auto stage_subvolume=subvolume_identity(staging_root.v);
          const auto destination_subvolume=subvolume_identity(job->destination_parent.v);
          record.set_staging_subvolume_id(stage_subvolume);
          record.set_destination_subvolume_id(destination_subvolume);
          record.set_maximum_bytes(request.maximum_bytes());record.set_maximum_entries(request.maximum_entries());
          *record.mutable_process_scope_parent()=parent_identity;
          record.set_broker_root_device(broker_identity.first);record.set_broker_root_inode(broker_identity.second);
          record.set_broker_root_boot_id(parent_identity.boot_id());
          record.set_reserved_process_scope_name("copy-"+hex(job->id));
          record.set_state(pb::FILE_COPY_JOB_JOURNAL_START_INTENT);
          record.set_created_at_unix_ms(unix_millis());record.set_updated_at_unix_ms(record.created_at_unix_ms());
          journal->begin_copy_job(record,journal_observer);
        }
        auto created = io_step(r, authz::FILE_COPY, true, effects, [&] {
          return mkdirat(staging_root.v, job->staging_name.c_str(), 0700);
        });
        if (created.value < 0) {
          if (created.error == EEXIST) fail(pb::FileActionError::CONFLICT, "copy staging name already exists");
          fail(pb::FileActionError::UNAVAILABLE, "cannot create private copy staging directory");
        }
        effects = true;
        try {
          if (fstatat(staging_root.v, job->staging_name.c_str(),
                      &job->staging_identity, AT_SYMLINK_NOFOLLOW) < 0 ||
              !S_ISDIR(job->staging_identity.st_mode) ||
              job->staging_identity.st_dev != staging_root_identity.st_dev)
            fail(pb::FileActionError::UNCERTAIN, "copy staging directory identity is uncertain");
          job->staging_directory = Fd(open_child_dir(staging_root.v, job->staging_name));
          if (job->staging_directory.v < 0)
            fail(pb::FileActionError::UNCERTAIN, "copy staging directory cannot be pinned");
          struct stat opened_stage{};
          if (fstat(job->staging_directory.v, &opened_stage) < 0 ||
              opened_stage.st_dev != job->staging_identity.st_dev ||
              opened_stage.st_ino != job->staging_identity.st_ino ||
              (project_subvolume != 0 && subvolume_identity(job->staging_directory.v) != project_subvolume))
            fail(pb::FileActionError::UNCERTAIN, "copy staging directory identity changed");
          if (fsync(staging_root.v) < 0)
            fail(pb::FileActionError::UNCERTAIN, "copy staging directory is not durable");
          const auto output_name = ".spatial-copy-output-" + hex(job->id);
          job->deadline=now()+std::chrono::seconds(300);
#ifdef SPATIAL_TEST_COPY_RECOVERY_FAULTS
          if(const char* raw=std::getenv("SPATIAL_TEST_COPY_DEADLINE_MS")) {
            char* end=nullptr;const auto value=std::strtoll(raw,&end,10);
            if(end!=raw&&*end=='\0'&&value>0&&value<=300000)
              job->deadline=now()+std::chrono::milliseconds(value);
          }
#endif
          auto before_launch=[&](const supervisor::v1::ProcessScopeIdentity& process_scope,
                                 const supervisor::v1::ProcessScopeIdentity& parent_scope) {
            if(!journal)return;
            *job->durable.mutable_staging()=pb::FileCopyFilesystemIdentity{};
            set_copy_identity(job->durable.mutable_staging(),job->staging_identity,true);
            *job->durable.mutable_process_scope()=process_scope;
            *job->durable.mutable_process_scope_parent()=parent_scope;
            job->durable.set_state(pb::FILE_COPY_JOB_JOURNAL_QUEUED);
            job->durable.set_updated_at_unix_ms(std::max(job->durable.updated_at_unix_ms(),unix_millis()));
            journal->update_copy_job(job->durable,journal_observer);
          };
          job->worker = std::make_unique<CopyTreeJob>(*resources, job->source_parent.v,
              job->staging_directory.v, 0, 0, request.maximum_bytes(), request.maximum_entries(),
              nullptr, job->source_name, output_name, source.directory, &source_stat,
              job->durable.reserved_process_scope_name(),before_launch,
              [this,owned=job.get()](const supervisor::v1::ProcessScopeIdentity& drained) {
                if(!journal)return;
                if(!owned->durable.has_process_scope() ||
                   drained.SerializeAsString()!=owned->durable.process_scope().SerializeAsString())
                  throw std::runtime_error("Copy worker drain identity changed");
                *owned->durable.mutable_drained_process_scope()=drained;
                persist_copy_job(*owned);
              });
          if(journal) { job->durable.set_state(pb::FILE_COPY_JOB_JOURNAL_RUNNING);persist_copy_job(*job); }
        } catch (...) {
          if(journal && job->durable.has_process_scope() && !job->durable.has_drained_process_scope()) {
            job->terminal.set_job_id(job->id);job->terminal.set_state(pb::FileCopyJobReceipt::UNCERTAIN);
            job->terminal.set_detail("Worker start failed without durable scope drain proof; stage retained");
            persist_copy_terminal(*job);
            fail(pb::FileActionError::UNCERTAIN,"copy start cleanup cannot be proven");
          }
          if (job->staging_identity.st_ino != 0) {
            try {
              if (job->staging_directory.v < 0)
                job->staging_directory = Fd(open_child_dir(staging_root.v, job->staging_name));
              if (job->staging_directory.v < 0)
                fail(pb::FileActionError::UNCERTAIN, "cannot pin failed copy staging directory");
              cleanup_copy_stage(*job,r,effects);
            } catch (...) {
              fail(pb::FileActionError::UNCERTAIN,"copy worker start failed and staging cleanup is unconfirmed");
            }
          }
          throw;
        }
        auto* receipt = out.mutable_copy_job();
        receipt->set_job_id(job->id); receipt->set_state(pb::FileCopyJobReceipt_State_QUEUED);
        const auto id = job->id;
        copy_jobs.emplace(id, std::move(job));
        return out;
      }
      if (request.command() != pb::FileCopyJob_Command_POLL && request.command() != pb::FileCopyJob_Command_CANCEL)
        fail(pb::FileActionError::INVALID_ARGUMENT, "unknown cabinet copy job command");
      if (request.job_id().size() != 16)
        fail(pb::FileActionError::INVALID_ARGUMENT, "invalid cabinet copy job id");
      const auto found = copy_jobs.find(request.job_id());
      if (found == copy_jobs.end()) {
        authorize(authority, r, authz::FILE_COPY, workspace, scope);
        if (!journal)
          fail(pb::FileActionError::NOT_FOUND, "cabinet copy job is unknown");
        const auto durable=journal->find_copy_job(workspace,request.job_id(),journal_observer);
        if(!durable || scope.empty() || durable->worktree_id()!=scope.front())
          fail(pb::FileActionError::NOT_FOUND, "cabinet copy job is unknown");
        if(durable->principal_issuer_id()!=r.principal().issuer_id() ||
           durable->principal_actor_id()!=r.principal().actor_id())
          fail(pb::FileActionError::UNAUTHORIZED, "cabinet copy job belongs to another principal");
        if(!durable->has_terminal_receipt())
          fail(pb::FileActionError::UNCERTAIN, "durable copy job has no recovered terminal receipt");
        *out.mutable_copy_job()=durable->terminal_receipt();
        return out;
      }
      auto& job = *found->second;
      if (job.principal != principal)
        fail(pb::FileActionError::UNAUTHORIZED, "cabinet copy job belongs to another principal");
      job.last_use = now();
      try {
        authorize(authority, r, authz::FILE_COPY, workspace, scope);
      } catch (...) {
        if (!job.finished) {
          try {
            request_copy_cancel(job);
            job.worker->cancel();
            cleanup_copy_stage_internal(job);
            job.terminal.Clear(); job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
            job.terminal.set_detail("authority was revoked; worker stopped and staging removed");
          } catch (...) {
            job.terminal.Clear(); job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
            job.terminal.set_detail("authority was revoked; worker or staging cleanup is unconfirmed");
          }
          job.finished=true;
          try { persist_copy_terminal(job); }
          catch(...) {
            job.terminal.Clear();job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt::UNCERTAIN);
            job.terminal.set_detail("Authority loss terminal journal commit is unconfirmed");
            fail(pb::FileActionError::UNCERTAIN,"copy authority-loss terminal durability is unconfirmed");
          }
        }
        fail(pb::FileActionError::UNAUTHORIZED, "cabinet copy authority was revoked");
      }
      auto revalidate = [&] {
        const auto source = get(job.source_id);
        const auto destination = get(job.destination_id, true);
        if (source.path != job.source_handle.path || source.gen != job.source_handle.gen ||
            destination.path != job.destination_handle.path || destination.gen != job.destination_handle.gen)
          fail(pb::FileActionError::STALE_HANDLE, "copy handle generation changed");
        auto source_parent_path = source.path; source_parent_path.pop_back();
        auto source_parent = directory(Handle{source_parent_path, 0, true});
        auto destination_parent = directory(destination);
        struct stat current_source{}, current_destination{};
        stat_child(source_parent.v, job.source_name, current_source);
        if (current_source.st_dev != job.source_identity.st_dev || current_source.st_ino != job.source_identity.st_ino ||
            (source.directory ? directory_generation(current_source) : generation(current_source)) != source.gen)
          fail(pb::FileActionError::STALE_HANDLE, "copy source filesystem identity changed");
        if (fstat(destination_parent.v, &current_destination) < 0 ||
            current_destination.st_dev != job.destination_identity.st_dev ||
            current_destination.st_ino != job.destination_identity.st_ino ||
            directory_generation(current_destination) != destination.gen)
          fail(pb::FileActionError::STALE_HANDLE, "copy destination filesystem identity changed");
        Fd stage_root_named_fd(open_copy_stage_root(staging_parent.v, staging_root_name));
        struct stat stage_root_now{}, stage_root_named{}, stage_root_open{};
        if (stage_root_named_fd.v < 0 || fstat(stage_root_named_fd.v, &stage_root_open) < 0 ||
            stage_root_open.st_dev != job.staging_root_identity.st_dev ||
            stage_root_open.st_ino != job.staging_root_identity.st_ino ||
            fstat(staging_root.v, &stage_root_now) < 0 ||
            stage_root_now.st_dev != job.staging_root_identity.st_dev ||
            stage_root_now.st_ino != job.staging_root_identity.st_ino ||
            fstatat(staging_parent.v,staging_root_name.c_str(),&stage_root_named,AT_SYMLINK_NOFOLLOW) < 0 ||
            stage_root_named.st_dev != job.staging_root_identity.st_dev ||
            stage_root_named.st_ino != job.staging_root_identity.st_ino ||
            (project_subvolume != 0 && subvolume_identity(destination_parent.v) != project_subvolume))
          fail(pb::FileActionError::STALE_HANDLE, "copy staging or destination subvolume identity changed");
      };
      if (request.command() == pb::FileCopyJob_Command_CANCEL) {
        if (!job.finished) {
          try {
            request_copy_cancel(job);
            job.worker->cancel();
            cleanup_copy_stage(job, r, effects);
            job.terminal.Clear(); job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
            job.terminal.set_detail("worker stopped and private staging removed");
            job.finished = true; persist_copy_terminal(job);
          } catch (...) {
            job.terminal.Clear(); job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
            job.terminal.set_detail("worker stop or private staging cleanup is unconfirmed");
            job.finished = true; persist_copy_terminal(job);
            fail(pb::FileActionError::UNCERTAIN, "copy cancellation cleanup is unconfirmed");
          }
        }
        *out.mutable_copy_job() = job.terminal;
        return out;
      }
      if (job.finished) { *out.mutable_copy_job() = job.terminal; return out; }
      bool publication_started=false;
      try {
        revalidate();
        const auto copied = job.worker->poll();
        if (!copied) {
          if(auto progress=job.worker->take_progress()) {
            if(progress->bytes>job.maximum_bytes||progress->entries>job.maximum_entries||
               progress->bytes<job.bytes_progress||progress->entries<job.entries_progress)
              fail(pb::FileActionError::UNAVAILABLE,"copy worker progress violates approved bounds or monotonicity");
            const bool changed=progress->bytes!=job.bytes_progress||progress->entries!=job.entries_progress;
            job.bytes_progress=progress->bytes;job.entries_progress=progress->entries;
            if(changed&&journal) {
              job.durable.set_bytes_progress(progress->bytes);job.durable.set_entries_progress(progress->entries);
              persist_copy_job(job);
            }
            auto* receipt=out.mutable_copy_job();receipt->set_job_id(job.id);
            receipt->set_state(pb::FileCopyJobReceipt_State_RUNNING);
            receipt->set_bytes_copied(progress->bytes);receipt->set_entries_copied(progress->entries);
          } else {
            auto* receipt = out.mutable_copy_job();
            receipt->set_job_id(job.id); receipt->set_state(pb::FileCopyJobReceipt_State_RUNNING);
            receipt->set_bytes_copied(job.bytes_progress);
            receipt->set_entries_copied(job.entries_progress);
          }
          return out;
        }
        revalidate();
        struct stat staging_now{};
        const auto output_name = ".spatial-copy-output-" + hex(job.id);
        if (fstatat(job.staging_directory.v, output_name.c_str(), &staging_now, AT_SYMLINK_NOFOLLOW) < 0 ||
            (!S_ISREG(staging_now.st_mode) && !S_ISDIR(staging_now.st_mode)))
          fail(pb::FileActionError::UNCERTAIN, "completed copy staging output is missing");
        if(journal) {
          auto* prepared=job.durable.mutable_prepared_result();
          prepared->set_staging_output_name(output_name);
          set_copy_identity(prepared->mutable_output(),staging_now,S_ISDIR(staging_now.st_mode),generation(staging_now));
          prepared->set_bytes_copied(copied->bytes);prepared->set_entries_copied(copied->entries);
          prepared->set_content_sha256(copied->digest);
          job.durable.set_bytes_progress(copied->bytes);job.durable.set_entries_progress(copied->entries);
          persist_copy_job(job);
        }
#if defined(SPATIAL_TEST_COPY_RECOVERY_FAULTS)
        // Named crash boundary after durable preparation and exact scope drain.
        if(const char* marker=std::getenv("SPATIAL_TEST_COPY_PREPARED_NOTIFY");marker && marker[0]=='/') {
          Fd notified(open(marker,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600));
          if(notified.v<0 || write(notified.v,"1",1)!=1 || fsync(notified.v)!=0)
            fail(pb::FileActionError::UNAVAILABLE,"cannot record prepared crash boundary");
          std::this_thread::sleep_for(std::chrono::seconds(5));
        }
#endif
        publication_started=true;
        auto published = io_step(r, authz::FILE_COPY, true, effects, [&] {
          return renameat2(job.staging_directory.v, output_name.c_str(), job.destination_parent.v,
                           job.destination_name.c_str(), RENAME_NOREPLACE);
        });
        if (published.value < 0) {
          if (published.error == EEXIST) {
            publication_started=false;
            job.terminal.set_job_id(job.id); job.terminal.set_state(pb::FileCopyJobReceipt_State_CONFLICT);
            job.terminal.set_detail("copy destination appeared before publication");
            cleanup_copy_stage(job, r, effects); job.finished = true; persist_copy_terminal(job); *out.mutable_copy_job() = job.terminal; return out;
          }
          fail(pb::FileActionError::UNCERTAIN, "copy publication outcome is uncertain");
        }
        effects = true;
#if defined(SPATIAL_TEST_COPY_RECOVERY_FAULTS)
        // Hold the real published inode before either directory sync. The
        // harness kills this broker at the ready marker or releases it explicitly.
        if(const char* raw=std::getenv("SPATIAL_TEST_COPY_POST_RENAME_BARRIER");raw && raw[0]=='/') {
          const std::string directory(raw);
          Fd ready(open((directory+"/ready").c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600));
          if(ready.v<0 || write(ready.v,"1",1)!=1 || fsync(ready.v)!=0)
            throw std::runtime_error("cannot record post-rename copy barrier");
          const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(30);
          bool released=false;
          while(std::chrono::steady_clock::now()<deadline) {
            Fd release(open((directory+"/release").c_str(),O_RDONLY|O_CLOEXEC|O_NOFOLLOW));
            if(release.v>=0) {
              struct stat release_stat{};
              if(fstat(release.v,&release_stat)<0 || !S_ISREG(release_stat.st_mode))
                throw std::runtime_error("post-rename copy barrier release is not a regular file");
              released=true;break;
            }
            if(errno!=ENOENT) throw std::runtime_error("cannot inspect post-rename copy barrier release");
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
          }
          if(!released) throw std::runtime_error("post-rename copy barrier timed out");
        }
#endif
        auto sync_stage = io_step(r, authz::FILE_COPY, true, effects, [&] { return fsync(job.staging_directory.v); });
        auto sync_destination = io_step(r, authz::FILE_COPY, true, effects, [&] { return sync_stage.value == 0 ? fsync(job.destination_parent.v) : -1; });
        if (sync_stage.value < 0 || sync_destination.value < 0)
          fail(pb::FileActionError::UNCERTAIN, "copy published but destination sync failed");
        struct stat published_stat{};
        if (fstatat(job.destination_parent.v, job.destination_name.c_str(), &published_stat, AT_SYMLINK_NOFOLLOW) < 0 ||
            published_stat.st_dev != staging_now.st_dev || published_stat.st_ino != staging_now.st_ino)
          fail(pb::FileActionError::UNCERTAIN, "published copy identity could not be proven");
        cleanup_copy_stage(job, r, effects);
        job.terminal.set_job_id(job.id); job.terminal.set_state(pb::FileCopyJobReceipt_State_COMPLETE);
        job.terminal.set_bytes_copied(copied->bytes); job.terminal.set_entries_copied(copied->entries);
        job.terminal.set_content_digest(copied->digest); job.finished = true; persist_copy_terminal(job);
        *out.mutable_copy_job() = job.terminal;
        return out;
      } catch (const CopyWorkerError& error) {
        if (publication_started) {
          job.terminal.Clear(); job.terminal.set_job_id(job.id);
          job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
          job.terminal.set_detail("copy worker failed after publication began; inspect the destination");
          job.finished = true; persist_copy_terminal(job);
          fail(pb::FileActionError::UNCERTAIN, "copy failure outcome is uncertain");
        }
        try {
          job.worker->cancel();
          cleanup_copy_stage(job, r, effects);
          job.terminal.Clear(); job.terminal.set_job_id(job.id);
          job.terminal.set_state(pb::FileCopyJobReceipt_State_FAILED);
          job.terminal.set_failure_code(error.code == CopyWorkerError::Code::resource_exhausted
              ? pb::FileActionError_Code_EXHAUSTED
              : error.code == CopyWorkerError::Code::invalid_argument
                  ? pb::FileActionError_Code_INVALID_ARGUMENT
                  : pb::FileActionError_Code_UNAVAILABLE);
          job.terminal.set_detail(std::string("copy worker failed before publication; ProcessScope stopped and staging removed: ") + error.what());
          job.finished = true; persist_copy_terminal(job);
          *out.mutable_copy_job() = job.terminal;
          return out;
        } catch (...) {
          job.terminal.Clear(); job.terminal.set_job_id(job.id);
          job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
          job.terminal.set_detail("copy worker failed; worker stop or staging cleanup is unconfirmed");
          job.finished = true; persist_copy_terminal(job);
          fail(pb::FileActionError::UNCERTAIN, "copy failure cleanup is unconfirmed");
        }
      } catch (const Failure&) {
        if(!job.finished) {
          if(publication_started) {
            job.terminal.Clear();job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
            job.terminal.set_detail("publication may have occurred; inspect the destination");
            job.finished=true; persist_copy_terminal(job);
          } else {
            try {
              request_copy_cancel(job);
              job.worker->cancel();
              cleanup_copy_stage(job,r,effects);
              job.terminal.Clear();job.terminal.set_job_id(job.id);
              job.terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
              job.terminal.set_detail("copy stopped before publication");
              job.finished=true; persist_copy_terminal(job);
            } catch (...) {
              job.terminal.Clear();job.terminal.set_job_id(job.id);
              job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
              job.terminal.set_detail("copy failed; worker or staging cleanup is unconfirmed");
              job.finished=true; persist_copy_terminal(job);
              fail(pb::FileActionError::UNCERTAIN,"copy failure cleanup is unconfirmed");
            }
          }
        }
        throw;
      } catch (...) {
        if(!job.finished) {
          if(publication_started) {
            job.terminal.Clear();job.terminal.set_job_id(job.id);
            job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
            job.terminal.set_detail("publication may have occurred; inspect the destination");
            job.finished=true; persist_copy_terminal(job);
          } else {
            try {
              request_copy_cancel(job);
              job.worker->cancel();
              cleanup_copy_stage(job,r,effects);
              job.terminal.Clear();job.terminal.set_job_id(job.id);
              job.terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
              job.terminal.set_detail("copy stopped before publication");
              job.finished=true; persist_copy_terminal(job);
            } catch (...) {
              job.terminal.Clear();job.terminal.set_job_id(job.id);
              job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
              job.terminal.set_detail("copy failed; worker or staging cleanup is unconfirmed");
              job.finished=true; persist_copy_terminal(job);
              fail(pb::FileActionError::UNCERTAIN,"copy failure cleanup is unconfirmed");
            }
          }
        }
        fail(pb::FileActionError::UNAVAILABLE, "copy worker failed");
      }
    }
    fail(pb::FileActionError::INVALID_ARGUMENT, "no file action selected");
  }
};
FileEngine::FileEngine(int root, std::string workspace, std::vector<std::string> scope,
                       FileAuthority& authority, MonotonicNow now, FileEffectExecutor* effects,
                       WorkspaceResources* resources, int staging_parent_fd,
                       std::string staging_root_name, FileOperationJournal* journal,
                       FileOperationJournal::Observer journal_observer,
                        const PinnedFileOperationJournal* cleanup_pin)
     : impl_(std::make_unique<Impl>(root, std::move(workspace), std::move(scope), authority,
                                   std::move(now), effects, resources, staging_parent_fd,
                                   std::move(staging_root_name), journal,
                                   std::move(journal_observer), cleanup_pin)) {}
FileEngine::~FileEngine() {
  if(!impl_)return;
  std::lock_guard lock(impl_->mutex);
  for(auto& [id,job]:impl_->copy_jobs) {
    (void)id;
    if(job->finished)continue;
    try {
      impl_->request_copy_cancel(*job);
      job->worker->cancel();
      impl_->cleanup_copy_stage_internal(*job);
      job->terminal.Clear();job->terminal.set_job_id(job->id);
      job->terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
      job->terminal.set_detail("file engine stopped; worker stopped and staging removed");
      job->finished=true;
      impl_->persist_copy_terminal_noexcept(*job);
    } catch (...) {
      job->terminal.Clear();job->terminal.set_job_id(job->id);
      job->terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
      job->terminal.set_detail("file engine stopped; worker or staging cleanup is unconfirmed");
      job->finished=true;
      impl_->persist_copy_terminal_noexcept(*job);
    }
  }
}
void FileEngine::stop_copy_job_for_authority_loss(const std::string& job_id,
                                                        const std::string& principal_identity) noexcept {
  std::lock_guard lock(impl_->mutex);
  const auto found = impl_->copy_jobs.find(job_id);
  if (found == impl_->copy_jobs.end()) return;
  auto& job = *found->second;
  if (job.principal != principal_identity || job.finished) return;
  try {
    impl_->request_copy_cancel(job);
    job.worker->cancel();
    impl_->cleanup_copy_stage_internal(job);
    job.terminal.Clear();
    job.terminal.set_job_id(job.id);
    job.terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
    job.terminal.set_detail("authority was revoked; worker stopped and staging removed");
  } catch (...) {
    job.terminal.Clear();
    job.terminal.set_job_id(job.id);
    job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
    job.terminal.set_detail("authority was revoked; worker stop or staging cleanup is unconfirmed");
  }
  job.finished = true;
  impl_->persist_copy_terminal_noexcept(job);
}
void FileEngine::reconcile_copy_job_authority(
    const std::function<bool(const std::string&)>& allowed) noexcept {
  if (!impl_ || !allowed) return;
  std::lock_guard lock(impl_->mutex);
  for (auto& [id, owned] : impl_->copy_jobs) {
    (void)id;
    auto& job = *owned;
    if (job.finished) continue;
    bool permitted = false;
    try { permitted = allowed(job.principal); } catch (...) { permitted = false; }
    if (permitted) continue;
    try {
      impl_->request_copy_cancel(job);
      job.worker->cancel();
      impl_->cleanup_copy_stage_internal(job);
      job.terminal.Clear();
      job.terminal.set_job_id(job.id);
      job.terminal.set_state(pb::FileCopyJobReceipt_State_CANCELLED);
      job.terminal.set_detail("authority changed; worker stopped and staging removed");
    } catch (...) {
      job.terminal.Clear();
      job.terminal.set_job_id(job.id);
      job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
      job.terminal.set_detail("authority changed; worker stop or staging cleanup is unconfirmed");
    }
    job.finished = true;
    impl_->persist_copy_terminal_noexcept(job);
  }
}
bool FileEngine::expire_one_copy_job() noexcept {
  if(!impl_)return false;
  std::lock_guard lock(impl_->mutex);
  const auto tick=impl_->now();
  for(auto& [id,owned]:impl_->copy_jobs) {
    (void)id;auto& job=*owned;
    if(job.finished||job.deadline==Impl::Clock::time_point{}||tick<job.deadline)continue;
    try {
      job.worker->cancel();
      impl_->cleanup_copy_stage_internal(job);
      job.terminal.Clear();job.terminal.set_job_id(job.id);
      job.terminal.set_state(pb::FileCopyJobReceipt_State_FAILED);
      job.terminal.set_failure_code(pb::FileActionError_Code_UNAVAILABLE);
      job.terminal.set_detail("absolute 300-second copy worker deadline expired; exact worker drain and staging cleanup confirmed");
      job.finished=true;
      try { impl_->persist_copy_terminal(job); }
      catch(...) {
        job.terminal.Clear();job.terminal.set_job_id(job.id);
        job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
        job.terminal.set_detail("copy worker deadline expired; terminal journal commit is unconfirmed");
        impl_->persist_copy_terminal_noexcept(job);
      }
    } catch(...) {
      job.terminal.Clear();job.terminal.set_job_id(job.id);
      job.terminal.set_state(pb::FileCopyJobReceipt_State_UNCERTAIN);
      job.terminal.set_detail("copy worker deadline expired; exact worker drain or staging cleanup is unconfirmed");
      job.finished=true;impl_->persist_copy_terminal_noexcept(job);
    }
    return true;
  }
  return false;
}
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
