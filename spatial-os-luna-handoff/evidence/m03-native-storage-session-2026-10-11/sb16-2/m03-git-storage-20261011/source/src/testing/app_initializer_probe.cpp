#include "workspace/app_initializer.hpp"
#include <fcntl.h>
#include <unistd.h>
#include <string_view>

namespace {
__attribute__((noinline)) void leak_witness() {
    auto* bytes = new char[73]; bytes[0] = 'x';
    asm volatile("" : : "r"(bytes) : "memory");
}
}
// Test only. Share the production body. Real confinement has independent witnesses.
int main(int argc, char** argv) {
    if (argc < 2 || argc > 3) return 3;
    if (argc == 3) {
        if (std::string_view(argv[2]) != "--leak-witness") return 3;
        leak_witness();
    }
    const int state = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    const auto result = spatial::supervise_application(state);
    if (state >= 0) close(state);
    return result;
}
