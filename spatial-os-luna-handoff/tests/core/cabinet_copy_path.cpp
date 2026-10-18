#include "workspace/copy_worker.hpp"
#include <cassert>
#include <filesystem>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

int main() {
    char pattern[] = "/tmp/spatial-cabinet-copy-path-XXXXXX";
    char* root = mkdtemp(pattern);
    assert(root != nullptr);
    const std::filesystem::path base(root);
    const auto source_path = base / "source";
    const auto nested_path = source_path / "nested";
    assert(mkdir(source_path.c_str(), 0700) == 0);
    assert(mkdir(nested_path.c_str(), 0700) == 0);
    const int source = open(source_path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    const int parent = open(base.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    const int nested = open(nested_path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    assert(source >= 0 && parent >= 0 && nested >= 0);
    assert(!spatial::cabinet_copy_destination_inside_source(source, parent));
    assert(spatial::cabinet_copy_destination_inside_source(source, nested));
    close(nested);
    close(parent);
    close(source);
    std::filesystem::remove_all(base);
    return 0;
}
