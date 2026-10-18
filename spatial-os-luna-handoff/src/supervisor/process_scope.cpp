#include "supervisor/process_scope.hpp"
#include "core/protocol.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <climits>
#include <csignal>
#include <charconv>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <exception>
#include <iostream>
#include <linux/close_range.h>
#include <linux/magic.h>
#include <poll.h>
#include <sstream>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <sys/file.h>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

namespace spatial::supervisor {
namespace {
using Clock = std::chrono::steady_clock;
struct Fd {
    int value = -1;
    explicit Fd(int fd = -1) : value(fd) {}
    ~Fd() { if (value >= 0) close(value); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
};
void check(bool okay, const char* message) {
    if (!okay) throw std::runtime_error(std::string(message) + ": " + std::strerror(errno));
}
void valid(bool okay, const char* message) {
    if (!okay) throw std::invalid_argument(message);
}
std::string contents(int directory, const char* name) {
    Fd fd(openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    check(fd.value >= 0, "Cannot open cgroup state");
    std::string result; char buffer[4096];
    for (;;) {
        auto size = read(fd.value, buffer, sizeof(buffer));
        if (size < 0 && errno == EINTR) continue;
        check(size >= 0, "Cannot read cgroup state");
        if (!size) return result;
        result.append(buffer, static_cast<std::size_t>(size));
        valid(result.size() <= 65536, "Excessive cgroup state");
    }
}
void control(int directory, const char* name, const std::string& value) {
    Fd fd(openat(directory, name, O_WRONLY | O_CLOEXEC | O_NOFOLLOW));
    check(fd.value >= 0, "Cannot open cgroup control");
    ssize_t size;
    do { size = write(fd.value, value.data(), value.size()); } while (size < 0 && errno == EINTR);
    check(size == static_cast<ssize_t>(value.size()), "Cannot write cgroup control");
}
void tick() { poll(nullptr, 0, 5); }
#ifdef SPATIAL_PROCESS_SCOPE_READTRACE
constexpr std::size_t kReadTraceSnapshotBytes = 4096;
constexpr std::size_t kReadTraceFileBytes = 65536;
constexpr std::size_t kReadTraceProcsBytes = 256;
constexpr std::size_t kReadTracePidCount = 8;
constexpr std::size_t kReadTraceChildDirectories = 8;
constexpr unsigned kReadTraceChildDepth = 2;

std::string read_trace_file(int directory, const char* name, std::size_t limit) noexcept {
    try {
        Fd fd(openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
        if (fd.value < 0) return "<unavailable>";
        std::string result;
        char buffer[256];
        while (result.size() < limit) {
            const auto wanted = std::min(sizeof(buffer), limit - result.size());
            const auto count = read(fd.value, buffer, wanted);
            if (count < 0 && errno == EINTR) continue;
            if (count < 0) return "<read-error>";
            if (!count) return result;
            result.append(buffer, static_cast<std::size_t>(count));
        }
        char extra;
        const auto count = read(fd.value, &extra, 1);
        if (count > 0) {
            constexpr std::string_view marker = "<truncated>";
            if (result.size() >= marker.size()) result.resize(result.size() - marker.size());
            result.append(marker);
        }
        return result;
    } catch (...) { return "<read-error>"; }
}

void append_trace_field(std::string& output, std::string_view name, std::string_view value) {
    if (output.size() >= kReadTraceSnapshotBytes) return;
    const auto append = [&](char byte) {
        if (output.size() < kReadTraceSnapshotBytes) output.push_back(byte);
    };
    if (!output.empty()) append(' ');
    for (const auto byte : name) append(byte);
    append('=');
    for (const unsigned char byte : value) {
        if (byte == '\n') { append('\\'); append('n'); }
        else if (byte == '\r') { append('\\'); append('r'); }
        else if (byte == '\\' || byte == ' ') { append('\\'); append(static_cast<char>(byte)); }
        else append(static_cast<char>(byte));
    }
}

std::uint64_t proc_start_time(pid_t pid) noexcept {
    try {
        const auto path = "/proc/" + std::to_string(pid) + "/stat";
        Fd fd(open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
        if (fd.value < 0) return 0;
        char buffer[2048];
        const auto count = read(fd.value, buffer, sizeof(buffer) - 1);
        if (count <= 0) return 0;
        buffer[count] = '\0';
        const std::string_view stat(buffer, static_cast<std::size_t>(count));
        const auto close = stat.rfind(')');
        if (close == std::string_view::npos || close + 2 >= stat.size()) return 0;
        std::istringstream fields(std::string(stat.substr(close + 2)));
        std::string token;
        for (unsigned field = 3; field <= 22; ++field) {
            if (!(fields >> token)) return 0;
            if (field == 22) {
                std::uint64_t value = 0;
                const auto parsed = std::from_chars(token.data(), token.data() + token.size(), value);
                return parsed.ec == std::errc{} && parsed.ptr == token.data() + token.size() ? value : 0;
            }
        }
    } catch (...) {}
    return 0;
}

void add_trace_pids(std::string_view raw, std::array<pid_t, kReadTracePidCount>& pids,
                    std::size_t& count, bool& limit_reached) {
    std::size_t cursor = 0;
    while (cursor < raw.size()) {
        while (cursor < raw.size() && (raw[cursor] == '\n' || raw[cursor] == '\r' || raw[cursor] == ' ')) ++cursor;
        if (cursor == raw.size()) break;
        std::uint64_t value = 0;
        const auto end = raw.find_first_of("\r\n \t", cursor);
        const auto stop = end == std::string_view::npos ? raw.size() : end;
        const auto parsed = std::from_chars(raw.data() + cursor, raw.data() + stop, value);
        if (parsed.ec == std::errc{} && parsed.ptr == raw.data() + stop && value > 1 && value <= INT_MAX) {
            const auto pid = static_cast<pid_t>(value);
            bool found = false;
            for (std::size_t i = 0; i < count; ++i) found = found || pids[i] == pid;
            if (!found) {
                if (count == pids.size()) limit_reached = true;
                else pids[count++] = pid;
            }
        }
        cursor = stop;
    }
}

void write_scope_readtrace(int scope, const v1::ProcessScopeIdentity& identity, std::string_view events,
                           bool populated) noexcept {
    try {
        const char* path = std::getenv("SPATIAL_SCOPE_READTRACE_PATH");
        if (!path || !*path) return;
        std::string line = "scope_read populated=" + std::to_string(populated ? 1 : 0);
        append_trace_field(line, "scope", identity.scope_name());
        append_trace_field(line, "device", std::to_string(identity.device()));
        append_trace_field(line, "inode", std::to_string(identity.inode()));
        append_trace_field(line, "events_used", events);

        std::array<pid_t, kReadTracePidCount> pids{};
        std::size_t pid_count = 0, child_count = 0;
        bool pid_limit_reached = false, child_limit_reached = false;
        const auto root_procs = read_trace_file(scope, "cgroup.procs", kReadTraceProcsBytes);
        add_trace_pids(root_procs, pids, pid_count, pid_limit_reached);
        append_trace_field(line, "root_procs", root_procs);

        const auto walk = [&](auto&& self, int directory, std::string prefix, unsigned depth) -> void {
            Fd duplicate(openat(directory, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
            if (duplicate.value < 0) return;
            DIR* entries = fdopendir(duplicate.value);
            if (!entries) return;
            duplicate.value = -1;
            for (;;) {
                errno = 0;
                const auto* entry = readdir(entries);
                if (!entry) break;
                const std::string name = entry->d_name;
                if (name == "." || name == "..") continue;
                struct stat info{};
                if (fstatat(directory, name.c_str(), &info, AT_SYMLINK_NOFOLLOW) < 0 || !S_ISDIR(info.st_mode)) continue;
                if (child_count >= kReadTraceChildDirectories || depth >= kReadTraceChildDepth) {
                    child_limit_reached = true; break;
                }
                Fd child(openat(directory, name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
                if (child.value < 0) continue;
                ++child_count;
                const auto child_path = prefix.empty() ? name : prefix + "/" + name;
                const auto procs = read_trace_file(child.value, "cgroup.procs", kReadTraceProcsBytes);
                add_trace_pids(procs, pids, pid_count, pid_limit_reached);
                append_trace_field(line, "child_path", child_path);
                append_trace_field(line, "child_procs", procs);
                self(self, child.value, child_path, depth + 1);
            }
            closedir(entries);
        };
        walk(walk, scope, "", 0);
        append_trace_field(line, "child_dirs", std::to_string(child_count));
        append_trace_field(line, "child_limit", child_limit_reached ? "1" : "0");
        for (std::size_t i = 0; i < pid_count; ++i) {
            append_trace_field(line, "pid_instance",
                std::to_string(pids[i]) + ":" + std::to_string(proc_start_time(pids[i])));
        }
        append_trace_field(line, "pid_limit", pid_limit_reached ? "1" : "0");
        constexpr std::string_view complete_marker = " truncated=0";
        constexpr std::string_view truncated_marker = " truncated=1";
        if (line.size() + complete_marker.size() + 1 > kReadTraceSnapshotBytes) {
            line.resize(kReadTraceSnapshotBytes - truncated_marker.size() - 1);
            line.append(truncated_marker);
        } else line.append(complete_marker);
        line.push_back('\n');

        Fd output(open(path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600));
        if (output.value < 0 || flock(output.value, LOCK_EX) < 0) return;
        struct stat info{};
        if (fstat(output.value, &info) < 0 || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
            (info.st_mode & 0077) != 0 || info.st_size < 0 || static_cast<std::uint64_t>(info.st_size) + line.size() > kReadTraceFileBytes) {
            (void)flock(output.value, LOCK_UN); return;
        }
        std::size_t written = 0;
        while (written < line.size()) {
            const auto count = write(output.value, line.data() + written, line.size() - written);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) break;
            written += static_cast<std::size_t>(count);
        }
        (void)flock(output.value, LOCK_UN);
    } catch (...) {}
}
#endif
void remove_empty_children(int directory, unsigned depth = 0) {
    valid(depth <= 8, "Excessive owned cgroup nesting");
    Fd duplicate(openat(directory, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    check(duplicate.value >= 0, "Cannot enumerate owned cgroups");
    DIR* entries = fdopendir(duplicate.value);
    check(entries != nullptr, "Cannot read owned cgroups");
    duplicate.value = -1;
    try {
        for (;;) {
            errno = 0; const auto* entry = readdir(entries);
            if (!entry) { check(errno == 0, "Cannot enumerate owned cgroups"); break; }
            const std::string name = entry->d_name;
            if (name == "." || name == "..") continue;
            struct stat info{};
            check(fstatat(directory, name.c_str(), &info, AT_SYMLINK_NOFOLLOW) == 0, "Cannot inspect owned cgroup child");
            if (!S_ISDIR(info.st_mode)) continue;
            valid(info.st_uid == geteuid(), "Foreign cgroup child prevents cleanup");
            Fd child(openat(directory, name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
            check(child.value >= 0, "Cannot pin owned cgroup child");
            struct stat retained{};
            check(fstat(child.value, &retained) == 0, "Cannot inspect retained cgroup child");
            valid(retained.st_dev == info.st_dev && retained.st_ino == info.st_ino, "Cgroup child identity changed");
            remove_empty_children(child.value, depth+1);
            check(fstatat(directory, name.c_str(), &info, AT_SYMLINK_NOFOLLOW) == 0, "Cannot recheck cgroup child");
            valid(retained.st_dev == info.st_dev && retained.st_ino == info.st_ino, "Cgroup child identity changed before removal");
            check(unlinkat(directory, name.c_str(), AT_REMOVEDIR) == 0, "Cannot remove empty owned cgroup child");
        }
    } catch (...) { closedir(entries); throw; }
    closedir(entries);
}
[[noreturn]] void child_failure(int gate, int error) {
    (void)send(gate, &error, sizeof(error), MSG_NOSIGNAL); _exit(126);
}
}

void validate_budget(const v1::ProcessScopeBudget& value) {
        const auto* budget = &value;
        validate_message(*budget);
        const auto page = sysconf(_SC_PAGESIZE);
        valid(page > 0, "Cannot determine memory page size");
        constexpr std::uint64_t maximum = 1ULL << 50;
        valid(budget->has_memory_bytes() && budget->memory_bytes() >= static_cast<std::uint64_t>(page) &&
              budget->memory_bytes() <= maximum && budget->memory_bytes() % page == 0,
              "Memory budget must be an explicit positive page multiple");
        valid(budget->has_swap_bytes() && budget->swap_bytes() <= maximum && budget->swap_bytes() % page == 0,
              "Swap budget must be an explicit nonnegative page multiple");
        valid(budget->has_processes() && budget->processes() > 0 && budget->processes() <= 1048576,
              "Process budget must be explicit and bounded");
        valid(budget->has_cpu_microseconds() && budget->cpu_microseconds() >= 1000 &&
              budget->cpu_microseconds() <= 6400000, "CPU budget must be explicit and bounded");
        valid(budget->io_size() > 0 && budget->io_size() <= 64, "Expected explicit I/O device budgets");
        std::set<std::pair<unsigned, unsigned>> devices;
        for (const auto& io : budget->io()) {
            valid(io.has_major() && io.has_minor() && io.major() > 0 && io.major() <= 4095 && io.minor() <= 1048575 &&
                  devices.emplace(io.major(), io.minor()).second, "Invalid or duplicate I/O device");
            valid(io.read_bytes_per_second() > 0 && io.read_bytes_per_second() <= maximum &&
                  io.write_bytes_per_second() > 0 && io.write_bytes_per_second() <= maximum,
                  "I/O rates must be positive and bounded");
        }
}
std::unique_ptr<ProcessScope> ProcessScope::prepare(const std::filesystem::path& root,
                                                  const v1::ProcessScopeBudget* budget) {
    return prepare_impl(root, budget, {});
}
std::unique_ptr<ProcessScope> ProcessScope::prepare_owned(const std::filesystem::path& root, const std::string& scope_name) {
    valid(scope_name.size() == 49 && scope_name.starts_with("authority-broker-") &&
          scope_name.substr(17).find_first_not_of("0123456789abcdef") == std::string::npos,
          "Invalid broker-owned scope identity");
    return prepare_impl(root, nullptr, scope_name);
}
std::unique_ptr<ProcessScope> ProcessScope::prepare_impl(const std::filesystem::path& root,
                                                  const v1::ProcessScopeBudget* budget,
                                                  const std::string& stable_name) {
    if (budget) validate_budget(*budget);
    auto result = std::unique_ptr<ProcessScope>(new ProcessScope);
    result->parent_ = open(root.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    check(result->parent_ >= 0, "Cannot open delegated cgroup");
    struct statfs fs{}; struct stat parent{}, above{};
    check(fstatfs(result->parent_, &fs) == 0 && fstat(result->parent_, &parent) == 0,
          "Cannot inspect delegated cgroup");
    Fd upper(openat(result->parent_, "..", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    check(upper.value >= 0 && fstat(upper.value, &above) == 0, "Cannot inspect cgroup parent");
    valid(fs.f_type == CGROUP2_SUPER_MAGIC && parent.st_uid == geteuid() &&
          !(parent.st_dev == above.st_dev && parent.st_ino == above.st_ino), "Expected owned delegated cgroup v2 directory");
    valid(contents(result->parent_, "cgroup.type") == "domain\n", "Expected domain cgroup");
    std::string random(32, '\0'); std::size_t received = 0;
    while (received != random.size()) {
        auto count = getrandom(random.data()+received, random.size()-received, 0);
        if (count < 0 && errno == EINTR) continue;
        check(count > 0, "Cannot create launch identity"); received += static_cast<std::size_t>(count);
    }
    std::string name = stable_name.empty() ? "authority-" : stable_name; constexpr char hex[] = "0123456789abcdef";
    if (stable_name.empty()) for (unsigned char c : random) { name += hex[c >> 4]; name += hex[c & 15]; }
    std::ifstream boot("/proc/sys/kernel/random/boot_id"); std::string boot_id; std::getline(boot, boot_id);
    valid(boot_id.size() == 36, "Cannot read kernel boot identity");
    result->identity_.set_launch_id(random); result->identity_.set_boot_id(boot_id);
    result->retained_scope_ = !stable_name.empty();
    if (mkdirat(result->parent_, name.c_str(), 0700) != 0) {
        if (!result->retained_scope_ || errno != EEXIST) check(false, "Cannot create authority scope");
    }
    result->identity_.set_scope_name(name);
    result->scope_ = openat(result->parent_, name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    check(result->scope_ >= 0, "Cannot retain authority scope");
    struct stat owned{}, named{}; check(fstat(result->scope_, &owned) == 0, "Cannot identify authority scope");
    check(fstatat(result->parent_, name.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0, "Cannot revalidate owned scope name");
    valid(owned.st_dev == named.st_dev && owned.st_ino == named.st_ino && owned.st_uid == geteuid(),
          "Owned scope identity changed during pinning");
    result->identity_.set_device(owned.st_dev); result->identity_.set_inode(owned.st_ino);
    if (result->retained_scope_ && owned.st_uid != geteuid()) throw std::invalid_argument("Broker scope is not owned by this identity");
    if (result->retained_scope_ && contents(result->scope_, "cgroup.type") != "domain\n") throw std::invalid_argument("Broker scope is not a domain cgroup");
    if (result->retained_scope_ && result->populated()) {
        control(result->scope_, "cgroup.kill", "1\n");
        const auto deadline = Clock::now() + std::chrono::seconds(5);
        while (result->populated()) { valid(Clock::now() < deadline, "Stale broker scope did not drain"); tick(); }
    }
    if (result->retained_scope_) {
        remove_empty_children(result->scope_);
        valid(contents(result->scope_, "cgroup.procs").empty(), "Stale broker scope still has processes");
    }
    if (budget) {
        // This empty scope cannot run a child until every required controller accepts its limit.
        control(result->scope_, "memory.max", std::to_string(budget->memory_bytes()) + "\n");
        control(result->scope_, "memory.swap.max", std::to_string(budget->swap_bytes()) + "\n");
        control(result->scope_, "memory.oom.group", "1\n");
        control(result->scope_, "pids.max", std::to_string(budget->processes()) + "\n");
        control(result->scope_, "cpu.max", std::to_string(budget->cpu_microseconds()) + " 100000\n");
        for (const auto& io : budget->io())
            control(result->scope_, "io.max", std::to_string(io.major()) + ":" + std::to_string(io.minor()) +
                    " rbps=" + std::to_string(io.read_bytes_per_second()) +
                    " wbps=" + std::to_string(io.write_bytes_per_second()) + "\n");
    }
    return result;
}

namespace {
std::uint64_t process_start(pid_t pid) {
    std::ifstream input("/proc/"+std::to_string(pid)+"/stat");std::string line;
    valid(static_cast<bool>(std::getline(input,line)),"Payload process identity is unavailable");
    const auto end=line.rfind(')');valid(end!=std::string::npos,"Payload process stat is malformed");
    std::istringstream fields(line.substr(end+2));std::string field;
    for(unsigned n=0;n<20;++n)valid(static_cast<bool>(fields>>field),"Payload process stat is incomplete");
    std::size_t used=0;const auto ticks=std::stoull(field,&used);
    valid(used==field.size() && ticks>0,"Payload process start time is invalid");return ticks;
}
v1::PayloadQuiescenceReceipt inventory(int directory,const v1::ProcessScopeIdentity& identity) {
    v1::PayloadQuiescenceReceipt result;*result.mutable_scope()=identity;
    std::istringstream ids(contents(directory,"cgroup.procs"));std::set<pid_t> ordered;pid_t pid=0;
    while(ids>>pid) {valid(pid>1 && ordered.size()<4096,"Payload process inventory exceeds its bound");ordered.insert(pid);}
    valid(ids.eof(),"Payload process inventory is malformed");
    for(const auto item:ordered) {auto* process=result.add_processes();process->set_pid(item);process->set_start_ticks(process_start(item));}
    return result;
}
}
void ProcessScope::attach_payload(pid_t pid) {
    valid(started_ && !removed_ && payload_<0 && pid>1 && pid!=child_ && pid!=getpid(),
          "Payload attachment requires a new held application child");
    std::istringstream members(contents(scope_,"cgroup.procs"));pid_t member=0;bool owned=false;
    while(members>>member)if(member==pid)owned=true;
    valid(owned,"Application payload is outside its owned process scope");
    const auto before=process_start(pid);
    check(mkdirat(scope_,"payload",0700)==0,"Cannot create owned payload boundary");
    payload_=openat(scope_,"payload",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    check(payload_>=0,"Cannot pin owned payload boundary");
    control(payload_,"cgroup.procs",std::to_string(pid)+"\n");
    const auto receipt=inventory(payload_,identity_);
    valid(receipt.processes_size()==1 && receipt.processes(0).pid()==static_cast<unsigned>(pid) &&
          receipt.processes(0).start_ticks()==before,"Payload identity changed during attachment");
}
void ProcessScope::begin_payload_freeze() {
    valid(payload_>=0 && !removed_ && !freeze_owned_,"No available owned application payload boundary");
    valid(contents(payload_,"cgroup.freeze")=="0\n","Payload was frozen by another owner");
    frozen_inventory_=inventory(payload_,identity_).SerializeAsString();
    freeze_owned_=true;
    try {control(payload_,"cgroup.freeze","1\n");}
    catch(...) {try {thaw_payload();}catch(...){} throw;}
}
bool ProcessScope::payload_frozen() const {
    valid(payload_>=0 && freeze_owned_ && !removed_,"No owned freeze is pending");
    return contents(payload_,"cgroup.events").find("frozen 1\n")!=std::string::npos;
}
v1::PayloadQuiescenceReceipt ProcessScope::payload_receipt() const {
    valid(payload_frozen(),"Payload freeze is incomplete");
    auto result=inventory(payload_,identity_);
    valid(result.SerializeAsString()==frozen_inventory_,"Payload process inventory changed during freeze");
    result.set_frozen(true);return result;
}
void ProcessScope::thaw_payload() {
    if(!freeze_owned_)return;
    valid(payload_>=0 && !removed_,"Owned payload freeze lost its boundary");
    control(payload_,"cgroup.freeze","0\n");
    valid(contents(payload_,"cgroup.freeze")=="0\n","Payload thaw could not be proven");
    freeze_owned_=false;frozen_inventory_.clear();
}

void ProcessScope::enable_children() {
    valid(!started_ && !removed_ && !populated(), "Child controllers require an empty prepared scope");
    control(scope_, "cgroup.subtree_control", "+cpu +memory +pids +io\n");
}
pid_t ProcessScope::direct_child_pid() const {
    valid(started_ && !removed_ && !reaped_ && child_ > 0, "No retained direct child");
    return child_;
}

std::unique_ptr<ProcessScope> ProcessScope::launch(const std::filesystem::path& root,
    const v1::AuthorityProcessLaunch& request, const std::array<int, 3>& streams,
    const std::vector<int>& inherited, bool allow_test_security_evidence) {
    auto result = prepare(root);
    result->start(request, streams, inherited, allow_test_security_evidence);
    return result;
}

void ProcessScope::start(const v1::AuthorityProcessLaunch& request,
    const std::array<int, 3>& streams, const std::vector<int>& inherited, bool allow_test_security_evidence) {
    valid(!started_ && !removed_, "Authority scope cannot start again");
    validate_message(request);
    valid(inherited.size() <= 16 && static_cast<std::size_t>(request.inherited_fds_size()) == inherited.size(),
          "Invalid inherited descriptor table size");
    std::set<unsigned> targets;
    for (const auto target : request.inherited_fds())
        valid(target >= 3 && target <= 255 && targets.insert(target).second, "Invalid inherited descriptor target");
    const auto& exe = request.executable();
    valid(!exe.empty() && exe.front() == '/' && exe.size() <= 4096 && exe.find('\0') == std::string::npos,
          "Authority executable must be an absolute path");
    valid(request.arguments_size() <= 128, "Too many authority arguments");
    std::size_t size = exe.size();
    std::vector<char*> argv{const_cast<char*>(exe.c_str())};
    for (const auto& arg : request.arguments()) {
        size += arg.size()+1;
        valid(size <= 65536 && arg.find('\0') == std::string::npos, "Invalid authority argument");
        argv.push_back(const_cast<char*>(arg.c_str()));
    }
    argv.push_back(nullptr);
    // Keep the launch environment minimal; only the explicitly trusted broker receives
    // its private diagnostic directory, and only in test builds.
    char path[] = "PATH=/usr/bin:/bin", lang[] = "LANG=C.UTF-8";
    std::string diagnostic_environment;
    char* environment[]{path, lang, nullptr, nullptr};
#ifdef SPATIAL_TEST_SECURITY_EVIDENCE_FORWARD
    const bool direct_broker = std::filesystem::path(exe).filename() == "workspaced";
    const bool broker_test_bootstrap = exe == "/usr/bin/python3" &&
        request.arguments_size() == 2 && request.arguments(0) == "-c" &&
        request.arguments(1).find("os.execv(") != std::string::npos &&
        request.arguments(1).find("/workspaced") != std::string::npos;
    if (allow_test_security_evidence && (direct_broker || broker_test_bootstrap)) {
        const char* evidence = std::getenv("SPATIAL_SECURITY_EVIDENCE");
        if (evidence && evidence[0] == '/' && strnlen(evidence, 4097) <= 4096) {
            Fd evidence_directory(open(evidence, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
            struct stat evidence_stat{};
            if (evidence_directory.value >= 0 && fstat(evidence_directory.value, &evidence_stat) == 0 &&
                S_ISDIR(evidence_stat.st_mode) && evidence_stat.st_uid == geteuid() &&
                !(evidence_stat.st_mode & 0077)) {
                diagnostic_environment = std::string("SPATIAL_SECURITY_EVIDENCE=") + evidence;
                environment[2] = diagnostic_environment.data();
                environment[3] = nullptr;
            }
        }
    }
#else
    (void)allow_test_security_evidence;
#endif
    std::array<Fd, 3> copied;
    for (std::size_t i = 0; i < copied.size(); ++i) {
        copied[i].value = fcntl(streams[i], F_DUPFD_CLOEXEC, 256);
        check(copied[i].value >= 0, "Cannot retain explicit authority stream");
    }
    std::vector<Fd> selected(inherited.size());
    for (std::size_t i = 0; i < inherited.size(); ++i) {
        selected[i].value = fcntl(inherited[i], F_DUPFD_CLOEXEC, 256);
        check(selected[i].value >= 0, "Cannot retain selected authority descriptor");
    }
    int pair[2]; check(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) == 0, "Cannot create startup gate");
    Fd gate(pair[0]), original_child_gate(pair[1]);
    Fd child_gate(fcntl(original_child_gate.value, F_DUPFD_CLOEXEC, 256));
    check(child_gate.value >= 0, "Cannot retain private startup gate");
    close(original_child_gate.value); original_child_gate.value = -1;
    const pid_t owner = getpid();
    started_ = true;
    child_ = fork(); check(child_ >= 0, "Cannot fork authority");
    if (!child_) {
        close(gate.value);
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) < 0) child_failure(child_gate.value, errno);
        if (getppid() != owner) child_failure(child_gate.value, ECHILD);
        char token; ssize_t count;
        do { count = read(child_gate.value, &token, 1); } while (count < 0 && errno == EINTR);
        if (count != 1 || token != 'G') child_failure(child_gate.value, EPIPE);
        // Select targets only after marking all private descriptors close-on-exec.
        // Retained sources and the gate are above the complete permitted target range.
        if (syscall(SYS_close_range, 3U, UINT_MAX, CLOSE_RANGE_CLOEXEC) < 0) child_failure(child_gate.value, errno);
        for (int i = 0; i < 3; ++i) if (dup2(copied[i].value, i) < 0) child_failure(child_gate.value, errno);
        for (std::size_t i = 0; i < selected.size(); ++i)
            if (dup2(selected[i].value, static_cast<int>(request.inherited_fds(static_cast<int>(i)))) < 0)
                child_failure(child_gate.value, errno);
        execve(exe.c_str(), argv.data(), environment);
        const int error = errno;
        (void)send(child_gate.value, &error, sizeof(error), MSG_NOSIGNAL);
        _exit(127);
    }
    close(child_gate.value); child_gate.value = -1;
    pidfd_ = static_cast<int>(syscall(SYS_pidfd_open, child_, 0));
    check(pidfd_ >= 0, "Cannot pin authority child");
    control(scope_, "cgroup.procs", std::to_string(child_)+"\n");
    valid(populated(), "Authority did not enter its owned scope");
    check(send(gate.value, "G", 1, MSG_NOSIGNAL) == 1, "Cannot release authority startup gate");
    pollfd ready{gate.value, POLLIN | POLLHUP, 0}; int polled;
    do { polled = poll(&ready, 1, 2000); } while (polled < 0 && errno == EINTR);
    check(polled > 0, "Authority exec deadline exceeded");
    int error = 0; ssize_t count;
    do { count = read(gate.value, &error, sizeof(error)); } while (count < 0 && errno == EINTR);
    if (count > 0) { errno = error; check(false, "Authority exec failed"); }
    check(count == 0, "Cannot confirm authority exec");
}

bool ProcessScope::populated() const {
    const auto event_contents = contents(scope_, "cgroup.events");
    std::istringstream input(event_contents);
    std::string key; unsigned long long value; bool found = false, populated = false;
    while (input >> key >> value) {
        if (key == "populated") {
            valid(!found && value <= 1, "Invalid cgroup population state");
            found = true; populated = value != 0;
        }
    }
    valid(found, "Missing cgroup population state");
#ifdef SPATIAL_PROCESS_SCOPE_READTRACE
    try {
        const char* trace_path = std::getenv("SPATIAL_SCOPE_READTRACE_PATH");
        if (trace_path && *trace_path && populated && started_ && child_ > 0 && pidfd_ >= 0 && child_exited())
            write_scope_readtrace(scope_, identity_, event_contents, populated);
    } catch (...) { /* Read tracing must never change the scope decision. */ }
#endif
    return populated;
}
bool ProcessScope::child_exited() const {
    if (reaped_ || child_ < 0) return true;
    valid(pidfd_ >= 0, "Direct child was not pinned");
    pollfd event{pidfd_, POLLIN, 0}; int result;
    do { result = poll(&event, 1, 0); } while (result < 0 && errno == EINTR);
    check(result >= 0 && !(event.revents & (POLLERR | POLLNVAL)), "Cannot inspect child exit");
    return event.revents & POLLIN;
}
v1::ProcessScopeState ProcessScope::state() const {
    v1::ProcessScopeState result; *result.mutable_identity() = identity_;
    result.set_populated(!removed_ && populated()); result.set_started(started_);
    result.set_direct_child_exited(started_ && child_exited()); return result;
}
int ProcessScope::duplicate_child_pidfd() const {
    valid(started_ && !removed_ && !reaped_ && child_ > 0 && pidfd_ >= 0,
        "No retained live child handle");
    valid(!child_exited() && populated(), "Owned child is no longer running");
    const int duplicate = fcntl(pidfd_, F_DUPFD_CLOEXEC, 3);
    check(duplicate >= 3, "Cannot duplicate retained child handle");
    try {
        valid(!child_exited() && populated(), "Owned child exited during handle duplication");
    } catch (...) { close(duplicate); throw; }
    return duplicate;
}
bool ProcessScope::exited_successfully() const {
    valid(started_ && child_ > 0 && !reaped_, "No retained child exit status");
    siginfo_t info{};
    int result;
    // The direct child remains unreaped and its pidfd remains owned until stop().
    do { result = waitid(P_PID, static_cast<id_t>(child_), &info, WEXITED | WNOHANG | WNOWAIT); }
    while (result < 0 && errno == EINTR);
    check(result == 0, "Cannot inspect retained child exit status");
    return info.si_pid == child_ && info.si_code == CLD_EXITED && info.si_status == 0;
}
int ProcessScope::exit_status() const {
    valid(started_ && child_ > 0 && !reaped_, "No retained child exit status");
    siginfo_t info{};
    int result;
    do { result = waitid(P_PID, static_cast<id_t>(child_), &info, WEXITED | WNOHANG | WNOWAIT); }
    while (result < 0 && errno == EINTR);
    check(result == 0, "Cannot inspect retained child exit status");
    valid(info.si_pid == child_, "Authority child has not exited");
    if (info.si_code == CLD_EXITED) return info.si_status;
    if (info.si_code == CLD_KILLED || info.si_code == CLD_DUMPED) return 128 + info.si_status;
    throw std::runtime_error("Unknown child exit state");
}
void ProcessScope::throttle_memory(std::uint64_t bytes) {
    valid(!started_ && !removed_ && scope_ >= 0 && bytes > 0 && !populated(),
          "Memory throttle requires an empty prepared scope");
    const auto maximum = contents(scope_, "memory.max");
    valid(maximum != "max\n" && bytes <= std::stoull(maximum), "Memory throttle must fit the hard budget");
    const auto value = std::to_string(bytes) + "\n";
    control(scope_, "memory.high", value);
    valid(contents(scope_, "memory.high") == value, "Memory throttle was not applied");
}
void ProcessScope::request_stop() {
    std::exception_ptr failure;
    const auto attempt = [&failure](auto&& action) {
        try { action(); }
        catch (...) { if (!failure) failure = std::current_exception(); }
    };
    if (freeze_owned_) attempt([this] { thaw_payload(); });
    if (payload_ >= 0 && !freeze_owned_) { close(payload_); payload_ = -1; }
    if (!removed_) {
        if (scope_ >= 0) attempt([this] { control(scope_, "cgroup.kill", "1\n"); });
        if (child_ > 0 && !reaped_) attempt([this] {
            const int rc = pidfd_ >= 0 ? static_cast<int>(syscall(SYS_pidfd_send_signal, pidfd_, SIGKILL, nullptr, 0)) : kill(child_, SIGKILL);
            if (rc < 0 && errno != ESRCH) throw std::runtime_error("Cannot terminate owned authority child");
        });
    }
    stop_requested_ = true;
    if (failure) std::rethrow_exception(failure);
}
const v1::ProcessScopeIdentity& ProcessScope::identity() const { return identity_; }
bool ProcessScope::diagnostic_populated() const { return scope_ >= 0 && populated(); }
bool ProcessScope::diagnostic_child_reaped() const { return reaped_; }
v1::ProcessScopeStopped ProcessScope::complete_stop(Clock::time_point deadline) {
    valid(stop_requested_, "Owned termination was not requested");
    if (!removed_) {
        while (scope_ >= 0 && populated()) {
            valid(Clock::now() < deadline, "Authority scope remains populated"); tick();
        }
        while (child_ > 0 && !reaped_) {
            int status; const auto waited = waitpid(child_, &status, WNOHANG);
            if (waited < 0 && errno == EINTR) continue;
            check(waited >= 0, "Cannot reap authority child");
            if (waited == child_) { reaped_ = true; break; }
            valid(Clock::now() < deadline, "Authority child reap deadline exceeded"); tick();
        }
        if (scope_ >= 0) remove_empty_children(scope_);
        if (!identity_.scope_name().empty() && !retained_scope_) {
            struct stat named{};
            check(fstatat(parent_, identity_.scope_name().c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0,
                  "Cannot find owned scope for removal");
            valid(identity_.inode() && identity_.device() == static_cast<std::uint64_t>(named.st_dev) &&
                  identity_.inode() == static_cast<std::uint64_t>(named.st_ino), "Owned scope identity changed");
            check(unlinkat(parent_, identity_.scope_name().c_str(), AT_REMOVEDIR) == 0, "Cannot remove owned scope");
        }
        removed_ = true;
    }
    v1::ProcessScopeStopped result; *result.mutable_identity() = identity_; return result;
}
v1::ProcessScopeStopped ProcessScope::stop() {
    request_stop();
    return complete_stop(Clock::now()+std::chrono::seconds(2));
}
ProcessScope::~ProcessScope() {
    try { stop(); }
    catch (const std::exception& error) {
        std::cerr << "authority process cleanup unconfirmed: " << error.what() << '\n'; _exit(2);
    }
    for (int fd : {pidfd_, scope_, parent_}) if (fd >= 0) close(fd);
}
}
