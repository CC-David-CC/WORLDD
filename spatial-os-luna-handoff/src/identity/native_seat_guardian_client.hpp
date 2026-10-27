#pragma once
#include "native_seat_guardian.pb.h"
#include "platform/presentation_stream.hpp"
#include <cstdint>
#include <string>
#include <sys/types.h>

namespace spatial::identity {
// World-side checked client. A successful return proves native binding and
// durable registration completed before the caller submits input.
class NativeSeatGuardianClient final {
public:
    NativeSeatGuardianClient(int fd,pid_t creator,pid_t sender);
    guardian::v1::SeatRegistrationReceipt register_seat(
        const guardian::v1::NativeSeatBinding&,std::uint64_t issuer_security_revision);
private:
    PresentationStream::File fd_;
    PresentationStream channel_;
    std::uint64_t sequence_=0;
    guardian::v1::GuardianReply receive();
};
}
