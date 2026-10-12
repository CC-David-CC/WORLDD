#pragma once
#include "supervisor/host_actor_controller.hpp"
#include <fstream>
#include <sstream>
#include <cstdlib>
#include <signal.h>
#include <vector>

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
    const std::vector<std::uint64_t> pids{server_pid, ids_a.identities().spatialwm_pid(),
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
    const auto state_a = controller.inspect(0).state();
    identity::v1::HostActorTransitionRequest request;
    request.set_protocol_version(1); request.set_transition_id(std::string(32, 'd'));
    request.set_departure_sequence(10); request.set_destination_world_id(dw);
    const auto denial = controller.transition(request); write_frame(std::cout, denial);
    check(denial.actor_transition().phase() == identity::v1::PREPARATION_REJECTED &&
          denial.actor_transition().has_server_error() && denial.actor_transition().server_error().code() == core::v1::FORBIDDEN &&
          denial.actor_control_available() && denial.host_selection() == identity::v1::HOST_SELECTION_NOT_ATTEMPTED,
          "Native admission denial did not preserve source");
    check(controller.inspect(1).state().id() == state_a.id() && session.inspect_presentation(a).selected() &&
          !session.inspect_presentation(b).selected(), "Denial disturbed source or foreground");
    core::v1::ActorActionRequest stop; stop.set_seq(1); stop.mutable_stop();
    check(controller.action(1, stop).has_action(), "Source action unusable after denial"); unchanged();
    request.set_transition_id(std::string(32, 'b')); request.set_destination_world_id(bw); request.set_departure_sequence(11);
    const auto ab = controller.transition(request); write_frame(std::cout, ab);
    check(ab.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED && ab.actor_transition().destination_activation_confirmed() &&
          controller.current_world() == bw && session.inspect_presentation(b).selected() && !session.inspect_presentation(a).selected(),
          "Native A-B owner selection failed");
    check(controller.inspect(2).state().workspace().empty(), "Source workspace carried into B"); unchanged();
    request.set_transition_id(std::string(32, 'a')); request.set_destination_world_id(aw); request.set_departure_sequence(12);
    const auto ba = controller.transition(request); write_frame(std::cout, ba);
    const auto resumed = controller.inspect(3).state();
    check(ba.host_selection() == identity::v1::HOST_SELECTION_CONFIRMED && controller.current_world() == aw &&
          session.inspect_presentation(a).selected() && !session.inspect_presentation(b).selected(), "Native B-A owner selection failed");
    check(resumed.id() == state_a.id() && resumed.room() == state_a.room() &&
          resumed.pose().SerializeAsString() == state_a.pose().SerializeAsString() && resumed.workspace() == state_a.workspace(),
          "A context did not resume"); unchanged();
    for (std::size_t i = 0; i < pids.size(); ++i)
        std::cerr << "retained-native-identity pid=" << pids[i] << " start=" << starts[i] << '\n';
    std::cerr << "PASS native-host-controller-A-B-A-denial-retained-owners-and-processes\n";
}
}
