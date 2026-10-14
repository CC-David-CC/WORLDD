#include "workspace/copy_journal.hpp"
#include <sqlite3.h>
#include <cassert>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>
#include <string>
#include <unistd.h>
using namespace spatial;
namespace {
const std::string actor(64, 'a');
void exec(sqlite3* db, const char* sql) { char* error=nullptr; if (sqlite3_exec(db,sql,nullptr,nullptr,&error)!=SQLITE_OK) { std::string m=error?error:"sql error"; sqlite3_free(error); throw std::runtime_error(m); } }
template<class F> void rejects(F f) { bool rejected=false; try { f(); } catch (const std::exception&) { rejected=true; } assert(rejected); }
template<class F> void rejects_with(F f, const std::string& expected) { try { f(); } catch (const std::invalid_argument& error) { if (error.what()!=expected) throw std::runtime_error(std::string("unexpected rejection: ")+error.what()); return; } throw std::runtime_error(std::string("expected rejection: ")+expected); }
template<class F> void exhausted(F f) { bool rejected=false; try { f(); } catch (const CopyJournalCapacityError& e) { rejected=e.code()==workspace::copy::v1::COPY_EXHAUSTED; } assert(rejected); }
workspace::copy::v1::WorkspaceCopyRequest request(char ch='a') {
    workspace::copy::v1::WorkspaceCopyRequest r; r.set_protocol_version(1); r.set_operation_id(std::string(16,ch)); r.set_source_workspace_id("workspace-01");
    r.set_expected_source_authority_revision(7); r.set_expected_source_policy_revision(8); r.set_destination_policy_profile("personal"); r.set_expected_destination_profile_revision(9);
    r.add_data_class(workspace::copy::v1::PROJECT_TREE); r.add_data_class(workspace::copy::v1::GIT_HISTORY); r.add_data_class(workspace::copy::v1::APPLICATION_STATE); return r;
}
std::vector<workspace::copy::v1::CopyManifestEntry> entries(std::map<std::string, workspace::copy::v1::SnapshotReceipt>& receipts, const std::string& digest) {
    std::vector<workspace::copy::v1::CopyManifestEntry> out;
    for (int i=0;i<4;++i) {
        const auto kind=i==0?workspace::v1::StorageObject::PROJECT:i==1?workspace::v1::StorageObject::REPOSITORY:workspace::v1::StorageObject::APPLICATION_STATE;
        workspace::v1::StorageObject source; source.set_kind(kind); source.set_id(i==0?"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":i==1?"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb":i==2?"11111111111111111111111111111111":"22222222222222222222222222222222");
        workspace::copy::v1::CopyManifestEntry e; e.set_data_class(i==0?workspace::copy::v1::PROJECT_TREE:i==1?workspace::copy::v1::GIT_HISTORY:workspace::copy::v1::APPLICATION_STATE); e.set_object_id(std::string(16, static_cast<char>('x'+i)));
        e.set_schema_id(i==0?"spatial.workspace.project-tree":i==1?"spatial.workspace.git-history":"spatial.workspace.application-state"); e.set_schema_version(1); e.set_source_storage_object(source.SerializeAsString()); e.set_byte_count(128 + i); e.set_content_digest(std::string(32, static_cast<char>('d'+i)));
        workspace::copy::v1::SnapshotReceipt r; r.set_source_storage_object(e.source_storage_object()); workspace::v1::StorageObject snap; snap.set_kind(workspace::v1::StorageObject::SNAPSHOT); snap.set_id(std::string(32,static_cast<char>('c'+i))); r.set_snapshot_storage_object(snap.SerializeAsString()); r.set_source_subvolume_id(10+i); r.set_snapshot_subvolume_id(20+i); r.set_content_digest(e.content_digest()); r.set_readonly(true); r.set_receipt_id(std::string(16, static_cast<char>('r'+i))); r.set_request_digest(digest); e.set_snapshot_receipt_id(r.receipt_id()); receipts.emplace(r.receipt_id(),r); out.push_back(e);
    }
    return out;
}
std::string manifest_bytes(const std::vector<workspace::copy::v1::CopyManifestEntry>& entries) {
    std::string out; for (const auto& e : entries) { auto b=e.SerializeAsString(); auto n=static_cast<std::uint32_t>(b.size()); out.push_back(static_cast<char>(n>>24)); out.push_back(static_cast<char>(n>>16)); out.push_back(static_cast<char>(n>>8)); out.push_back(static_cast<char>(n)); out += b; } return out;
}
std::string snapshot_receipt_bytes(const std::map<std::string,workspace::copy::v1::SnapshotReceipt>& receipts) { std::string out; for(const auto& item:receipts){ auto b=item.second.SerializeAsString(); auto n=static_cast<std::uint32_t>(b.size()); out.push_back(static_cast<char>(n>>24)); out.push_back(static_cast<char>(n>>16)); out.push_back(static_cast<char>(n>>8)); out.push_back(static_cast<char>(n)); out+=b; } return out; }
void assert_fresh_targets(const std::vector<workspace::copy::v1::CopyManifestEntry>& entries,
                          const std::map<std::string,workspace::copy::v1::SnapshotReceipt>& receipts,
                          const std::vector<workspace::v1::StorageObjectState>& targets) {
    std::set<std::string> protected_ids;
    for (const auto& entry : entries) {
        workspace::v1::StorageObject source;
        if (!source.ParseFromString(entry.source_storage_object()))
            throw std::runtime_error("fixture source identity is malformed");
        protected_ids.insert(source.id());
    }
    for (const auto& item : receipts) {
        workspace::v1::StorageObject source, snapshot;
        if (!source.ParseFromString(item.second.source_storage_object()) ||
            !snapshot.ParseFromString(item.second.snapshot_storage_object()))
            throw std::runtime_error("fixture snapshot identity is malformed");
        protected_ids.insert(source.id());
        protected_ids.insert(snapshot.id());
    }
    std::set<std::string> target_ids;
    for (const auto& target : targets) {
        const auto& id = target.object().id();
        if (!target_ids.insert(id).second || protected_ids.contains(id))
            throw std::runtime_error("fixture publication targets must be unique and fresh");
    }
}
}
int main() {
    char path[]="/tmp/spatial-copy-journal-XXXXXX"; int fd=mkstemp(path); assert(fd>=0); close(fd);
    sqlite3* db=nullptr; assert(sqlite3_open(path,&db)==SQLITE_OK); exec(db,"PRAGMA foreign_keys=ON; PRAGMA user_version=4; CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL); INSERT INTO repositories VALUES('keep','origin','/registered','ready');");
    exec(db,"BEGIN IMMEDIATE"); CopyJournal::migrate_schema(db); exec(db,"COMMIT");
    auto invalid_class=request('i'); invalid_class.add_data_class(static_cast<workspace::copy::v1::CopyDataClass>(12345)); rejects([&]{CopyJournal(db).begin(invalid_class,actor);}); auto overflow_revision=request('o'); overflow_revision.set_expected_source_authority_revision(std::numeric_limits<std::uint64_t>::max()); rejects([&]{CopyJournal(db).begin(overflow_revision,actor);});
    rejects([&]{CopyJournal(db).begin(request('x'),"actor-key-1");});
    auto r=request(); std::string bound_digest, expected_snapshot_receipts;
    {
        CopyJournal journal(db); auto first=journal.begin(r,actor); assert(first.phase==workspace::copy::v1::REQUESTED); bound_digest=first.request_digest;
        assert(journal.begin(r,actor).operation_id==first.operation_id);
        auto changed=request(); changed.set_source_workspace_id("workspace-02"); rejects([&]{journal.begin(changed,actor);}); rejects([&]{journal.begin(r,std::string(64,'b'));});
        journal.transition(r.operation_id(),actor,workspace::copy::v1::REQUESTED,workspace::copy::v1::AUTHORIZED,"auth-proof-v1");
    }
    sqlite3_close(db); db=nullptr; assert(sqlite3_open(path,&db)==SQLITE_OK);
    {
        CopyJournal journal(db); auto recovered=journal.begin(r,actor); assert(recovered.phase==workspace::copy::v1::AUTHORIZED);
        assert(journal.get(r.operation_id(),actor).request_bytes==r.SerializeAsString()); assert(journal.request(r.operation_id(),actor).SerializeAsString()==r.SerializeAsString()); rejects([&]{journal.request(r.operation_id(),std::string(64,'z'));});
        journal.transition(r.operation_id(),actor,workspace::copy::v1::AUTHORIZED,workspace::copy::v1::AUTHORIZED,"auth-proof-v1");
        rejects([&]{journal.transition(r.operation_id(),actor,workspace::copy::v1::AUTHORIZED,workspace::copy::v1::AUTHORIZED,"changed-proof");}); rejects([&]{journal.transition(r.operation_id(),actor,workspace::copy::v1::AUTHORIZED,workspace::copy::v1::VALIDATED,"bypass-validation");}); rejects([&]{journal.transition(r.operation_id(),actor,workspace::copy::v1::AUTHORIZED,workspace::copy::v1::ABORTED,"bypass-abort");});
        auto effect_id=std::string(16,'e'); journal.record_effect_intent(r.operation_id(),actor,effect_id,"snapshot",bound_digest,"registered-object-intent"); journal.record_effect_intent(r.operation_id(),actor,effect_id,"snapshot",bound_digest,"registered-object-intent"); rejects([&]{journal.record_effect_intent(r.operation_id(),actor,effect_id,"snapshot",bound_digest,"changed-intent");});
        journal.record_effect_receipt(r.operation_id(),actor,effect_id,bound_digest,"private-receipt"); journal.record_effect_receipt(r.operation_id(),actor,effect_id,bound_digest,"private-receipt"); rejects([&]{journal.record_effect_receipt(r.operation_id(),actor,effect_id,bound_digest,"changed-receipt");});
        auto effects=journal.effects(r.operation_id(),actor); assert(effects.size()==1 && !effects[0].uncertain && effects[0].receipt=="private-receipt"); rejects([&]{journal.effects(r.operation_id(),std::string(64,'z'));});
        std::map<std::string,workspace::copy::v1::SnapshotReceipt> rs; auto es=entries(rs,bound_digest); CopyJournal::validate_manifest(es,rs,1024,8); rejects([&]{CopyJournal::validate_manifest(es,rs,100,8);}); auto duplicate=es; duplicate.push_back(es.front()); rejects([&]{CopyJournal::validate_manifest(duplicate,rs,1024,8);});
        auto bad_receipts=rs; bad_receipts.erase(bad_receipts.begin()); rejects([&]{CopyJournal::validate_manifest(es,bad_receipts,1024,8);}); auto bad_schema=es; bad_schema.front().set_schema_version(2); rejects([&]{CopyJournal::validate_manifest(bad_schema,rs,1024,8);}); auto bad_identity=es; bad_identity.front().set_source_storage_object(std::string("arbitrary-path")); rejects([&]{CopyJournal::validate_manifest(bad_identity,rs,1024,8);}); auto malformed_source=es; workspace::v1::StorageObject malformed_object; assert(malformed_object.ParseFromString(malformed_source.front().source_storage_object())); malformed_object.set_id("short"); malformed_source.front().set_source_storage_object(malformed_object.SerializeAsString()); rejects([&]{CopyJournal::validate_manifest(malformed_source,rs,1024,8);});
        journal.transition(r.operation_id(),actor,workspace::copy::v1::AUTHORIZED,workspace::copy::v1::SNAPSHOTS_READY,"snapshots"); journal.transition(r.operation_id(),actor,workspace::copy::v1::SNAPSHOTS_READY,workspace::copy::v1::DESTINATION_ALLOCATED,"destination-intent"); journal.transition(r.operation_id(),actor,workspace::copy::v1::DESTINATION_ALLOCATED,workspace::copy::v1::DATA_STAGED,"staging");
        auto manifest=manifest_bytes(es); rejects([&]{journal.record_validated_result(r.operation_id(),actor,"workspace-01",manifest,es,rs,1024,8);}); journal.record_validated_result(r.operation_id(),actor,"destination-workspace",manifest,es,rs,1024,8); expected_snapshot_receipts=snapshot_receipt_bytes(rs); assert(journal.get(r.operation_id(),actor).snapshot_receipts==expected_snapshot_receipts); auto changed_receipts=rs; changed_receipts.begin()->second.GetReflection()->MutableUnknownFields(&changed_receipts.begin()->second)->AddVarint(99,1); rejects([&]{journal.record_validated_result(r.operation_id(),actor,"destination-workspace",manifest,es,changed_receipts,1024,8);});

        auto extra_request=request('k'); extra_request.clear_data_class(); extra_request.add_data_class(workspace::copy::v1::PROJECT_TREE); extra_request.add_data_class(workspace::copy::v1::GIT_HISTORY); extra_request.add_data_class(workspace::copy::v1::ENVIRONMENT_CONFIG); auto extra=journal.begin(extra_request,actor); journal.transition(extra.operation_id,actor,workspace::copy::v1::REQUESTED,workspace::copy::v1::AUTHORIZED,"auth"); journal.transition(extra.operation_id,actor,workspace::copy::v1::AUTHORIZED,workspace::copy::v1::SNAPSHOTS_READY,"snapshots"); journal.transition(extra.operation_id,actor,workspace::copy::v1::SNAPSHOTS_READY,workspace::copy::v1::DESTINATION_ALLOCATED,"destination"); journal.transition(extra.operation_id,actor,workspace::copy::v1::DESTINATION_ALLOCATED,workspace::copy::v1::DATA_STAGED,"staging"); std::map<std::string,workspace::copy::v1::SnapshotReceipt> extra_rs; auto extra_es=entries(extra_rs,extra.request_digest); rejects([&]{journal.record_validated_result(extra.operation_id,actor,"different-destination",manifest_bytes(extra_es),extra_es,extra_rs,1024,8);});
        std::vector<workspace::v1::StorageObjectState> dest;
        for (auto kind : {workspace::v1::StorageObject::PROJECT,workspace::v1::StorageObject::REPOSITORY,workspace::v1::StorageObject::APPLICATION_STATE,workspace::v1::StorageObject::APPLICATION_STATE}) { workspace::v1::StorageObjectState state; state.mutable_object()->set_kind(kind); const auto id=kind==workspace::v1::StorageObject::PROJECT?"77777777777777777777777777777777":kind==workspace::v1::StorageObject::REPOSITORY?"88888888888888888888888888888888":dest.size()==2?"99999999999999999999999999999999":"00000000000000000000000000000000"; state.mutable_object()->set_id(id); state.set_subvolume_id(30+dest.size()); state.set_quota_group(100+dest.size()); state.set_referenced_bytes(8); state.set_exclusive_bytes(4); state.set_maximum_bytes(1024); state.set_accounting_consistent(true); state.set_operation_digest(std::string(32,static_cast<char>(dest.size()+1))); dest.push_back(state); }
        assert_fresh_targets(es,rs,dest);
        auto publication_id=std::string(16,'p');
        std::vector<CopyJournal::PublicationTarget> targets;
        for (const auto& receipt : dest) targets.push_back({receipt.object(),receipt.operation_digest()});
        auto reused_source=targets; reused_source.front().object.set_id("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        rejects_with([&]{journal.record_publication_intent(r.operation_id(),actor,std::string(16,'v'),reused_source);},
                     "duplicate or reused publication target identity");
        auto reused_snapshot=targets; reused_snapshot.front().object.set_id("cccccccccccccccccccccccccccccccc");
        rejects_with([&]{journal.record_publication_intent(r.operation_id(),actor,std::string(16,'w'),reused_snapshot);},
                     "duplicate or reused publication target identity");
        journal.record_publication_intent(r.operation_id(),actor,publication_id,targets); rejects([&]{journal.record_effect_receipt(r.operation_id(),actor,publication_id,bound_digest,"forged-publication");}); rejects([&]{journal.transition(r.operation_id(),actor,workspace::copy::v1::VALIDATED,workspace::copy::v1::PUBLISHED,"forged-publication");}); rejects([&]{journal.record_effect_intent(r.operation_id(),actor,std::string(16,'u'),"publication",bound_digest,"untyped");}); auto bad_source_dest=dest; bad_source_dest.front().mutable_object()->set_id("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"); rejects([&]{journal.record_publication_receipts(r.operation_id(),actor,publication_id,bad_source_dest);}); auto bad_snapshot_dest=dest; bad_snapshot_dest.front().mutable_object()->set_id(std::string(32,'c')); rejects([&]{journal.record_publication_receipts(r.operation_id(),actor,publication_id,bad_snapshot_dest);}); auto bad_subvolume_dest=dest; bad_subvolume_dest.front().set_subvolume_id(10); rejects([&]{journal.record_publication_receipts(r.operation_id(),actor,publication_id,bad_subvolume_dest);}); auto wrong_digest_dest=dest; wrong_digest_dest.front().set_operation_digest(std::string(32,'z')); rejects([&]{journal.record_publication_receipts(r.operation_id(),actor,publication_id,wrong_digest_dest);}); auto publication_proof=journal.record_publication_receipts(r.operation_id(),actor,publication_id,dest); assert(journal.record_publication_receipts(r.operation_id(),actor,publication_id,dest)==publication_proof);
        journal.transition(r.operation_id(),actor,workspace::copy::v1::VALIDATED,workspace::copy::v1::PUBLISHED,publication_proof);
        rejects([&]{journal.transition(r.operation_id(),actor,workspace::copy::v1::PUBLISHED,workspace::copy::v1::ABORTED,"late");});
        auto cancelled=request('c'); journal.begin(cancelled,actor); journal.abort(cancelled.operation_id(),actor,workspace::copy::v1::COPY_FORBIDDEN,"denied"); assert(journal.get(cancelled.operation_id(),actor).phase==workspace::copy::v1::ABORTED && journal.get(cancelled.operation_id(),actor).error==workspace::copy::v1::COPY_FORBIDDEN); journal.abort(cancelled.operation_id(),actor,workspace::copy::v1::COPY_FORBIDDEN,"denied"); rejects([&]{journal.abort(cancelled.operation_id(),actor,workspace::copy::v1::COPY_FORBIDDEN,"different");}); rejects([&]{journal.abort(cancelled.operation_id(),actor,workspace::copy::v1::COPY_UNAVAILABLE,"denied");});
        auto pending=request('b'); journal.begin(pending,actor); journal.transition(pending.operation_id(),actor,workspace::copy::v1::REQUESTED,workspace::copy::v1::AUTHORIZED,"auth"); auto p=journal.get(pending.operation_id(),actor);
        journal.record_effect_intent(pending.operation_id(),actor,std::string(16,'z'),"stage",p.request_digest,"external-effect");
    }
    sqlite3_close(db); db=nullptr; assert(sqlite3_open(path,&db)==SQLITE_OK);
    { CopyJournal journal(db); assert(journal.get(r.operation_id(),actor).snapshot_receipts==expected_snapshot_receipts); auto pending=request('b'); assert(journal.get(pending.operation_id(),actor).phase==workspace::copy::v1::AUTHORIZED); auto pending_effects=journal.effects(pending.operation_id(),actor); assert(pending_effects.size()==1 && pending_effects[0].uncertain); journal.recover_unresolved_effects(pending.operation_id(),actor); assert(journal.get(pending.operation_id(),actor).phase==workspace::copy::v1::UNCERTAIN); journal.recover_unresolved_effects(pending.operation_id(),actor); rejects([&]{journal.abort(pending.operation_id(),actor,workspace::copy::v1::COPY_UNAVAILABLE,"abort");}); }
    sqlite3_close(db); unlink(path);
    sqlite3* outside=nullptr; assert(sqlite3_open(":memory:",&outside)==SQLITE_OK); rejects([&]{CopyJournal::migrate_schema(outside);}); sqlite3_close(outside);
    sqlite3* rollback=nullptr; assert(sqlite3_open(":memory:",&rollback)==SQLITE_OK); exec(rollback,"PRAGMA user_version=4; CREATE TABLE copy_effects(bad TEXT);"); exec(rollback,"BEGIN IMMEDIATE"); rejects([&]{CopyJournal::migrate_schema(rollback);}); exec(rollback,"ROLLBACK"); sqlite3_stmt* q=nullptr; assert(sqlite3_prepare_v2(rollback,"PRAGMA user_version",-1,&q,nullptr)==SQLITE_OK); assert(sqlite3_step(q)==SQLITE_ROW && sqlite3_column_int(q,0)==4); sqlite3_finalize(q); sqlite3_close(rollback);
    sqlite3* limited=nullptr; assert(sqlite3_open(":memory:",&limited)==SQLITE_OK); exec(limited,"PRAGMA user_version=4; BEGIN IMMEDIATE"); CopyJournal::migrate_schema(limited); exec(limited,"COMMIT");
    { CopyJournal journal(limited, CopyJournal::Limits{1,1}); auto only=request('l'); const std::string opaque_principal=std::string(32,'\0')+std::string(32,static_cast<char>(0xff)); auto first=journal.begin(only,opaque_principal); assert(journal.begin(only,opaque_principal).operation_id==first.operation_id); exhausted([&]{journal.begin(request('m'),opaque_principal);}); journal.transition(only.operation_id(),opaque_principal,workspace::copy::v1::REQUESTED,workspace::copy::v1::AUTHORIZED,"limit-authorized");
      const auto effect=std::string(16,'q'); journal.record_effect_intent(only.operation_id(),opaque_principal,effect,"stage",first.request_digest,"bounded-intent"); journal.record_effect_intent(only.operation_id(),opaque_principal,effect,"stage",first.request_digest,"bounded-intent"); exhausted([&]{journal.record_effect_intent(only.operation_id(),opaque_principal,std::string(16,'w'),"stage",first.request_digest,"over-limit");}); journal.record_effect_receipt(only.operation_id(),opaque_principal,effect,first.request_digest,"bounded-receipt"); journal.record_effect_receipt(only.operation_id(),opaque_principal,effect,first.request_digest,"bounded-receipt"); assert(journal.effects(only.operation_id(),opaque_principal).front().receipt=="bounded-receipt");
      sqlite3_stmt* count=nullptr; assert(sqlite3_prepare_v2(limited,"SELECT (SELECT count(*) FROM copy_operations),(SELECT count(*) FROM copy_effects)",-1,&count,nullptr)==SQLITE_OK); assert(sqlite3_step(count)==SQLITE_ROW && sqlite3_column_int(count,0)==1 && sqlite3_column_int(count,1)==1); sqlite3_finalize(count); }
    sqlite3_close(limited);
    std::cout << "copy journal restart, conflicts, effect receipts, uncertainty, manifest, publication and migration checks passed\n";
}
