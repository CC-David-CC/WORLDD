#pragma once
#include "supervisor/host_actor_controller.hpp"
#include <fstream>
#include <sstream>
#include <cstdlib>
#include <signal.h>
#include <vector>
#include <chrono>
#include <thread>
#include <algorithm>
#include <utility>
#include <poll.h>
#include <sys/syscall.h>

namespace {
const char* native_setting(const char* name) {
    const auto* value = std::getenv(name);
    if (!value || !*value) throw std::runtime_error(std::string("Missing native fixture setting: ") + name);
    return value;
}
std::string process_start(std::uint64_t pid) {
    std::ifstream file("/proc/" + std::to_string(pid) + "/stat");
    std::string line; std::getline(file, line);
    const auto end = line.rfind(')');
    if (!file || end == std::string::npos) throw std::runtime_error("Missing retained native process");
    std::istringstream fields(line.substr(end + 2)); std::string value;
    for (int field = 3; field <= 22; ++field)
        if (!(fields >> value)) throw std::runtime_error("Malformed native process identity");
    return value;
}
std::string application_code() {
    return R"PY(import json,os,subprocess,sys,time
from pathlib import Path
status=Path('/proc/self/status').read_text()
fds=[]
for fd in range(3,256):
 try: os.fstat(fd); fds.append(fd)
 except OSError: pass
Path('/state/data/confinement.json').write_text(json.dumps(dict(cwd=os.getcwd(),fds=fds,
 mnt=os.readlink('/proc/self/ns/mnt'),no_new_privs='NoNewPrivs:\t1' in status)))
out=open('/state/data/probe.jsonl','w')
p=subprocess.Popen(['/project/probe','--id',sys.argv[1]],stdin=subprocess.PIPE,stdout=out,stderr=out)
sent=0
while not Path('/state/data/release').exists():
 if p.poll() is not None: raise SystemExit(p.returncode or 1)
 commands=Path('/state/data/commands')
 if commands.exists():
  data=commands.read_bytes(); end=data.rfind(b'\n')+1
  if end>sent: p.stdin.write(data[sent:end]);p.stdin.flush();sent=end
 time.sleep(.01)
p.terminate();p.wait();out.close()
)PY";
}
struct NativeApplication {
    std::filesystem::path data;
    std::uint64_t pid = 0, namespace_pid = 0, wrapper_pid = 0;
    std::string start, wrapper_start;
    std::string pid_namespace;
    struct Pin {
        int fd = -1;
        Pin() = default;
        explicit Pin(int value) : fd(value) {}
        Pin(const Pin&) = delete;
        Pin& operator=(const Pin&) = delete;
        Pin(Pin&& other) noexcept : fd(std::exchange(other.fd, -1)) {}
        Pin& operator=(Pin&& other) noexcept { if (this != &other) { if (fd >= 0) close(fd); fd = std::exchange(other.fd, -1); } return *this; }
        ~Pin() { if (fd >= 0) close(fd); }
    } pin, wrapper_pin;
};
struct ProcIdentity {
    std::uint64_t parent = 0, local_pid = 0;
    unsigned pid_levels = 0;
};
ProcIdentity proc_identity(std::uint64_t pid) {
    std::ifstream file("/proc/" + std::to_string(pid) + "/status");
    ProcIdentity result; std::string line;
    while (std::getline(file, line)) {
        if (line.starts_with("PPid:")) { std::istringstream in(line.substr(5)); in >> result.parent; }
        if (line.starts_with("NSpid:")) {
            std::istringstream in(line.substr(6)); std::uint64_t value = 0;
            while (in >> value) { result.local_pid = value; ++result.pid_levels; }
        }
    }
    if (!result.parent || !result.local_pid) throw std::runtime_error("Cannot inspect application process ancestry");
    return result;
}
std::string proc_link(std::uint64_t pid, const char* item) {
    return std::filesystem::read_symlink("/proc/" + std::to_string(pid) + "/" + item).string();
}
bool live_pidfd(int fd) {
    pollfd state{fd, POLLIN, 0};
    const auto result = poll(&state, 1, 0);
    if (result < 0) throw std::runtime_error("Cannot poll pinned witness");
    return result == 0;
}
bool descends_from(std::uint64_t pid, std::uint64_t owner) {
    for (unsigned depth = 0; depth < 16 && pid > 1; ++depth) {
        if (pid == owner) return true;
        pid = proc_identity(pid).parent;
    }
    return false;
}
NativeApplication pin_witness(const std::filesystem::path& data, const std::string& name,
                              std::uint64_t local_pid, std::uint64_t workspaced_pid) {
    NativeApplication selected; unsigned matches = 0;
    for (const auto& entry : std::filesystem::directory_iterator("/proc")) {
        const auto label = entry.path().filename().string();
        if (label.empty() || !std::all_of(label.begin(), label.end(), [](unsigned char c) { return c >= '0' && c <= '9'; })) continue;
        try {
            const auto pid = std::stoull(label);
            const auto status = proc_identity(pid);
            if (status.pid_levels < 2 || status.local_pid != local_pid) continue;
            std::ifstream command(entry.path() / "cmdline", std::ios::binary);
            const std::string args(std::istreambuf_iterator<char>(command), {});
            if (args != std::string("/project/probe\0--id\0", 20) + name + '\0') continue;
            NativeApplication::Pin pin(static_cast<int>(syscall(SYS_pidfd_open, pid, 0)));
            if (pin.fd < 0 || !live_pidfd(pin.fd)) continue;
            const auto start = process_start(pid);
            const auto parent = status.parent;
            if (!descends_from(parent, workspaced_pid) ||
                proc_link(pid, "ns/pid") != proc_link(parent, "ns/pid") ||
                !std::filesystem::equivalent(entry.path() / "root/state/data", data)) continue;
            std::ifstream wrapper("/proc/" + std::to_string(parent) + "/cmdline", std::ios::binary);
            const std::string wrapper_args(std::istreambuf_iterator<char>(wrapper), {});
            if (wrapper_args.size() < name.size() + 1 ||
                wrapper_args.compare(wrapper_args.size() - name.size() - 1, name.size() + 1, name + '\0') != 0) continue;
            NativeApplication::Pin wrapper_pin(static_cast<int>(syscall(SYS_pidfd_open, parent, 0)));
            if (wrapper_pin.fd < 0 || !live_pidfd(wrapper_pin.fd)) continue;
            const auto wrapper_start = process_start(parent);
            const auto namespace_id = proc_link(pid, "ns/pid");
            if (process_start(pid) != start || !live_pidfd(pin.fd) ||
                proc_identity(pid).parent != parent ||
                process_start(parent) != wrapper_start || !live_pidfd(wrapper_pin.fd)) continue;
            selected.data = data; selected.pid = pid; selected.namespace_pid = local_pid;
            selected.wrapper_pid = parent; selected.wrapper_start = wrapper_start;
            selected.start = start; selected.pid_namespace = namespace_id;
            selected.pin = std::move(pin); selected.wrapper_pin = std::move(wrapper_pin);
            ++matches;
        } catch (const std::exception&) { /* A disappearing or unrelated process is not a witness. */ }
    }
    if (matches != 1) throw std::runtime_error("Cannot uniquely pin owned namespace X witness");
    return selected;
}
std::string read_file(const std::filesystem::path& path) {
    std::ifstream in(path);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}
