#pragma once
#include "identity/native_seat_guardian_dispatch.hpp"
#include "platform/presentation_stream.hpp"
#include <memory>
#include <sys/types.h>

namespace spatial::identity {
// Owns two checked local channels. The caller supplies identities established by
// a trusted supervisor. Failure permanently fences both transports. This class
// does not establish issuer commit, guardian recovery, or participant authority.
class NativeSeatGuardianService final {
public:
    enum class Progress { idle, progress, fence_complete };
    NativeSeatGuardianService(NativeSeatGuardian&,std::string issuer_id,std::string service_incarnation,
        std::uint64_t authority_generation,int world_fd,pid_t creator,pid_t world_sender,
        int issuer_fd,pid_t issuer_sender);
    ~NativeSeatGuardianService();
    void announce();
    // Handles at most one complete request from each channel and flushes replies.
    Progress pump();
    void flush();
    bool pending_output() const;
    int world_descriptor() const;
    int issuer_descriptor() const;
private:
    // Own both descriptors before validating context or constructing either
    // transport, so every partially constructed service closes its channels.
    PresentationStream::File world_fd_,issuer_fd_;
    NativeSeatGuardian& guardian_;
    NativeSeatGuardianDispatch dispatch_;
    PresentationStream world_,issuer_;
    bool announced_=false,failed_=false;
    void fail() noexcept;
};
}
