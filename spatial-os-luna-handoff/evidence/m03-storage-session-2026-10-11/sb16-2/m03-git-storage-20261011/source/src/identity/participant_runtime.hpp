#pragma once
#include "issuer.pb.h"
#include <filesystem>
#include <memory>
#include <optional>
#include <vector>

namespace spatial::supervisor { class AuthorityLaunch; }
namespace spatial::identity {
// The authority thread consumes events, settles effects, then explicitly acknowledges.
// poll() and submit() never wait for network I/O. Failure is permanent.
// The calling process must ignore SIGPIPE before construction.
class ParticipantRuntime {
public:
    ParticipantRuntime(const std::filesystem::path& config,
                       const std::vector<authority::v1::Domain>& expected_domains);
    ~ParticipantRuntime();
    ParticipantRuntime(const ParticipantRuntime&) = delete;
    ParticipantRuntime& operator=(const ParticipantRuntime&) = delete;
    void submit(v1::ParticipantCommand command);
    std::optional<v1::ParticipantUpdate> poll();
    bool failed() const;
private:
    friend class spatial::supervisor::AuthorityLaunch;
    // Preserve the child's revision. The issuer resolves synchronization races.
    void submit_forwarded(v1::ParticipantCommand command);
    void submit_impl(v1::ParticipantCommand command, bool forwarded);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
