#include "supervisor/native_scope.hpp"
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <unistd.h>

// Independent descriptor witness. Native effects use the frozen production object.
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::runtime_error("Expected sealed configuration descriptor");
        const int config = std::stoi(argv[1]);
        constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
        const int before = fcntl(config, F_GET_SEALS);
        if (before < 0 || (before & seals) != seals)
            throw std::runtime_error("Witness input is not sealed");
        auto scope = std::make_unique<spatial::supervisor::NativeScope>(config);
        const int after = fcntl(config, F_GET_SEALS);
        if (after < 0 || (after & seals) != seals) {
            scope->stop(); scope.reset();
            std::cerr << "FAIL: NativeScope consumed or replaced the caller-owned configuration descriptor\n";
            return 42;
        }
        // Mirror AuthorityLaunch's local RAII owner closing its configuration.
        close(config);
        const int unrelated = open("/dev/null", O_RDONLY | O_CLOEXEC);
        if (unrelated < 0) throw std::runtime_error("Cannot open unrelated descriptor witness");
        scope->stop(); scope.reset();
        if (fcntl(unrelated, F_GETFD) < 0) {
            std::cerr << "FAIL: NativeScope closed an unrelated reused descriptor\n";
            return 43;
        }
        close(unrelated);
        std::cout << "PASS: caller configuration ownership and unrelated descriptor preserved\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Witness could not complete: " << error.what() << '\n';
        return 2;
    }
}
