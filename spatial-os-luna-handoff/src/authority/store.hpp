#pragma once
#include "grants.pb.h"
#include <filesystem>
#include <functional>
#include <memory>

namespace spatial::authority {
// Callers must supply authenticated identities and a current identity checker.
// This library does not authenticate caller-supplied protobuf actor references.
using IdentityCheck = std::function<bool(const v1::ActorRef&)>;
class Store {
public:
    Store(const std::filesystem::path& directory, const v1::Domain& domain, bool initialize);
    ~Store();
    Store(const Store&) = delete;
    Store& operator=(const Store&) = delete;
    // Only explicit trusted domain provisioning may call install_root.
    // Ordinary actors must use delegate, including domain administrators.
    v1::GrantRecord install_root(const v1::GrantTerms&, std::uint64_t expected_revision);
    v1::GrantRecord delegate(const v1::ActorRef& caller, const std::string& parent,
                            const v1::GrantTerms&, std::uint64_t expected_revision, const IdentityCheck&);
    v1::GrantRecord revoke(const v1::ActorRef& caller, const std::string& id,
                          std::uint64_t expected_revision, const IdentityCheck&);
    v1::Decision check(const v1::ActorRef& caller, const v1::Scope&, v1::Right, const IdentityCheck&);
    v1::GrantRecord get(const std::string& id);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace spatial::authority
