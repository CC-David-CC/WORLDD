#pragma once
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include "copy.pb.h"
#include "storage.pb.h"
struct sqlite3;

namespace spatial {
class CopyJournalCapacityError : public std::runtime_error {
public:
    explicit CopyJournalCapacityError(workspace::copy::v1::CopyErrorCode code);
    workspace::copy::v1::CopyErrorCode code() const noexcept { return code_; }
private:
    workspace::copy::v1::CopyErrorCode code_;
};
class CopyJournal {
public:
    struct PublicationTarget { workspace::v1::StorageObject object; std::string operation_digest; };
    struct Limits { std::size_t max_operation_records = 4096; std::size_t max_effects_per_operation = 256; };
    struct Operation { std::string operation_id, requester, request_bytes, request_digest; workspace::copy::v1::CopyPhase phase; workspace::copy::v1::CopyErrorCode error; std::string destination_workspace_id, manifest, manifest_digest, snapshot_receipts, phase_payload, authorization_binding; };
    struct Effect { std::string effect_id, kind, request_digest, intent, receipt; bool uncertain = false; };
    explicit CopyJournal(sqlite3* db);
    CopyJournal(sqlite3* db, Limits limits);
    Operation begin(const workspace::copy::v1::WorkspaceCopyRequest&, const std::string& authenticated_requester,
                    const std::string& authorization_binding = {});
    bool exact_binding(const std::string& operation_id, const std::string& authenticated_requester,
                       const std::string& exact_request_bytes) const;
    Operation get(const std::string& operation_id, const std::string& authenticated_requester) const;
    workspace::copy::v1::WorkspaceCopyRequest request(const std::string& operation_id, const std::string& authenticated_requester) const;
    std::vector<Effect> effects(const std::string& operation_id, const std::string& authenticated_requester) const;
    void transition(const std::string& operation_id, const std::string& authenticated_requester,
                    workspace::copy::v1::CopyPhase expected, workspace::copy::v1::CopyPhase next,
                    const std::string& transition_payload);
    void record_publication_intent(const std::string& operation_id, const std::string& authenticated_requester, const std::string& effect_id, const std::vector<PublicationTarget>& targets);
    void record_effect_intent(const std::string& operation_id, const std::string& authenticated_requester,
                              const std::string& effect_id, const std::string& kind,
                              const std::string& request_digest, const std::string& intent);
    void record_effect_receipt(const std::string& operation_id, const std::string& authenticated_requester,
                               const std::string& effect_id, const std::string& request_digest,
                               const std::string& receipt);
    void abort(const std::string& operation_id, const std::string& authenticated_requester,
               workspace::copy::v1::CopyErrorCode error, const std::string& terminal_payload);
    std::vector<Operation> pending() const;
    void mark_uncertain(const std::string& operation_id, const std::string& authenticated_requester,
                        workspace::copy::v1::CopyErrorCode error);
    void recover_unresolved_effects(const std::string& operation_id, const std::string& authenticated_requester);
    static std::string digest(const std::string& bytes);
    void record_validated_result(const std::string& operation_id, const std::string& authenticated_requester,
                                 const std::string& destination_workspace_id, const std::string& serialized_manifest,
                                 const std::vector<workspace::copy::v1::CopyManifestEntry>& entries,
                                 const std::map<std::string, workspace::copy::v1::SnapshotReceipt>& receipts,
                                 std::uint64_t max_total_bytes, std::size_t max_entries);
    std::string record_publication_receipts(const std::string& operation_id, const std::string& authenticated_requester, const std::string& publication_effect_id,
                                    const std::vector<workspace::v1::StorageObjectState>& destination_receipts);
    static void migrate_schema(sqlite3* db);
    static void migrate_v5(sqlite3* db);
    static void validate_manifest(const std::vector<workspace::copy::v1::CopyManifestEntry>& entries,
                                  const std::map<std::string, workspace::copy::v1::SnapshotReceipt>& receipts,
                                  std::uint64_t max_total_bytes, std::size_t max_entries);
private:
    sqlite3* db_;
    Limits limits_;
    void require_requester(const std::string& operation_id, const std::string& requester) const;
    void record_effect_intent_impl(const std::string&, const std::string&, const std::string&, const std::string&, const std::string&, const std::string&);
    void require_publication_receipt_private(const std::string&, const std::string&) const;
    void record_effect_receipt_impl(const std::string&, const std::string&, const std::string&, const std::string&, const std::string&);
};
}
