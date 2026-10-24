#pragma once
#include "control/adapters.hpp"
#include <optional>
#include <sys/types.h>

namespace spatial::control {
// Takes an inherited unnamed private seqpacket endpoint. It never connects.
// Each endpoint has one thread owner. Actor identity stays outside the stream.
class DeviceStream final {
public:
    enum class Role { Capture, Controller };
    DeviceStream(int owned_descriptor,pid_t expected_peer,std::uint64_t capture_generation,Role);
    ~DeviceStream();
    DeviceStream(const DeviceStream&)=delete;
    DeviceStream& operator=(const DeviceStream&)=delete;
    // Nonblocking. Any undeliverable packet permanently fences this stream.
    void emit(const v1::DeviceEvent&,DeviceAdapters::Clock::time_point captured);
    std::optional<v1::CapturedDeviceEvent> receive();
    void fence() noexcept;
    int descriptor() const noexcept {return descriptor_;}
    bool failed() const noexcept {return failed_;}
private:
    const int descriptor_;
    const std::uint64_t generation_;
    const Role role_;
    std::uint64_t sequence_=0;
    bool failed_=false;
};
}
