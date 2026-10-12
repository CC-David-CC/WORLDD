#pragma once
#include <openssl/ssl.h>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace spatial::identity::tls_detail {
inline void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
struct Fd {
    int value;
    explicit Fd(int fd = -1) : value(fd) {}
    ~Fd() { reset(); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    void reset(int fd = -1) { if (value >= 0) close(value); value = fd; }
};
using Context = std::unique_ptr<SSL_CTX, decltype(&SSL_CTX_free)>;
std::string load_config(const std::filesystem::path& path);
void nonblocking(int fd);
Context make_context(bool server, const std::filesystem::path& certificate,
                     const std::filesystem::path& private_key, const std::filesystem::path& trust);
}
