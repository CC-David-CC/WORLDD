#pragma once
#include "identity/native_seat_guardian.hpp"
#include <cstdint>
#include <string>
namespace spatial::identity {
// One instance belongs to one checked world channel and one checked issuer
// channel. The caller authenticates and monitors both transports; this class
// validates their roles and binding context, not their operating-system peers.
class NativeSeatGuardianDispatch final {
public:
    enum class Role { world, issuer };
    NativeSeatGuardianDispatch(NativeSeatGuardian&,std::string issuer_id,
        std::string service_incarnation,std::uint64_t authority_generation);
    guardian::v1::GuardianReply dispatch(Role,const guardian::v1::GuardianRequest&);
private:
    NativeSeatGuardian& guardian_;
    const std::string issuer_id_,service_incarnation_;
    const std::uint64_t authority_generation_;
    std::uint64_t world_sequence_=0,issuer_sequence_=0;
};
}
