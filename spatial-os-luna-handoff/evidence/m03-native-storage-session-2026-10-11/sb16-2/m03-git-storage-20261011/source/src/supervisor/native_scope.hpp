#pragma once
#include "native_scope.pb.h"
#include <memory>

namespace spatial::supervisor {
// Owns one native authority grant on one retained supervisor connection.
// A stopped receipt proves native revocation, not process or issuer cleanup.
class NativeScope final {
public:
    // Borrows the sealed descriptor. The caller retains ownership on success and failure.
    explicit NativeScope(int sealed_config_fd);
    ~NativeScope();
    NativeScope(const NativeScope&) = delete;
    NativeScope& operator=(const NativeScope&) = delete;
    v1::NativeScopeReady ready() const;
    v1::NativeScopeStopped stop();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
