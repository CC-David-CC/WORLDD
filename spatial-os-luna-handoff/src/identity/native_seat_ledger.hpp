#pragma once
#include "native_seat_guardian.pb.h"
#include <string>
#include <optional>
#include <vector>
struct sqlite3;

namespace spatial::identity {
// Private persistent primitive for a future supervised guardian. The caller owns
// one dedicated SQLite connection, serializes use, and authenticates every peer.
// These records do not grant authority and do not themselves revoke X seats.
class NativeSeatLedger final {
public:
    struct Obligation {
        std::string registration_id;
        guardian::v1::NativeSeatBinding binding;
    };
    explicit NativeSeatLedger(sqlite3* database);
    std::string register_seat(const guardian::v1::SeatRegistration&, const std::string& new_registration_id);
    std::vector<Obligation> begin_fence(const guardian::v1::NativeFenceIntent&);
    void prepare_fence(const guardian::v1::NativeFenceIntent&);
    std::optional<Obligation> first_outstanding(const std::string& intent_id);
    std::size_t outstanding_count(const std::string& intent_id);
    std::size_t obligation_count(const std::string& intent_id);
    std::vector<Obligation> outstanding(const std::string& intent_id);
    // Call only after the exact native server confirmed this seat is fenced.
    void record_confirmed_fence(const std::string& intent_id, const std::string& registration_id);
    std::vector<guardian::v1::NativeFenceIntent> pending_intents();
private:
    sqlite3* db_;
};
}
