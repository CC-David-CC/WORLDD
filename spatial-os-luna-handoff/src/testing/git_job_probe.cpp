#include "workspace/git_command.hpp"
#include <iostream>
// Component fixture. Python independently observes and kills its real process tree.
int main(int argc, char** argv) {
    if (argc != 2 || !std::filesystem::path(argv[1]).is_absolute()) return 2;
    try {
        const auto result = spatial::git_command(argv[1], {"-c", "alias.waiter=!sh -c 'sleep 120 & wait'", "waiter"});
        return result.status == 0 ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 2;
    }
}
