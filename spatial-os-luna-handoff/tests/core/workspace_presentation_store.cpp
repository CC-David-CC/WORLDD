#include "world/store.hpp"
#include "world/workspace_presentations.hpp"
#include <filesystem>
#include <iostream>
#include <cstdlib>
#include <vector>
#include <stdexcept>
#include <unistd.h>
#include <sqlite3.h>
#include <openssl/evp.h>

using namespace spatial;
namespace {
void expect(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
wire::Pose pose(double x) {
    wire::Pose value; value.mutable_position()->set_x(x); value.mutable_orientation()->set_w(1); return value;
}
wire::Entity room(const char* id) {
    wire::Entity value; value.set_id(id); value.set_kind(wire::ROOM); value.set_label(id); return value;
}
std::string binary_id(unsigned char seed) {
    std::string value(16, '\0');
    for (std::size_t i=0;i<value.size();++i) value[i]=static_cast<char>(seed+i);
    value[3]='\0'; return value;
}
std::string digest_bytes(unsigned char seed) {
    const std::string payload="presentation-request-"+std::to_string(seed);
    std::string digest(32, '\0'); unsigned int length=0;
    expect(EVP_Digest(payload.data(),payload.size(),reinterpret_cast<unsigned char*>(digest.data()),&length,EVP_sha256(),nullptr)==1 && length==32,"compute SHA-256 test request digest");
    return digest;
}
std::string principal_bytes(unsigned char seed) {
    std::string value(64, '\0');
    for (std::size_t i=0;i<value.size();++i) value[i]=static_cast<char>(seed+i);
    value[31]='\0'; value[57]='\0'; return value;
}
std::string temp_state() {
    auto base=std::filesystem::temp_directory_path()/"spatial-presentation-store-XXXXXX";
    auto name=base.string(); std::vector<char> bytes(name.begin(),name.end()); bytes.push_back('\0');
    auto* created=mkdtemp(bytes.data()); if(!created) throw std::runtime_error("mkdtemp failed"); return created;
}
}
int main() {
    const auto rep_a=binary_id(1), rep_b=binary_id(2), rep_quota=binary_id(3);
    const auto directory=temp_state();
    try {
        wire::WorldInfoRequest digest_request;
        const auto computed_digest=request_digest(digest_request);
        const auto serialized_request=digest_request.SerializeAsString();
        std::string expected_digest(32,'\0'); unsigned int expected_length=0;
        expect(EVP_Digest(serialized_request.data(),serialized_request.size(),reinterpret_cast<unsigned char*>(expected_digest.data()),&expected_length,EVP_sha256(),nullptr)==1 && expected_length==32,"compute expected protobuf request digest");
        expect(computed_digest==expected_digest && computed_digest.size()==32,"request digest is the exact raw SHA-256 of serialized protobuf bytes");
        {
            WorldStore store(directory);
            const auto create_a=binary_id(11), create_b=binary_id(12), move_a=binary_id(13), move_stale=binary_id(14), create_fail=binary_id(15), remove_a=binary_id(16), quota_op=binary_id(17);
            const auto request_a=digest_bytes(31), request_b=digest_bytes(32), request_move=digest_bytes(33), request_stale=digest_bytes(34), request_fail=digest_bytes(35), request_remove=digest_bytes(36), request_quota=digest_bytes(37);
            const auto principal=principal_bytes(41), other_principal=principal_bytes(42);
            store.create(room("meeting")); store.create(room("office"));
            store.workspace_available("shared-workspace", true);
            wire::Entity actor; actor.set_id("viewer"); actor.set_kind(wire::ACTOR); actor.set_room("office"); actor.set_label("viewer"); *actor.mutable_pose()=pose(1.25); store.create_actor(actor); store.select_workspace("viewer","shared-workspace");
            WorkspacePresentation a; a.id=rep_a; a.workspace_id="shared-workspace"; a.room_id=store.info().entry_room(); a.pose=pose(1);
            auto created=store.create_presentation(a,store.info().world_revision(),create_a,request_a,principal);
            expect(created.generation==1,"create starts at generation one");
            auto replay=store.create_presentation(a,0,create_a,request_a,principal);
            expect(replay.id==created.id && replay.pose.SerializeAsString()==created.pose.SerializeAsString(),"create replay returns original receipt");
            bool other_actor_replay=false; try { store.create_presentation(a,0,create_a,request_a,other_principal); } catch(const WorldError& e) { other_actor_replay=e.code==wire::REVISION_CONFLICT; }
            expect(other_actor_replay,"operation IDs are bound to the authenticated principal");
            bool conflict=false;try { store.create_presentation(a,0,create_a,digest_bytes(99),principal); } catch(const WorldError& e) { conflict=e.code==wire::REVISION_CONFLICT; }
            expect(conflict,"changed create replay conflicts");
            sqlite3* receipt_db=nullptr;
            expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&receipt_db)==SQLITE_OK,"open receipt validation witness");
            sqlite3_stmt* receipt_query=nullptr;
            expect(sqlite3_prepare_v2(receipt_db,"SELECT result FROM workspace_presentation_ops WHERE operation_id=lower(hex(?1))",-1,&receipt_query,nullptr)==SQLITE_OK,"prepare create receipt read");
            sqlite3_bind_blob(receipt_query,1,create_a.data(),static_cast<int>(create_a.size()),SQLITE_TRANSIENT);
            expect(sqlite3_step(receipt_query)==SQLITE_ROW,"read valid create receipt");
            const auto* receipt_blob=static_cast<const char*>(sqlite3_column_blob(receipt_query,0));
            const auto receipt_size=sqlite3_column_bytes(receipt_query,0);
            std::string malformed_receipt(receipt_blob,static_cast<std::size_t>(receipt_size));
            sqlite3_finalize(receipt_query);
            expect(malformed_receipt.size()>20 && malformed_receipt.compare(0,4,"WPR1")==0 && malformed_receipt[12]=='\0',"create receipt has canonical nonremoved marker");
            malformed_receipt[12]='\1';
            sqlite3_stmt* receipt_update=nullptr;
            expect(sqlite3_prepare_v2(receipt_db,"UPDATE workspace_presentation_ops SET result=?1 WHERE operation_id=lower(hex(?2))",-1,&receipt_update,nullptr)==SQLITE_OK,"prepare malformed receipt update");
            sqlite3_bind_blob(receipt_update,1,malformed_receipt.data(),static_cast<int>(malformed_receipt.size()),SQLITE_TRANSIENT);
            sqlite3_bind_blob(receipt_update,2,create_a.data(),static_cast<int>(create_a.size()),SQLITE_TRANSIENT);
            expect(sqlite3_step(receipt_update)==SQLITE_DONE && sqlite3_changes(receipt_db)==1,"corrupt only the create removed marker");
            sqlite3_finalize(receipt_update); sqlite3_close(receipt_db);
            bool malformed_rejected=false;
            try { store.presentation_operation(create_a,request_a,principal,WorkspacePresentationOperation::create); }
            catch(const WorldError& e) { malformed_rejected=e.code==wire::STORAGE_ERROR; }
            expect(malformed_rejected,"create decoder rejects tombstone marker under the wrong operation kind");
            a.id=rep_b; a.room_id="office"; a.pose=pose(2);
            auto second=store.create_presentation(a,store.info().world_revision(),create_b,request_b,principal);
            expect(second.workspace_id==created.workspace_id && store.presentations().size()==2,"multiple refs preserve one workspace identity");
            auto moved=store.move_presentation(rep_a,"office",pose(3),1,move_a,request_move,principal);
            expect(moved.generation==2 && moved.room_id=="office","move advances generation and placement");
            wire::WorldDeleteRequest delete_room; delete_room.set_id("office"); delete_room.set_expected_revision(store.get("office").revision());
            bool room_in_use=false; try { store.erase(delete_room); } catch(const WorldError& e) { room_in_use=e.code==wire::IN_USE; }
            expect(room_in_use && store.get("office").kind()==wire::ROOM,"room deletion is blocked while a presentation refers to it");
            auto move_replay=store.move_presentation(rep_a,"meeting",pose(7),1,move_a,request_move,principal);
            expect(move_replay.generation==2 && move_replay.room_id=="office","move replay returns committed result");
            bool stale=false;try { store.move_presentation(rep_a,"meeting",pose(7),1,move_stale,request_stale,principal); } catch(const WorldError& e) { stale=e.code==wire::REVISION_CONFLICT; }
            expect(stale,"stale generation is rejected");
            auto visible=visible_presentations(store,"office",[&rep_a](const auto&,const auto& id){return id==rep_a;},[](const auto&){return false;},[](const auto&,const auto&){return false;});
            expect(visible.size()==1 && visible.front().locked && visible.front().representation_id==rep_a,"scoped view omits unauthorized geometry and retains locked permitted geometry");
            auto hidden=visible_presentations(store,"office",[](const auto&,const auto&){return false;},[](const auto&){return true;},[](const auto&,const auto&){return true;});
            expect(hidden.empty(),"denied world observation omits every presentation");
            auto all_visible=visible_presentations(store,"office",[](const auto&,const auto&){return true;},[](const auto&){return true;},[](const auto&,const auto&){return true;});
            expect(all_visible.size()==2,"authorized world observation includes both same-room representations");
            auto uses=visible_presentations(store,"office",[](const auto&,const auto&){return true;},[](const auto& workspace){return workspace=="shared-workspace";},[&rep_a](const auto& id,const auto& workspace){return id==rep_a && workspace=="shared-workspace";});
            expect(uses.size()==2 && !uses.front().locked && uses.front().usable && !uses.back().usable,"workspace use is freshly checked per representation");
            const auto before_failed_create=store.info().world_revision();
            sqlite3* fault_db=nullptr; expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&fault_db)==SQLITE_OK,"open database fault witness");
            char* trigger_error=nullptr; const auto trigger_rc=sqlite3_exec(fault_db,"CREATE TRIGGER fail_representation BEFORE INSERT ON workspace_presentations BEGIN SELECT RAISE(FAIL,'presentation witness'); END",nullptr,nullptr,&trigger_error);
            const std::string trigger_message=trigger_error?trigger_error:""; sqlite3_free(trigger_error); sqlite3_close(fault_db); expect(trigger_rc==SQLITE_OK,trigger_message.c_str());
            WorkspacePresentation failed; failed.id=binary_id(250); failed.workspace_id="shared-workspace"; failed.room_id="office"; failed.pose=pose(4);
            bool storage_failed=false; try { store.create_presentation(failed,before_failed_create,create_fail,request_fail,principal); } catch(const WorldError& e) { storage_failed=e.code==wire::STORAGE_ERROR; }
            expect(storage_failed,"injected database failure is reported");
            expect(store.info().world_revision()==before_failed_create && store.presentations().size()==2,"failed storage mutation rolls back row and world revision");
            expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&fault_db)==SQLITE_OK,"reopen database fault witness");
            trigger_error=nullptr; const auto drop_rc=sqlite3_exec(fault_db,"DROP TRIGGER fail_representation",nullptr,nullptr,&trigger_error);
            const std::string drop_message=trigger_error?trigger_error:""; sqlite3_free(trigger_error); sqlite3_close(fault_db); expect(drop_rc==SQLITE_OK,drop_message.c_str());
            // Fill only the presentation table to its subsystem ceiling, then verify controlled rejection before commit.
            expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&fault_db)==SQLITE_OK,"open presentation quota witness");
            trigger_error=nullptr;
            sqlite3_stmt* quota_insert=nullptr;
            expect(sqlite3_prepare_v2(fault_db,"INSERT INTO workspace_presentations(id,workspace_id,room_id,pose,generation) VALUES(?1,'shared-workspace','office',?2,1)",-1,&quota_insert,nullptr)==SQLITE_OK,"prepare legal quota rows");
            const auto legal_pose=pose(0).SerializeAsString();
            trigger_error=nullptr;
            const auto quota_begin=sqlite3_exec(fault_db,"BEGIN IMMEDIATE",nullptr,nullptr,&trigger_error);
            const std::string quota_begin_message=trigger_error?trigger_error:""; sqlite3_free(trigger_error);
            expect(quota_begin==SQLITE_OK,quota_begin_message.c_str());
            for(int i=0;i<4094;++i){
                std::string key(32,'0'); key[0]='f'; key[1]='e'; auto n=static_cast<unsigned>(i+1);
                constexpr char hex[]="0123456789abcdef"; for(int j=31;j>=2;--j){key[j]=hex[n&15];n>>=4;}
                sqlite3_bind_text(quota_insert,1,key.data(),static_cast<int>(key.size()),SQLITE_TRANSIENT);
                sqlite3_bind_blob(quota_insert,2,legal_pose.data(),static_cast<int>(legal_pose.size()),SQLITE_TRANSIENT);
                const auto row_rc=sqlite3_step(quota_insert); expect(row_rc==SQLITE_DONE,"seed legal presentation quota row"); sqlite3_reset(quota_insert); sqlite3_clear_bindings(quota_insert);
            }
            sqlite3_finalize(quota_insert);
            trigger_error=nullptr;
            const auto quota_commit=sqlite3_exec(fault_db,"COMMIT",nullptr,nullptr,&trigger_error);
            const std::string quota_commit_message=trigger_error?trigger_error:""; sqlite3_free(trigger_error);
            expect(quota_commit==SQLITE_OK,quota_commit_message.c_str());
            sqlite3_close(fault_db);
            const auto quota_revision=store.info().world_revision();
            WorkspacePresentation over_quota; over_quota.id=rep_quota; over_quota.workspace_id="shared-workspace"; over_quota.room_id="office"; over_quota.pose=pose(9);
            bool quota_rejected=false; try { store.create_presentation(over_quota,quota_revision,quota_op,request_quota,principal); } catch(const WorldError& e) { quota_rejected=e.code==wire::RESOURCE_EXHAUSTED; }
            expect(quota_rejected && store.info().world_revision()==quota_revision,"presentation quota rejects before mutation commit");
            expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&fault_db)==SQLITE_OK,"reopen presentation quota witness");
            trigger_error=nullptr; const auto clear_quota=sqlite3_exec(fault_db,"DELETE FROM workspace_presentations WHERE id LIKE 'fe%'",nullptr,nullptr,&trigger_error);
            const std::string clear_message=trigger_error?trigger_error:""; sqlite3_free(trigger_error); sqlite3_close(fault_db); expect(clear_quota==SQLITE_OK,clear_message.c_str());
            store.remove_presentation(rep_a,2,remove_a,request_remove,principal);
            const auto remove_receipt=store.presentation_operation(remove_a,request_remove,principal,WorkspacePresentationOperation::remove);
            expect(remove_receipt && remove_receipt->removed && remove_receipt->source_room_id=="office" &&
                   remove_receipt->presentation.id==rep_a,"remove replay preserves the committed representation scope");
            store.remove_presentation(rep_a,0,remove_a,request_remove,principal);
            expect(store.presentations().size()==1 && store.presentation(rep_b).workspace_id=="shared-workspace","removal affects one reference only");
            expect(store.workspace_available("shared-workspace"),"presentation mutations preserve availability");
            expect(store.selected_workspace("viewer")=="shared-workspace","presentation mutations preserve actor selection");
        }
        {
            WorldStore restored(directory);
            expect(restored.presentations().size()==1,"reference survives restart");
            auto remaining=restored.presentation(rep_b);
            expect(remaining.room_id=="office" && remaining.generation==1 && remaining.pose.position().x()==2,"recovery preserves ID, transform, and generation");
            expect(restored.workspace_available("shared-workspace"),"restart preserves independent availability");
            expect(restored.selected_workspace("viewer")=="shared-workspace","restart preserves independent selection");
        }
        std::string world_id;
        { WorldStore restored(directory); world_id=restored.info().world_id(); }
        sqlite3* db=nullptr; expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&db)==SQLITE_OK,"open migration witness database");
        char* error=nullptr; const auto rc=sqlite3_exec(db,"DROP TABLE workspace_presentation_ops; DROP TABLE workspace_presentations; PRAGMA user_version=2",nullptr,nullptr,&error);
        const std::string migration_error=error?error:""; sqlite3_free(error); sqlite3_close(db); expect(rc==SQLITE_OK,migration_error.c_str());
        bool explicit_required=false;
        try { WorldStore unapproved(directory); } catch(const WorldError& e) { explicit_required=e.code==wire::STORAGE_ERROR; }
        expect(explicit_required,"v2 state refuses an implicit schema upgrade");
        { WorldStore migrated(directory,false,false,true); expect(migrated.info().world_id()==world_id,"explicit v2 migration preserves world identity"); }
        { WorldStore migrated_state(directory); const auto migrated_actor=migrated_state.get("viewer"); expect(migrated_actor.room()=="office" && migrated_actor.pose().position().x()==1.25,"v2 migration preserves actor room and pose"); expect(migrated_state.selected_workspace("viewer")=="shared-workspace","v2 migration preserves per-world workspace selection"); }
        expect(sqlite3_open((std::filesystem::path(directory)/"world.db").c_str(),&db)==SQLITE_OK,"reopen migrated state");
        sqlite3_stmt* version=nullptr; expect(sqlite3_prepare_v2(db,"PRAGMA user_version",-1,&version,nullptr)==SQLITE_OK,"read migrated schema version");
        expect(sqlite3_step(version)==SQLITE_ROW && sqlite3_column_int(version,0)==3,"explicit migration advances schema to v3");
        sqlite3_finalize(version); sqlite3_close(db);
    } catch(const std::exception& error) {
        std::filesystem::remove_all(directory); std::cerr << error.what() << '\n'; return 1;
    }
    std::filesystem::remove_all(directory); return 0;
}
