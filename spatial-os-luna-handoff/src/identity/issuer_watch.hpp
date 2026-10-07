#pragma once
#include "issuer.pb.h"
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>

namespace spatial::identity {
// The worker owns its TLS client. poll() never waits for network I/O.
// Failure is permanent. Consumers must cancel sessions that depend on this feed.
// The calling process must ignore SIGPIPE before construction.
class IssuerWatcher {
public:
    explicit IssuerWatcher(const std::filesystem::path& config, std::optional<std::uint64_t> after = {});
    ~IssuerWatcher();
    IssuerWatcher(const IssuerWatcher&) = delete;
    IssuerWatcher& operator=(const IssuerWatcher&) = delete;
    std::optional<v1::RevocationUpdate> poll();
    bool failed() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
