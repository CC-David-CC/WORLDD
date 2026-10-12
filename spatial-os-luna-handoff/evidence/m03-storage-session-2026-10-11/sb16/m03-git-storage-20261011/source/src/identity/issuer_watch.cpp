#include "identity/issuer_watch.hpp"
#include "identity/issuer_client.hpp"
#include <algorithm>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace spatial::identity {
struct IssuerWatcher::Impl {
    mutable std::mutex mutex;
    std::deque<v1::RevocationUpdate> updates;
    bool broken = false;
    // Destroy the worker before the state it can access.
    std::jthread worker;

    void fail_locked(spatial::core::v1::ErrorCode code, const std::string& message) {
        broken = true; updates.clear();
        auto& update = updates.emplace_back(); update.set_protocol_version(1);
        update.mutable_error()->set_code(code); update.mutable_error()->set_message(message);
    }
    bool publish(v1::RevocationBatch batch) {
        std::lock_guard lock(mutex);
        if (updates.size() >= 32) {
            fail_locked(spatial::core::v1::RESOURCE_EXHAUSTED, "Issuer revocation consumer fell behind"); return false;
        }
        auto& update = updates.emplace_back(); update.set_protocol_version(1);
        *update.mutable_batch() = std::move(batch); return true;
    }
    void run(std::stop_token stop, const std::filesystem::path& config, std::optional<std::uint64_t> after) {
        try {
            IssuerClient client(config, [stop] {
                if (stop.stop_requested()) throw std::runtime_error("Issuer watcher stopped");
            });
            const auto wait = std::min<std::int64_t>(1000, client.timeout().count()/2);
            bool first = true, backlog = false;
            while (!stop.stop_requested()) {
                v1::IssuerRequest request; request.set_protocol_version(1);
                auto* read = request.mutable_revocations();
                if (after) read->set_after_revision(*after);
                read->set_wait_ms(first || backlog ? 0 : static_cast<std::uint32_t>(wait));
                auto reply = client.call(std::move(request));
                if (reply.has_error()) {
                    std::lock_guard lock(mutex); fail_locked(reply.error().code(), reply.error().message()); return;
                }
                auto batch = std::move(*reply.mutable_revocations());
                after = batch.through_revision(); backlog = *after < batch.head_revision();
                if ((first || batch.events_size()) && !publish(std::move(batch))) return;
                first = false;
            }
        } catch (const std::exception&) {
            if (!stop.stop_requested()) {
                std::lock_guard lock(mutex);
                fail_locked(spatial::core::v1::UNAVAILABLE, "Issuer revocation feed failed");
            }
        }
    }
    Impl(const std::filesystem::path& config, std::optional<std::uint64_t> after) {
        if (after && *after > INT64_MAX) throw std::invalid_argument("Revocation cursor exceeds storage range");
        worker = std::jthread([this, config, after](std::stop_token stop) { run(stop, config, after); });
    }
};
IssuerWatcher::IssuerWatcher(const std::filesystem::path& config, std::optional<std::uint64_t> after)
    : impl_(std::make_unique<Impl>(config, after)) {}
IssuerWatcher::~IssuerWatcher() = default;
std::optional<v1::RevocationUpdate> IssuerWatcher::poll() {
    std::lock_guard lock(impl_->mutex);
    if (impl_->updates.empty()) return {};
    auto update = std::move(impl_->updates.front()); impl_->updates.pop_front(); return update;
}
bool IssuerWatcher::failed() const { std::lock_guard lock(impl_->mutex); return impl_->broken; }
}
