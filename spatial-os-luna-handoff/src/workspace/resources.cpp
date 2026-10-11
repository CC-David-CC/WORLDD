#include "workspace/resources.hpp"
#include "workspace/storage_devices.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include "world/store.hpp"
#include <google/protobuf/text_format.h>
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <linux/magic.h>
#include <map>
#include <set>
#include <sstream>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <unistd.h>
#include <utility>

namespace spatial {
namespace {
using Budget = supervisor::v1::ProcessScopeBudget;
using Device = std::pair<unsigned, unsigned>;
void require(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
bool identifier(const std::string& value) { return !value.empty() && value.size() <= 128 && value.find('\0') == std::string::npos; }
std::string read_at(int directory, const char* name) {
    const int fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    check(fd >= 0, "Cannot open resource hierarchy state");
    std::string result; char buffer[4096];
    for (;;) {
        const auto size = read(fd, buffer, sizeof(buffer));
        if (size < 0 && errno == EINTR) continue;
        if (size < 0 || result.size() > 65536) { close(fd); throw std::runtime_error("Cannot read resource hierarchy state"); }
        if (!size) break;
        result.append(buffer, static_cast<std::size_t>(size));
    }
    close(fd); return result;
}
void write_at(int directory, const char* name, const std::string& value) {
    const int fd = openat(directory, name, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    check(fd >= 0, "Cannot open resource hierarchy control");
    ssize_t size;
    do { size = write(fd, value.data(), value.size()); } while (size < 0 && errno == EINTR);
    close(fd); check(size == static_cast<ssize_t>(value.size()), "Cannot configure resource hierarchy");
}
void write_budget(int directory, const supervisor::v1::ProcessScopeBudget& budget) {
    write_at(directory, "memory.max", std::to_string(budget.memory_bytes()) + "\n");
    write_at(directory, "memory.swap.max", std::to_string(budget.swap_bytes()) + "\n");
    write_at(directory, "memory.oom.group", "1\n");
    write_at(directory, "pids.max", std::to_string(budget.processes()) + "\n");
    write_at(directory, "cpu.max", std::to_string(budget.cpu_microseconds()) + " 100000\n");
    for (const auto& io : budget.io())
        write_at(directory, "io.max", std::to_string(io.major()) + ":" + std::to_string(io.minor()) +
            " rbps=" + std::to_string(io.read_bytes_per_second()) + " wbps=" +
            std::to_string(io.write_bytes_per_second()) + "\n");
}
std::set<std::string> words(const std::string& value) {
    std::istringstream input(value); std::set<std::string> result; std::string item;
    while (input >> item) result.insert(item);
    return result;
}
std::string actor_key(const workspace::v1::ActorResourceLimit& actor) {
    if (actor.has_principal()) {
        require(actor.principal().issuer_id().size() == 32 && actor.principal().actor_id().size() == 32,
                "Resource actor requires an exact issuer and actor identity");
        return "p" + actor.principal().issuer_id() + actor.principal().actor_id();
    }
    require(actor.has_fixture_actor() && identifier(actor.fixture_actor()), "Invalid resource actor selector");
    return "f" + actor.fixture_actor();
}
Budget intersect(const Budget& left, const Budget& right) {
    supervisor::validate_budget(left); supervisor::validate_budget(right);
    Budget result;
    result.set_memory_bytes(std::min(left.memory_bytes(), right.memory_bytes()));
    result.set_swap_bytes(std::min(left.swap_bytes(), right.swap_bytes()));
    result.set_processes(std::min(left.processes(), right.processes()));
    result.set_cpu_microseconds(std::min(left.cpu_microseconds(), right.cpu_microseconds()));
    std::map<Device, const Budget::IoLimit*> devices;
    for (const auto& io : right.io()) devices.emplace(Device{io.major(), io.minor()}, &io);
    require(left.io_size() == right.io_size(), "Resource policies must cover the same I/O devices");
    for (const auto& io : left.io()) {
        const auto found = devices.find({io.major(), io.minor()});
        require(found != devices.end(), "Resource policies must cover the same I/O devices");
        auto* output = result.add_io(); output->set_major(io.major()); output->set_minor(io.minor());
        output->set_read_bytes_per_second(std::min(io.read_bytes_per_second(), found->second->read_bytes_per_second()));
        output->set_write_bytes_per_second(std::min(io.write_bytes_per_second(), found->second->write_bytes_per_second()));
    }
    return result;
}
void validate_policy(const workspace::v1::WorkspaceResourcePolicy& policy, bool defaults) {
    require(defaults ? policy.workspace().empty() : identifier(policy.workspace()), "Invalid workspace resource policy identity");
    require(policy.has_total() && policy.has_actor_default(), "Explicit workspace and actor budgets are required");
    (void)intersect(policy.total(), policy.actor_default());
    require(policy.actors_size() <= 128 && policy.profiles_size() > 0 && policy.profiles_size() <= 32,
            "Resource policy size exceeds limits");
    std::set<std::string> actors, profiles;
    for (const auto& actor : policy.actors()) {
        require(actors.insert(actor_key(actor)).second && actor.has_maximum(), "Duplicate or incomplete actor resource policy");
        (void)intersect(policy.total(), actor.maximum());
    }
    for (const auto& profile : policy.profiles()) {
        require(identifier(profile.name()) && profiles.insert(profile.name()).second && profile.has_maximum(),
                "Duplicate or incomplete application resource profile");
        (void)intersect(policy.total(), profile.maximum());
    }
}
void validate_devices(const Budget& budget, const std::array<std::filesystem::path, 3>& paths) {
    std::array<identity::tls_detail::Fd, 3> files;
    std::array<int, 3> handles;
    for (std::size_t index = 0; index < paths.size(); ++index) {
        files[index].reset(open(paths[index].c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC));
        handles[index] = files[index].value;
    }
    validate_storage_device_handles(budget, handles);
}

struct Group {
    std::unique_ptr<supervisor::ProcessScope> scope;
    std::filesystem::path path;
    identity::tls_detail::Fd directory;
    Budget budget;
    std::uint64_t memory = 0, swap = 0, processes = 0, applications = 0;
    std::map<std::string, std::shared_ptr<Group>> actors;
    Group(const std::filesystem::path& parent, const Budget& value) : budget(value) {
        scope = supervisor::ProcessScope::prepare(parent, &budget);
        path = parent / scope->state().identity().scope_name();
        directory.reset(open(path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        struct stat info{};
        const auto identity = scope->state().identity();
        check(directory.value >= 0 && fstat(directory.value, &info) == 0 &&
              static_cast<std::uint64_t>(info.st_dev) == identity.device() && info.st_ino == identity.inode(),
              "Cannot retain resource accounting group");
        scope->enable_children();
    }
    bool fits(const Budget& value) const {
        return value.memory_bytes() <= budget.memory_bytes()-memory && value.swap_bytes() <= budget.swap_bytes()-swap &&
               value.processes() <= budget.processes()-processes;
    }
    void reserve(const Budget& value) {
        memory += value.memory_bytes(); swap += value.swap_bytes(); processes += value.processes(); ++applications;
    }
    void release(const Budget& value) {
        require(applications && memory >= value.memory_bytes() && swap >= value.swap_bytes() && processes >= value.processes(),
                "Resource reservation accounting is inconsistent");
        memory -= value.memory_bytes(); swap -= value.swap_bytes(); processes -= value.processes(); --applications;
    }
};
}
void validate_resource_config(const workspace::v1::BrokerResourceConfig& config) {
    validate_message(config);
    require(config.protocol_version() == 2 && config.has_broker_budget() && config.has_manager_budget() &&
            config.has_default_policy() && config.workspaces_size() <= 64,
            "Resource configuration version 2 requires an explicit broker envelope and default policy");
    supervisor::validate_budget(config.broker_budget());
    supervisor::validate_budget(config.manager_budget());
    require(config.manager_budget().memory_bytes() < config.broker_budget().memory_bytes() &&
            config.manager_budget().swap_bytes() <= config.broker_budget().swap_bytes() &&
            config.manager_budget().processes() < config.broker_budget().processes() &&
            config.manager_budget().cpu_microseconds() <= config.broker_budget().cpu_microseconds(),
            "Broker envelope must leave capacity beyond its manager reservation");
    (void)intersect(config.broker_budget(), config.manager_budget());
    validate_policy(config.default_policy(), true);
    (void)intersect(config.broker_budget(), config.default_policy().total());
    std::set<std::string> workspaces;
    for (const auto& policy : config.workspaces()) {
        validate_policy(policy, false);
        (void)intersect(config.broker_budget(), policy.total());
        require(workspaces.insert(policy.workspace()).second, "Duplicate workspace resource policy");
    }
}
int seal_resource_config(const workspace::v1::BrokerResourceConfig& configuration,
                         const std::filesystem::path& owned_scope) {
    validate_resource_config(configuration);
    require(configuration.delegated_cgroup().empty(), "Broker resource template must not select a cgroup");
    require(owned_scope.is_absolute() && owned_scope.string().find('\0') == std::string::npos,
            "Broker resources require an absolute owned scope");
    auto bound = configuration; bound.set_delegated_cgroup(owned_scope.string());
    const auto bytes = bound.SerializeAsString();
    require(!bytes.empty() && bytes.size() <= 65536, "Broker resource configuration exceeds limit");
    identity::tls_detail::Fd file(memfd_create("broker-resource-policy", MFD_CLOEXEC | MFD_ALLOW_SEALING));
    check(file.value >= 3, "Cannot create broker resource handoff");
    std::size_t written = 0;
    while (written < bytes.size()) {
        const auto count = write(file.value, bytes.data()+written, bytes.size()-written);
        if (count < 0 && errno == EINTR) continue;
        check(count > 0, "Cannot write broker resource handoff");
        written += static_cast<std::size_t>(count);
    }
    check(fcntl(file.value, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0,
          "Cannot seal broker resource handoff");
    return std::exchange(file.value, -1);
}
struct WorkspaceResources::Impl {
    workspace::v1::BrokerResourceConfig config;
    std::filesystem::path root;
    int directory = -1;
    identity::tls_detail::Fd manager_directory;
    bool manager_created = false, moved = false, enabled = false;
    std::map<std::string, std::shared_ptr<Group>> groups;
    std::shared_ptr<Group> git_group;
    std::uint64_t memory = 0, swap = 0, processes = 0;
    std::uint64_t unfunded(const char* counter, std::uint64_t manager_reserve, unsigned dimension) const {
        auto funded = std::min(std::stoull(read_at(manager_directory.value, counter)),
                               static_cast<unsigned long long>(manager_reserve));
        for (const auto& [name, group] : groups) {
            (void)name;
            const auto reserve = dimension == 0 ? group->memory : dimension == 1 ? group->swap : group->processes;
            funded += std::min(std::stoull(read_at(group->directory.value, counter)),
                               static_cast<unsigned long long>(reserve));
        }
        if (git_group) {
            const auto& budget = git_group->budget;
            const auto reserve = dimension == 0 ? budget.memory_bytes() : dimension == 1 ? budget.swap_bytes() : budget.processes();
            funded += std::min(std::stoull(read_at(git_group->directory.value, counter)),
                               static_cast<unsigned long long>(reserve));
        }
        const auto current = std::stoull(read_at(directory, counter));
        return current > funded ? current-funded : 0;
    }
    bool fits(const Budget& value) const {
        const auto& total = config.broker_budget();
        const auto& manager = config.manager_budget();
        auto available = [](std::uint64_t maximum, std::uint64_t reserve, std::uint64_t held,
                            std::uint64_t current, std::uint64_t request) {
            if (reserve > maximum) return false;
            auto remaining = maximum - reserve;
            if (held > remaining) return false;
            remaining -= held;
            if (current > remaining) return false;
            return request <= remaining - current;
        };
        // Existing reservations already fund their bounded usage. Count remaining charges
        // separately, including cache reparented after earlier scopes disappear.
        return available(total.memory_bytes(), manager.memory_bytes(), memory,
                   unfunded("memory.current", manager.memory_bytes(), 0), value.memory_bytes()) &&
               available(total.swap_bytes(), manager.swap_bytes(), swap,
                   unfunded("memory.swap.current", manager.swap_bytes(), 1), value.swap_bytes()) &&
               available(total.processes(), manager.processes(), processes,
                   unfunded("pids.current", manager.processes(), 2), value.processes());
    }
    void reserve(const Budget& value) {
        memory += value.memory_bytes(); swap += value.swap_bytes(); processes += value.processes();
    }
    void release(const Budget& value) {
        require(memory >= value.memory_bytes() && swap >= value.swap_bytes() && processes >= value.processes(),
                "Broker envelope reservation accounting is inconsistent");
        memory -= value.memory_bytes(); swap -= value.swap_bytes(); processes -= value.processes();
    }
    void initialize(workspace::v1::BrokerResourceConfig value) {
        validate_resource_config(value); config = std::move(value);
        root = config.delegated_cgroup();
        std::ifstream current("/proc/self/cgroup"); std::string line; std::getline(current, line);
        require(line.starts_with("0::/") && root.is_absolute() &&
                root == std::filesystem::path("/sys/fs/cgroup") / line.substr(4) && line != "0::/",
                "Resource root must be the broker's exact owned cgroup");
        directory = open(root.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        check(directory >= 0, "Cannot pin broker resource root");
        struct statfs fs{}; struct stat info{};
        check(fstatfs(directory, &fs) == 0 && fstat(directory, &info) == 0, "Cannot inspect broker resource root");
        require(fs.f_type == CGROUP2_SUPER_MAGIC && info.st_uid == geteuid() && read_at(directory, "cgroup.type") == "domain\n",
                "Expected an owned domain cgroup");
        require(words(read_at(directory, "cgroup.procs")) == std::set<std::string>{std::to_string(getpid())} &&
                words(read_at(directory, "cgroup.subtree_control")).empty(), "Broker resource root must be exclusive and unconfigured");
        std::istringstream stats(read_at(directory, "cgroup.stat")); std::string key; unsigned long long count = 0; bool empty = false;
        while (stats >> key >> count) if (key == "nr_descendants") empty = count == 0;
        require(empty, "Broker resource root must have no existing descendants");
        const auto available = words(read_at(directory, "cgroup.controllers"));
        for (const auto* name : {"cpu", "memory", "pids", "io"}) require(available.contains(name), "Required controller is unavailable");
        // Bind the exclusive root once, before opening any execution database or running Git.
        // The envelope covers the manager and every nested workspace process.
        require(read_at(directory, "memory.max") == "max\n" && read_at(directory, "memory.swap.max") == "max\n" &&
                read_at(directory, "pids.max") == "max\n" && read_at(directory, "cpu.max") == "max 100000\n" &&
                read_at(directory, "io.max").empty(), "Broker envelope must not replace existing root limits");
        write_budget(directory, config.broker_budget());
        check(mkdirat(directory, "broker-manager", 0700) == 0, "Cannot create broker manager group"); manager_created = true;
        const int manager = openat(directory, "broker-manager", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        check(manager >= 0, "Cannot pin broker manager group");
        try {
            write_at(manager, "cgroup.procs", "0\n"); moved = true; enabled = true;
            write_at(directory, "cgroup.subtree_control", "+cpu +memory +pids +io\n");
            write_budget(manager, config.manager_budget());
        } catch (...) { close(manager); throw; }
        close(manager);
        manager_directory.reset(openat(directory, "broker-manager", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        check(manager_directory.value >= 0, "Cannot retain manager resource accounting");
    }
    ~Impl() {
        try {
            groups.clear();
            git_group.reset();
            if (enabled) write_at(directory, "cgroup.subtree_control", "-cpu -memory -pids -io\n");
            if (moved) write_at(directory, "cgroup.procs", "0\n");
            if (manager_created) check(unlinkat(directory, "broker-manager", AT_REMOVEDIR) == 0, "Cannot remove broker manager group");
        } catch (const std::exception& error) { std::cerr << "resource cleanup unconfirmed: " << error.what() << '\n'; _exit(2); }
        if (directory >= 0) close(directory);
    }
};
struct ApplicationResources::Impl {
    std::shared_ptr<WorkspaceResources::Impl> owner;
    std::shared_ptr<Group> workspace, actor;
    std::unique_ptr<supervisor::ProcessScope> scope;
    Budget budget;
    std::string workspace_key, actor_key;
    bool reserved = false;
    ~Impl() {
        try {
            scope.reset(); // Checked kernel cleanup precedes capacity release.
            if (reserved) {
                owner->release(budget);
                actor->release(budget); workspace->release(budget);
                if (!actor->applications) workspace->actors.erase(actor_key);
                if (!workspace->applications) owner->groups.erase(workspace_key);
            }
            actor.reset(); workspace.reset(); owner.reset();
        } catch (const std::exception& error) {
            std::cerr << "resource reservation cleanup unconfirmed: " << error.what() << '\n'; _exit(2);
        }
    }
};
struct GitJobResources::Impl {
    std::shared_ptr<WorkspaceResources::Impl> owner;
    std::shared_ptr<Group> group;
    std::unique_ptr<supervisor::ProcessScope> scope;
    Budget budget;
    bool reserved = false;
    ~Impl() {
        try {
            scope.reset();
            if (reserved) group->release(budget);
            group.reset();
            owner.reset();
        } catch (const std::exception& error) {
            std::cerr << "Git worker resource cleanup unconfirmed: " << error.what() << '\n'; _exit(2);
        }
    }
};
WorkspaceResources::WorkspaceResources(const std::filesystem::path& configuration) : impl_(std::make_shared<Impl>()) {
    workspace::v1::BrokerResourceConfig value;
    require(google::protobuf::TextFormat::ParseFromString(identity::tls_detail::load_config(configuration), &value),
            "Invalid broker resource configuration");
    impl_->initialize(std::move(value));
}
WorkspaceResources::WorkspaceResources(int descriptor, bool require_aggregate) : impl_(std::make_shared<Impl>()) {
    identity::tls_detail::Fd file(descriptor);
    constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    struct stat info{};
    const int actual = descriptor >= 3 ? fcntl(descriptor, F_GET_SEALS) : -1;
    require(actual >= 0 && (actual & seals) == seals && fstat(descriptor, &info) == 0 &&
            S_ISREG(info.st_mode) && info.st_uid == geteuid() && info.st_size > 0 && info.st_size <= 65536,
            "Broker resource handoff must be an owned sealed protobuf");
    std::string bytes(static_cast<std::size_t>(info.st_size), '\0');
    ssize_t count;
    do { count = pread(descriptor, bytes.data(), bytes.size(), 0); } while (count < 0 && errno == EINTR);
    require(count == static_cast<ssize_t>(bytes.size()), "Cannot read broker resource handoff");
    workspace::v1::BrokerResourceConfig value;
    require(value.ParseFromString(bytes), "Invalid broker resource protobuf");
    require(!require_aggregate || value.protocol_version() == 2,
            "Production broker requires an aggregate resource envelope");
    file.reset();
    impl_->initialize(std::move(value));
}
WorkspaceResources::~WorkspaceResources() = default;
GitJobResources::GitJobResources(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
GitJobResources::~GitJobResources() = default;
supervisor::ProcessScope& GitJobResources::scope() { return *impl_->scope; }
ApplicationResources::ApplicationResources(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
ApplicationResources::~ApplicationResources() = default;
supervisor::ProcessScope& ApplicationResources::scope() { return *impl_->scope; }
std::unique_ptr<GitJobResources> WorkspaceResources::prepare_git_job(std::span<const int> directory_handles) {
    require(!directory_handles.empty() && directory_handles.size() <= 12, "Git worker requires bounded directory handles");
    validate_storage_device_handles(impl_->config.broker_budget(), directory_handles);
    Budget budget;
    budget.set_memory_bytes(256ULL * 1024 * 1024);
    budget.set_swap_bytes(0);
    budget.set_processes(16);
    budget.set_cpu_microseconds(200000);
    for (const auto& source : impl_->config.broker_budget().io()) {
        auto* target = budget.add_io();
        target->set_major(source.major()); target->set_minor(source.minor());
        target->set_read_bytes_per_second(source.read_bytes_per_second());
        target->set_write_bytes_per_second(source.write_bytes_per_second());
    }
    supervisor::validate_budget(budget);
    if (!impl_->git_group) {
        if (!impl_->fits(budget)) throw WorldError(wire::RESOURCE_EXHAUSTED, "Broker envelope cannot reserve Git worker capacity");
        auto group = std::make_shared<Group>(impl_->root, budget);
        group->scope->throttle_memory(192ULL * 1024 * 1024);
        impl_->reserve(budget);
        impl_->git_group = std::move(group);
    }
    // Keep completed-job cache in this bounded parent. Each new job still gets its own scope.
    if (!impl_->git_group->fits(budget))
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Git worker capacity is occupied");
    auto prepared = std::make_unique<GitJobResources::Impl>();
    prepared->owner = impl_; prepared->group = impl_->git_group; prepared->budget = budget;
    prepared->group->reserve(budget); prepared->reserved = true;
    prepared->scope = supervisor::ProcessScope::prepare(prepared->group->path, &budget);
    prepared->scope->throttle_memory(192ULL * 1024 * 1024);
    return std::unique_ptr<GitJobResources>(new GitJobResources(std::move(prepared)));
}
std::unique_ptr<ApplicationResources> WorkspaceResources::prepare(const workspace::v1::LaunchRequest& request,
    const std::array<std::filesystem::path, 3>& paths) {
    const auto* policy = &impl_->config.default_policy();
    for (const auto& selected : impl_->config.workspaces()) if (selected.workspace() == request.workspace()) policy = &selected;
    workspace::v1::ActorResourceLimit actor;
    if (request.has_principal()) *actor.mutable_principal() = request.principal(); else actor.set_fixture_actor(request.actor());
    const auto key = actor_key(actor);
    auto actor_budget = policy->actor_default();
    for (const auto& selected : policy->actors()) if (actor_key(selected) == key) actor_budget = selected.maximum();
    actor_budget = intersect(policy->total(), actor_budget);
    const Budget* profile = nullptr;
    for (const auto& selected : policy->profiles()) if (selected.name() == request.command().profile()) profile = &selected.maximum();
    if (!profile) throw WorldError(wire::FORBIDDEN, "Application resource profile is unavailable");
    const auto effective = intersect(actor_budget, *profile); validate_devices(effective, paths);
    if (!impl_->fits(effective)) throw WorldError(wire::RESOURCE_EXHAUSTED, "Broker envelope capacity is reserved or occupied");
    auto& workspace = impl_->groups[request.workspace()];
    if (!workspace) workspace = std::make_shared<Group>(impl_->root, policy->total());
    if (!workspace->fits(effective)) throw WorldError(wire::RESOURCE_EXHAUSTED, "Workspace resource capacity is reserved");
    auto& actor_group = workspace->actors[key];
    if (!actor_group) actor_group = std::make_shared<Group>(workspace->path, actor_budget);
    if (!workspace->fits(effective) || !actor_group->fits(effective))
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Workspace or actor resource capacity is reserved");
    auto prepared = std::make_unique<ApplicationResources::Impl>();
    prepared->owner = impl_; prepared->workspace = workspace; prepared->actor = actor_group;
    prepared->workspace_key = request.workspace(); prepared->actor_key = key; prepared->budget = effective;
    impl_->reserve(effective); workspace->reserve(effective); actor_group->reserve(effective); prepared->reserved = true;
    prepared->scope = supervisor::ProcessScope::prepare(actor_group->path, &effective);
    return std::unique_ptr<ApplicationResources>(new ApplicationResources(std::move(prepared)));
}
}
