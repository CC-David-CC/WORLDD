#pragma once
#include "identity/native_seat_guardian_dispatch.hpp"
#include "platform/presentation_stream.hpp"
#include <memory>
#include <sys/types.h>

namespace spatial::identity {
// Supervisor-owned native guard endpoint for one world child.
class NativeSeatGuardianWorldService final {
public:
    NativeSeatGuardianWorldService(NativeSeatGuardian&,std::string issuer_id,std::string service_incarnation,
        std::uint64_t authority_generation,int world_fd,pid_t creator,pid_t world_sender);
    ~NativeSeatGuardianWorldService();
    NativeSeatGuardianWorldService(const NativeSeatGuardianWorldService&)=delete;
    NativeSeatGuardianWorldService& operator=(const NativeSeatGuardianWorldService&)=delete;
    void announce();
    bool pump();
    void fail() noexcept;
    int descriptor() const;
private:
    PresentationStream::File world_fd_;
    NativeSeatGuardian& guardian_;
    NativeSeatGuardianDispatch dispatch_;
    PresentationStream world_;
    bool announced_=false,failed_=false;
};
}
