#include "supervisor/host_capture_receiver.hpp"
#include <map>
#include <poll.h>
#include <cerrno>
#include <iostream>
namespace spatial::supervisor {
HostCaptureReceiver::HostCaptureReceiver(HostActorWorker& worker,HostActorWorker::Binding binding,
        std::unique_ptr<control::DeviceStream> stream)
    :worker_(worker),binding_(std::move(binding)),stream_(std::move(stream)) {
    if(!binding_ || !stream_)throw std::invalid_argument("Capture receiver requires an exact binding and stream");
    thread_=std::thread([this]{run();});
}
HostCaptureReceiver::~HostCaptureReceiver(){stopping_.store(true);if(thread_.joinable())thread_.join();}
void HostCaptureReceiver::run() noexcept {
    using Clock=control::DeviceAdapters::Clock;
    struct Source {std::uint64_t generation,sequence;Clock::time_point captured;};
    std::map<std::string,Source> active;
    std::map<std::string,std::uint64_t> retired;
    const auto need=[](bool value,const char* detail){if(!value)throw std::invalid_argument(detail);};
    try {
        while(!stopping_.load() && !binding_->retired()) {
            for(unsigned count=0;count<64 && !stopping_.load() && !binding_->retired();++count) {
                auto packet=stream_->receive();if(!packet)break;
                const auto now=Clock::now();
                const auto captured=Clock::time_point(std::chrono::duration_cast<Clock::duration>(
                    std::chrono::nanoseconds(static_cast<std::int64_t>(packet->monotonic_nanoseconds()))));
                const auto& event=packet->event();
                need(captured<=now && event.protocol_version()==1 && !event.source_id().empty() &&
                    event.source_id().size()<=80 && event.source_id().find('\0')==std::string::npos &&
                    event.source_generation()>0 && event.event_sequence()>0 &&
                    event.event_case()!=control::v1::DeviceEvent::EVENT_NOT_SET,"Invalid independent capture envelope");
                auto found=active.find(event.source_id());
                if(event.has_connected()) {
                    const auto kind=event.connected().kind();
                    need((kind==control::v1::KEYBOARD || kind==control::v1::MOUSE || kind==control::v1::SPACEMOUSE) &&
                        found==active.end() && active.size()<64 &&
                        (!retired.contains(event.source_id()) || event.source_generation()>retired.at(event.source_id())),
                        "Invalid or retired independent source connection");
                    active.emplace(event.source_id(),Source{event.source_generation(),event.event_sequence(),captured});
                } else {
                    need(found!=active.end() && found->second.generation==event.source_generation() &&
                        event.event_sequence()>found->second.sequence && captured>=found->second.captured,
                        "Stale independent source event");
                    found->second.sequence=event.event_sequence();found->second.captured=captured;
                    if(event.has_disconnected()) {
                        need(retired.size()<4096 || retired.contains(event.source_id()),"Too many retired capture sources");
                        retired[event.source_id()]=event.source_generation();active.erase(found);
                    }
                }
                if(!worker_.post_device(binding_,std::move(*packet))) {
                    if(!binding_->retired())throw std::runtime_error("Independent capture queue refused input");
                    return;
                }
            }
            const auto now=Clock::now();
            for(const auto& [id,source]:active) {
                (void)id;need(now>=source.captured && now-source.captured<std::chrono::milliseconds(350),
                    "Independent capture source exceeded350ms silence");
            }
            pollfd waiter{stream_->descriptor(),POLLIN,0};const int ready=poll(&waiter,1,10);
            need(ready>=0 || errno==EINTR,"Independent capture poll failed");
            need(!(waiter.revents&(POLLERR|POLLNVAL)),"Independent capture endpoint failed");
            // POLLHUP is drained by receive(), including any final queued packet.
        }
    } catch(const std::exception& error) {
        if(!stopping_.load() && !binding_->retired()) {
            std::cerr<<"Capture receiver retired its exact binding: "<<error.what()<<'\n';
            worker_.capture_failed(binding_);
        }
    } catch(...) {if(!stopping_.load())worker_.capture_failed(binding_);}
}
}
