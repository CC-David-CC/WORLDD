#include "supervisor/native_scope.hpp"
#include "workspace/native_launch.hpp"
#include <cerrno>
#include <fcntl.h>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <unistd.h>

// Independent descriptor witness. Native effects use the frozen production object.
int main(int argc, char** argv) {
    try {
        if (argc != 2 && argc != 3) throw std::runtime_error("Expected configuration descriptor and optional rejection mode");
        const int config = std::stoi(argv[1]);
        constexpr int seals = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
        const int before = fcntl(config, F_GET_SEALS);
        if (argc == 3) {
            const std::string mode(argv[2]);
            if (before != 0 || (mode != "scope-reject" && mode != "launch-reject"))
                throw std::runtime_error("Expected unsealed descriptor and a known rejection mode");
            bool rejected = false;
            try {
                if (mode == "scope-reject") {
                    spatial::supervisor::NativeScope scope(config);
                } else {
                    spatial::NativeLaunch launch(config);
                }
            } catch (const std::invalid_argument& error) {
                rejected = std::string(error.what()) == "Native launch configuration must be fully sealed";
            }
            if (!rejected) throw std::runtime_error("Native configuration was not rejected for missing seals");
            const int after = fcntl(config, F_GET_SEALS);
            if (mode == "scope-reject") {
                if (after != before) {
                    std::cerr << "FAIL: NativeScope consumed the caller-owned rejected configuration descriptor\n";
                    return 42;
                }
                close(config);
                std::cout << "PASS: NativeScope rejection preserves caller configuration ownership\n";
            } else {
                if (after >= 0 || errno != EBADF) {
                    std::cerr << "FAIL: NativeLaunch rejection did not consume its configuration descriptor\n";
                    return 44;
                }
                std::cout << "PASS: NativeLaunch rejection retains its consuming interface\n";
            }
            return 0;
        }
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
