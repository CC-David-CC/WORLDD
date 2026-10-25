#include "world/store.hpp"
#include "world/surfaces.hpp"
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <unistd.h>
using namespace spatial;
namespace {
void check(bool value,const char* message) {if(!value)throw std::runtime_error(message);}
template<class F> void error(wire::ErrorCode code,F function) {
    try {function();}catch(const WorldError& e) {check(e.code==code,"Wrong application slot rejection");return;}
    throw std::runtime_error("Application slot request unexpectedly succeeded");
}
template<class F> void invalid(F function) {
    try {function();}catch(const std::invalid_argument&) {return;}
    throw std::runtime_error("Invalid application slot request unexpectedly succeeded");
}
wire::Entity slot(const std::string& room,const char* id="slot-one") {
    wire::Entity e;e.set_id(id);e.set_kind(wire::SURFACE_SLOT);e.set_label("Application");e.set_room(room);
    e.mutable_pose()->mutable_position()->set_z(-2);e.mutable_pose()->mutable_orientation()->set_w(1);
    e.mutable_size()->set_x(1.2);e.mutable_size()->set_y(.8);auto* b=e.mutable_application_slot();
    b->set_workspace("workspace-one");b->set_application("application-one");b->set_server_id(std::string(32,'s'));
    b->set_surface_id("producer-one-1");return e;
}
wire::NativeSnapshot native(const char* id="producer-one-1",unsigned generation=1) {
    wire::NativeSnapshot snapshot;auto* s=snapshot.add_surfaces();s->mutable_handle()->set_id(id);
    s->mutable_handle()->set_generation(generation);s->set_xid(42);s->set_mapped(true);
    s->mutable_pixels()->set_width(120);s->mutable_pixels()->set_height(80);
    auto* a=s->mutable_application_identity();a->set_workspace("workspace-one");a->set_application("application-one");
    a->set_server_id(std::string(32,'s'));a->set_connection_id(1);return snapshot;
}
}
int main() {
    char path[]="/tmp/spatial-application-slots-XXXXXX";auto* root=mkdtemp(path);if(!root)return 1;
    const std::filesystem::path directory(root);
    try {
        const std::string operation(16,'o'),digest(32,'d'),principal(64,'p');wire::Entity created;
        {
            WorldStore store(directory);const auto entry=store.info().entry_room();store.workspace_available("workspace-one",true);
            const auto revision=store.info().world_revision();const auto requested=slot(entry);
            check(!store.application_slot_operation(requested,operation,digest,principal),
                  "Unknown placement operation produced a replay receipt");
            invalid([&]{store.create(requested);});check(store.info().world_revision()==revision,"Unchecked slot changed the world");
            std::cout<<"PASS slot-creation-requires-checked-boundary\n";
            error(wire::REVISION_CONFLICT,[&]{store.create_application_slot(requested,revision+1,operation,digest,principal);});
            check(store.snapshot().entities_size()==1 && store.info().world_revision()==revision,"Stale CAS committed geometry");
            std::cout<<"PASS slot-stale-revision-has-no-effects\n";
            created=store.create_application_slot(requested,revision,operation,digest,principal);
            check(created.revision()==1 && created.world_revision()==revision+1,"Slot did not commit once");
            auto renamed=requested;renamed.set_id("unused-new-entropy");
            const auto replay=store.create_application_slot(renamed,revision,operation,digest,principal);
            check(replay.SerializeAsString()==created.SerializeAsString() && store.info().world_revision()==revision+1,"Replay duplicated placement");
            const auto preflight=store.application_slot_operation(requested,operation,digest,principal);
            check(preflight && preflight->SerializeAsString()==created.SerializeAsString(),
                  "Checked replay preflight lost the exact committed slot");
            error(wire::REVISION_CONFLICT,[&]{store.application_slot_operation(requested,operation,std::string(32,'e'),principal);});
            std::cout<<"PASS slot-atomic-receipt-and-exact-replay\n";
            error(wire::REVISION_CONFLICT,[&]{store.create_application_slot(requested,revision,operation,std::string(32,'e'),principal);});
            error(wire::REVISION_CONFLICT,[&]{store.create_application_slot(requested,revision,operation,digest,std::string(64,'q'));});
            auto other=requested;other.mutable_application_slot()->set_application("application-two");
            error(wire::REVISION_CONFLICT,[&]{store.create_application_slot(other,revision,operation,digest,principal);});
            std::cout<<"PASS slot-operation-cannot-change-principal-digest-or-binding\n";
            error(wire::BUSY,[&]{store.create_application_slot(renamed,store.info().world_revision(),std::string(16,'b'),digest,principal);});
            wire::WorldUpdateRequest update;update.set_id(created.id());update.set_expected_revision(created.revision());
            update.mutable_patch()->mutable_application_slot()->CopyFrom(created.application_slot());
            invalid([&]{store.update(update);});
            std::cout<<"PASS slot-binding-is-unique-and-immutable\n";
        }
        {
            WorldStore store(directory);Surfaces surfaces(store);surfaces.refresh(native());const auto first=surfaces.get("producer-one-1");
            check(first.slot()==created.id() && first.room()==created.room() && first.pose().SerializeAsString()==created.pose().SerializeAsString(),"Restart lost exact placement");
            surfaces.refresh(native("producer-one-1",2));check(surfaces.get("producer-one-1").slot()==created.id(),"Resize lost application identity");
            wire::SurfaceValidateRequest old;old.mutable_handle()->set_id("producer-one-1");old.mutable_handle()->set_generation(1);
            error(wire::STALE_TARGET,[&]{surfaces.validate_handle(old);});
            std::cout<<"PASS slot-restart-and-resize-preserve-identity-not-old-generation\n";
            surfaces.refresh(native("producer-two-1"));auto replaced=surfaces.get("producer-two-1");
            check(replaced.room().empty() && !replaced.has_slot(),"Reused XID adopted a different compositor surface");
            auto changed=native();changed.mutable_surfaces(0)->mutable_application_identity()->set_server_id(std::string(32,'t'));
            surfaces.refresh(changed);check(surfaces.get("producer-one-1").room().empty(),"Replacement server adopted stale placement");
            changed=native();changed.mutable_surfaces(0)->mutable_application_identity()->set_application("application-two");
            surfaces.refresh(changed);check(surfaces.get("producer-one-1").room().empty(),"Different application adopted stale placement");
            std::cout<<"PASS slot-replaced-native-identities-stay-unbound\n";
            auto correct=native();surfaces.refresh(correct);wire::WorldDeleteRequest remove;remove.set_id(created.id());remove.set_expected_revision(created.revision());store.erase(remove);surfaces.forget_slot(created.id());
            error(wire::STALE_TARGET,[&]{store.create_application_slot(slot(created.room()),store.info().world_revision(),operation,digest,principal);});
            error(wire::STALE_TARGET,[&]{store.application_slot_operation(slot(created.room()),operation,digest,principal);});
            check(store.snapshot().entities_size()==1,"Deleted placement was resurrected");surfaces.refresh(correct);check(surfaces.get("producer-one-1").room().empty(),"Deleted placement retained native attachment");
            std::cout<<"PASS slot-deletion-retains-operation-tombstone\n";
        }
        {
            WorldStore store(directory/"fault",false,true);store.workspace_available("workspace-one",true);const auto revision=store.info().world_revision();
            wire::FaultArmRequest fault;fault.set_point(wire::FAULT_STORAGE_NEXT_WRITE);fault.set_action(wire::FAULT_ERROR);fault.set_storage_error(wire::STORAGE_FAULT_ENOSPC);store.arm_fault(fault);
            error(wire::STORAGE_ERROR,[&]{store.create_application_slot(slot(store.info().entry_room()),revision,operation,digest,principal);});
            check(store.info().world_revision()==revision && store.snapshot().entities_size()==1,"Failed transaction committed a slot");
            const auto retry=store.create_application_slot(slot(store.info().entry_room()),revision,operation,digest,principal);check(retry.revision()==1,"Failed transaction retained a false operation receipt");
            std::cout<<"PASS slot-storage-failure-rolls-back-geometry-and-receipt\n";
        }
        std::filesystem::remove_all(directory);return 0;
    }catch(const std::exception& error) {std::cerr<<error.what()<<'\n';std::cerr<<"Preserved slot failure state: "<<directory<<'\n';return 1;}
}
