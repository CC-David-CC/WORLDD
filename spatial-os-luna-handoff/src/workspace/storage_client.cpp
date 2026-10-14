#include "workspace/storage_client.hpp"
#include "workspace/copy_collections.hpp"
#include "workspace/storage_channel.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include "world/store.hpp"
#include <set>
#include <map>
#include <array>
#include <openssl/evp.h>
#include <fcntl.h>
#include <linux/btrfs.h>
#include <linux/btrfs_tree.h>
#include <linux/magic.h>
#include <sys/ioctl.h>
#include <sys/statfs.h>
#include <sys/stat.h>

namespace spatial {
namespace {
namespace pb = workspace::v1;
void require(bool condition, const char* message) { if (!condition) throw std::invalid_argument(message); }
[[noreturn]] void unavailable() {
    throw WorldError(wire::UNAVAILABLE, "Storage owner unavailable; inspect pending effects before retrying");
}
}
struct StorageClient::Impl {
    std::filesystem::path volume;
    identity::tls_detail::Fd volume_root;
    std::string filesystem_id;
    std::unique_ptr<StorageChannel> channel;
    std::uint64_t sequence = 0;
    bool exchanging = false;
    std::function<void()> observer;
    Impl(int fd, const std::filesystem::path& volume_path) {
        if (!volume_path.is_absolute()) unavailable();
        volume = std::filesystem::canonical(volume_path);
        channel = std::make_unique<StorageChannel>(fd, 0, std::nullopt);
        identity::tls_detail::Fd root(open(volume.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        struct statfs filesystem{}; struct stat directory{}; btrfs_ioctl_fs_info_args info{}; btrfs_ioctl_get_subvol_info_args subvolume{};
        if (!volume.is_absolute() || root.value < 0 || fstatfs(root.value, &filesystem) < 0 ||
            filesystem.f_type != BTRFS_SUPER_MAGIC || fstat(root.value, &directory) < 0 || directory.st_ino != 256 ||
            ioctl(root.value, BTRFS_IOC_GET_SUBVOL_INFO, &subvolume) < 0 || subvolume.treeid != 5 ||
            ioctl(root.value, BTRFS_IOC_FS_INFO, &info) < 0) unavailable();
        volume_root.reset(fcntl(root.value,F_DUPFD_CLOEXEC,3));
        if(volume_root.value<0) unavailable();
        filesystem_id=std::string(reinterpret_cast<char*>(info.fsid),BTRFS_FSID_SIZE);
        pb::StorageOwnerRequest request; request.mutable_hello();
        const auto reply = exchange(std::move(request), {});
        if (!reply.has_identity() || reply.identity().filesystem_id() !=
            std::string(reinterpret_cast<char*>(info.fsid), BTRFS_FSID_SIZE) ||
            reply.identity().process_id() != static_cast<unsigned>(channel->peer_pid()) ||
            !reply.identity().accounting_consistent()) unavailable();
    }
    pb::StorageOwnerReply exchange(pb::StorageOwnerRequest request, const std::function<void()>& tick) {
        if (exchanging) throw WorldError(wire::BUSY,"Storage owner exchange is already pending");
        struct ExchangeGuard { bool& active; explicit ExchangeGuard(bool& value):active(value){active=true;} ~ExchangeGuard(){active=false;} } guard(exchanging);
        if (!channel || sequence == UINT64_MAX) unavailable();
        request.set_protocol_version(1); request.set_request_id(++sequence);
        validate_message(request);
        try {
            const auto deadline = StorageChannel::Clock::now()+std::chrono::seconds(5);
            const auto progress = [&] { if (observer) observer(); if (tick) tick(); };
            channel->send(request.SerializeAsString(), deadline, progress);
            const auto frame = channel->receive(deadline, false, progress);
            pb::StorageOwnerReply reply;
            if (!frame || !reply.ParseFromString(*frame)) unavailable();
            validate_message(reply);
            if (reply.protocol_version() != 1 || reply.request_id() != sequence ||
                reply.outcome_case() == pb::StorageOwnerReply::OUTCOME_NOT_SET) unavailable();
            return reply;
        } catch (...) { channel.reset(); throw; }
    }
};
StorageClient::StorageClient(int descriptor, const std::filesystem::path& volume) {
    try { impl_ = std::make_unique<Impl>(descriptor, volume); }
    catch (const WorldError&) { throw; }
    catch (...) { unavailable(); }
}
StorageClient::~StorageClient() = default;
void StorageClient::set_observer(std::function<void()> observer) { impl_->observer = std::move(observer); }
void StorageClient::check_alive() const {
    try { if (!impl_->channel) unavailable(); impl_->channel->check_alive(); }
    catch (...) { unavailable(); }
}
void StorageClient::fence_unavailable() noexcept { if (impl_) impl_->channel.reset(); }
workspace::v1::StorageObjectState StorageClient::call(pb::StorageOwnerRequest request, const std::function<void()>& tick) {
    pb::StorageObject expected;
    if (request.has_repository_prepare()) expected = request.repository_prepare().object();
    else if (request.has_project_prepare()) expected = request.project_prepare().object();
    else if (request.has_workspace_bind()) expected = request.workspace_bind().project();
    else if (request.has_application_prepare()) expected = request.application_prepare().object();
    else if (request.has_retirement_prepare()) expected = request.retirement_prepare().object();
    else if (request.has_inspect()) expected = request.inspect().object();
    else if (request.has_snapshot_prepare()) expected = request.snapshot_prepare().snapshot_object();
    else if (request.has_snapshot_inspect()) expected = request.snapshot_inspect().snapshot_object();
    else if (request.has_snapshot_retire()) expected = request.snapshot_retire().snapshot_object();
    else if (request.has_copy_stage_prepare()) expected = request.copy_stage_prepare().object();
    else if (request.has_copy_domain_prepare()) expected=request.copy_domain_prepare().object();
    else throw std::invalid_argument("Expected a storage object operation");
    const bool snapshot_prepare = request.has_snapshot_prepare();
    const bool snapshot_inspect = request.has_snapshot_inspect();
    const bool snapshot_retire = request.has_snapshot_retire();
    const std::string snapshot_digest = snapshot_prepare ? request.snapshot_prepare().request_digest() : std::string{};
    pb::StorageOwnerReply reply;
    try { reply = impl_->exchange(std::move(request), tick); }
    catch (const WorldError&) { throw; }
    catch (...) { unavailable(); }
    if (reply.has_error()) {
        const auto code = reply.error().code();
        throw WorldError(code == pb::StorageOwnerError::EXHAUSTED ? wire::RESOURCE_EXHAUSTED :
                         code == pb::StorageOwnerError::CONFLICT ? wire::OPERATION_CONFLICT :
                         code == pb::StorageOwnerError::INVALID_ARGUMENT ? wire::INVALID_ARGUMENT :
                         code == pb::StorageOwnerError::UNAVAILABLE ? wire::UNAVAILABLE :
                         code == pb::StorageOwnerError::UNAUTHORIZED ? wire::FORBIDDEN : wire::STORAGE_ERROR,
                         reply.error().message().c_str());
    }
    if (!reply.has_state() || !reply.state().accounting_consistent() ||
        reply.state().object().SerializeAsString() != expected.SerializeAsString()) {
        impl_->channel.reset(); unavailable();
    }
    if (expected.kind() == pb::StorageObject::SNAPSHOT) {
        const auto& state = reply.state();
        if (!state.has_source_object() || state.source_object().kind() == pb::StorageObject::UNSPECIFIED ||
            state.source_object().id().size() != 32 || state.source_subvolume_id() == 0 ||
            state.operation_digest().size() != 32 ||
            ((snapshot_prepare || snapshot_inspect) && !state.read_only()) ||
            (snapshot_retire && state.read_only()) ||
            (snapshot_prepare && state.operation_digest() != snapshot_digest)) {
            impl_->channel.reset(); unavailable();
        }
    }
    return reply.state();
}
}

namespace spatial {
namespace {
bool valid_object_ref(const workspace::v1::StorageObject& object, workspace::v1::StorageObject::Kind kind) {
    return object.kind()==kind && object.id().size()==32 &&
        object.id().find_first_not_of("0123456789abcdef")==std::string::npos;
}
bool valid_stage_receipt(const workspace::v1::StorageCopyStageReceipt& r,
                         const std::string& operation,const std::string& digest) {
    using Receipt=workspace::v1::StorageCopyStageReceipt;
    const bool known=r.state()==Receipt::PREPARED||r.state()==Receipt::COPYING||
        r.state()==Receipt::SEALED||r.state()==Receipt::UNCERTAIN;
    if(!known||r.operation_id()!=operation||r.request_digest()!=digest||
       !valid_object_ref(r.snapshot_object(),workspace::v1::StorageObject::SNAPSHOT)||
       !valid_object_ref(r.destination_object(),workspace::v1::StorageObject::COPY_STAGING)||
       !r.source_subvolume_id()||!r.destination_subvolume_id()||
       r.source_subvolume_id()==r.destination_subvolume_id()||
       !r.maximum_bytes()||r.maximum_bytes()>4ULL*1024*1024*1024||
       !r.maximum_entries()||r.maximum_entries()>200000||
       r.actual_bytes()>r.maximum_bytes()||r.actual_entries()>r.maximum_entries()||
       (r.claimed()&&r.state()!=Receipt::COPYING)) return false;
    if(r.state()==Receipt::PREPARED)
        return !r.claimed()&&!r.actual_bytes()&&!r.actual_entries()&&r.content_digest().empty();
    if(r.state()==Receipt::COPYING)
        return !r.actual_bytes()&&!r.actual_entries()&&r.content_digest().empty();
    if(r.state()==Receipt::SEALED)
        return !r.claimed()&&r.content_digest().size()==32;
    return !r.claimed()&&!r.actual_bytes()&&!r.actual_entries()&&r.content_digest().empty();
}
void copy_error(const workspace::v1::StorageOwnerReply& reply) {
    namespace pb = workspace::v1;
    if (!reply.has_error()) return;
    const auto code = reply.error().code();
    throw WorldError(code == pb::StorageOwnerError::EXHAUSTED ? wire::RESOURCE_EXHAUSTED :
        code == pb::StorageOwnerError::CONFLICT ? wire::OPERATION_CONFLICT :
        code == pb::StorageOwnerError::INVALID_ARGUMENT ? wire::INVALID_ARGUMENT :
                         code == pb::StorageOwnerError::UNAVAILABLE ? wire::UNAVAILABLE :
        code == pb::StorageOwnerError::UNCERTAIN ? wire::UNAVAILABLE :
        code == pb::StorageOwnerError::UNAUTHORIZED ? wire::FORBIDDEN : wire::STORAGE_ERROR,
        reply.error().message().c_str());
}
}
int StorageClient::open_object(const workspace::v1::StorageObjectState& state) const {
    namespace pb = workspace::v1;
    require(state.accounting_consistent() && state.subvolume_id()>0 &&
        state.object().id().size()==32 && state.object().id().find_first_not_of("0123456789abcdef")==std::string::npos,
        "Unverified storage object identity");
    const char* collection=nullptr;
    switch(state.object().kind()) {
    case pb::StorageObject::PROJECT: collection="worktrees"; break;
    case pb::StorageObject::REPOSITORY: collection="repositories"; break;
    case pb::StorageObject::APPLICATION_STATE: collection="applications"; break;
    case pb::StorageObject::SNAPSHOT: collection="snapshots"; break;
    case pb::StorageObject::COPY_STAGING: collection="staging"; break;
    default: throw WorldError(wire::INVALID_ARGUMENT,"Storage object has no copy descriptor");
    }
    identity::tls_detail::Fd directory(openat(impl_->volume_root.value,collection,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
    if(directory.value<0) unavailable();
    auto component=state.object().id();
    if(state.object().kind()==pb::StorageObject::REPOSITORY)component+=".git";
    identity::tls_detail::Fd object(openat(directory.value,component.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
    struct stat status{}; btrfs_ioctl_get_subvol_info_args subvolume{}; btrfs_ioctl_fs_info_args fs{};
    if(object.value<0 || fstat(object.value,&status)<0 || !S_ISDIR(status.st_mode) ||
       ioctl(object.value,BTRFS_IOC_GET_SUBVOL_INFO,&subvolume)<0 ||
       ioctl(object.value,BTRFS_IOC_FS_INFO,&fs)<0 ||
       subvolume.treeid!=state.subvolume_id() ||
       std::string(reinterpret_cast<char*>(fs.fsid),BTRFS_FSID_SIZE)!=impl_->filesystem_id ||
       static_cast<bool>(subvolume.flags&BTRFS_ROOT_SUBVOL_RDONLY)!=state.read_only()) {
        impl_->channel.reset(); unavailable();
    }
    return std::exchange(object.value,-1);
}
namespace {
std::string destination_digest(const std::string& bytes) {
    std::array<unsigned char,32> value{};unsigned count=0;
    require(EVP_Digest(bytes.data(),bytes.size(),value.data(),&count,EVP_sha256(),nullptr)==1 && count==32,"Cannot digest destination receipt");
    return {reinterpret_cast<char*>(value.data()),value.size()};
}
void validate_destination_receipt(const pb::StorageCopyDestinationReceipt& receipt,
                                 const pb::StorageCopyDestinationPrepare* intent) {
    validate_message(receipt);
    const auto& allocation=receipt.allocation();
    require(receipt.has_allocation() && allocation.operation_id().size()==16 &&
            allocation.request_digest().size()==32 && allocation.workspace_id().size()==32 &&
            allocation.domains_size()>=2 && allocation.domains_size()<=5 && allocation.state_companions_size()<=32 &&
            receipt.allocation_digest().size()==32 &&
            (receipt.state()==pb::StorageCopyDestinationReceipt::ALLOCATED ||
             receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED ||
             receipt.state()==pb::StorageCopyDestinationReceipt::UNCERTAIN),
            "Malformed copy destination receipt");
    if(intent)require(allocation.SerializeAsString()==intent->SerializeAsString(),"Destination receipt differs from pinned allocation");
    require(receipt.allocation_digest()==destination_digest(allocation.SerializeAsString()),"Destination allocation digest does not match exact intent");
    if(receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED)require(receipt.validation_digest().size()==32 && receipt.publication_digest().size()==32,"Publication omitted final-validation binding");
    if(receipt.state()==pb::StorageCopyDestinationReceipt::UNCERTAIN) {
        require(receipt.objects_size()==0 && receipt.state_companions_size()==0,"Uncertain receipt invented completed destination objects");return;
    }
    require(receipt.objects_size()==allocation.domains_size() &&
            receipt.state_companions_size()==allocation.state_companions_size(),"Destination receipt omitted or added an allocated domain/companion");
    std::set<std::string> ids;std::set<std::uint64_t> subvolumes;
    for(int i=0;i<receipt.objects_size();++i) {
        const auto& object=receipt.objects(i);
        const auto& domain=allocation.domains(i);
        const auto& expected=receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED?domain.destination():domain.staging();
        require(object.object().SerializeAsString()==expected.SerializeAsString() &&
                object.subvolume_id()>0 && object.quota_group()>0 && object.maximum_bytes()>0 &&
                object.accounting_consistent() && !object.read_only() &&
                object.operation_digest()==(receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED?receipt.publication_digest():receipt.allocation_digest()) &&
                ids.insert(object.object().id()).second && subvolumes.insert(object.subvolume_id()).second,
                "Copy destination receipt failed identity or accounting validation");
    }
    std::set<std::string> keys;
    for(int index=0;index<receipt.state_companions_size();++index) {
        const auto& object=receipt.state_companions(index);const auto& plan=allocation.state_companions(index);
        copy_collections::contract(plan.source().contract());
        require(copy_collections::key(plan.source().key()) && keys.insert(plan.source().key()).second &&
                plan.source().state_object().kind()==pb::StorageObject::APPLICATION_STATE &&
                copy_collections::object_id(plan.source().state_object().id()) && plan.snapshot().kind()==pb::StorageObject::SNAPSHOT &&
                plan.staging().kind()==pb::StorageObject::COPY_STAGING && plan.destination().kind()==pb::StorageObject::APPLICATION_STATE &&
                copy_collections::object_id(plan.snapshot().id()) && copy_collections::object_id(plan.staging().id()) &&
                copy_collections::object_id(plan.destination().id()) && allocation.state_mapping_revision()>0 &&
                !allocation.source_workspace_id().empty(),"Malformed pinned companion allocation");
        const auto& expected=receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED?plan.destination():plan.staging();
        require(object.object().SerializeAsString()==expected.SerializeAsString() && object.subvolume_id()>0 && object.quota_group()>0 &&
                object.maximum_bytes()>0 && object.accounting_consistent() && !object.read_only() &&
                object.operation_digest()==(receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED?receipt.publication_digest():receipt.allocation_digest()) &&
                ids.insert(object.object().id()).second && subvolumes.insert(object.subvolume_id()).second,
                "Destination companion receipt differs from exact planned identity, quota, or publication binding");
    }
    require((allocation.state_companions_size()==0 && allocation.state_mapping_revision()==0) ||
            (allocation.state_companions_size()>0 && allocation.state_mapping_revision()>0),"Companion mapping revision is absent or unexpected");
    require(receipt.state()==pb::StorageCopyDestinationReceipt::PUBLISHED
        ? (allocation.state_companions_size()>0?receipt.destination_state_mapping_revision()==1:receipt.destination_state_mapping_revision()==0)
        : receipt.destination_state_mapping_revision()==0,"Destination mapping revision differs from fresh publication");
}
}
pb::StorageCopyDestinationReceipt StorageClient::prepare_copy_destination(const pb::StorageCopyDestinationPrepare& intent) {
    pb::StorageOwnerRequest request;*request.mutable_copy_destination_prepare()=intent;
    const auto reply=impl_->exchange(std::move(request),{});copy_error(reply);
    try {require(reply.has_copy_destination(),"Missing destination receipt");validate_destination_receipt(reply.copy_destination(),&intent);}
    catch(...){fence_unavailable();throw;}
    return reply.copy_destination();
}
pb::StorageCopyDestinationReceipt StorageClient::publish_copy_destination(const pb::StorageCopyDestinationPublish& intent) {
    pb::StorageOwnerRequest request;*request.mutable_copy_destination_publish()=intent;
    const auto reply=impl_->exchange(std::move(request),{});copy_error(reply);
    try {require(reply.has_copy_destination(),"Missing publication receipt");validate_destination_receipt(reply.copy_destination(),&intent.allocation());
        require(reply.copy_destination().publication_digest()==destination_digest(intent.SerializeAsString()) &&
                reply.copy_destination().validation_digest()==destination_digest(intent.validation().SerializeAsString()),"Publication receipt differs from exact final-validation intent");}
    catch(...){fence_unavailable();throw;}
    return reply.copy_destination();
}
pb::StorageCopyDestinationReceipt StorageClient::inspect_copy_destination(const pb::StorageCopyDestinationInspect& intent) {
    pb::StorageOwnerRequest request;*request.mutable_copy_destination_inspect()=intent;
    const auto reply=impl_->exchange(std::move(request),{});copy_error(reply);
    try {require(reply.has_copy_destination(),"Missing destination inspection receipt");validate_destination_receipt(reply.copy_destination(),nullptr);
        require(reply.copy_destination().allocation().operation_id()==intent.operation_id() &&
                reply.copy_destination().allocation().request_digest()==intent.request_digest(),"Destination inspection binding differs");}
    catch(...){fence_unavailable();throw;}
    return reply.copy_destination();
}
workspace::v1::StorageConfiguredApplicationAllocation StorageClient::prepare_configured_application(const workspace::v1::StorageConfiguredApplicationPrepare& request) {
    workspace::v1::StorageOwnerRequest message;*message.mutable_configured_application_prepare()=request;
    const auto reply=impl_->exchange(std::move(message),{});copy_error(reply);
    try {
        require(reply.has_configured_allocation(),"Missing configured allocation receipt");
        const auto& value=reply.configured_allocation();validate_message(value);
        require(value.has_persistent() && value.has_runtime() &&
                value.request_digest()==destination_digest(request.SerializeAsString()) &&
                value.persistent().object().SerializeAsString()==request.persistent().object().SerializeAsString() &&
                value.runtime().object().SerializeAsString()==request.runtime_object().SerializeAsString() &&
                value.state_mapping_revision()>0 &&
                value.state_mapping_revision()>=request.persistent().expected_state_mapping_revision() &&
                value.state_mapping_revision()-request.persistent().expected_state_mapping_revision()<=1,
                "Configured allocation receipt changed its request, identities, or mapping revision");
        for(const auto* state:{&value.persistent(),&value.runtime()})
            require(state->object().kind()==workspace::v1::StorageObject::APPLICATION_STATE &&
                    copy_collections::object_id(state->object().id()) && state->subvolume_id()>0 && state->quota_group()>0 &&
                    state->accounting_consistent() && !state->read_only() &&
                    request.persistent().expected_state_maximum_bytes()>0 &&
                    request.persistent().expected_storage_policy_digest().size()==32 &&
                    state->maximum_bytes()==request.persistent().expected_state_maximum_bytes(),
                    "Configured allocation differs from its independently pinned state quota");
        require(value.persistent().maximum_bytes()==value.runtime().maximum_bytes() &&
                value.persistent().subvolume_id()!=value.runtime().subvolume_id() &&
                value.persistent().object().id()!=value.runtime().object().id(),"Configured runtime reused persistent storage");
    }catch(...){fence_unavailable();throw;}
    return reply.configured_allocation();
}
workspace::v1::StorageConfiguredApplicationReply StorageClient::query_configured_application(const workspace::v1::StorageConfiguredApplicationQuery& request) {
    workspace::v1::StorageOwnerRequest message;*message.mutable_configured_application_query()=request;
    const auto reply=impl_->exchange(std::move(message),{});copy_error(reply);
    try {
        require(reply.has_configured_application(),"Missing configured application receipt");
        const auto& value=reply.configured_application();validate_message(value);
        require(value.catalog_revision()>0 && value.has_definition() && value.definition().key()==request.key(),
                "Configured application reply changed its key or revision");
        copy_collections::definition(value.definition().definition());
        copy_collections::contract(value.definition().state_contract());
        require(copy_collections::profile_name(value.definition().profile()),"Invalid configured application profile");
        require(value.has_storage_policy() && value.storage_policy().has_total_bytes() &&
                value.storage_policy().total_bytes()>=4*1024*1024 && value.storage_policy().total_bytes()<=(1ULL<<50) && value.storage_policy().total_bytes()%4096==0 &&
                value.storage_policy().has_actor_state_default_bytes() && value.storage_policy().actor_state_default_bytes()>=4*1024*1024 &&
                value.storage_policy().actor_state_default_bytes()<=(1ULL<<50) && value.storage_policy().actor_state_default_bytes()%4096==0 &&
                (value.storage_policy().workspace().empty() || value.storage_policy().workspace()==request.workspace_id()) &&
                value.storage_policy().profiles_size()>0 && value.storage_policy().profiles_size()<=32 &&
                value.storage_policy().actors_size()<=128,"Configured independent storage policy is missing or unbounded");
        bool selected=false;std::set<std::string> profiles;
        for(const auto& profile:value.storage_policy().profiles()) {
            require(copy_collections::profile_name(profile.name()) && profiles.insert(profile.name()).second &&
                    profile.has_state_bytes() && profile.state_bytes()>=4*1024*1024 && profile.state_bytes()<=(1ULL<<50) && profile.state_bytes()%4096==0,
                    "Configured storage policy contains invalid/duplicate application profiles");
            if(profile.name()==value.definition().profile())selected=true;
        }
        require(selected,"Configured application profile is absent from its independent storage policy");
        std::set<std::string> actors;
        for(const auto& actor:value.storage_policy().actors()) {
            const bool valid=actor.has_principal()?actor.principal().issuer_id().size()==32 && actor.principal().actor_id().size()==32:
                actor.has_fixture_actor() && copy_collections::profile_name(actor.fixture_actor());
            const auto subject=actor.has_principal()?"p"+actor.principal().SerializeAsString():"f"+actor.fixture_actor();
            require(valid && actors.insert(subject).second && actor.has_state_bytes() && actor.state_bytes()>=4*1024*1024 &&
                    actor.state_bytes()<=(1ULL<<50) && actor.state_bytes()%4096==0,
                    "Configured storage policy contains an invalid/duplicate actor ceiling");
        }
        if(value.has_state()) {
            const auto& state=value.state();
            require(value.state_mapping_revision()>0 && state.has_object() &&
                    state.object().kind()==workspace::v1::StorageObject::APPLICATION_STATE &&
                    copy_collections::object_id(state.object().id()) && state.subvolume_id()>0 && state.quota_group()>0 &&
                    !state.read_only() && state.accounting_consistent() && state.maximum_bytes()>0,
                    "Configured application state receipt is invalid");
        }
    }catch(...){fence_unavailable();throw;}
    return reply.configured_application();
}
workspace::v1::StorageCopyCatalogReply StorageClient::query_copy_catalog(const workspace::v1::StorageCopyCatalogQuery& request) {
    workspace::v1::StorageOwnerRequest message; *message.mutable_copy_catalog_query()=request;
    const auto reply=impl_->exchange(std::move(message),{});
    copy_error(reply);
    auto fail=[&]() -> void { impl_->channel.reset(); unavailable(); };
    if (!reply.has_copy_catalog()) fail();
    try { validate_message(reply.copy_catalog()); } catch (...) { fail(); }
    if (!reply.copy_catalog().GetReflection()->GetUnknownFields(reply.copy_catalog()).empty() ||
        reply.copy_catalog().catalog_revision()==0 ||
        (request.expected_catalog_revision()!=0 &&
         reply.copy_catalog().catalog_revision()!=request.expected_catalog_revision()) ||
        !reply.copy_catalog().has_destination_profile() ||
        !reply.copy_catalog().destination_profile().GetReflection()->GetUnknownFields(reply.copy_catalog().destination_profile()).empty() ||
        reply.copy_catalog().destination_profile().name()!=request.destination_profile() ||
        reply.copy_catalog().destination_profile().revision()!=request.expected_destination_profile_revision() ||
        reply.copy_catalog().entries_size()!=request.data_classes_size()) fail();
    const auto& profile=reply.copy_catalog().destination_profile();
    if (!profile.has_storage_policy() || !profile.storage_policy().workspace().empty() ||
        profile.storage_policy().actors_size()!=0 || profile.maximum_total_bytes()<4*1024*1024 ||
        profile.maximum_total_bytes()>(4ULL<<30) || profile.maximum_total_bytes()%4096!=0 ||
        profile.maximum_entries()==0 ||
        profile.maximum_entries()>200000 || profile.storage_policy().total_bytes()>profile.maximum_total_bytes() ||
        profile.execution_resource_profile().empty() || profile.application_profile().empty()) fail();
    std::set<int> allowed;
    for (const auto value:profile.allowed_classes())
        if (value<workspace::copy::v1::PROJECT_TREE || value>workspace::copy::v1::ENVIRONMENT_CONFIG ||
            !allowed.insert(static_cast<int>(value)).second) fail();
    if (!allowed.contains(workspace::copy::v1::PROJECT_TREE) ||
        !allowed.contains(workspace::copy::v1::GIT_HISTORY)) fail();
    bool has_application_profile=false;
    for (const auto& value:profile.storage_policy().profiles())
        if (value.name()==profile.application_profile() && value.has_state_bytes()) has_application_profile=true;
    if (!has_application_profile) fail();
    std::set<int> requested;
    for (const auto value:request.data_classes()) if (!requested.insert(static_cast<int>(value)).second) fail();
    std::set<int> returned;
    for (const auto& entry:reply.copy_catalog().entries()) {
        if (!entry.GetReflection()->GetUnknownFields(entry).empty() ||
            entry.workspace_id()!=request.workspace_id() || entry.workspace_id().empty() ||
            entry.workspace_id().size()>128 || entry.workspace_id().find('\0')!=std::string::npos ||
            !entry.has_source_object() ||
            !entry.source_object().GetReflection()->GetUnknownFields(entry.source_object()).empty() ||
            entry.source_object().id().size()!=32 ||
            entry.source_object().id().find_first_not_of("0123456789abcdef")!=std::string::npos ||
            (entry.schema_version()!=1 && !(entry.schema_version()==2 && (entry.data_class()==workspace::copy::v1::APPLICATION_DEFINITION || entry.data_class()==workspace::copy::v1::APPLICATION_STATE))) || entry.maximum_bytes()<4*1024*1024 ||
            entry.maximum_bytes()>(4ULL<<30) || entry.maximum_entries()==0 ||
            entry.maximum_entries()>200000 ||
            entry.consistency()!=workspace::v1::CRASH_CONSISTENT_AFTER_QUIESCENCE ||
            !requested.contains(static_cast<int>(entry.data_class())) ||
            !returned.insert(static_cast<int>(entry.data_class())).second) fail();
        const char* schema=nullptr;
        workspace::v1::StorageObject::Kind expected=workspace::v1::StorageObject::UNSPECIFIED;
        switch(entry.data_class()) {
        case workspace::copy::v1::PROJECT_TREE:
            schema="spatial.workspace.project-tree"; expected=workspace::v1::StorageObject::PROJECT; break;
        case workspace::copy::v1::GIT_HISTORY:
            schema="spatial.workspace.git-history"; expected=workspace::v1::StorageObject::REPOSITORY; break;
        case workspace::copy::v1::APPLICATION_DEFINITION:
            schema="spatial.workspace.application-definition"; expected=workspace::v1::StorageObject::APPLICATION_DEFINITION; break;
        case workspace::copy::v1::APPLICATION_STATE:
            schema="spatial.workspace.application-state"; expected=workspace::v1::StorageObject::APPLICATION_STATE; break;
        case workspace::copy::v1::ENVIRONMENT_CONFIG:
            schema="spatial.workspace.environment-config"; expected=workspace::v1::StorageObject::ENVIRONMENT_CONFIG; break;
        default: fail();
        }
        if (entry.schema_id()!=schema || entry.source_object().kind()!=expected ||
            (entry.data_class()==workspace::copy::v1::APPLICATION_STATE && entry.schema_version()!=2)) fail();
        bool permitted=false;
        for (const auto value:reply.copy_catalog().destination_profile().allowed_classes())
            if (value==entry.data_class()) permitted=true;
        if (!permitted) fail();
    }
    if (returned!=requested) fail();
    try {
        bool expects_mapping=false;
        for(const auto& entry:reply.copy_catalog().entries()) {
            if(entry.schema_version()==2 && entry.data_class()==workspace::copy::v1::APPLICATION_DEFINITION) {
                workspace::v1::CopyApplicationDefinitionCollection definitions;
                require(definitions.ParseFromString(entry.typed_export()),"Malformed catalog definition collection");
                copy_collections::definitions(definitions);
            }
            if(entry.schema_version()==2 && entry.data_class()==workspace::copy::v1::APPLICATION_STATE) {
                expects_mapping=true;workspace::v1::CopyApplicationStateCollection descriptor;
                require(descriptor.ParseFromString(entry.typed_export()),"Malformed catalog state collection");
                copy_collections::states(descriptor,false);
                require(reply.copy_catalog().has_state_mapping(),"Catalog omitted selected state mapping");
                const auto& mapping=reply.copy_catalog().state_mapping();copy_collections::mapping(mapping);
                require(mapping.workspace_id()==request.workspace_id() &&
                        (request.expected_state_mapping_revision()==0 || mapping.revision()==request.expected_state_mapping_revision()),
                        "Catalog state mapping workspace/revision differs");
                std::map<std::string,std::string> expected;
                for(const auto& slot:descriptor.slots())expected.emplace(slot.key(),slot.contract().SerializeAsString());
                for(const auto& state:mapping.entries()) {
                    const auto found=expected.find(state.key());
                    require(found!=expected.end() && found->second==state.contract().SerializeAsString(),"Catalog state mapping substituted a contract or key");
                    require(state.state_object().SerializeAsString()!=entry.source_object().SerializeAsString(),"Collection descriptor cannot be an application state source");
                    expected.erase(found);
                }
                require(expected.empty(),"Catalog state mapping omitted a configured application");
            }
        }
        require(expects_mapping==reply.copy_catalog().has_state_mapping() &&
                (expects_mapping || request.expected_state_mapping_revision()==0),"Unselected state mapping was returned");
    } catch(...) {fail();}
    return reply.copy_catalog();
}
workspace::v1::StorageCopyStageReceipt StorageClient::begin_copy_stage(const workspace::v1::StorageCopyStageBegin& request) {
    workspace::v1::StorageOwnerRequest message; *message.mutable_copy_stage_begin()=request;
    const auto reply=impl_->exchange(std::move(message),{});
    copy_error(reply);
    if(!reply.has_copy_stage() || !valid_stage_receipt(reply.copy_stage(),request.operation_id(),request.request_digest()) ||
       reply.copy_stage().snapshot_object().SerializeAsString()!=request.snapshot_object().SerializeAsString() ||
       reply.copy_stage().destination_object().SerializeAsString()!=request.destination_object().SerializeAsString() ||
       reply.copy_stage().maximum_bytes()!=request.maximum_bytes() || reply.copy_stage().maximum_entries()!=request.maximum_entries()) {
        impl_->channel.reset(); unavailable();
    }
    return reply.copy_stage();
}
workspace::v1::StorageCopyStageReceipt StorageClient::start_copy_stage(const workspace::v1::StorageCopyStageStart& request) {
    workspace::v1::StorageOwnerRequest message; *message.mutable_copy_stage_start()=request;
    const auto reply=impl_->exchange(std::move(message),{});
    copy_error(reply);
    if(!reply.has_copy_stage() || !valid_stage_receipt(reply.copy_stage(),request.operation_id(),request.request_digest()) ||
       (reply.copy_stage().claimed() && reply.copy_stage().state()!=workspace::v1::StorageCopyStageReceipt::COPYING) ||
       (!reply.copy_stage().claimed() && reply.copy_stage().state()==workspace::v1::StorageCopyStageReceipt::PREPARED)) {
        impl_->channel.reset(); unavailable();
    }
    return reply.copy_stage();
}
workspace::v1::StorageCopyStageReceipt StorageClient::seal_copy_stage(const workspace::v1::StorageCopyStageSeal& request) {
    workspace::v1::StorageOwnerRequest message; *message.mutable_copy_stage_seal()=request;
    const auto reply=impl_->exchange(std::move(message),{});
    copy_error(reply);
    if(!reply.has_copy_stage() || !valid_stage_receipt(reply.copy_stage(),request.operation_id(),request.request_digest()) ||
       reply.copy_stage().state()!=workspace::v1::StorageCopyStageReceipt::SEALED ||
       reply.copy_stage().snapshot_object().SerializeAsString()!=request.snapshot_object().SerializeAsString() ||
       reply.copy_stage().destination_object().SerializeAsString()!=request.destination_object().SerializeAsString() ||
       reply.copy_stage().actual_bytes()!=request.actual_bytes() || reply.copy_stage().actual_entries()!=request.actual_entries() ||
       reply.copy_stage().content_digest()!=request.content_digest()) {
        impl_->channel.reset(); unavailable();
    }
    return reply.copy_stage();
}
workspace::v1::StorageCopyStageReceipt StorageClient::mark_copy_stage_uncertain(const workspace::v1::StorageCopyStageUncertain& request) {
    workspace::v1::StorageOwnerRequest message; *message.mutable_copy_stage_uncertain()=request;
    const auto reply=impl_->exchange(std::move(message),{}); copy_error(reply);
    if(!reply.has_copy_stage() || !valid_stage_receipt(reply.copy_stage(),request.operation_id(),request.request_digest()) ||
       (reply.copy_stage().state()!=workspace::v1::StorageCopyStageReceipt::UNCERTAIN &&
        reply.copy_stage().state()!=workspace::v1::StorageCopyStageReceipt::SEALED)) {
        impl_->channel.reset(); unavailable();
    }
    return reply.copy_stage();
}
workspace::v1::StorageCopyStageReceipt StorageClient::inspect_copy_stage(const workspace::v1::StorageCopyStageInspect& request) {
    workspace::v1::StorageOwnerRequest message; *message.mutable_copy_stage_inspect()=request;
    const auto reply=impl_->exchange(std::move(message),{});
    copy_error(reply);
    if(!reply.has_copy_stage() || !valid_stage_receipt(reply.copy_stage(),request.operation_id(),request.request_digest())) {
        impl_->channel.reset(); unavailable();
    }
    return reply.copy_stage();
}

} // namespace spatial
