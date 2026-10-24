#pragma once
#include "supervisor/host_actor_worker.hpp"
#include <atomic>
#include <thread>
namespace spatial::supervisor {
// Owns the Controller endpoint on one independent thread. No actor exchanges.
class HostCaptureReceiver final {
public:
    HostCaptureReceiver(HostActorWorker&,HostActorWorker::Binding,std::unique_ptr<control::DeviceStream>);
    ~HostCaptureReceiver();
    HostCaptureReceiver(const HostCaptureReceiver&)=delete;
    HostCaptureReceiver& operator=(const HostCaptureReceiver&)=delete;
private:
    void run() noexcept;
    HostActorWorker& worker_;
    HostActorWorker::Binding binding_;
    std::unique_ptr<control::DeviceStream> stream_;
    std::atomic<bool> stopping_{false};
    std::thread thread_;
};
}
