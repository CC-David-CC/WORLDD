#include "world/server.hpp"
#include "world/authority.hpp"
#include "world/identity_sessions.hpp"
#include "world/actor_tls.hpp"
#include "world/surfaces.hpp"
#include "platform/render_worker.hpp"
#include "platform/compositor_config.hpp"
#include "platform/broker.hpp"
#include <charconv>
#include <algorithm>
#include <csignal>
#include <set>
#include <sys/stat.h>
#include <unistd.h>
#include <iostream>
#include <string_view>

int main(int argc, char** argv) {
    std::filesystem::path state, config, identity_config, authority_seed, actor_tls_config;
    std::vector<int> actor_channels;
    std::string display;
    bool fixture_clock = false, fixture_faults = false, migration = false;
    int broker_fd = -1, broker_pid = -1, actor_listener = -1, native_fd = -1;
    try {
        signal(SIGPIPE, SIG_IGN);
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--fixture-clock" && !fixture_clock) fixture_clock = true;
            else if (argument == "--fixture-faults" && !fixture_faults) fixture_faults = true;
            else if (argument == "--migrate-v1" && !migration) migration = true;
            else if (argument == "--state-dir" && state.empty() && i + 1 < argc) state = argv[++i];
            else if (argument == "--config" && config.empty() && i + 1 < argc) config = argv[++i];
            else if (argument == "--display" && display.empty() && i + 1 < argc) display = argv[++i];
            else if (argument == "--init-authority" && authority_seed.empty() && i + 1 < argc) authority_seed = argv[++i];
            else if (argument == "--identity-config" && identity_config.empty() && i + 1 < argc) identity_config = argv[++i];
            else if (argument == "--actor-tls-config" && actor_tls_config.empty() && i + 1 < argc) actor_tls_config = argv[++i];
            else if (argument == "--native-security-fd" && native_fd == -1 && i + 1 < argc) {
                const std::string_view number(argv[++i]);
                const auto parsed = std::from_chars(number.data(), number.data()+number.size(), native_fd);
                if (parsed.ec != std::errc{} || parsed.ptr != number.data()+number.size() || native_fd <= STDERR_FILENO)
                    throw std::invalid_argument("Native compositor configuration: invalid inherited descriptor");
            }
            else if (argument == "--actor-listen-fd" && actor_listener == -1 && i + 1 < argc) {
                const std::string_view number(argv[++i]);
                const auto parsed = std::from_chars(number.data(), number.data()+number.size(), actor_listener);
                if (parsed.ec != std::errc{} || parsed.ptr != number.data()+number.size() || actor_listener <= STDERR_FILENO)
                    throw std::invalid_argument("Invalid actor listener descriptor");
            }
            else if (argument == "--actor-fd" && i + 1 < argc) {
                int fd = -1; const std::string_view number(argv[++i]);
                const auto parsed = std::from_chars(number.data(), number.data()+number.size(), fd);
                if (parsed.ec != std::errc{} || parsed.ptr != number.data()+number.size() || fd <= STDERR_FILENO || actor_channels.size() >= 32)
                    throw std::invalid_argument("Invalid actor channel descriptor");
                actor_channels.push_back(fd);
            }
            else if ((argument == "--workspace-fd" || argument == "--workspace-pid") && i + 1 < argc) {
                int& value = argument == "--workspace-fd" ? broker_fd : broker_pid;
                if (value != -1) throw std::invalid_argument("Duplicate broker argument");
                const std::string_view number(argv[++i]);
                const auto result = std::from_chars(number.data(), number.data() + number.size(), value);
                if (result.ec != std::errc{} || result.ptr != number.data() + number.size() || value <= 1)
                    throw std::invalid_argument("Invalid broker argument");
            }
            else throw std::invalid_argument("Unknown or incomplete worldd argument");
        }
        if (!state.is_absolute()) throw std::invalid_argument("State directory must be absolute");
        if ((actor_listener == -1) != actor_tls_config.empty()) throw std::invalid_argument("Actor TLS listener and configuration are required together");
        if (identity_config.empty() != (actor_channels.empty() && actor_listener == -1))
            throw std::invalid_argument("World identity configuration and actor transport are required together");
        if (native_fd >= 0 && (native_fd == broker_fd || native_fd == actor_listener ||
                std::find(actor_channels.begin(), actor_channels.end(), native_fd) != actor_channels.end()))
            throw std::invalid_argument("Native compositor configuration: descriptor shares another channel");
        if (actor_listener >= 0) {
            if (actor_listener == broker_fd) throw std::invalid_argument("Actor listener cannot share the broker descriptor");
            spatial::prepare_actor_listener(actor_listener);
        }
        std::set<int> unique_channels;
        for (const auto fd : actor_channels) {
            if (!unique_channels.insert(fd).second || fd == broker_fd || fd == actor_listener) throw std::invalid_argument("Duplicate actor or broker channel");
            spatial::prepare_actor_channel(fd);
        }
        if (!authority_seed.empty()) {
            if (native_fd >= 0 || migration || fixture_clock || fixture_faults || !config.empty() || !display.empty() || !identity_config.empty() || broker_fd != -1 || broker_pid != -1)
                throw std::invalid_argument("Authority initialization only accepts --state-dir and --init-authority");
            umask(0077); spatial::WorldStore world(state);
            spatial::initialize_world_authority(authority_seed, world.info().world_id());
            std::cerr << "World authority initialization complete\n"; return 0;
        }
        if (migration) {
            if (native_fd >= 0 || fixture_clock || fixture_faults || !config.empty() || !display.empty() || !identity_config.empty() || broker_fd != -1 || broker_pid != -1)
                throw std::invalid_argument("World migration only accepts --state-dir and --migrate-v1");
            umask(0077); spatial::WorldStore world(state, true);
            std::cerr << "World context schema migration complete\n"; return 0;
        }
        if ((broker_fd == -1) != (broker_pid == -1)) throw std::invalid_argument("Incomplete broker connection");
        std::unique_ptr<spatial::CompositorConfig> native;
        if (native_fd >= 0) {
            native = std::make_unique<spatial::CompositorConfig>(native_fd);
            if (!display.empty() && display != native->display())
                throw std::invalid_argument("Native compositor configuration: conflicting display");
            display = native->display();
        }
        if (config.empty()) config = std::filesystem::read_symlink("/proc/self/exe").parent_path() / "runtime.textproto";
        // Management pipes and actor-only sockets have separate protobuf interfaces.
        const auto settings = spatial::load_runtime_config(config);
        umask(0077);
        spatial::WorldStore world(state, false, fixture_faults);
        spatial::Surfaces surfaces(world);
        std::unique_ptr<spatial::BrokerClient> broker;
        if (broker_fd >= 0) broker = std::make_unique<spatial::BrokerClient>(broker_fd, broker_pid);
        std::unique_ptr<spatial::RenderWorker> renderer;
        if (!display.empty()) renderer = std::make_unique<spatial::RenderWorker>(
            std::filesystem::read_symlink("/proc/self/exe").parent_path() / "spatialwm", state, display, native ? native->descriptor() : -1);
        if (native) native->consume();
        spatial::Actors actors(world, settings, fixture_clock, &surfaces, renderer.get(), broker.get());
        std::unique_ptr<spatial::IdentitySessions> sessions;
        if (!identity_config.empty()) sessions = std::make_unique<spatial::IdentitySessions>(identity_config, actor_channels, world, actors, broker.get(), actor_listener, actor_tls_config);
        spatial::serve(world, actors, renderer.get(), &surfaces, broker.get(), sessions.get());
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "worldd: " << error.what() << '\n'; return 2;
    }
}
