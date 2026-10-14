#include "workspace/copy_journal.hpp"
#include <sqlite3.h>
#include <openssl/evp.h>
#include <algorithm>
#include <array>
#include <limits>
#include <set>
#include <stdexcept>

namespace spatial {
namespace {
void dbcheck(int rc) { if (rc != SQLITE_OK && rc != SQLITE_ROW && rc != SQLITE_DONE) throw std::runtime_error("copy journal database operation failed"); }
struct Statement {
    sqlite3_stmt* s = nullptr;
    Statement(sqlite3* db, const char* sql) { dbcheck(sqlite3_prepare_v2(db, sql, -1, &s, nullptr)); }
    ~Statement() { sqlite3_finalize(s); }
    void text(int i, const std::string& v) { dbcheck(sqlite3_bind_text(s, i, v.data(), static_cast<int>(v.size()), SQLITE_TRANSIENT)); }
    void blob(int i, const std::string& v) { dbcheck(sqlite3_bind_blob(s, i, v.data(), static_cast<int>(v.size()), SQLITE_TRANSIENT)); }
    void integer(int i, std::int64_t v) { dbcheck(sqlite3_bind_int64(s, i, v)); }
    int step() { int rc = sqlite3_step(s); dbcheck(rc); return rc; }
    std::string bytes(int i) const { const auto* p = sqlite3_column_blob(s, i); return p ? std::string(static_cast<const char*>(p), sqlite3_column_bytes(s, i)) : std::string{}; }
    std::string text_value(int i) const { const auto* p = sqlite3_column_text(s, i); return p ? std::string(reinterpret_cast<const char*>(p), sqlite3_column_bytes(s, i)) : std::string{}; }
    std::int64_t integer_value(int i) const { return sqlite3_column_int64(s, i); }
};
void require(bool value, const char* why) { if (!value) throw std::invalid_argument(why); }
bool valid_class(int c) {
    using namespace workspace::copy::v1;
    return c == static_cast<int>(PROJECT_TREE) || c == static_cast<int>(GIT_HISTORY) || c == static_cast<int>(APPLICATION_DEFINITION) || c == static_cast<int>(APPLICATION_STATE) || c == static_cast<int>(ENVIRONMENT_CONFIG);
}
bool valid_schema_id(const std::string& id) {
    return !id.empty() && id.size() <= 128 && id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-") == std::string::npos;
}
workspace::copy::v1::CopyDataClass checked_class(int raw) {
    require(valid_class(raw), "unsupported copy data class");
    return static_cast<workspace::copy::v1::CopyDataClass>(raw);
}
bool valid_schema(workspace::copy::v1::CopyDataClass type, const std::string& id, unsigned version) {
    using namespace workspace::copy::v1;
    if (version != 1) return false;
    switch (type) {
        case PROJECT_TREE: return id == "spatial.workspace.project-tree";
        case GIT_HISTORY: return id == "spatial.workspace.git-history";
        case APPLICATION_DEFINITION: return id == "spatial.workspace.application-definition";
        case APPLICATION_STATE: return id == "spatial.workspace.application-state";
        case ENVIRONMENT_CONFIG: return id == "spatial.workspace.environment-config";
        default: return false;
    }
}
std::vector<std::string> unframe(const std::string& bytes, std::size_t maximum) {
    require(bytes.size() <= maximum, "framed private receipts exceed bound");
    std::vector<std::string> result; std::size_t offset=0;
    while(offset<bytes.size()) { require(bytes.size()-offset>=4, "truncated framed private receipt");
        const auto n=(static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset]))<<24)|(static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset+1]))<<16)|(static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset+2]))<<8)|static_cast<unsigned char>(bytes[offset+3]); offset+=4;
        require(n>0 && n<=65536 && bytes.size()-offset>=n, "invalid framed private receipt length"); result.emplace_back(bytes.data()+offset,n); offset+=n;
        require(result.size()<=4096, "framed private receipt count exceeds bound");
    }
    return result;
}
bool transition_allowed(workspace::copy::v1::CopyPhase from, workspace::copy::v1::CopyPhase to) {
    using namespace workspace::copy::v1;
    return (from == REQUESTED && to == AUTHORIZED) || (from == AUTHORIZED && to == SNAPSHOTS_READY) ||
        (from == SNAPSHOTS_READY && to == DESTINATION_ALLOCATED) || (from == DESTINATION_ALLOCATED && to == DATA_STAGED) ||
        (from == DATA_STAGED && to == VALIDATED) || (from == VALIDATED && to == PUBLISHED);
}
}
std::string CopyJournal::digest(const std::string& bytes) {
    std::array<unsigned char, EVP_MAX_MD_SIZE> out{}; unsigned int length = 0;
    if (EVP_Digest(bytes.data(), bytes.size(), out.data(), &length, EVP_sha256(), nullptr) != 1 || length != 32)
        throw std::runtime_error("copy journal digest unavailable");
    return std::string(reinterpret_cast<const char*>(out.data()), length);
}
CopyJournalCapacityError::CopyJournalCapacityError(workspace::copy::v1::CopyErrorCode code)
    : std::runtime_error("copy journal capacity exhausted"), code_(code) {}
