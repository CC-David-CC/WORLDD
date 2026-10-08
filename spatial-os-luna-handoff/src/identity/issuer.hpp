#pragma once
#include "issuer.pb.h"
#include <chrono>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>

namespace spatial::identity {
class Issuer {
public:
    // Initialization is an explicit local owner operation, never a wire request.
    Issuer(const std::filesystem::path& state, const std::optional<std::string>& initial_owner = {},
           std::chrono::milliseconds challenge_lifetime = std::chrono::milliseconds(30000), bool migrate = false);
    ~Issuer();
    Issuer(const Issuer&) = delete;
    Issuer& operator=(const Issuer&) = delete;
    v1::IssuerInfo info();
    v1::ChallengeReply challenge(const v1::IssuerOperation& operation);
    v1::AdminResult execute(const v1::IssuerExecute& request);
    v1::ChallengeReply enrollment_begin(const v1::EnrollmentIntent& intent);
    v1::EnrollmentRecord enrollment_complete(const v1::EnrollmentComplete& request);
    v1::IdentityStatus credential_check(const v1::SignedCredential& credential);
    v1::ActorStatusBatch actor_status(const v1::ActorStatusRead& request);
    v1::ChallengeReply authentication_begin(const v1::AuthenticationBegin& request);
    v1::AuthenticationResult authentication_complete(const v1::AuthenticationComplete& request);
    v1::RevocationBatch revocations(const v1::RevocationRead& request);
    std::uint64_t security_revision();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial::identity
