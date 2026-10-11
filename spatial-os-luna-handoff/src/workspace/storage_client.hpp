#pragma once
#include "storage.pb.h"
#include <filesystem>
#include <functional>
#include <memory>

namespace spatial {
class StorageClient final {
public:
    // Consume the private descriptor. Verify the volume before broker filesystem effects.
    StorageClient(int descriptor, const std::filesystem::path& volume);
    ~StorageClient();
    void set_observer(std::function<void()> observer);
    workspace::v1::StorageObjectState call(workspace::v1::StorageOwnerRequest request,
                                         const std::function<void()>& tick = {});
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
