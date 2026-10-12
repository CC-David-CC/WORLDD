#pragma once
#include <memory>

namespace spatial {
// A private, non-default VFS for an explicitly enabled world fixture.
// It delegates real I/O and fails one xWrite. Keep it alive until all handles close.
class StorageVfs {
public:
    StorageVfs();
    ~StorageVfs();
    StorageVfs(const StorageVfs&) = delete;
    StorageVfs& operator=(const StorageVfs&) = delete;
    const char* name() const;
    bool armed() const;
    void arm();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial
