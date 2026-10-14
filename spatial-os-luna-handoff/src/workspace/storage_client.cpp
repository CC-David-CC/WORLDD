#include "workspace/storage_client.hpp"
#include "workspace/storage_channel.hpp"
#include "core/protocol.hpp"
#include "identity/tls_support.hpp"
#include "world/store.hpp"
#include <fcntl.h>
#include <linux/btrfs.h>
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
    int volume_fd = -1;
    std::string filesystem_id;
    std::unique_ptr<StorageChannel> channel;
    std::uint64_t sequence = 0;
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
        volume_fd=fcntl(root.value,F_DUPFD_CLOEXEC,3);
        if(volume_fd<0) unavailable();
        filesystem_id=std::string(reinterpret_cast<char*>(info.fsid),BTRFS_FSID_SIZE);
        pb::StorageOwnerRequest request; request.mutable_hello();
        const auto reply = exchange(std::move(request), {});
        if (!reply.has_identity() || reply.identity().filesystem_id() !=
            std::string(reinterpret_cast<char*>(info.fsid), BTRFS_FSID_SIZE) ||
            reply.identity().process_id() != static_cast<unsigned>(channel->peer_pid()) ||
            !reply.identity().accounting_consistent()) unavailable();
    }
    ~Impl() { if(volume_fd>=0) close(volume_fd); }
    pb::StorageOwnerReply exchange(pb::StorageOwnerRequest request, const std::function<void()>& tick) {
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
    identity::tls_detail::Fd directory(openat(impl_->volume_fd,collection,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
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
       static_cast<bool>(subvolume.flags&BTRFS_SUBVOL_RDONLY)!=state.read_only()) {
        impl_->channel.reset(); unavailable();
    }
    return std::exchange(object.value,-1);
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
