#include "authority/store.hpp"
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <sys/stat.h>

// Explicit local maintenance. This is not an actor authority endpoint.
int main(int argc, char** argv) {
    try {
        umask(0077);
        if (argc != 7 || std::string_view(argv[1]) != "--state-dir" ||
            std::string_view(argv[3]) != "--domain-kind" || std::string_view(argv[5]) != "--domain-id")
            throw std::invalid_argument("Usage: spatial-authority-migrate --state-dir ABSOLUTE_DIRECTORY --domain-kind world|workspace --domain-id ID");
        spatial::authority::v1::Domain domain;
        const std::string_view kind(argv[4]);
        if (kind == "world") domain.set_kind(spatial::authority::v1::WORLD);
        else if (kind == "workspace") domain.set_kind(spatial::authority::v1::WORKSPACE);
        else throw std::invalid_argument("Expected world or workspace domain kind");
        domain.set_id(argv[6]);
        spatial::authority::Store store(argv[2], domain, false, true);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "spatial-authority-migrate: " << error.what() << '\n'; return 2;
    }
}
