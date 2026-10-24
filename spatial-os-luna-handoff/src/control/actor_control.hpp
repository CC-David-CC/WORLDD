#pragma once

#include "world/actor_session_client.hpp"
#include <functional>
#include <mutex>

namespace spatial::control {

// Construct only from a checked, authenticated session. It is never event input.
struct ActorContext {
    std::string world;
    std::string actor;
    std::uint64_t generation = 0;
};

// One instance owns sequencing for one authenticated actor session.
// Adapters propose existing semantic actions. They cannot supply tokens or sequences.
class ActorControl final {
public:
    using Action = std::function<identity::v1::WorldSessionReply(const core::v1::ActorActionRequest&)>;
    using Inspect = std::function<identity::v1::WorldSessionReply()>;
    // Must close the exact owned actor channel. It must not reconnect or replay.
    using Close = std::function<void()>;

    ActorControl(ActorContext context, Action action, Inspect inspect, Close close);
    ~ActorControl();
    ActorControl(const ActorControl&) = delete;
    ActorControl& operator=(const ActorControl&) = delete;

    core::v1::ActorActionReply propose(std::uint64_t generation,
                                      const core::v1::ActorActionRequest& proposal);
    core::v1::ActorStateReply inspect(std::uint64_t generation);
    void cancel(std::uint64_t generation);
    void close() noexcept;
    bool available() const;
    const ActorContext& context() const noexcept { return context_; }

private:
    void require_context(std::uint64_t generation) const;
    void fail_locked() noexcept;
    const ActorContext context_;
    Action action_;
    Inspect inspect_;
    Close close_;
    mutable std::mutex mutex_;
    std::uint64_t sequence_ = 0;
    bool failed_ = false;
};
}
