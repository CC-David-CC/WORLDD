#pragma once
#include "identity/issuer.hpp"
#include <filesystem>
#include <memory>

namespace spatial::identity {
// Validate configuration and the inherited listener before opening issuer state.
// The supervisor keeps stdin open. EOF stops this listener and all its peers.
class IssuerTlsServer {
public:
    IssuerTlsServer(int listener, const std::filesystem::path& config);
    ~IssuerTlsServer();
    IssuerTlsServer(const IssuerTlsServer&) = delete;
    IssuerTlsServer& operator=(const IssuerTlsServer&) = delete;
    void serve(Issuer& issuer);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
