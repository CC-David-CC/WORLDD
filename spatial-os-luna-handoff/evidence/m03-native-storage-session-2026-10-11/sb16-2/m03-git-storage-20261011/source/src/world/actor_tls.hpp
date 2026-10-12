#pragma once
#include "identity/tls_support.hpp"
#include <chrono>
#include <filesystem>
#include <memory>
#include <string>

namespace spatial {
void prepare_actor_listener(int descriptor);
class ActorTlsPeer {
public:
    ActorTlsPeer(int descriptor, SSL_CTX* context, std::chrono::milliseconds timeout,
                 std::chrono::milliseconds idle);
    ~ActorTlsPeer();
    int handshake(); // -1 closed, 0 pending, 1 ready.
    int read(std::string& input);
    int write(const std::string& output, std::size_t& sent);
    bool expired(bool authenticated) const;
    bool ready() const;
    bool read_needs_write() const;
    std::chrono::milliseconds timeout() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
class ActorTlsListener {
public:
    ActorTlsListener(int descriptor, const std::filesystem::path& configuration);
    int accept();
    std::unique_ptr<ActorTlsPeer> peer(int descriptor);
    std::size_t limit() const { return limit_; }
private:
    identity::tls_detail::Fd descriptor_;
    identity::tls_detail::Context context_{nullptr, SSL_CTX_free};
    std::chrono::milliseconds timeout_{5000}, idle_{60000};
    std::size_t limit_ = 32;
};
}
