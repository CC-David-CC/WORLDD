#pragma once
#include "issuer.pb.h"
#include <chrono>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>

namespace spatial::identity {
// Created by the TLS server. This context is never decoded from a wire request.
// Identity proof alone does not activate execution or acknowledge cancellation.
struct IssuerConnection {
    std::string binding;
    std::optional<v1::AuthorityAuthenticationResult> authority;
};
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
    v1::BootstrapProfileState profile_read(const v1::BootstrapProfileRead& request);
    v1::ChallengeReply enrollment_begin(const v1::EnrollmentIntent& intent);
    v1::EnrollmentRecord enrollment_complete(const v1::EnrollmentComplete& request);
    v1::IdentityStatus credential_check(const v1::SignedCredential& credential);
    v1::ActorStatusBatch actor_status(const v1::ActorStatusRead& request);
    v1::ChallengeReply authentication_begin(const v1::AuthenticationBegin& request);
    v1::AuthenticationResult authentication_complete(const v1::AuthenticationComplete& request);
    v1::RevocationBatch revocations(const v1::RevocationRead& request);
    std::uint64_t security_revision();
    v1::ChallengeReply authority_authentication_begin(const v1::AuthorityAuthenticationBegin& request,
                                                     IssuerConnection& connection);
    v1::AuthorityAuthenticationResult authority_authentication_complete(const v1::AuthorityAuthenticationComplete& request,
                                                                      IssuerConnection& connection);
    v1::ChallengeReply authority_launch_begin(const v1::AuthorityLaunchBegin& request, IssuerConnection& connection);
    v1::AuthorityLaunchAuthorization authority_launch_complete(const v1::AuthorityLaunchComplete& request, IssuerConnection& connection);
    v1::ChallengeReply authority_fence_begin(const v1::AuthorityFenceBegin& request, IssuerConnection& connection);
    v1::AuthorityFenceState authority_fence_complete(const v1::AuthorityFenceComplete& request, IssuerConnection& connection);
    v1::AuthorityActivationState authority_activate(const v1::AuthorityActivate& request, IssuerConnection& connection);
    v1::AuthorityActivationState authority_applied(const v1::AuthorityApplied& request, IssuerConnection& connection);
    v1::AuthorityActivationState authority_release(const v1::AuthorityRelease& request, IssuerConnection& connection);
    v1::AuthorityActivationState authority_state(IssuerConnection& connection);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial::identity
