#include "workspace/resources.hpp"
#include "supervisor/process_scope_controller_reset.hpp"
#include "workspace/file_operation_journal.hpp"
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
    Group(const std::filesystem::path& parent, const Budget& value,
          const std::string& reserved_name = {}) : budget(value) {
        scope = reserved_name.empty() ? supervisor::ProcessScope::prepare(parent, &budget)
                                      : supervisor::ProcessScope::prepare_named(parent, reserved_name, &budget);
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
    std::set<std::string> copy_names;
    require(config.copy_profiles_size()<=32,"Too many copy resource profiles");
    for(const auto& profile:config.copy_profiles()) {
        require(identifier(profile.name()) && profile.revision()>0 && copy_names.insert(profile.name()).second &&
                profile.has_policy() && profile.policy().actors_size()==0,"Invalid independent copy resource profile");
        validate_policy(profile.policy(),true);
        (void)intersect(config.broker_budget(),profile.policy().total());
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
    std::map<std::string,workspace::v1::WorkspaceResourcePolicy> copied_policies;
    std::filesystem::path root;
    int directory = -1;
    identity::tls_detail::Fd manager_directory;
    bool manager_created = false, moved = false, enabled = false, activated = false;
    std::map<std::string, std::shared_ptr<Group>> groups;
    std::shared_ptr<Group> git_group;
    FileOperationJournal* git_journal = nullptr;
    std::filesystem::path git_recovery_parent_path;
    std::uint64_t memory = 0, swap = 0, processes = 0;
    Budget git_budget() const {
        Budget budget;
        budget.set_memory_bytes(256ULL * 1024 * 1024);
        budget.set_swap_bytes(0); budget.set_processes(16); budget.set_cpu_microseconds(200000);
        for (const auto& source : config.broker_budget().io()) {
            auto* target = budget.add_io(); target->set_major(source.major()); target->set_minor(source.minor());
            target->set_read_bytes_per_second(source.read_bytes_per_second());
            target->set_write_bytes_per_second(source.write_bytes_per_second());
        }
        supervisor::validate_budget(budget);
        return budget;
    }
    void ensure_git_group() {
        activate();
        if(git_group) return;
        const auto budget=git_budget();
        if(!fits(budget)) throw WorldError(wire::RESOURCE_EXHAUSTED,"Broker envelope cannot reserve Git worker capacity");
        if(git_journal) {
            struct stat root_stat{};
            if(fstat(directory,&root_stat)!=0) throw std::runtime_error("Cannot inspect Git worker root");
            std::ifstream boot_file("/proc/sys/kernel/random/boot_id");std::string boot_id;std::getline(boot_file,boot_id);
            const auto reservation=git_journal->reserve_git_worker_parent(
                static_cast<std::uint64_t>(root_stat.st_dev),static_cast<std::uint64_t>(root_stat.st_ino),root.string(),boot_id,{});
            if(reservation.state==GitWorkerParentRecord::State::bound)
                throw std::runtime_error("Bound Git worker parent requires broker-wide recovery before reuse");
            if(reservation.state!=GitWorkerParentRecord::State::reserved)
                throw std::runtime_error("Git worker parent reservation was not durable");
            supervisor::ProcessScope::remove_reserved_git_parent(root,
                reservation.root_device,reservation.root_inode,reservation.boot_id,"spatial-git-workers");
        }
        auto group=std::make_shared<Group>(root,budget,"spatial-git-workers");
        group->scope->throttle_memory(192ULL*1024*1024);
        if(git_journal) {
            const auto bytes=group->scope->identity().SerializeAsString();
            struct stat root_stat{};if(fstat(directory,&root_stat)!=0)throw std::runtime_error("Cannot inspect Git worker root after creation");
            std::ifstream boot_file("/proc/sys/kernel/random/boot_id");std::string boot_id;std::getline(boot_file,boot_id);
            git_journal->bind_git_worker_parent(static_cast<std::uint64_t>(root_stat.st_dev),
                static_cast<std::uint64_t>(root_stat.st_ino),root.string(),boot_id,bytes,{});
        }
        reserve(budget); git_group=std::move(group);
    }
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
        require(words(read_at(directory, "cgroup.procs")) == std::set<std::string>{std::to_string(getpid())},
                "Broker resource root must contain only the broker before recovery");
        const auto available = words(read_at(directory, "cgroup.controllers"));
        for (const auto* name : {"cpu", "memory", "pids", "io"}) require(available.contains(name), "Required controller is unavailable");
    }
    void remove_empty_child(const char* name) {
        const int child_fd=openat(directory,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if(child_fd<0 && errno==ENOENT)return;
        check(child_fd>=0,"Cannot open stale broker cgroup");
        identity::tls_detail::Fd child(child_fd);
        struct statfs fs{};struct stat info{};
        check(fstatfs(child.value,&fs)==0 && fstat(child.value,&info)==0,"Cannot inspect stale broker cgroup");
        require(fs.f_type==CGROUP2_SUPER_MAGIC && info.st_uid==geteuid(),"Stale broker child is not an owned cgroup");
        require(read_at(child.value,"cgroup.procs").empty() &&
                read_at(child.value,"cgroup.events").find("populated 0\n")!=std::string::npos,
                "Stale broker child is populated");
        std::istringstream stats(read_at(child.value,"cgroup.stat"));std::string key;unsigned long long count=0;bool known=false;
        while(stats>>key>>count)if(key=="nr_descendants"){known=true;require(count==0,"Stale broker child has descendants");}
        require(known,"Stale broker child descendant count is unavailable");
        struct stat named{};check(fstatat(directory,name,&named,AT_SYMLINK_NOFOLLOW)==0,"Stale broker child disappeared");
        require(named.st_dev==info.st_dev && named.st_ino==info.st_ino,"Stale broker child name changed");
        check(unlinkat(directory,name,AT_REMOVEDIR)==0,"Cannot remove empty stale broker child");
    }
    void activate() {
        if(activated)return;
        require(words(read_at(directory,"cgroup.procs"))==std::set<std::string>{std::to_string(getpid())},
                "Broker resource root is not exclusive after copy recovery");
        remove_empty_child("broker-manager");
        std::istringstream stats(read_at(directory,"cgroup.stat"));std::string key;unsigned long long count=0;bool empty=false;
        while(stats>>key>>count)if(key=="nr_descendants")empty=count==0;
        require(empty,"Broker resource root still has unknown descendants after copy recovery");
        const auto active=words(read_at(directory,"cgroup.subtree_control"));
        for(const auto& name:active)require(name=="cpu"||name=="memory"||name=="pids"||name=="io",
                "Broker resource root has an unknown subtree controller");
        if(!active.empty()) {
            const auto disable=supervisor::detail::stale_subtree_controller_disable_command(
                read_at(directory,"cgroup.subtree_control"));
            if(!disable.empty())write_at(directory,"cgroup.subtree_control",disable);
        }
        const auto expected_cpu=std::to_string(config.broker_budget().cpu_microseconds())+" 100000\n";
        auto compatible=[](const std::string& current,const std::string& expected,const std::string& fresh) {
            return current==fresh || current==expected;
        };
        require(compatible(read_at(directory,"memory.max"),std::to_string(config.broker_budget().memory_bytes())+"\n","max\n") &&
                compatible(read_at(directory,"memory.swap.max"),std::to_string(config.broker_budget().swap_bytes())+"\n","max\n") &&
                compatible(read_at(directory,"pids.max"),std::to_string(config.broker_budget().processes())+"\n","max\n") &&
                compatible(read_at(directory,"cpu.max"),expected_cpu,"max 100000\n") &&
                compatible(read_at(directory,"memory.oom.group"),"1\n","0\n"),
                "Broker envelope would replace an unknown root limit");
        const auto io=read_at(directory,"io.max");
        std::map<std::string,std::pair<std::string,std::string>> expected_io;
        for(const auto& item:config.broker_budget().io())
            expected_io[std::to_string(item.major())+":"+std::to_string(item.minor())]=
                {std::to_string(item.read_bytes_per_second()),std::to_string(item.write_bytes_per_second())};
        std::map<std::string,std::pair<std::string,std::string>> actual_io;
        std::istringstream io_lines(io);std::string io_line;
        while(std::getline(io_lines,io_line)) {
            std::istringstream fields(io_line);std::string device,field,read="max",write="max";
            if(!(fields>>device))continue;
            while(fields>>field) {
                const auto split=field.find('=');
                require(split!=std::string::npos,"Malformed existing broker I/O limit");
                const auto name=field.substr(0,split),value=field.substr(split+1);
                if(name=="rbps")read=value;else if(name=="wbps")write=value;
                else require((name=="riops"||name=="wiops")&&value=="max",
                    "Broker envelope would replace an unknown I/O operation limit");
            }
            actual_io[device]={read,write};
        }
        require(io.empty() || actual_io==expected_io,"Broker envelope would replace unknown I/O limits");
        write_budget(directory,config.broker_budget());
        check(mkdirat(directory,"broker-manager",0700)==0,"Cannot create broker manager group");manager_created=true;
        const int manager=openat(directory,"broker-manager",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        check(manager>=0,"Cannot pin broker manager group");
        try {
            write_at(manager,"cgroup.procs","0\n");moved=true;
            write_at(directory,"cgroup.subtree_control","+cpu +memory +pids +io\n");enabled=true;
            write_budget(manager,config.manager_budget());
        } catch(...) {close(manager);throw;}
        close(manager);
        manager_directory.reset(openat(directory,"broker-manager",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
        check(manager_directory.value>=0,"Cannot retain manager resource accounting");
        activated=true;
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
const supervisor::v1::ProcessScopeIdentity& GitJobResources::parent_identity() const {
    return impl_->group->scope->identity();
}
ApplicationResources::ApplicationResources(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
ApplicationResources::~ApplicationResources() = default;
supervisor::ProcessScope& ApplicationResources::scope() { return *impl_->scope; }
workspace::v1::WorkspaceCopyResourceProfile WorkspaceResources::resolve_copy_profile(const std::string& name,const std::string& application_profile) const {
    for(const auto& configured:impl_->config.copy_profiles())if(configured.name()==name) {
        auto selected=configured;selected.mutable_policy()->clear_profiles();
        for(const auto& application:configured.policy().profiles())if(application.name()==application_profile)*selected.mutable_policy()->add_profiles()=application;
        require(selected.policy().profiles_size()==1,"Copy application profile is absent from the independent resource policy");return selected;
    }
    throw WorldError(wire::FORBIDDEN,"Independent copy resource profile is unavailable");
}
void WorkspaceResources::register_copy_workspace(const std::string& workspace,const workspace::v1::WorkspaceCopyResourceProfile& pinned) {
    require(!workspace.empty() && workspace.size()<=128 && pinned.has_policy() && pinned.policy().profiles_size()==1,"Malformed persisted copy resource binding");
    const auto current=resolve_copy_profile(pinned.name(),pinned.policy().profiles(0).name());
    require(current.SerializeAsString()==pinned.SerializeAsString(),"Persisted destination resource policy changed");
    auto policy=pinned.policy();policy.set_workspace(workspace);
    const auto found=impl_->copied_policies.find(workspace);
    if(found!=impl_->copied_policies.end())require(found->second.SerializeAsString()==policy.SerializeAsString(),"Copy resource policy is already bound differently");
    else impl_->copied_policies.emplace(workspace,std::move(policy));
}
bool WorkspaceResources::has_profile(const std::string& name, const std::string& workspace) const {
    if (name.empty() || name.size()>128 ||
        std::any_of(name.begin(),name.end(),[](unsigned char c){return c<0x20 || c==0x7f;})) return false;
    const auto* policy=&impl_->config.default_policy();
    for (const auto& selected:impl_->config.workspaces())
        if (!workspace.empty() && selected.workspace()==workspace) policy=&selected;
    for (const auto& selected:policy->profiles()) if (selected.name()==name) return true;
    return false;
}
const supervisor::v1::ProcessScopeIdentity& WorkspaceResources::git_worker_parent_identity() {
    impl_->ensure_git_group();
    return impl_->git_group->scope->identity();
}
std::filesystem::path WorkspaceResources::git_worker_parent_path() const {
    if(!impl_->git_recovery_parent_path.empty()) return impl_->git_recovery_parent_path;
    return impl_->git_group ? impl_->git_group->path : impl_->root / "spatial-git-workers";
}
std::filesystem::path WorkspaceResources::broker_root_path() const { return impl_->root; }
void WorkspaceResources::set_git_worker_recovery_parent_path(const std::filesystem::path& path) {
    require(!path.empty() && path.is_absolute() && path.lexically_normal()==path,
        "Git worker recovery parent path must be absolute and normalized");
    impl_->git_recovery_parent_path=path;
}
void WorkspaceResources::clear_git_worker_recovery_parent_path() { impl_->git_recovery_parent_path.clear(); }
void WorkspaceResources::bind_git_worker_journal(FileOperationJournal* journal) {
    require(!impl_->git_group,"Git worker journal must bind before worker parent creation");
    impl_->git_journal=journal;
}
void WorkspaceResources::finish_initialization() { impl_->activate(); }
std::pair<std::uint64_t,std::uint64_t> WorkspaceResources::broker_root_identity() const {
    struct stat info{};
    if(fstat(impl_->directory,&info)!=0) throw std::runtime_error("Cannot inspect broker cgroup identity");
    return {static_cast<std::uint64_t>(info.st_dev),static_cast<std::uint64_t>(info.st_ino)};
}
std::unique_ptr<GitJobResources> WorkspaceResources::prepare_git_job(
    std::span<const int> directory_handles, const std::string& reserved_scope_name) {
    require(!directory_handles.empty() && directory_handles.size() <= 12, "Git worker requires bounded directory handles");
    validate_storage_device_handles(impl_->config.broker_budget(), directory_handles);
    Budget budget=impl_->git_budget();
    impl_->ensure_git_group();
    // Keep completed-job cache in this bounded parent. Each new job still gets its own scope.
    if (!impl_->git_group->fits(budget))
        throw WorldError(wire::RESOURCE_EXHAUSTED, "Git worker capacity is occupied");
    auto prepared = std::make_unique<GitJobResources::Impl>();
    prepared->owner = impl_; prepared->group = impl_->git_group; prepared->budget = budget;
    prepared->group->reserve(budget); prepared->reserved = true;
    prepared->scope = reserved_scope_name.empty()
        ? supervisor::ProcessScope::prepare(prepared->group->path, &budget)
        : supervisor::ProcessScope::prepare_named(prepared->group->path, reserved_scope_name, &budget);
    prepared->scope->throttle_memory(192ULL * 1024 * 1024);
    return std::unique_ptr<GitJobResources>(new GitJobResources(std::move(prepared)));
}
std::unique_ptr<ApplicationResources> WorkspaceResources::prepare(const workspace::v1::LaunchRequest& request,
    const std::array<std::filesystem::path, 3>& paths) {
    impl_->activate();
    const auto* policy = &impl_->config.default_policy();
    for (const auto& selected : impl_->config.workspaces()) if (selected.workspace() == request.workspace()) policy = &selected;
    if(const auto copied=impl_->copied_policies.find(request.workspace());copied!=impl_->copied_policies.end())policy=&copied->second;
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
