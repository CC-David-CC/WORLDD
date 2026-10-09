#pragma once
#include "grants.pb.h"
#include <filesystem>
#include <functional>
#include <memory>
#include <vector>

namespace spatial::authority {
// Validate scope, rights, and delegation independently of the recipient.
// This checks syntax only. It does not authorize a grant.
void validate_permissions(const v1::GrantTerms&);
// Callers must supply authenticated identities and a current identity checker.
// This library does not authenticate caller-supplied protobuf actor references.
using IdentityCheck = std::function<bool(const v1::ActorRef&)>;
class Store {
public:
    Store(const std::filesystem::path& directory, const v1::Domain& domain, bool initialize, bool migrate = false);
    ~Store();
    Store(const Store&) = delete;
    Store& operator=(const Store&) = delete;
    // Only explicit trusted domain provisioning may call install_root.
    // Ordinary actors must use delegate, including domain administrators.
    v1::GrantRecord install_root(const v1::GrantTerms&, std::uint64_t expected_revision);
    std::vector<v1::GrantRecord> install_roots(const std::vector<v1::GrantTerms>&, std::uint64_t expected_revision);
    // Collect candidate-chain identities before external status I/O. This grants no authority.
    std::vector<v1::ActorRef> subjects(const v1::ActorRef& caller);
    v1::GrantPage list(const v1::ActorRef& caller, const std::string& after, std::uint32_t limit);
    v1::GrantRecord delegate(const v1::ActorRef& caller, const std::string& parent,
                            const v1::GrantTerms&, std::uint64_t expected_revision, const IdentityCheck&);
    v1::GrantRecord revoke(const v1::ActorRef& caller, const std::string& id,
                          std::uint64_t expected_revision, const IdentityCheck&);
    v1::Decision check(const v1::ActorRef& caller, const v1::Scope&, v1::Right, const IdentityCheck&);
    v1::GrantRecord get(const std::string& id);
    // Adapters authenticate the actor and fetch the exact issuer profile first.
    // These methods never accept a recipient supplied by an enrollment intent.
    v1::BootstrapBinding bind_bootstrap(const v1::ActorRef& caller, const v1::BootstrapSelector&,
        const std::string& parent, const v1::GrantTerms&, std::uint64_t expected_revision, const IdentityCheck&);
    v1::BootstrapBinding bootstrap_binding(const v1::BootstrapSelector&);
    std::vector<v1::ActorRef> bootstrap_subjects(const std::vector<std::string>& bindings, const v1::ActorRef& recipient);
    v1::GrantRecord materialize_bootstrap(const std::string& binding, const v1::ActorRef& recipient,
        const v1::BootstrapSelector&, const v1::GrantTerms& checked_template, const IdentityCheck&);
    v1::BootstrapBinding revoke_bootstrap(const v1::ActorRef& caller, const std::string& binding,
        std::uint64_t expected_revision, const IdentityCheck&);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial::authority
