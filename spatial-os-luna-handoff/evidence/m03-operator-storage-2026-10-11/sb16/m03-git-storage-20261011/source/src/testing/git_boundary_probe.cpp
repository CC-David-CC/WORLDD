#include "workspace/git_command.hpp"
#include "git_probe_scope.hpp"
#include <iostream>

// Independent worker-boundary witness. Use the actual administrative Git helper.
int main(int argc, char** argv) {
    if ((argc != 2 && argc != 3) || !std::filesystem::path(argv[1]).is_absolute()) return 2;
    try {
        auto scope = git_probe_scope(argv[1]);
        const auto result = spatial::git_command(*scope, argv[1],
            {"hash-object", "--path=tracked.txt", "tracked.txt"}, {{argc == 3 ? argv[2] : argv[1], true}});
        std::cout << result.output;
        return result.status == 0 ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 2;
    }
}