template<class Condition> void await_native(Condition condition, const char* detail) {
    const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(6);
    while (!condition()) {
        if (std::chrono::steady_clock::now() >= end) throw std::runtime_error(detail);
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}
void witness_sync(const NativeApplication& app, unsigned seq) {
    { std::ofstream out(app.data / "commands", std::ios::app); out << "sync " << seq << '\n'; }
    await_native([&] { return read_file(app.data / "probe.jsonl").find("\"event\":\"ack\",\"seq\":" + std::to_string(seq) + "}") != std::string::npos; },
        "Independent X witness did not acknowledge stage boundary");
}
void retain_application(const NativeApplication& app) {
    if (!live_pidfd(app.pin.fd) || !live_pidfd(app.wrapper_pin.fd) ||
        process_start(app.pid) != app.start || process_start(app.wrapper_pid) != app.wrapper_start ||
        proc_link(app.pid, "ns/pid") != app.pid_namespace ||
        proc_identity(app.pid).local_pid != app.namespace_pid ||
        proc_identity(app.pid).parent != app.wrapper_pid)
        throw std::runtime_error("Confined application witness identity changed");
}
void host_actor_native_checks(spatial::supervisor::SessionCoordinator& session,
    const spatial::workspace::v1::ControlOwner& a,
    const spatial::workspace::v1::ControlOwner& b,
    const spatial::workspace::v1::ControlOwner& denied) {
    using namespace spatial;
    const auto check = [](bool value, const char* detail) { if (!value) throw std::runtime_error(detail); };
    identity::v1::SignedCredential credential;
    check(google::protobuf::TextFormat::ParseFromString(
        identity::tls_detail::load_config(native_setting("SPATIAL_HOST_ACTOR_CREDENTIAL")), &credential), "Bad fixture credential");
    const std::filesystem::path key(native_setting("SPATIAL_HOST_ACTOR_KEY"));
    core::v1::Request identities; identities.set_protocol_version(1); identities.mutable_runtime_identities();
    const auto ids_a = session.world_call(a, identities);
    const auto ids_b = session.world_call(b, identities);
    const auto ids_denied = session.world_call(denied, identities);
    check(ids_a.has_identities() && ids_b.has_identities() && ids_denied.has_identities(), "Missing runtime identities");
    const auto a_before = session.inspect_presentation(a);
    const auto b_before = session.inspect_presentation(b);
    const auto d_before = session.inspect_presentation(denied);
    const auto a_launch = session.world_launch(a).SerializeAsString();
    const auto b_launch = session.world_launch(b).SerializeAsString();
    const auto d_launch = session.world_launch(denied).SerializeAsString();
    const auto broker_launch = session.broker_launch().SerializeAsString();
    const auto server_pid = std::stoull(native_setting("SPATIAL_HOST_ACTOR_SERVER_PID"));
    const std::vector<std::uint64_t> pids{server_pid, ids_a.identities().spatialwm_pid(), ids_a.identities().workspaced_pid(),
        ids_a.identities().worldd_pid(), ids_b.identities().worldd_pid(), ids_denied.identities().worldd_pid()};
    std::vector<std::string> starts; for (auto pid : pids) starts.push_back(process_start(pid));
    unsigned checkpoint = 0;
    const auto unchanged = [&] {
        ++checkpoint;
        check(session.world_call(a, identities).identities().SerializeAsString() == ids_a.identities().SerializeAsString() &&
              session.world_call(b, identities).identities().SerializeAsString() == ids_b.identities().SerializeAsString() &&
              session.world_call(denied, identities).identities().SerializeAsString() == ids_denied.identities().SerializeAsString(), "Runtime PID changed");
        check(session.world_launch(a).SerializeAsString() == a_launch && session.world_launch(b).SerializeAsString() == b_launch &&
              session.world_launch(denied).SerializeAsString() == d_launch && session.broker_launch().SerializeAsString() == broker_launch,
              "Retained launch identity changed");
        const auto pa = session.inspect_presentation(a), pb = session.inspect_presentation(b), pd = session.inspect_presentation(denied);
        check(pa.binding().SerializeAsString() == a_before.binding().SerializeAsString() &&
              pb.binding().SerializeAsString() == b_before.binding().SerializeAsString() &&
              pd.binding().SerializeAsString() == d_before.binding().SerializeAsString() &&
              pa.stage() != render::v1::RETIRED && pb.stage() != render::v1::RETIRED && pd.stage() != render::v1::RETIRED,
              "World detached or retained binding changed");
        std::cerr << "native-checkpoint=" << checkpoint << " selected=" << (pa.selected() ? pa.binding().world_id() : pb.binding().world_id())
                  << " A-binding=" << pa.binding().ShortDebugString() << " B-binding=" << pb.binding().ShortDebugString() << '\n';
        for (std::size_t i = 0; i < pids.size(); ++i) {
            check(process_start(pids[i]) == starts[i], "Native process start identity changed");
            std::cerr << "native-checkpoint=" << checkpoint << " pid=" << pids[i] << " start=" << starts[i] << '\n';
        }
    };
    unchanged();
    const auto channel = [&](const auto& owner, unsigned index, const std::string& world) {
        return std::make_unique<ActorSessionClient>(session.take_actor_channel(owner, index), credential, key, world);
    };
    const auto aw = a_before.binding().world_id(), bw = b_before.binding().world_id(), dw = d_before.binding().world_id();
    auto initial = channel(a, 0, aw); initial->authenticate();
    supervisor::HostActorController controller(session, aw, std::move(initial));
    controller.register_world(aw, a, [&] { return channel(a, 1, aw); });
    controller.register_world(bw, b, [&] { return channel(b, 0, bw); });
    controller.register_world(dw, denied, [&] { return channel(denied, 0, dw); });
    std::uint64_t management_seq = 100;
    const auto management = [&](const auto& owner, core::v1::Request request) {
        request.set_protocol_version(1); request.set_request_id(++management_seq);
        auto reply = session.world_call(owner, request);
        check(!reply.has_error(), "Real world management operation failed");
        return reply;
    };
    const auto action = [&](std::uint64_t generation, core::v1::ActorActionRequest request) {
        auto reply = controller.action(generation, request);
        check(reply.has_action() && reply.action().accepted(), "Authorized actor action failed");
        return reply;
    };
    const auto actor_key = [&](std::uint64_t generation, std::uint64_t seq, const char* name, bool pressed) {
        core::v1::ActorActionRequest request; request.set_seq(seq);
        request.mutable_key()->set_keysym(name); request.mutable_key()->set_pressed(pressed);
        return action(generation, request);
    };
    const auto actor_button = [&](std::uint64_t generation, std::uint64_t seq, bool pressed) {
        core::v1::ActorActionRequest request; request.set_seq(seq);
        request.mutable_pointer()->set_button(1); request.mutable_pointer()->set_pressed(pressed);
        return action(generation, request);
    };
    const auto aim_at_app = [&](std::uint64_t generation, std::uint64_t seq) {
        core::v1::ActorActionRequest request; request.set_seq(seq);
        request.mutable_aim()->mutable_direction()->set_z(-1);
        return action(generation, request);
    };
    const auto launch_application = [&](const auto& owner, std::uint64_t generation,
                                        const std::string& name, std::uint64_t seq) {
        core::v1::Request available;
        available.mutable_workspace_available()->set_id("workspace-one");
        available.mutable_workspace_available()->set_available(true);
        management(owner, available);
        core::v1::ActorActionRequest select; select.set_seq(seq++);
        select.mutable_workspace_select()->set_id("workspace-one"); action(generation, select);
        core::v1::ActorActionRequest launch; launch.set_seq(seq++);
        auto* process = launch.mutable_process_launch();
        process->set_operation_id("held-input-" + name); process->set_profile("development");
        process->add_argv("/usr/bin/python3"); process->add_argv("-c");
        process->add_argv(application_code()); process->add_argv(name);
        const auto result = action(generation, launch);
        check(result.action().has_process() && !result.action().process().process_id().empty(),
              "Confined application launch lacked process identity");
        NativeApplication app;
        app.data = std::filesystem::path(native_setting("SPATIAL_HOST_ACTOR_EXECUTION")) /
            "applications" / result.action().process().process_id() / "data";
        await_native([&] { return read_file(app.data / "probe.jsonl").find("\"event\":\"ready\"") != std::string::npos; },
            "Confined native X witness did not start");
        const auto events = read_file(app.data / "probe.jsonl");
        const auto at = events.find("\"pid\":");
        check(at != std::string::npos, "Independent application PID missing");
        app = pin_witness(app.data, name, std::stoull(events.substr(at + 6)), ids_a.identities().workspaced_pid());
        std::string surface_id;
        await_native([&] {
            core::v1::Request find; find.mutable_surface_find()->set_probe_id(name);
            find.set_protocol_version(1); find.set_request_id(++management_seq);
            const auto found = session.world_call(owner, find);
            if (found.has_surface() && found.surface().has_surface()) surface_id = found.surface().surface().id();
            return !surface_id.empty();
        }, "Confined native application surface missing");
        core::v1::Request info; info.mutable_world_info(); const auto room = management(owner, info).world_info().entry_room();
        core::v1::Request place; auto* surface = place.mutable_surface_place();
        surface->set_id(surface_id); surface->set_room(room);
        surface->mutable_pose()->mutable_position()->set_z(-2);
        surface->mutable_pose()->mutable_orientation()->set_w(1);
        surface->mutable_size()->set_x(1); surface->mutable_size()->set_y(1);
        management(owner, place);
        retain_application(app);
        return app;
    };
    const auto source_app = launch_application(a, 0, "host-held-A", 1);
    aim_at_app(0, 3);
    actor_button(0, 4, true); actor_key(0, 5, "Shift_L", true);
    witness_sync(source_app, 1);
    check(read_file(source_app.data / "probe.jsonl").find("\"event\":\"key\",\"pressed\":true") != std::string::npos &&
          read_file(source_app.data / "probe.jsonl").find("\"event\":\"button\",\"pressed\":true") != std::string::npos,
          "Source X witness did not receive held input");
    const auto state_a = controller.inspect(0).state();
    check(state_a.held_keys_size() > 0 && state_a.held_buttons_size() > 0 && state_a.has_focus(),
          "Source actor did not hold native focus, key and button");
    identity::v1::HostActorTransitionRequest request;
    request.set_protocol_version(1); request.set_transition_id(std::string(32, 'd'));
    request.set_departure_sequence(10); request.set_destination_world_id(dw);
    const auto denial = controller.transition(request); write_frame(std::cout, denial);
    check(denial.actor_transition().phase() == identity::v1::PREPARATION_REJECTED &&
          denial.actor_transition().has_server_error() && denial.actor_transition().server_error().code() == core::v1::FORBIDDEN &&
          denial.actor_control_available() && denial.host_selection() == identity::v1::HOST_SELECTION_NOT_ATTEMPTED,
          "Native admission denial did not preserve source");
    check(controller.inspect(1).state().SerializeAsString() == state_a.SerializeAsString() && session.inspect_presentation(a).selected() &&
          !session.inspect_presentation(b).selected(), "Denial disturbed source or foreground");
    actor_key(1, 6, "a", true); actor_key(1, 7, "a", false);
    witness_sync(source_app, 2);
    const auto still_held = controller.inspect(1).state();
    check(still_held.held_keys_size() > 0 && still_held.held_buttons_size() > 0 && still_held.has_focus() &&
          session.inspect_world(a).reservations_size() == 1,
          "Private denial lost source held input or application control");
    retain_application(source_app); unchanged();
    request.set_transition_id(std::string(32, 'b')); request.set_destination_world_id(bw); request.set_departure_sequence(11);
    const auto ab = controller.transition(request); write_frame(std::cout, ab);
    check(ab.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED && ab.actor_transition().destination_activation_confirmed() &&
          controller.current_world() == bw && session.inspect_presentation(b).selected() && !session.inspect_presentation(a).selected(),
          "Native A-B owner selection failed");
    witness_sync(source_app, 3);
    const auto source_log = read_file(source_app.data / "probe.jsonl");
    check(source_log.find("\"event\":\"key\",\"pressed\":false") != std::string::npos &&
          source_log.find("\"event\":\"button\",\"pressed\":false") != std::string::npos,
          "Source X witness lacked release events at A-B");
    check(session.inspect_world(a).reservations_size() == 0, "Source application control lease survived A-B");
    retain_application(source_app);
    check(controller.inspect(ab.operation_generation()).state().workspace().empty(), "Source workspace carried into B");
    const auto destination_app = launch_application(b, ab.operation_generation(), "host-held-B", 1);
    aim_at_app(ab.operation_generation(), 3); actor_button(ab.operation_generation(), 4, true);
    witness_sync(destination_app, 1);
    const auto state_b = controller.inspect(ab.operation_generation()).state();
    witness_sync(source_app, 4); witness_sync(destination_app, 2);
    core::v1::ActorActionRequest delayed; delayed.set_seq(90);
    delayed.mutable_key()->set_keysym("a"); delayed.mutable_key()->set_pressed(true);
    bool stale_rejected = false;
    try { (void)controller.action(ab.operation_generation() - 1, delayed); }
    catch (const std::invalid_argument& error) {
        stale_rejected = std::string(error.what()) == "Unavailable or stale host actor context";
    }
    check(stale_rejected, "Valid delayed key did not receive the specific stale-generation rejection");
    witness_sync(source_app, 5); witness_sync(destination_app, 3);
    const auto state_b_after_stale = controller.inspect(ab.operation_generation()).state();
    check(state_b_after_stale.SerializeAsString() == state_b.SerializeAsString() &&
          controller.current_world() == bw && session.inspect_presentation(b).selected(),
          "Stale action changed destination state or selection");
    action(ab.operation_generation(), delayed);
    actor_key(ab.operation_generation(), 91, "a", false);
    actor_button(ab.operation_generation(), 92, false);
    witness_sync(destination_app, 4);
    retain_application(source_app); retain_application(destination_app);
    unchanged();
    request.set_transition_id(std::string(32, 'a')); request.set_destination_world_id(aw); request.set_departure_sequence(100);
    const auto ba = controller.transition(request); write_frame(std::cout, ba);
    witness_sync(destination_app, 5); witness_sync(source_app, 6);
    const auto resumed = controller.inspect(3).state();
    check(ba.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED && controller.current_world() == aw &&
          session.inspect_presentation(a).selected() && !session.inspect_presentation(b).selected(), "Native B-A owner selection failed");
    check(resumed.id() == state_a.id() && resumed.room() == state_a.room() &&
          resumed.pose().SerializeAsString() == state_a.pose().SerializeAsString() && resumed.workspace() == state_a.workspace(),
          "A context did not resume"); unchanged();
    check(resumed.held_keys_size() == 0 && resumed.held_buttons_size() == 0 && !resumed.has_focus() &&
          session.inspect_world(a).reservations_size() == 0,
          "A resumed old held input, focus, capture, or application lease");
    aim_at_app(ba.operation_generation(), 101);
    actor_button(ba.operation_generation(), 102, true);
    actor_key(ba.operation_generation(), 103, "a", true);
    actor_key(ba.operation_generation(), 104, "a", false);
    actor_button(ba.operation_generation(), 105, false);
    witness_sync(source_app, 7);
    retain_application(source_app); retain_application(destination_app);
    for (std::size_t i = 0; i < pids.size(); ++i)
        std::cerr << "retained-native-identity pid=" << pids[i] << " start=" << starts[i] << '\n';
    std::cerr << "PASS native-host-controller-held-input-A-B-A-stale-generation-private-denial\n";
}
}
