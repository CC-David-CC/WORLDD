#pragma once
#include <memory>
#include <sys/types.h>

namespace spatial::supervisor {
class AuthorityLaunch;
// Optional private coordinator endpoint. Owns the socket and one pending handle reply.
class ChildHandleServer final {
public:
    explicit ChildHandleServer(int owned_socket, pid_t parent);
    ~ChildHandleServer();
    ChildHandleServer(const ChildHandleServer&) = delete;
    ChildHandleServer& operator=(const ChildHandleServer&) = delete;
    void pump(const AuthorityLaunch& launch);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
