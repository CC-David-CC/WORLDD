#pragma once
#include "workspace/control.hpp"
#include <memory>

namespace spatial {
// Owns a separate supervisor transport and the channels admitted through it.
class ControlChannels final {
public:
    using Dispatch = std::function<workspace::v1::Reply(const workspace::v1::Request&, const std::string&, const std::string&)>;
    ControlChannels(int fd, int peer, ControlLeases&);
    ~ControlChannels();
    ControlChannels(const ControlChannels&) = delete;
    ControlChannels& operator=(const ControlChannels&) = delete;
    void administrative();
    void serve(const Dispatch&);
    void revoked(const std::string&, const ControlLeases::Lease&);
    bool pending_output() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
