#include "workspace/copy_worker.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_client.hpp"
#include "world/store.hpp"
#include <openssl/evp.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <dirent.h>
#include <thread>
#include <unistd.h>
#include <array>
#include <cerrno>
#include <vector>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

namespace {
std::string random_bytes(std::size_t count) {
    std::random_device source; std::string result(count, '\0');
    for (auto& value : result) value = static_cast<char>(source());
    return result;
}
std::string digest(const std::string& bytes) {
    std::array<unsigned char, 32> output{}; unsigned size = 0;
    if (EVP_Digest(bytes.data(), bytes.size(), output.data(), &size, EVP_sha256(), nullptr) != 1 || size != output.size())
        throw std::runtime_error("SHA-256 failed");
    return {reinterpret_cast<const char*>(output.data()), output.size()};
}
std::string object_id() {
    static constexpr char hex[] = "0123456789abcdef";
    const auto bytes = random_bytes(16); std::string result; result.reserve(32);
    for (unsigned char value : bytes) { result.push_back(hex[value >> 4]); result.push_back(hex[value & 15]); }
    return result;
}
struct Fd { int value; explicit Fd(int descriptor=-1):value(descriptor){} ~Fd(){if(value>=0)close(value);} };
void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
std::size_t descriptor_count() {
    DIR* directory=opendir("/proc/self/fd");if(!directory)throw std::runtime_error("cannot inspect descriptor table");
    std::size_t count=0;while(readdir(directory))++count;closedir(directory);return count;
}
void write_all(int fd,const void* data,std::size_t size) {
    auto* bytes=static_cast<const char*>(data);
    while(size){ssize_t n=write(fd,bytes,size);if(n<0&&errno==EINTR)continue;require(n>0,"fake owner response write failed");bytes+=n;size-=static_cast<std::size_t>(n);}
}
void exercise_constructor_failure_cleanup(const std::filesystem::path& volume) {
    const auto baseline=descriptor_count();
    for(int attempt=0;attempt<32;++attempt) {
        int pair[2];require(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,pair)==0,"cannot create malformed-owner socket");
        spatial::workspace::v1::StorageOwnerReply reply;reply.set_protocol_version(1);reply.set_request_id(1);
        auto* identity=reply.mutable_identity();identity->set_filesystem_id(std::string(16,'x'));
        identity->set_process_id(static_cast<std::uint32_t>(getpid()));identity->set_accounting_consistent(true);
        const auto payload=reply.SerializeAsString();const auto size=static_cast<std::uint32_t>(payload.size());
        std::array<unsigned char,4> prefix{static_cast<unsigned char>(size>>24),static_cast<unsigned char>(size>>16),static_cast<unsigned char>(size>>8),static_cast<unsigned char>(size)};
        std::thread peer([&,attempt]{if(attempt%2==0){write_all(pair[1],prefix.data(),prefix.size());write_all(pair[1],payload.data(),payload.size());}close(pair[1]);});
        try { spatial::StorageClient bad(pair[0],volume);peer.join();throw std::runtime_error("invalid owner hello was accepted"); }
        catch(const spatial::WorldError&) { if(peer.joinable())peer.join(); }
        require(descriptor_count()==baseline,"failed owner hello leaked descriptors");
    }
}
}
int main(int argc,char** argv) {
    namespace pb=spatial::workspace::v1;
    try {
        if(argc!=5)throw std::invalid_argument("Expected storage fd, volume, resource config, and mode");
        const auto mode=std::string(argv[4]); if(mode!="success"&&mode!="symlink"&&mode!="fd-leak")throw std::invalid_argument("Invalid probe mode");
        if(mode=="fd-leak") { require(geteuid()==0,"fd-leak probe requires root owner credentials"); exercise_constructor_failure_cleanup(argv[2]); std::cout<<"MALFORMED_OWNER_FD_STABLE 32\n"; return 0; }
        spatial::StorageClient storage(std::stoi(argv[1]),argv[2]);
        spatial::WorkspaceResources resources(argv[3]);
        pb::StorageObject repo; repo.set_kind(pb::StorageObject::REPOSITORY); repo.set_id(object_id());
        pb::StorageOwnerRequest request; request.mutable_repository_prepare()->mutable_object()->CopyFrom(repo);
        (void)storage.call(std::move(request));
        pb::StorageObject project; project.set_kind(pb::StorageObject::PROJECT); project.set_id(object_id());
        request.Clear(); request.mutable_project_prepare()->mutable_object()->CopyFrom(project);
        request.mutable_project_prepare()->mutable_repository()->CopyFrom(repo);
        const auto project_state=storage.call(std::move(request));
        Fd project_fd(storage.open_object(project_state)); require(project_fd.value>=0,"source descriptor unavailable");
        constexpr char filename[]="production-copy-probe.bin";
        const std::size_t payload_size=24*1024*1024;
        std::vector<char> payload(payload_size,'p');
        if(mode=="success") {
            Fd file(openat(project_fd.value,filename,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600));
            std::size_t written=0;while(written<payload.size()){const auto n=write(file.value,payload.data()+written,payload.size()-written);require(n>0,"cannot write source witness");written+=static_cast<std::size_t>(n);}
            require(file.value>=0&&fsync(file.value)==0,"cannot create source witness");
        } else require(symlinkat("/etc/passwd",project_fd.value,filename)==0,"cannot create symlink witness");
        pb::StorageSnapshotPrepare snapshot; *snapshot.mutable_source_object()=project;
        snapshot.mutable_snapshot_object()->set_kind(pb::StorageObject::SNAPSHOT);
        snapshot.mutable_snapshot_object()->set_id(object_id()); snapshot.set_operation_id(random_bytes(16));
        snapshot.set_request_digest(digest(snapshot.SerializeAsString()));
        request.Clear(); *request.mutable_snapshot_prepare()=snapshot;
        const auto snapshot_state=storage.call(std::move(request));
        pb::StorageObject staging; staging.set_kind(pb::StorageObject::COPY_STAGING); staging.set_id(object_id());
        request.Clear(); request.mutable_copy_stage_prepare()->mutable_object()->CopyFrom(staging);
        request.mutable_copy_stage_prepare()->mutable_repository()->CopyFrom(repo);
        (void)storage.call(std::move(request));
        pb::StorageCopyStageBegin intent; *intent.mutable_snapshot_object()=snapshot.snapshot_object();
        *intent.mutable_destination_object()=staging; intent.set_operation_id(random_bytes(16));
        intent.set_request_digest(digest(intent.SerializeAsString())); intent.set_maximum_bytes(32*1024*1024); intent.set_maximum_entries(32);
        if(mode=="symlink") {
            try { (void)spatial::copy_registered_snapshot_to_staging(storage,resources,intent); }
            catch (const spatial::WorldError& error) {
                require(error.code==spatial::wire::INVALID_ARGUMENT,"symlink rejection did not report the typed helper cause");
                pb::StorageCopyStageInspect inspect; inspect.set_operation_id(intent.operation_id()); inspect.set_request_digest(intent.request_digest());
                const auto receipt=storage.inspect_copy_stage(inspect);
                require(receipt.state()==pb::StorageCopyStageReceipt::UNCERTAIN,"malicious tree did not remain explicitly uncertain");
                std::cout<<"SYMLINK_REJECTED UNCERTAIN\n"; return 0;
            }
            throw std::runtime_error("copy worker accepted a symlink");
        }
        const auto sealed=spatial::copy_registered_snapshot_to_staging(storage,resources,intent);
        require(sealed.state()==pb::StorageCopyStageReceipt::SEALED&&sealed.actual_bytes()==payload.size()&&sealed.actual_entries()==1&&sealed.content_digest().size()==32,"copy worker returned an invalid sealed receipt");
        Fd source(storage.open_object(snapshot_state));
        pb::StorageOwnerRequest inspect_destination; inspect_destination.mutable_inspect()->mutable_object()->CopyFrom(staging);
        const auto destination_state=storage.call(std::move(inspect_destination)); Fd destination(storage.open_object(destination_state));
        struct stat source_stat{},destination_stat{};
        require(fstatat(source.value,filename,&source_stat,AT_SYMLINK_NOFOLLOW)==0&&
            fstatat(destination.value,filename,&destination_stat,AT_SYMLINK_NOFOLLOW)==0&&
            (source_stat.st_dev!=destination_stat.st_dev||source_stat.st_ino!=destination_stat.st_ino),"copy reused source inode");
        Fd copied(openat(destination.value,filename,O_RDONLY|O_NOFOLLOW|O_CLOEXEC)); std::vector<char> bytes(payload.size());
        std::size_t read_count=0;while(read_count<bytes.size()){const auto n=read(copied.value,bytes.data()+read_count,bytes.size()-read_count);require(n>0,"copied file truncated");read_count+=static_cast<std::size_t>(n);}
        require(copied.value>=0&&bytes==payload,"copied bytes differ");
        const auto retry=spatial::copy_registered_snapshot_to_staging(storage,resources,intent);
        require(retry.SerializeAsString()==sealed.SerializeAsString(),"identical retry changed sealed receipt");
        std::cout<<"SEALED "<<sealed.actual_bytes()<<" "<<sealed.actual_entries()<<" "<<sealed.content_digest().size()<<" retry=identical inode=independent\n";
        return 0;
    } catch(const std::exception& error) { std::cerr<<"copy-worker-production-probe: "<<error.what()<<'\n'; return 1; }
}