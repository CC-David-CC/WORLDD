#include "world/server.hpp"
#include "world/surfaces.hpp"
#include "platform/render_worker.hpp"
#include "platform/broker.hpp"
#include <charconv>
#include <sys/stat.h>
#include <iostream>
#include <string_view>

int main(int argc, char** argv) {
    std::filesystem::path state, config;
    std::string display;
    bool fixture_clock = false, fixture_faults = false, migration = false;
    int broker_fd = -1, broker_pid = -1;
    try {
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--fixture-clock" && !fixture_clock) fixture_clock = true;
            else if (argument == "--fixture-faults" && !fixture_faults) fixture_faults = true;
            else if (argument == "--migrate-v1" && !migration) migration = true;
            else if (argument == "--state-dir" && state.empty() && i + 1 < argc) state = argv[++i];
            else if (argument == "--config" && config.empty() && i + 1 < argc) config = argv[++i];
            else if (argument == "--display" && display.empty() && i + 1 < argc) display = argv[++i];
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
        if (migration) {
            if (fixture_clock || fixture_faults || !config.empty() || !display.empty() || broker_fd != -1 || broker_pid != -1)
                throw std::invalid_argument("World migration only accepts --state-dir and --migrate-v1");
            umask(0077); spatial::WorldStore world(state, true);
            std::cerr << "World context schema migration complete\n"; return 0;
        }
        if ((broker_fd == -1) != (broker_pid == -1)) throw std::invalid_argument("Incomplete broker connection");
        if (config.empty()) config = std::filesystem::read_symlink("/proc/self/exe").parent_path() / "runtime.textproto";
        // Inherited pipes are trusted management channels. No actor listener.
        const auto settings = spatial::load_runtime_config(config);
        umask(0077);
        spatial::WorldStore world(state, false, fixture_faults);
        spatial::Surfaces surfaces(world);
        std::unique_ptr<spatial::BrokerClient> broker;
        if (broker_fd >= 0) broker = std::make_unique<spatial::BrokerClient>(broker_fd, broker_pid);
        std::unique_ptr<spatial::RenderWorker> renderer;
        if (!display.empty()) renderer = std::make_unique<spatial::RenderWorker>(
            std::filesystem::read_symlink("/proc/self/exe").parent_path() / "spatialwm", state, display);
        spatial::Actors actors(world, settings, fixture_clock, &surfaces, renderer.get(), broker.get());
        spatial::serve(world, actors, renderer.get(), &surfaces, broker.get());
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "worldd: " << error.what() << '\n'; return 2;
    }
}