CopyJournal::CopyJournal(sqlite3* db) : CopyJournal(db, Limits{}) {}
CopyJournal::CopyJournal(sqlite3* db, Limits limits) : db_(db), limits_(limits) {
    require(db_ != nullptr, "copy journal requires execution database");
    require(limits_.max_operation_records > 0 && limits_.max_operation_records <= 65536 &&
            limits_.max_effects_per_operation > 0 && limits_.max_effects_per_operation <= 256, "copy journal limits out of bounds");
}
CopyJournal::Operation CopyJournal::begin(const workspace::copy::v1::WorkspaceCopyRequest& request, const std::string& requester) {
    require(requester.size() == 64, "authenticated requester must be exact issuer and actor key bytes");
    require(request.protocol_version() == 1 && request.operation_id().size() == 16 && !request.source_workspace_id().empty() && request.source_workspace_id().size() <= 128, "invalid copy request identity");
    const auto max_revision = static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
    require(request.expected_source_authority_revision() > 0 && request.expected_source_authority_revision() <= max_revision && request.expected_source_policy_revision() > 0 && request.expected_source_policy_revision() <= max_revision && request.expected_destination_profile_revision() > 0 && request.expected_destination_profile_revision() <= max_revision, "copy request revisions must fit positive SQLite integers");
    require(!request.destination_policy_profile().empty() && request.destination_policy_profile().size() <= 128, "invalid destination policy profile");
    require(request.data_class_size() > 0 && request.data_class_size() <= 8, "copy request class count out of bounds");
    std::set<int> classes;
    for (int i = 0; i < request.data_class_size(); ++i) {
        const int raw = static_cast<int>(request.data_class(i));
        (void)checked_class(raw);
        require(classes.insert(raw).second, "duplicate copy data class");
    }
    const std::string bytes = request.SerializeAsString(); require(bytes.size() <= 64 * 1024, "copy request exceeds journal bound");
    const auto request_digest = digest(bytes);
    Statement query(db_, "SELECT requester,request,request_digest,phase,error,destination,manifest,manifest_digest FROM copy_operations WHERE operation_id=?"); query.blob(1, request.operation_id());
    if (query.step() == SQLITE_ROW) {
        if (query.bytes(0) != requester || query.bytes(1) != bytes || query.bytes(2) != request_digest) throw std::invalid_argument("copy operation id is bound to different request or requester");
        return get(request.operation_id(), requester);
    }
    Statement insert(db_, "INSERT INTO copy_operations(operation_id,requester,request,request_digest,source_workspace,source_authority_revision,source_policy_revision,destination_profile,destination_profile_revision,data_classes,phase,error,phase_payload) SELECT ?,?,?,?,?,?,?,?,?,?,?,0,? WHERE (SELECT count(*) FROM copy_operations) < ?");
    insert.blob(1, request.operation_id()); insert.blob(2, requester); insert.blob(3, bytes); insert.blob(4, request_digest); insert.text(5, request.source_workspace_id());
    insert.integer(6, static_cast<std::int64_t>(request.expected_source_authority_revision())); insert.integer(7, static_cast<std::int64_t>(request.expected_source_policy_revision()));
    insert.text(8, request.destination_policy_profile()); insert.integer(9, static_cast<std::int64_t>(request.expected_destination_profile_revision()));
    std::string packed; for (int i = 0; i < request.data_class_size(); ++i) { const auto raw = static_cast<unsigned>(request.data_class(i)); packed.push_back(static_cast<char>((raw >> 8) & 255)); packed.push_back(static_cast<char>(raw & 255)); }
    insert.blob(10, packed); insert.integer(11, workspace::copy::v1::REQUESTED); insert.blob(12, std::string{}); insert.integer(13, static_cast<std::int64_t>(limits_.max_operation_records)); insert.step(); if (sqlite3_changes(db_) != 1) throw CopyJournalCapacityError(workspace::copy::v1::COPY_EXHAUSTED);
    return {request.operation_id(), requester, bytes, request_digest, workspace::copy::v1::REQUESTED, workspace::copy::v1::COPY_ERROR_UNSPECIFIED, {}, {}, {}, {}};
}
workspace::copy::v1::WorkspaceCopyRequest CopyJournal::request(const std::string& operation_id, const std::string& requester) const {
    const auto operation = get(operation_id, requester);
    workspace::copy::v1::WorkspaceCopyRequest decoded;
    require(decoded.ParseFromString(operation.request_bytes) && decoded.SerializeAsString() == operation.request_bytes, "stored copy request is not canonical");
    require(decoded.operation_id() == operation_id && decoded.protocol_version() == 1 && digest(operation.request_bytes) == operation.request_digest, "stored copy request binding is corrupt");
    return decoded;
}
void CopyJournal::require_requester(const std::string& operation_id, const std::string& requester) const {
    require(operation_id.size()==16 && requester.size()==64, "invalid opaque copy identity binding");
    Statement q(db_, "SELECT requester FROM copy_operations WHERE operation_id=?"); q.blob(1, operation_id);
    if (q.step() != SQLITE_ROW) throw std::invalid_argument("copy operation not found");
    if (q.bytes(0) != requester) throw std::invalid_argument("copy requester binding mismatch");
}
CopyJournal::Operation CopyJournal::get(const std::string& operation_id, const std::string& requester) const {
    require(operation_id.size() == 16, "invalid copy operation id"); require_requester(operation_id, requester);
    Statement q(db_, "SELECT request,request_digest,phase,error,destination,manifest,manifest_digest,snapshot_receipts FROM copy_operations WHERE operation_id=?"); q.blob(1, operation_id);
    if (q.step() != SQLITE_ROW) throw std::invalid_argument("copy operation not found");
    const auto raw_phase = q.integer_value(2), raw_error = q.integer_value(3);
    const auto request_bytes=q.bytes(0), request_digest=q.bytes(1), destination=q.text_value(4), manifest=q.bytes(5), manifest_digest=q.bytes(6), snapshot_receipts=q.bytes(7);
    require(raw_phase >= workspace::copy::v1::REQUESTED && raw_phase <= workspace::copy::v1::UNCERTAIN, "stored copy phase is invalid");
    require(raw_error == workspace::copy::v1::COPY_ERROR_UNSPECIFIED || (raw_error >= workspace::copy::v1::COPY_INVALID_ARGUMENT && raw_error <= workspace::copy::v1::COPY_OPERATION_CONFLICT), "stored copy error is invalid");
    require(request_bytes.size()<=64*1024 && request_digest.size()==32 && destination.size()<=128 && manifest.size()<=1024*1024 && manifest_digest.size()<=32 && snapshot_receipts.size()<=1024*1024, "stored copy operation exceeds configured bounds");
    require(manifest.empty() ? (manifest_digest.empty() && snapshot_receipts.empty()) : (!destination.empty() && manifest_digest.size()==32 && !snapshot_receipts.empty()), "stored validated copy result is incomplete");
    return {operation_id, requester, request_bytes, request_digest, static_cast<workspace::copy::v1::CopyPhase>(raw_phase), static_cast<workspace::copy::v1::CopyErrorCode>(raw_error), destination, manifest, manifest_digest, snapshot_receipts};
}
void CopyJournal::transition(const std::string& id, const std::string& requester, workspace::copy::v1::CopyPhase expected,
                             workspace::copy::v1::CopyPhase next, const std::string& payload) {
    require(id.size() == 16 && payload.size() <= 1024 * 1024 && requester.size() == 64, "copy transition identity or payload out of bounds"); require_requester(id, requester);
    Statement q(db_, "SELECT phase,phase_payload,manifest,manifest_digest,destination,request_digest FROM copy_operations WHERE operation_id=?"); q.blob(1, id);
    if (q.step() != SQLITE_ROW) throw std::invalid_argument("copy operation not found");
    auto current = static_cast<workspace::copy::v1::CopyPhase>(q.integer_value(0));
    if (current != expected) throw std::invalid_argument("copy phase compare-and-swap conflict");
    require(next != workspace::copy::v1::VALIDATED && next != workspace::copy::v1::ABORTED, "validated and aborted phases require proof-bearing APIs");
    if (current == next) { if (q.bytes(1) != payload) throw std::invalid_argument("same-phase copy retry payload conflict"); return; }
    require(transition_allowed(current, next), "illegal copy phase transition");
    if (next == workspace::copy::v1::UNCERTAIN) throw std::invalid_argument("uncertain phase requires unresolved effect recovery");
    if (current == workspace::copy::v1::VALIDATED && next == workspace::copy::v1::PUBLISHED)
        require(!q.text_value(4).empty() && q.bytes(2).size() > 0 && q.bytes(3).size() == 32, "published copy requires destination and validated manifest");
    if (current == workspace::copy::v1::VALIDATED && next == workspace::copy::v1::PUBLISHED) {
        Statement proof(db_, "SELECT count(*) FROM copy_effects WHERE operation_id=? AND kind='publication' AND request_digest=? AND state=1 AND receipt=?");
        proof.blob(1,id); proof.blob(2,q.bytes(5)); proof.blob(3,payload); proof.step();
        require(proof.integer_value(0)==1, "published copy requires one matching completed publication receipt");
    }
    Statement u(db_, "UPDATE copy_operations SET phase=?,phase_payload=? WHERE operation_id=? AND phase=?");
    Statement unresolved(db_, "SELECT count(*) FROM copy_effects WHERE operation_id=? AND state=0"); unresolved.blob(1, id); unresolved.step();
    require(unresolved.integer_value(0) == 0, "cannot advance while an external effect is unresolved");
    u.integer(1, next); u.blob(2, payload); u.blob(3, id); u.integer(4, expected); u.step();
    require(sqlite3_changes(db_) == 1, "copy phase transition raced");
}
void CopyJournal::record_publication_intent(const std::string& id, const std::string& requester, const std::string& effect_id,
                                             const std::vector<PublicationTarget>& targets) {
    require_requester(id,requester); require(effect_id.size()==16 && targets.size()>=2 && targets.size()<=64, "publication target count out of bounds");
    Statement op(db_, "SELECT phase,request_digest,destination,manifest,snapshot_receipts FROM copy_operations WHERE operation_id=?"); op.blob(1,id); require(op.step()==SQLITE_ROW && op.integer_value(0)==workspace::copy::v1::VALIDATED && !op.text_value(2).empty(), "publication intent requires a validated destination");
    std::vector<PublicationTarget> ordered=targets; std::sort(ordered.begin(),ordered.end(),[](const auto& a,const auto& b){ return std::make_pair(static_cast<int>(a.object.kind()),a.object.id())<std::make_pair(static_cast<int>(b.object.kind()),b.object.id()); });
    bool project=false,repository=false; std::size_t app_targets=0, app_entries=0; std::set<std::string> ids, source_ids, snapshot_ids; std::string intent; const auto destination=op.text_value(2); const auto destination_len=static_cast<std::uint32_t>(destination.size()); intent.push_back(static_cast<char>(destination_len>>24)); intent.push_back(static_cast<char>(destination_len>>16)); intent.push_back(static_cast<char>(destination_len>>8)); intent.push_back(static_cast<char>(destination_len)); intent.append(destination);
    for(const auto& bytes:unframe(op.bytes(3),1024*1024)) { workspace::copy::v1::CopyManifestEntry entry; require(entry.ParseFromString(bytes) && entry.SerializeAsString()==bytes, "stored manifest entry is corrupt"); if(entry.data_class()==workspace::copy::v1::APPLICATION_STATE) ++app_entries; workspace::v1::StorageObject source; require(source.ParseFromString(entry.source_storage_object()), "stored source identity is corrupt"); source_ids.insert(source.id()); }
    for(const auto& bytes:unframe(op.bytes(4),1024*1024)) { workspace::copy::v1::SnapshotReceipt snap; require(snap.ParseFromString(bytes) && snap.SerializeAsString()==bytes, "stored snapshot receipt is corrupt"); workspace::v1::StorageObject source,snapshot; require(source.ParseFromString(snap.source_storage_object()) && snapshot.ParseFromString(snap.snapshot_storage_object()), "stored snapshot identity is corrupt"); source_ids.insert(source.id()); snapshot_ids.insert(snapshot.id()); }
    for(const auto& target:ordered) {
        const auto& object=target.object; require((object.kind()==workspace::v1::StorageObject::PROJECT || object.kind()==workspace::v1::StorageObject::REPOSITORY || object.kind()==workspace::v1::StorageObject::APPLICATION_STATE) && object.id().size()==32 && object.id().find_first_not_of("0123456789abcdef")==std::string::npos && target.operation_digest.size()==32, "invalid typed publication target");
        require(ids.insert(object.id()).second && !source_ids.contains(object.id()) && !snapshot_ids.contains(object.id()), "duplicate or reused publication target identity");
        if(object.kind()==workspace::v1::StorageObject::PROJECT) { require(!project,"duplicate project target"); project=true; }
        if(object.kind()==workspace::v1::StorageObject::REPOSITORY) { require(!repository,"duplicate repository target"); repository=true; }
        if(object.kind()==workspace::v1::StorageObject::APPLICATION_STATE) ++app_targets;
        const auto bytes=object.SerializeAsString(); require(bytes.size()<=65536-36 && intent.size()<=65536-36-bytes.size(), "publication intent exceeds journal record bound"); const auto n=static_cast<std::uint32_t>(bytes.size()); intent.push_back(static_cast<char>(n>>24)); intent.push_back(static_cast<char>(n>>16)); intent.push_back(static_cast<char>(n>>8)); intent.push_back(static_cast<char>(n)); intent.append(bytes); intent.append(target.operation_digest);
    }
    require(project && repository && app_targets==app_entries, "publication targets must exactly match project, repository, and application-state allocation");
    record_effect_intent_impl(id,requester,effect_id,"publication",op.bytes(1),intent);
}
void CopyJournal::record_effect_intent(const std::string& id, const std::string& requester, const std::string& effect_id,
                                       const std::string& kind, const std::string& request_digest, const std::string& intent) {
    require(kind != "publication", "publication intent requires typed destination targets");
    record_effect_intent_impl(id,requester,effect_id,kind,request_digest,intent);
}
void CopyJournal::record_effect_intent_impl(const std::string& id, const std::string& requester, const std::string& effect_id,
                                       const std::string& kind, const std::string& request_digest, const std::string& intent) {
    require_requester(id, requester); require(effect_id.size() == 16 && !kind.empty() && kind.size() <= 64 && request_digest.size() == 32 && !intent.empty() && intent.size() <= 65536, "invalid external effect intent");
    Statement q(db_, "SELECT request_digest,phase FROM copy_operations WHERE operation_id=?"); q.blob(1, id); require(q.step() == SQLITE_ROW, "copy operation missing");
    if (q.bytes(0) != request_digest) throw std::invalid_argument("effect intent request digest mismatch");
    Statement old(db_, "SELECT kind,request_digest,intent,state,receipt FROM copy_effects WHERE operation_id=? AND effect_id=?"); old.blob(1, id); old.blob(2, effect_id);
    if (old.step() == SQLITE_ROW) { if (old.text_value(0) != kind || old.bytes(1) != request_digest || old.bytes(2) != intent) throw std::invalid_argument("effect idempotency conflict"); return; }
    const auto current = static_cast<workspace::copy::v1::CopyPhase>(q.integer_value(1)); require(current >= workspace::copy::v1::AUTHORIZED && current <= workspace::copy::v1::VALIDATED, "external effects require a nonterminal authorized phase");
    Statement ins(db_, "INSERT INTO copy_effects(operation_id,effect_id,kind,request_digest,intent,state,receipt) SELECT ?,?,?,?,?,0,X'' WHERE (SELECT count(*) FROM copy_effects WHERE operation_id=?) < ?");
    ins.blob(1, id); ins.blob(2, effect_id); ins.text(3, kind); ins.blob(4, request_digest); ins.blob(5, intent); ins.blob(6,id); ins.integer(7,static_cast<std::int64_t>(limits_.max_effects_per_operation)); ins.step(); if (sqlite3_changes(db_) != 1) throw CopyJournalCapacityError(workspace::copy::v1::COPY_EXHAUSTED);
}
void CopyJournal::record_effect_receipt(const std::string& id, const std::string& requester, const std::string& effect_id,
                                        const std::string& request_digest, const std::string& receipt) {
    require_requester(id,requester);
    require_publication_receipt_private(id,effect_id);
    record_effect_receipt_impl(id,requester,effect_id,request_digest,receipt);
}
void CopyJournal::require_publication_receipt_private(const std::string& id, const std::string& effect_id) const {
    Statement q(db_, "SELECT kind FROM copy_effects WHERE operation_id=? AND effect_id=?"); q.blob(1,id); q.blob(2,effect_id);
    if(q.step()==SQLITE_ROW && q.text_value(0)=="publication") throw std::invalid_argument("publication receipts require typed destination validation");
}
void CopyJournal::record_effect_receipt_impl(const std::string& id, const std::string& requester, const std::string& effect_id,
                                        const std::string& request_digest, const std::string& receipt) {
    require_requester(id, requester); require(effect_id.size() == 16 && request_digest.size() == 32 && !receipt.empty() && receipt.size() <= 1024 * 1024, "invalid external effect receipt");
    Statement q(db_, "SELECT request_digest,state,receipt FROM copy_effects WHERE operation_id=? AND effect_id=?"); q.blob(1, id); q.blob(2, effect_id);
    if (q.step() != SQLITE_ROW || q.bytes(0) != request_digest) throw std::invalid_argument("external effect receipt has no matching intent");
    if (q.integer_value(1) == 1) { if (q.bytes(2) != receipt) throw std::invalid_argument("external effect receipt conflict"); return; }
    Statement u(db_, "UPDATE copy_effects SET state=1,receipt=? WHERE operation_id=? AND effect_id=? AND state=0"); u.blob(1, receipt); u.blob(2, id); u.blob(3, effect_id); u.step(); require(sqlite3_changes(db_) == 1, "external effect receipt raced");
}
std::vector<CopyJournal::Effect> CopyJournal::effects(const std::string& id, const std::string& requester) const {
    require_requester(id, requester);
    Statement binding(db_, "SELECT request_digest FROM copy_operations WHERE operation_id=?"); binding.blob(1,id); require(binding.step()==SQLITE_ROW,"copy operation missing"); const auto bound_digest=binding.bytes(0);
    Statement q(db_, "SELECT effect_id,kind,request_digest,intent,receipt,state FROM copy_effects WHERE operation_id=? ORDER BY effect_id"); q.blob(1,id);
    std::vector<Effect> result; while(q.step()==SQLITE_ROW) {
        const auto effect_id=q.bytes(0), kind=q.text_value(1), request_digest=q.bytes(2), intent=q.bytes(3), receipt=q.bytes(4); const auto state=q.integer_value(5);
        require(effect_id.size()==16 && !kind.empty() && kind.size()<=64 && request_digest.size()==32 && request_digest==bound_digest && !intent.empty() && intent.size()<=1024*1024 && (state==0 || state==1), "stored copy effect is invalid");
        require((state==0 && receipt.empty()) || (state==1 && !receipt.empty() && receipt.size()<=1024*1024), "stored copy effect receipt state is invalid");
        require(result.size()<limits_.max_effects_per_operation, "stored copy effect count exceeds configured bound");
        result.push_back({effect_id,kind,request_digest,intent,receipt,state==0});
    }
    return result;
}
void CopyJournal::abort(const std::string& id, const std::string& requester, workspace::copy::v1::CopyErrorCode error, const std::string& payload) {
    require(id.size()==16 && requester.size()==64 && payload.size()<=1024*1024 && error>=workspace::copy::v1::COPY_INVALID_ARGUMENT && error<=workspace::copy::v1::COPY_OPERATION_CONFLICT, "invalid terminal copy error");
    require_requester(id,requester);
    Statement q(db_, "SELECT phase,phase_payload,error FROM copy_operations WHERE operation_id=?"); q.blob(1,id); require(q.step()==SQLITE_ROW,"copy operation missing");
    const auto phase=static_cast<workspace::copy::v1::CopyPhase>(q.integer_value(0));
    if(phase==workspace::copy::v1::ABORTED) { if(q.bytes(1)!=payload || q.integer_value(2)!=error) throw std::invalid_argument("terminal copy retry conflict"); return; }
    require(phase>=workspace::copy::v1::REQUESTED && phase<=workspace::copy::v1::VALIDATED,"copy operation is terminal");
    Statement pending(db_,"SELECT count(*) FROM copy_effects WHERE operation_id=? AND state=0"); pending.blob(1,id); pending.step(); require(pending.integer_value(0)==0,"cannot abort with unresolved external effect");
    Statement u(db_,"UPDATE copy_operations SET phase=?,error=?,phase_payload=? WHERE operation_id=? AND phase=?"); u.integer(1,workspace::copy::v1::ABORTED); u.integer(2,error); u.blob(3,payload); u.blob(4,id); u.integer(5,phase); u.step(); require(sqlite3_changes(db_)==1,"copy abort raced");
}
void CopyJournal::recover_unresolved_effects(const std::string& id, const std::string& requester) {
    require_requester(id, requester);
    Statement q(db_, "SELECT phase FROM copy_operations WHERE operation_id=?"); q.blob(1, id); require(q.step() == SQLITE_ROW, "copy operation missing");
    auto phase = static_cast<workspace::copy::v1::CopyPhase>(q.integer_value(0));
    if (phase == workspace::copy::v1::PUBLISHED || phase == workspace::copy::v1::ABORTED || phase == workspace::copy::v1::UNCERTAIN) return;
    Statement pending(db_, "SELECT count(*) FROM copy_effects WHERE operation_id=? AND state=0"); pending.blob(1, id); pending.step();
    if (pending.integer_value(0) == 0) return;
    Statement u(db_, "UPDATE copy_operations SET phase=?,error=? WHERE operation_id=? AND phase=?"); u.integer(1, workspace::copy::v1::UNCERTAIN); u.integer(2, workspace::copy::v1::COPY_UNCERTAIN); u.blob(3, id); u.integer(4, phase); u.step();
    require(sqlite3_changes(db_) == 1, "copy uncertainty transition raced");
}
void CopyJournal::record_validated_result(const std::string& id, const std::string& requester, const std::string& destination,
                                         const std::string& serialized_manifest,
                                         const std::vector<workspace::copy::v1::CopyManifestEntry>& entries,
                                         const std::map<std::string, workspace::copy::v1::SnapshotReceipt>& receipts,
                                         std::uint64_t max_total_bytes, std::size_t max_entries) {
    require(!destination.empty() && destination.size() <= 128 && destination.find('\0') == std::string::npos, "invalid destination workspace identity");
    require(!serialized_manifest.empty() && serialized_manifest.size() <= 1024 * 1024, "serialized manifest exceeds transport bound");
    std::string canonical;
    for (const auto& entry : entries) {
        const auto bytes = entry.SerializeAsString();
        require(bytes.size() <= 1024 * 1024 - 4 && canonical.size() <= 1024 * 1024 - 4 - bytes.size(), "manifest exceeds transport bound");
        const auto n = static_cast<std::uint32_t>(bytes.size());
        canonical.push_back(static_cast<char>((n >> 24) & 255)); canonical.push_back(static_cast<char>((n >> 16) & 255));
        canonical.push_back(static_cast<char>((n >> 8) & 255)); canonical.push_back(static_cast<char>(n & 255)); canonical.append(bytes);
    }
    require(canonical == serialized_manifest, "manifest bytes do not match validated entries");
    validate_manifest(entries, receipts, max_total_bytes, max_entries);
    require_requester(id, requester);
    Statement q(db_, "SELECT phase,manifest,manifest_digest,destination,request_digest,source_workspace,request,snapshot_receipts FROM copy_operations WHERE operation_id=?"); q.blob(1, id);
    require(q.step() == SQLITE_ROW, "copy operation missing");
    for (const auto& item : receipts) require(item.first == item.second.receipt_id() && item.second.request_digest() == q.bytes(4), "snapshot receipt key or request binding mismatch");
    require(destination != q.text_value(5), "copy destination must have a distinct workspace identity");
    workspace::copy::v1::WorkspaceCopyRequest bound_request;
    require(bound_request.ParseFromString(q.bytes(6)) && bound_request.SerializeAsString() == q.bytes(6), "stored copy request is not canonical");
    std::set<int> requested_classes; for (int i=0;i<bound_request.data_class_size();++i) requested_classes.insert(static_cast<int>(checked_class(static_cast<int>(bound_request.data_class(i)))));
    std::set<int> manifest_classes; for (const auto& entry : entries) manifest_classes.insert(static_cast<int>(checked_class(static_cast<int>(entry.data_class()))));
    require(manifest_classes == requested_classes, "manifest classes do not match the exact request");
    std::string packed_receipts;
    for (const auto& item : receipts) { const auto bytes=item.second.SerializeAsString(); require(bytes.size()<=65536 && bytes.size()<=1024*1024-4 && packed_receipts.size()<=1024*1024-4-bytes.size(), "snapshot receipts exceed transport bound"); const auto n=static_cast<std::uint32_t>(bytes.size()); packed_receipts.push_back(static_cast<char>(n>>24)); packed_receipts.push_back(static_cast<char>(n>>16)); packed_receipts.push_back(static_cast<char>(n>>8)); packed_receipts.push_back(static_cast<char>(n)); packed_receipts.append(bytes); }
    const auto phase = static_cast<workspace::copy::v1::CopyPhase>(q.integer_value(0));
    if (phase == workspace::copy::v1::VALIDATED) {
        if (q.bytes(1) != serialized_manifest || q.bytes(2) != digest(serialized_manifest) || q.text_value(3) != destination || q.bytes(7) != packed_receipts)
            throw std::invalid_argument("validated manifest retry conflict");
        return;
    }
    require(phase == workspace::copy::v1::DATA_STAGED, "manifest validation requires staged data");
    Statement pending(db_, "SELECT count(*) FROM copy_effects WHERE operation_id=? AND state=0"); pending.blob(1,id); pending.step(); require(pending.integer_value(0)==0, "cannot validate while an external effect is unresolved");
    Statement u(db_, "UPDATE copy_operations SET phase=?,destination=?,manifest=?,manifest_digest=?,phase_payload=?,snapshot_receipts=? WHERE operation_id=? AND phase=?");
    u.integer(1, workspace::copy::v1::VALIDATED); u.text(2, destination); u.blob(3, serialized_manifest); u.blob(4, digest(serialized_manifest));
    u.blob(5, digest(serialized_manifest)); u.blob(6, packed_receipts); u.blob(7, id); u.integer(8, workspace::copy::v1::DATA_STAGED); u.step();
    require(sqlite3_changes(db_) == 1, "manifest validation raced");
}
std::string CopyJournal::record_publication_receipts(const std::string& id, const std::string& requester,
                                              const std::string& effect_id, const std::vector<workspace::v1::StorageObjectState>& receipts) {
    using namespace workspace::v1;
    require_requester(id, requester); require(effect_id.size()==16 && receipts.size() >= 2 && receipts.size() <= 64, "destination receipt count out of bounds");
    Statement op(db_, "SELECT phase,request_digest,manifest,snapshot_receipts,destination FROM copy_operations WHERE operation_id=?"); op.blob(1, id);
    require(op.step() == SQLITE_ROW, "copy operation missing"); require(op.integer_value(0) == workspace::copy::v1::VALIDATED, "destination receipts require validated manifest");
    const auto request_digest = op.bytes(1); std::string packed; bool have_project=false, have_repository=false; std::set<std::string> object_ids, subvolume_ids;
    const auto pending=effects(id,requester); const Effect* publication=nullptr;
    for (const auto& effect : pending) if (effect.effect_id==effect_id && effect.kind=="publication" && effect.request_digest==request_digest) publication=&effect;
    require(publication!=nullptr, "publication receipt requires its exact pre-effect intent");
    std::map<std::pair<int,std::string>,std::string> targets;
    const auto& intent=publication->intent; require(intent.size()>=4, "publication intent malformed"); std::size_t offset=0; const auto dest_len=(static_cast<std::uint32_t>(static_cast<unsigned char>(intent[0]))<<24)|(static_cast<std::uint32_t>(static_cast<unsigned char>(intent[1]))<<16)|(static_cast<std::uint32_t>(static_cast<unsigned char>(intent[2]))<<8)|static_cast<unsigned char>(intent[3]); offset=4; require(dest_len>0 && dest_len<=128 && intent.size()-offset>=dest_len && intent.substr(offset,dest_len)==op.text_value(4), "publication intent destination mismatch"); offset+=dest_len;
    for (std::size_t i=offset;i<intent.size();) { require(intent.size()-i>=4, "publication intent frame truncated"); const auto n=(static_cast<std::uint32_t>(static_cast<unsigned char>(intent[i]))<<24)|(static_cast<std::uint32_t>(static_cast<unsigned char>(intent[i+1]))<<16)|(static_cast<std::uint32_t>(static_cast<unsigned char>(intent[i+2]))<<8)|static_cast<unsigned char>(intent[i+3]); i+=4; require(n>0 && n<=65536 && intent.size()-i>=n+32, "publication intent item invalid"); StorageObject target; require(target.ParseFromArray(intent.data()+i,static_cast<int>(n)) && target.SerializeAsString()==intent.substr(i,n), "publication target identity not canonical"); i+=n; const auto digest=intent.substr(i,32); i+=32; require((target.kind()==StorageObject::PROJECT || target.kind()==StorageObject::REPOSITORY || target.kind()==StorageObject::APPLICATION_STATE) && target.id().size()==32 && target.id().find_first_not_of("0123456789abcdef")==std::string::npos && targets.emplace(std::make_pair(static_cast<int>(target.kind()),target.id()),digest).second, "duplicate or invalid publication target"); }
    std::set<std::string> source_ids, snapshot_ids, protected_subvolumes; std::size_t expected_app_states=0;
    for(const auto& bytes:unframe(op.bytes(2),1024*1024)) { workspace::copy::v1::CopyManifestEntry entry; require(entry.ParseFromString(bytes) && entry.SerializeAsString()==bytes, "stored manifest entry is corrupt"); if(entry.data_class()==workspace::copy::v1::APPLICATION_STATE) ++expected_app_states; workspace::v1::StorageObject source; require(source.ParseFromString(entry.source_storage_object()), "stored source identity is corrupt"); source_ids.insert(source.id()); }
    for(const auto& bytes:unframe(op.bytes(3),1024*1024)) { workspace::copy::v1::SnapshotReceipt snap; require(snap.ParseFromString(bytes) && snap.SerializeAsString()==bytes, "stored snapshot receipt is corrupt"); workspace::v1::StorageObject source, snapshot; require(source.ParseFromString(snap.source_storage_object()) && snapshot.ParseFromString(snap.snapshot_storage_object()), "stored snapshot identities are corrupt"); source_ids.insert(source.id()); snapshot_ids.insert(snapshot.id()); protected_subvolumes.insert(std::to_string(snap.source_subvolume_id())); protected_subvolumes.insert(std::to_string(snap.snapshot_subvolume_id())); }
    for (const auto& receipt : receipts) {
        const auto& object = receipt.object();
        require(receipt.accounting_consistent() && !receipt.read_only() && receipt.subvolume_id() > 0 && receipt.quota_group() > 0 && receipt.maximum_bytes() > 0 && receipt.referenced_bytes() <= receipt.maximum_bytes() && receipt.exclusive_bytes() <= receipt.referenced_bytes(), "destination receipt accounting or mutability invalid");
        require(object.kind() == StorageObject::PROJECT || object.kind() == StorageObject::REPOSITORY || object.kind() == StorageObject::APPLICATION_STATE, "destination receipt object kind invalid");
        require(receipt.source_object().kind()==StorageObject::UNSPECIFIED && receipt.source_object().id().empty() && receipt.source_subvolume_id()==0, "destination receipt carries source snapshot identity");
        require(object.id().size() == 32 && object.id().find_first_not_of("0123456789abcdef") == std::string::npos, "destination receipt object is not registered");
        require(object_ids.insert(object.id()).second && subvolume_ids.insert(std::to_string(receipt.subvolume_id())).second, "duplicate destination receipt identity");
        require(!source_ids.contains(object.id()) && !snapshot_ids.contains(object.id()) && !protected_subvolumes.contains(std::to_string(receipt.subvolume_id())), "destination receipt reuses a source or snapshot identity");
        const auto target=targets.find({static_cast<int>(object.kind()),object.id()}); require(target!=targets.end() && receipt.operation_digest()==target->second, "destination receipt differs from the recorded storage intent");
        if(object.kind()==StorageObject::PROJECT) { require(!have_project, "duplicate destination project"); have_project=true; }
        if(object.kind()==StorageObject::REPOSITORY) { require(!have_repository, "duplicate destination repository"); have_repository=true; }
        const auto bytes = receipt.SerializeAsString(); require(bytes.size() <= 65536, "destination receipt exceeds limit");
        const auto n = static_cast<std::uint32_t>(bytes.size()); packed.push_back(static_cast<char>(n>>24)); packed.push_back(static_cast<char>(n>>16)); packed.push_back(static_cast<char>(n>>8)); packed.push_back(static_cast<char>(n)); packed.append(bytes);
    }
    const auto app_targets=static_cast<std::size_t>(std::count_if(targets.begin(),targets.end(),[](const auto& t){return t.first.first==StorageObject::APPLICATION_STATE;}));
    require(have_project && have_repository && targets.size()==receipts.size() && app_targets==expected_app_states, "publication requires exact project, Git repository, and application receipt targets");
    require(packed.size() <= 1024 * 1024, "publication receipt set exceeds transport bound");
    record_effect_receipt_impl(id,requester,effect_id,request_digest,packed);
    return packed;
}
void CopyJournal::migrate_schema(sqlite3* db) {
    require(db != nullptr && sqlite3_get_autocommit(db) == 0, "copy journal migration requires an active transaction");
    char* message = nullptr;
    const char* sql = "CREATE TABLE copy_operations("
        "operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),requester BLOB NOT NULL CHECK(length(requester)=64),request BLOB NOT NULL,request_digest BLOB NOT NULL CHECK(length(request_digest)=32),"
        "source_workspace TEXT NOT NULL,source_authority_revision INTEGER NOT NULL CHECK(source_authority_revision>0),source_policy_revision INTEGER NOT NULL CHECK(source_policy_revision>0),"
        "destination_profile TEXT NOT NULL,destination_profile_revision INTEGER NOT NULL CHECK(destination_profile_revision>0),data_classes BLOB NOT NULL,"
        "phase INTEGER NOT NULL CHECK(phase BETWEEN 1 AND 9),error INTEGER NOT NULL DEFAULT 0,destination TEXT NOT NULL DEFAULT '',manifest BLOB NOT NULL DEFAULT X'',manifest_digest BLOB NOT NULL DEFAULT X'',phase_payload BLOB NOT NULL DEFAULT X'',snapshot_receipts BLOB NOT NULL DEFAULT X'');"
        "CREATE TABLE copy_effects(operation_id BLOB NOT NULL REFERENCES copy_operations(operation_id) ON DELETE CASCADE,effect_id BLOB NOT NULL CHECK(length(effect_id)=16),"
        "kind TEXT NOT NULL,request_digest BLOB NOT NULL CHECK(length(request_digest)=32),intent BLOB NOT NULL,state INTEGER NOT NULL CHECK(state IN(0,1)),receipt BLOB NOT NULL,"
        "PRIMARY KEY(operation_id,effect_id));"
        "CREATE INDEX copy_effects_pending ON copy_effects(operation_id,state);PRAGMA user_version=5;";
    const int rc = sqlite3_exec(db, sql, nullptr, nullptr, &message);
    if (rc != SQLITE_OK) { sqlite3_free(message); throw std::runtime_error("copy journal schema migration failed"); }
}void CopyJournal::validate_manifest(const std::vector<workspace::copy::v1::CopyManifestEntry>& entries,
                                   const std::map<std::string, workspace::copy::v1::SnapshotReceipt>& receipts,
                                   std::uint64_t max_total_bytes, std::size_t max_entries) {
    using namespace workspace::copy::v1; require(!entries.empty() && entries.size() <= max_entries && max_entries <= 4096, "manifest entry count out of bounds");
    std::set<std::pair<int, std::string>> identities; std::set<std::string> used_receipts; std::uint64_t total = 0; std::set<int> classes;
    for (const auto& e : entries) {
        const auto data_class = checked_class(static_cast<int>(e.data_class()));
        require(e.object_id().size() == 16 && valid_schema_id(e.schema_id()) && valid_schema(data_class, e.schema_id(), e.schema_version()), "invalid typed manifest identity or schema");
        require(e.source_storage_object().size() <= 512 && e.content_digest().size() == 32 && e.snapshot_receipt_id().size() == 16, "manifest identity or digest length invalid");
        workspace::v1::StorageObject source; require(source.ParseFromString(e.source_storage_object()) && source.SerializeAsString() == e.source_storage_object(), "manifest storage identity is not canonical");
        require(source.kind() == workspace::v1::StorageObject::PROJECT || source.kind() == workspace::v1::StorageObject::REPOSITORY || source.kind() == workspace::v1::StorageObject::APPLICATION_STATE, "manifest source storage kind is not copyable");
        if (data_class == PROJECT_TREE) require(source.kind() == workspace::v1::StorageObject::PROJECT, "project tree must reference a registered project object");
        if (data_class == GIT_HISTORY) require(source.kind() == workspace::v1::StorageObject::REPOSITORY, "Git history must reference a registered repository object");
        if (data_class == APPLICATION_STATE) require(source.kind() == workspace::v1::StorageObject::APPLICATION_STATE, "application state must reference its registered object");
        require(source.id().size() == 32 && source.id().find_first_not_of("0123456789abcdef") == std::string::npos, "manifest storage identity is not registered-object form");
        require(identities.emplace(static_cast<int>(e.data_class()), e.object_id()).second, "duplicate manifest class/object id");
        require(e.byte_count() <= max_total_bytes && total <= max_total_bytes - e.byte_count(), "manifest byte total exceeds trusted bound"); total += e.byte_count(); classes.insert(static_cast<int>(e.data_class()));
        const auto it = receipts.find(e.snapshot_receipt_id()); require(it != receipts.end() && used_receipts.insert(e.snapshot_receipt_id()).second, "manifest snapshot receipt missing or reused"); const auto& receipt = it->second;
        require(receipt.receipt_id() == e.snapshot_receipt_id() && receipt.readonly() && receipt.request_digest().size() == 32 && receipt.content_digest() == e.content_digest() && receipt.source_storage_object() == e.source_storage_object(), "snapshot receipt does not witness manifest entry");
        workspace::v1::StorageObject snapshot; require(snapshot.ParseFromString(receipt.snapshot_storage_object()) && snapshot.SerializeAsString() == receipt.snapshot_storage_object() && snapshot.kind() == workspace::v1::StorageObject::SNAPSHOT, "snapshot receipt identity invalid");
        require(snapshot.id().size() == 32 && snapshot.id().find_first_not_of("0123456789abcdef") == std::string::npos && snapshot.id() != source.id(), "snapshot identity is not registered-object form");
        require(receipt.receipt_id().size() == 16 && receipt.source_subvolume_id() > 0 && receipt.snapshot_subvolume_id() > 0 && receipt.source_subvolume_id() != receipt.snapshot_subvolume_id(), "snapshot receipt subvolume identity invalid");
    }
    require(classes.contains(PROJECT_TREE) && classes.contains(GIT_HISTORY) && used_receipts.size() == receipts.size(), "manifest must pair project tree and Git history and bind every receipt exactly once");
}
}
