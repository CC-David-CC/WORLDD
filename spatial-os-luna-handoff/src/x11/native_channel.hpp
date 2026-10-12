#pragma once
#include "broker.pb.h"
#include "security.pb.h"
#include <memory>
#include <string>
#include <utility>
namespace spatial {
// Private local X transport. Channel failure closes this owner, never the process.
class NativeChannel {
public:
    NativeChannel();
    ~NativeChannel();
    NativeChannel(const NativeChannel&) = delete;
    NativeChannel& operator=(const NativeChannel&) = delete;
    void connect_server(const workspace::v1::NativeLaunchConfig&, security::v1::Role);
    security::v1::Reply call(security::v1::Request);
    std::pair<std::string, std::string> request(unsigned major, std::string payload);
    void check();
    void reset();
    bool connected() const;
    int socket_fd() const;
    const std::string& number() const;
    const std::string& server_id() const;
    const std::string& credential_id() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
