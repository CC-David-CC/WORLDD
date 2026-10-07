#include "world/server.hpp"
#include "world/surfaces.hpp"
#include "platform/render_worker.hpp"
#include <sys/stat.h>
#include <iostream>
#include <string_view>

int main(int argc, char** argv) {
    std::filesystem::path state, config;
    std::string display;
    bool fixture_clock = false;
    try {
        for (int i = 1; i < argc; ++i) {
            const std::string_view argument(argv[i]);
            if (argument == "--fixture-clock" && !fixture_clock) fixture_clock = true;
            else if (argument == "--state-dir" && state.empty() && i + 1 < argc) state = argv[++i];
            else if (argument == "--config" && config.empty() && i + 1 < argc) config = argv[++i];
            else if (argument == "--display" && display.empty() && i + 1 < argc) display = argv[++i];
            else throw std::invalid_argument("Unknown or incomplete worldd argument");
        }
        if (!state.is_absolute()) throw std::invalid_argument("State directory must be absolute");
        if (config.empty()) config = std::filesystem::read_symlink("/proc/self/exe").parent_path() / "runtime.textproto";
        // Inherited pipes are trusted management channels. No actor listener.
        const auto settings = spatial::load_runtime_config(config);
        umask(0077);
        spatial::WorldStore world(state);
        spatial::Surfaces surfaces(world);
        spatial::Actors actors(world, settings, fixture_clock, &surfaces);
        std::unique_ptr<spatial::RenderWorker> renderer;
        if (!display.empty()) renderer = std::make_unique<spatial::RenderWorker>(
            std::filesystem::read_symlink("/proc/self/exe").parent_path() / "spatialwm", state, display);
        spatial::serve(world, actors, renderer.get(), &surfaces);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "worldd: " << error.what() << '\n'; return 2;
    }
}
