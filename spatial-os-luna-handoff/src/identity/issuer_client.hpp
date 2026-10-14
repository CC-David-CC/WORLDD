#pragma once
#include "issuer.pb.h"
#include <chrono>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>

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
    std::chrono::milliseconds timeout() const noexcept;
    // Fixed-category diagnostics contain timing and transfer counters only.
    std::string failure_diagnostic() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
