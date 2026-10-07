#pragma once
#include "issuer.pb.h"
#include <filesystem>
#include <functional>
#include <memory>

namespace spatial::identity {
// Single-threaded checked transport. The calling process must ignore SIGPIPE.
// A failure never reconnects or replays. Construct a new client only after reconciliation.
class IssuerClient {
public:
    explicit IssuerClient(const std::filesystem::path& config, std::function<void()> tick = {});
    ~IssuerClient();
    IssuerClient(const IssuerClient&) = delete;
    IssuerClient& operator=(const IssuerClient&) = delete;
    v1::IssuerReply call(v1::IssuerRequest request);
    void set_tick(std::function<void()> tick);
    bool failed() const noexcept;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
