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
[[noreturn]] void unavailable() {
    throw WorldError(wire::UNAVAILABLE, "Storage owner unavailable; inspect pending effects before retrying");
}
}
struct StorageClient::Impl {
    std::unique_ptr<StorageChannel> channel;
    std::uint64_t sequence = 0;
    std::function<void()> observer;
    Impl(int fd, const std::filesystem::path& volume) {
        channel = std::make_unique<StorageChannel>(fd, 0, std::nullopt);
        identity::tls_detail::Fd root(open(volume.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        struct statfs filesystem{}; struct stat directory{}; btrfs_ioctl_fs_info_args info{}; btrfs_ioctl_get_subvol_info_args subvolume{};
        if (!volume.is_absolute() || root.value < 0 || fstatfs(root.value, &filesystem) < 0 ||
            filesystem.f_type != BTRFS_SUPER_MAGIC || fstat(root.value, &directory) < 0 || directory.st_ino != 256 ||
            ioctl(root.value, BTRFS_IOC_GET_SUBVOL_INFO, &subvolume) < 0 || subvolume.treeid != 5 ||
            ioctl(root.value, BTRFS_IOC_FS_INFO, &info) < 0) unavailable();
        pb::StorageOwnerRequest request; request.mutable_hello();
        const auto reply = exchange(std::move(request), {});
        if (!reply.has_identity() || reply.identity().filesystem_id() !=
            std::string(reinterpret_cast<char*>(info.fsid), BTRFS_FSID_SIZE) ||
            reply.identity().process_id() != static_cast<unsigned>(channel->peer_pid()) ||
            !reply.identity().accounting_consistent()) unavailable();
    }
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
workspace::v1::StorageObjectState StorageClient::call(pb::StorageOwnerRequest request, const std::function<void()>& tick) {
    pb::StorageObject expected;
    if (request.has_repository_prepare()) expected = request.repository_prepare().object();
    else if (request.has_project_prepare()) expected = request.project_prepare().object();
    else if (request.has_workspace_bind()) expected = request.workspace_bind().project();
    else if (request.has_application_prepare()) expected = request.application_prepare().object();
    else if (request.has_retirement_prepare()) expected = request.retirement_prepare().object();
    else if (request.has_inspect()) expected = request.inspect().object();
    else throw std::invalid_argument("Expected a storage object operation");
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
    return reply.state();
}
}
