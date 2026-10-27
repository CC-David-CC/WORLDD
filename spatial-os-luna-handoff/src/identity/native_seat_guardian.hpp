#pragma once
#include "identity/native_seat_ledger.hpp"
#include <cstddef>
namespace spatial { class NativeInput; }
namespace spatial::identity {
// Trusted, isolated guardian primitive. The caller authenticates every request,
// owns its dedicated ledger and native connection, and serializes this object.
// This class does not coordinate issuer commits or create transport receipts.
class NativeSeatGuardian final {
public:
    NativeSeatGuardian(NativeSeatLedger&, NativeInput&);
    std::string register_seat(const guardian::v1::SeatRegistration&, const std::string& registration_id);
    // Performs at most one checked native fence. Returns remaining obligations.
    // An exception leaves the obligation unresolved for an exact retry.
    std::size_t fence_step(const guardian::v1::NativeFenceIntent&);
    void disconnect() noexcept;
private:
    void check_native(const guardian::v1::NativeSeatBinding&) const;
    NativeSeatLedger& ledger_;
    NativeInput& native_;
};
}
