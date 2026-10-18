#include "workspace/broker_state_metadata.hpp"
#include <cerrno>
#include <cstdlib>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
void write_file(int root, const char* name, const std::string& contents, mode_t mode = 0600) {
    const int fd = openat(root, name, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) throw std::runtime_error("cannot create metadata test file");
    std::size_t written = 0;
    while (written < contents.size()) {
        const auto count = write(fd, contents.data() + written, contents.size() - written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { close(fd); throw std::runtime_error("cannot write metadata test file"); }
        written += static_cast<std::size_t>(count);
    }
    if (fchmod(fd, mode) != 0) { close(fd); throw std::runtime_error("cannot set metadata test mode"); }
    close(fd);
}
void expect_rejected(int root) {
    try { spatial::workspace::detail::validate_broker_state_metadata(root, getuid()); }
    catch (const std::invalid_argument&) { return; }
    throw std::runtime_error("unsafe broker metadata was accepted");
}
void remove_file(int root, const char* name) { if (unlinkat(root, name, 0) != 0) throw std::runtime_error("cannot remove metadata test file"); }
}

int main() {
    char path[] = "/tmp/spatial-storage-state-metadata.XXXXXX";
    if (!mkdtemp(path)) return 1;
    const int root = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (root < 0) return 2;
    int result = 0;
    try {
        using namespace spatial::workspace::detail;
        if (!is_broker_state_metadata_name(broker_instance_id_name) ||
            !is_broker_state_metadata_name(broker_instance_lock_name) ||
            is_broker_state_metadata_name("unrelated") || is_broker_state_metadata_name(".broker-instance-id.bak"))
            throw std::runtime_error("broker metadata allowlist is not exact");
        write_file(root, ".broker-instance-id", "0123456789abcdef0123456789abcdef");
        write_file(root, ".broker-instance.lock", "");
        int lock = openat(root, ".broker-instance.lock", O_RDWR | O_CLOEXEC);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB) != 0) throw std::runtime_error("cannot hold broker lock fixture");
        validate_broker_state_metadata(root, getuid());
        flock(lock, LOCK_UN); close(lock);
        expect_rejected(root);
        remove_file(root, ".broker-instance.lock");
        remove_file(root, ".broker-instance-id");

        write_file(root, ".broker-instance-id", "bad");
        write_file(root, ".broker-instance.lock", "");
        lock = openat(root, ".broker-instance.lock", O_RDWR | O_CLOEXEC);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB) != 0) throw std::runtime_error("cannot hold malformed fixture lock");
        expect_rejected(root);
        flock(lock, LOCK_UN); close(lock);
        remove_file(root, ".broker-instance.lock");
        remove_file(root, ".broker-instance-id");

        write_file(root, ".broker-instance-id", "0123456789abcdef0123456789abcdef", 0644);
        write_file(root, ".broker-instance.lock", "");
        lock = openat(root, ".broker-instance.lock", O_RDWR | O_CLOEXEC);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB) != 0) throw std::runtime_error("cannot hold mode fixture lock");
        expect_rejected(root);
        flock(lock, LOCK_UN); close(lock);
        remove_file(root, ".broker-instance.lock");
        remove_file(root, ".broker-instance-id");

        write_file(root, "identity-target", "0123456789abcdef0123456789abcdef");
        if (symlinkat("identity-target", root, ".broker-instance-id") != 0) throw std::runtime_error("cannot create identity symlink");
        write_file(root, ".broker-instance.lock", "");
        lock = openat(root, ".broker-instance.lock", O_RDWR | O_CLOEXEC);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB) != 0) throw std::runtime_error("cannot hold symlink fixture lock");
        expect_rejected(root);
        if (is_broker_state_metadata_name("identity-target")) throw std::runtime_error("extra volume entry passed allowlist");
        flock(lock, LOCK_UN); close(lock);
        remove_file(root, ".broker-instance.lock");
        remove_file(root, ".broker-instance-id");
        remove_file(root, "identity-target");

        write_file(root, ".broker-instance-id", "0123456789abcdef0123456789abcdef");
        expect_rejected(root);
        remove_file(root, ".broker-instance-id");
        if (rmdir(path) != 0) throw std::runtime_error("cannot remove metadata test directory");
        close(root);
    } catch (...) {
        unlinkat(root, ".broker-instance.lock", 0);
        unlinkat(root, ".broker-instance-id", 0);
        unlinkat(root, "identity-target", 0);
        close(root);
        rmdir(path);
        result = 3;
    }
    if (result != 0) return result;
    return 0;
}
