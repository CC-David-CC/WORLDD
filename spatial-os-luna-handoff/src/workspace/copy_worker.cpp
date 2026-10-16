#include "workspace/copy_worker.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_client.hpp"
#include "world/store.hpp"
#include "process_scope.pb.h"
#include "copy_worker.pb.h"
#include <google/protobuf/unknown_field_set.h>
#include <sys/mman.h>
#include <algorithm>
#include <charconv>
#include <string_view>
#include <utility>
#include <linux/btrfs.h>
#include <linux/btrfs_tree.h>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <stdexcept>
#include <string>
#include <vector>
#include <iostream>
#include <set>
namespace spatial {
namespace {
struct Fd { int value=-1; explicit Fd(int v=-1):value(v){} ~Fd(){if(value>=0)close(value);} void close_now(){if(value>=0){::close(value);value=-1;}} Fd(const Fd&)=delete; Fd& operator=(const Fd&)=delete; Fd(Fd&& other) noexcept:value(std::exchange(other.value,-1)){} Fd& operator=(Fd&& other) noexcept {if(this!=&other){if(value>=0)close(value);value=std::exchange(other.value,-1);}return *this;} };
void require(bool v,const char*m){if(!v)throw std::invalid_argument(m);}
std::string read_pipe(int fd,std::size_t limit){
 std::string out; std::array<char,256>b{};
 for(;;){auto n=read(fd,b.data(),b.size());if(n==0)return out;if(n<0&&errno==EINTR)continue;if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return out;if(n<0)throw std::runtime_error("copy worker pipe read failed");require(out.size()+static_cast<size_t>(n)<=limit,"copy worker receipt too large");out.append(b.data(),n);}
}
bool matches_intent(const workspace::v1::StorageCopyStageReceipt& receipt,
                    const workspace::v1::StorageCopyStageBegin& intent,
                    const workspace::v1::StorageObjectState& source,
                    const workspace::v1::StorageObjectState& destination) {
    return receipt.operation_id()==intent.operation_id() &&
        receipt.request_digest()==intent.request_digest() &&
        receipt.snapshot_object().SerializeAsString()==intent.snapshot_object().SerializeAsString() &&
        receipt.destination_object().SerializeAsString()==intent.destination_object().SerializeAsString() &&
        receipt.source_subvolume_id()==source.subvolume_id() &&
        receipt.destination_subvolume_id()==destination.subvolume_id() &&
        receipt.maximum_bytes()==intent.maximum_bytes() &&
        receipt.maximum_entries()==intent.maximum_entries();
}
void require_intent_receipt(const workspace::v1::StorageCopyStageReceipt& receipt,
                            const workspace::v1::StorageCopyStageBegin& intent,
                            const workspace::v1::StorageObjectState& source,
                            const workspace::v1::StorageObjectState& destination,
                            StorageClient& storage) {
    if (!matches_intent(receipt,intent,source,destination)) {
        storage.fence_unavailable();
        throw WorldError(wire::UNAVAILABLE,"Storage owner copy receipt does not match pinned stage intent");
    }
}
}
void validate_copy_stage_receipt(const workspace::v1::StorageCopyStageReceipt& receipt,
    const workspace::v1::StorageCopyStageBegin& intent,const workspace::v1::StorageObjectState& source,
    const workspace::v1::StorageObjectState& destination,StorageClient& storage) {
    require_intent_receipt(receipt,intent,source,destination,storage);
}
workspace::v1::StorageCopyStageReceipt copy_registered_snapshot_to_staging(StorageClient& storage, WorkspaceResources& resources,
 const workspace::v1::StorageCopyStageBegin& intent,const std::function<void()>& progress) {
 using namespace workspace::v1;
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(300);
 auto tick=[&]{if(std::chrono::steady_clock::now()>=deadline)throw WorldError(wire::TIMEOUT,"copy operation deadline expired");if(progress)progress();if(std::chrono::steady_clock::now()>=deadline)throw WorldError(wire::TIMEOUT,"copy operation deadline expired");};
 StorageOwnerRequest source_request; *source_request.mutable_inspect()->mutable_object()=intent.snapshot_object();
 const auto source_state=storage.call(std::move(source_request),tick);
 require(source_state.object().kind()==StorageObject::SNAPSHOT&&source_state.read_only(),"Copy source is not a registered immutable snapshot");
 StorageOwnerRequest destination_request; *destination_request.mutable_inspect()->mutable_object()=intent.destination_object();
 const auto destination_state=storage.call(std::move(destination_request),tick);
 require(destination_state.object().kind()==StorageObject::COPY_STAGING&&!destination_state.read_only(),"Copy destination is not private staging");
 Fd source_fd(storage.open_object(source_state)),destination_fd(storage.open_object(destination_state));
 tick();
 auto receipt=storage.begin_copy_stage(intent);
 tick();
 require_intent_receipt(receipt,intent,source_state,destination_state,storage);
 if(receipt.state()==StorageCopyStageReceipt::SEALED)return receipt;
 StorageCopyStageStart claim;claim.set_operation_id(intent.operation_id());claim.set_request_digest(intent.request_digest());
 tick();
 receipt=storage.start_copy_stage(claim);
 require_intent_receipt(receipt,intent,source_state,destination_state,storage);
 if(!receipt.claimed()) {
  if(receipt.state()==StorageCopyStageReceipt::SEALED)return receipt;
  throw WorldError(wire::UNAVAILABLE,"Copy stage is already active or uncertain; inspect receipt");
 }
 try {
  tick();
  const auto copied=run_copy_tree_worker(resources,source_fd.value,destination_fd.value,source_state.subvolume_id(),
      destination_state.subvolume_id(),intent.maximum_bytes(),intent.maximum_entries(),tick);
  StorageCopyStageSeal seal;*seal.mutable_snapshot_object()=intent.snapshot_object();*seal.mutable_destination_object()=intent.destination_object();
  seal.set_operation_id(intent.operation_id());seal.set_request_digest(intent.request_digest());seal.set_actual_bytes(copied.bytes);
  seal.set_actual_entries(copied.entries);seal.set_content_digest(copied.digest);
  tick();
  auto sealed=storage.seal_copy_stage(seal);
  require_intent_receipt(sealed,intent,source_state,destination_state,storage);
  return sealed;
 } catch (...) {
  try {
   StorageCopyStageUncertain uncertain;uncertain.set_operation_id(intent.operation_id());uncertain.set_request_digest(intent.request_digest());
   const auto marked=storage.mark_copy_stage_uncertain(uncertain);
   require_intent_receipt(marked,intent,source_state,destination_state,storage);
  } catch (...) {}
  throw;
 }
}
struct CopyTreeJob::Impl {
 std::unique_ptr<GitJobResources> job;
 Fd out,diagnostic;
 std::string output,errors;
 bool diagnostics_emitted=false,diagnostics_overflow=false;
 void drain_errors() {
  if(diagnostic.value<0)return;
  std::array<char,1024> bytes{};
  for(;;){auto n=read(diagnostic.value,bytes.data(),bytes.size());if(n<0 && errno==EINTR)continue;
   if(n<=0){if(n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK)diagnostics_overflow=true;break;}
   const auto remaining=65536-errors.size();const auto count=std::min(remaining,static_cast<std::size_t>(n));errors.append(bytes.data(),count);
   if(static_cast<std::size_t>(n)>remaining){diagnostics_overflow=true;break;}
  }
 }
 void emit_errors() {
  if(diagnostics_emitted || errors.empty()){return;}
  diagnostics_emitted=true;
  std::cerr<<"Bounded spatial copy helper diagnostics begin\n"<<errors<<"\nBounded spatial copy helper diagnostics end\n";
 }
 ~Impl(){job.reset();drain_errors();emit_errors();}
 std::uint64_t max_bytes=0;
 std::uint32_t max_entries=0;
 bool completed=false,finalizing=false,derive_head=false;
 unsigned expected_protocol=1;
 std::set<int> expected_classes;
 std::string expected_branch;
 std::vector<std::string> expected_state_validations;
};
CopyTreeJob::CopyTreeJob(WorkspaceResources& resources,int source_fd,int destination_fd,
 std::uint64_t expected_source_subvolume,std::uint64_t expected_destination_subvolume,
 std::uint64_t max_bytes,std::uint32_t max_entries,const workspace::copyworker::v1::StateExportRequest* state_export):impl_(std::make_unique<Impl>()) {
 impl_->max_bytes=max_bytes;impl_->max_entries=max_entries;
 require(source_fd>=0&&destination_fd>=0&&expected_source_subvolume>0&&expected_destination_subvolume>0&&
   expected_source_subvolume!=expected_destination_subvolume&&max_bytes>0&&max_entries>0,"invalid copy worker request");
 struct stat a{},b{}; btrfs_ioctl_get_subvol_info_args source_info{},destination_info{};
 require(fstat(source_fd,&a)==0&&fstat(destination_fd,&b)==0&&S_ISDIR(a.st_mode)&&S_ISDIR(b.st_mode)&&
   ioctl(source_fd,BTRFS_IOC_GET_SUBVOL_INFO,&source_info)>=0&&ioctl(destination_fd,BTRFS_IOC_GET_SUBVOL_INFO,&destination_info)>=0&&
   source_info.treeid==expected_source_subvolume&&destination_info.treeid==expected_destination_subvolume&&
   (source_info.flags&BTRFS_ROOT_SUBVOL_RDONLY)!=0&&(destination_info.flags&BTRFS_ROOT_SUBVOL_RDONLY)==0,
   "pinned copy roots do not match registered snapshot/staging subvolumes");
 auto ancestor=[](int root,int descendant){
  struct stat expected{};if(fstat(root,&expected)<0)throw std::runtime_error("cannot inspect pinned copy root");
  Fd current(openat(descendant,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC));if(current.value<0)throw std::runtime_error("cannot pin copy root ancestry");
  for(unsigned depth=0;depth<4096;++depth){struct stat now{};if(fstat(current.value,&now)<0)throw std::runtime_error("cannot inspect copy root ancestry");
   if(now.st_dev==expected.st_dev&&now.st_ino==expected.st_ino)return true;
   Fd parent(openat(current.value,"..",O_RDONLY|O_DIRECTORY|O_CLOEXEC));if(parent.value<0)throw std::runtime_error("cannot inspect parent copy root");
   struct stat up{};if(fstat(parent.value,&up)<0)throw std::runtime_error("cannot inspect parent copy root");if(up.st_dev==now.st_dev&&up.st_ino==now.st_ino)return false;
   current=std::move(parent);
  }throw std::runtime_error("copy root ancestry depth exceeded");
 };
 require(!ancestor(source_fd,destination_fd)&&!ancestor(destination_fd,source_fd),"copy roots cannot be nested");
 struct stat a_check{},b_check{};require(fstat(source_fd,&a_check)==0&&fstat(destination_fd,&b_check)==0&&
   (a_check.st_dev!=b_check.st_dev||a_check.st_ino!=b_check.st_ino),"copy roots alias");
 const char* bwrap="/usr/local/libexec/spatial-bwrap"; const char* helper="/usr/local/libexec/spatial-copy-tree-worker";
 Fd bf(open(bwrap,O_PATH|O_NOFOLLOW|O_CLOEXEC)),hf(open(helper,O_PATH|O_NOFOLLOW|O_CLOEXEC));
 struct stat bs{},hs{}; require(bf.value>=0&&hf.value>=0&&fstat(bf.value,&bs)==0&&fstat(hf.value,&hs)==0&&S_ISREG(bs.st_mode)&&bs.st_uid==0&&(bs.st_mode&0022)==0&&S_ISREG(hs.st_mode)&&hs.st_uid==0&&(hs.st_mode&0022)==0&&(hs.st_mode&0111)!=0,"trusted worker binaries unavailable");
 spatial::workspace::copyworker::v1::WorkerRequest job_request;impl_->expected_protocol=state_export?2:1;job_request.set_protocol_version(impl_->expected_protocol);job_request.set_maximum_bytes(max_bytes);job_request.set_maximum_entries(max_entries);
 if(state_export){validate_message(*state_export);*job_request.mutable_state_export()=*state_export;}
 std::string job_bytes;require(job_request.SerializeToString(&job_bytes)&&job_bytes.size()<=4096,"cannot serialize bounded worker request");
 Fd job_config(memfd_create("spatial-copy-worker-job",MFD_CLOEXEC|MFD_ALLOW_SEALING));require(job_config.value>=0,"cannot create worker job config");
 std::size_t config_written=0;while(config_written<job_bytes.size()){auto n=write(job_config.value,job_bytes.data()+config_written,job_bytes.size()-config_written);if(n<0&&errno==EINTR)continue;require(n>0,"cannot write worker job config");config_written+=static_cast<std::size_t>(n);}
 require(lseek(job_config.value,0,SEEK_SET)==0&&fcntl(job_config.value,F_ADD_SEALS,F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL)==0,"cannot seal worker job config");
 std::array<int,2> handles{source_fd,destination_fd}; impl_->job=resources.prepare_git_job(handles); auto& scope=impl_->job->scope();
 int pipefd[2]; require(pipe2(pipefd,O_CLOEXEC)==0,"cannot create worker receipt pipe"); Fd out(pipefd[0]),writer(pipefd[1]); int flags=fcntl(out.value,F_GETFL);require(flags>=0&&fcntl(out.value,F_SETFL,flags|O_NONBLOCK)==0,"cannot make receipt reader nonblocking");
 int error_pipe[2];require(pipe2(error_pipe,O_CLOEXEC)==0,"Cannot create worker diagnostic pipe");
 Fd error_read(error_pipe[0]),error_write(error_pipe[1]);require(fcntl(error_read.value,F_SETFL,O_NONBLOCK)==0,"Cannot bound helper diagnostics");
 impl_->diagnostic=std::move(error_read);
 Fd nullfd(open("/dev/null",O_RDWR|O_CLOEXEC)); require(nullfd.value>=0,"cannot open worker null device");
 std::vector<std::string> args{bwrap,"--unshare-all","--unshare-user","--disable-userns","--die-with-parent","--new-session","--cap-drop","ALL","--hostname","spatial-copy-worker","--clearenv","--setenv","PATH","/usr/bin:/bin","--setenv","HOME","/nonexistent","--setenv","TMPDIR","/tmp","--setenv","LC_ALL","C","--setenv","ASAN_OPTIONS","detect_leaks=1:abort_on_error=1","--setenv","UBSAN_OPTIONS","halt_on_error=1:print_stacktrace=1","--ro-bind","/usr/bin","/usr/bin","--ro-bind","/usr/lib","/usr/lib","--ro-bind","/usr/lib64","/usr/lib64","--ro-bind","/usr/share","/usr/share","--symlink","usr/bin","/bin","--symlink","usr/lib","/lib","--symlink","usr/lib64","/lib64","--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/source","--dir","/destination","--dir","/usr/local","--dir","/usr/local/libexec","--ro-bind-fd","3","/source","--bind-fd","4","/destination","--ro-bind-fd","5","/usr/local/libexec/spatial-copy-tree-worker","--ro-bind-data","7","/job.pb","--remount-ro","/","--",helper};
 auto command=args;
 supervisor::v1::AuthorityProcessLaunch launch; launch.set_executable("/proc/self/fd/6");
 for(std::size_t i=1;i<command.size();++i)launch.add_arguments(command[i]);
 launch.add_inherited_fds(6);launch.add_inherited_fds(3);launch.add_inherited_fds(4);launch.add_inherited_fds(5);launch.add_inherited_fds(7);
 scope.start(launch,{nullfd.value,writer.value,error_write.value},{bf.value,source_fd,destination_fd,hf.value,job_config.value});
 writer.close_now();error_write.close_now();
 impl_->out=std::move(out);
}
CopyTreeJob::CopyTreeJob(WorkspaceResources& resources,const std::vector<int>& directories,
 const workspace::copyworker::v1::WorkerRequest& request):impl_(std::make_unique<Impl>()) {
 require(request.protocol_version()==2 && request.has_finalization() && !request.has_state_export() && directories.size()>=2 && directories.size()<=5 &&
         directories.size()==static_cast<std::size_t>(request.finalization().classes_size()),"Invalid finalization handles");
 impl_->max_bytes=request.maximum_bytes();impl_->max_entries=request.maximum_entries();impl_->finalizing=true;impl_->expected_protocol=2;
 for(const auto data_class:request.finalization().classes())require(impl_->expected_classes.insert(data_class).second,"Duplicate finalization job class");
 impl_->expected_branch=request.finalization().expected_branch();impl_->derive_head=request.finalization().derive_head_from_snapshot();
 require(request.finalization().state_companions_size()<=32,"Finalization companion count exceeds bound");
 std::set<std::string> state_keys;
 for(const auto& state:request.finalization().state_companions()) {
  validate_message(state);require(state_keys.insert(state.key()).second,"Finalization requested duplicate state companion");
  impl_->expected_state_validations.push_back(state.SerializeAsString());
 }
 require(impl_->expected_state_validations.empty() || impl_->expected_classes.contains(workspace::copy::v1::APPLICATION_STATE),
         "Unselected state companions reached finalization");
 require(!impl_->derive_head || impl_->expected_branch.empty(),"Derived snapshot HEAD cannot also pin a registry branch");
 const char* bwrap="/usr/local/libexec/spatial-bwrap";const char* helper="/usr/local/libexec/spatial-copy-finalize-worker";
 Fd binary(open(bwrap,O_PATH|O_NOFOLLOW|O_CLOEXEC)),worker(open(helper,O_PATH|O_NOFOLLOW|O_CLOEXEC));
 struct stat a{},b{};
 require(binary.value>=0 && worker.value>=0 && fstat(binary.value,&a)==0 && fstat(worker.value,&b)==0 &&
         S_ISREG(a.st_mode) && S_ISREG(b.st_mode) && a.st_uid==0 && b.st_uid==0 &&
         !(a.st_mode&0022) && !(b.st_mode&0022) && (b.st_mode&0111),"Trusted finalization binaries unavailable");
 const auto bytes=request.SerializeAsString();require(!bytes.empty() && bytes.size()<=16384,"Finalization job exceeds protobuf bound");
 Fd config(memfd_create("spatial-copy-finalize",MFD_CLOEXEC|MFD_ALLOW_SEALING));require(config.value>=0,"Cannot create finalization input");
 std::size_t offset=0;while(offset<bytes.size()){auto n=write(config.value,bytes.data()+offset,bytes.size()-offset);if(n<0&&errno==EINTR)continue;require(n>0,"Cannot write finalization input");offset+=n;}
 require(lseek(config.value,0,SEEK_SET)==0 &&
         fcntl(config.value,F_ADD_SEALS,F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL)==0,"Cannot rewind and seal finalization input");
 impl_->job=resources.prepare_git_job(directories);auto& scope=impl_->job->scope();
 int pipes[2];require(pipe2(pipes,O_CLOEXEC)==0,"Cannot create finalization receipt pipe");Fd out(pipes[0]),writer(pipes[1]);
 require(fcntl(out.value,F_SETFL,O_NONBLOCK)==0,"Cannot bound finalization receipt");
 int error_pipe[2];require(pipe2(error_pipe,O_CLOEXEC)==0,"Cannot create worker diagnostic pipe");
 Fd error_read(error_pipe[0]),error_write(error_pipe[1]);require(fcntl(error_read.value,F_SETFL,O_NONBLOCK)==0,"Cannot bound helper diagnostics");
 impl_->diagnostic=std::move(error_read);
 Fd nullfd(open("/dev/null",O_RDWR|O_CLOEXEC));require(nullfd.value>=0,"Cannot open worker null device");
 std::vector<std::string> args{"--unshare-all","--unshare-user","--disable-userns","--die-with-parent","--new-session", "--cap-drop","ALL",
   "--clearenv","--setenv","PATH","/usr/bin:/bin","--setenv","HOME","/nonexistent","--setenv","LC_ALL","C","--setenv","ASAN_OPTIONS","detect_leaks=1:abort_on_error=1","--setenv","UBSAN_OPTIONS","halt_on_error=1:print_stacktrace=1",
   "--setenv","GIT_CONFIG_NOSYSTEM","1","--setenv","GIT_CONFIG_GLOBAL","/dev/null","--setenv","GIT_CONFIG_SYSTEM","/dev/null",
   "--setenv","GIT_TERMINAL_PROMPT","0","--setenv","GIT_NO_LAZY_FETCH","1","--setenv","GIT_NO_REPLACE_OBJECTS","1",
   "--ro-bind","/usr/bin","/usr/bin","--ro-bind","/usr/lib","/usr/lib","--ro-bind","/usr/lib64","/usr/lib64","--ro-bind","/usr/share","/usr/share",
   "--symlink","usr/bin","/bin","--symlink","usr/lib","/lib","--symlink","usr/lib64","/lib64","--proc","/proc","--dev","/dev","--tmpfs","/tmp",
   "--dir","/usr/local","--dir","/usr/local/libexec","--ro-bind-fd","5",helper,"--ro-bind-data","7","/job.pb","--dir","/domains"};
 supervisor::v1::AuthorityProcessLaunch launch;launch.set_executable("/proc/self/fd/6");
 launch.add_inherited_fds(6);launch.add_inherited_fds(5);launch.add_inherited_fds(7);
 std::vector<int> inherited{binary.value,worker.value,config.value};
 for(std::size_t n=0;n<directories.size();++n) {
   struct stat info{};btrfs_ioctl_get_subvol_info_args subvolume{};
   require(fstat(directories[n],&info)==0 && S_ISDIR(info.st_mode) &&
           ioctl(directories[n],BTRFS_IOC_GET_SUBVOL_INFO,&subvolume)>=0 && subvolume.treeid>0,
           "Finalization requires pinned registered staging roots");
   const auto descriptor=8+static_cast<unsigned>(n);const auto path="/domains/"+std::to_string(request.finalization().classes(static_cast<int>(n)));
   args.insert(args.end(),{"--dir",path,"--bind-fd",std::to_string(descriptor),path});
   launch.add_inherited_fds(descriptor);inherited.push_back(directories[n]);
 }
 args.insert(args.end(),{"--remount-ro","/","--",helper});for(const auto& arg:args)launch.add_arguments(arg);
 scope.start(launch,{nullfd.value,writer.value,error_write.value},inherited);writer.close_now();error_write.close_now();impl_->out=std::move(out);
}
CopyTreeJob::~CopyTreeJob()=default;
supervisor::v1::ProcessScopeState CopyTreeJob::identity() const {return impl_->job->scope().state();}
std::optional<CopyWorkerReceipt> CopyTreeJob::poll() {
 require(!impl_->completed,"Copy worker result was already consumed");
 auto& scope=impl_->job->scope();
 auto& output=impl_->output;
 impl_->drain_errors();
 if(impl_->diagnostics_overflow){impl_->emit_errors();throw WorldError(wire::STORAGE_ERROR,"Copy helper diagnostics exceeded their bound");}
 const auto max_bytes=impl_->max_bytes;const auto max_entries=impl_->max_entries;
 const std::size_t result_bound=impl_->finalizing?16388:4100;
 output+=read_pipe(impl_->out.value,result_bound);
 require(output.size()<=result_bound,"copy worker protobuf result too large");
 if(!scope.state().direct_child_exited())return std::nullopt;
 const auto status=scope.exit_status();
 (void)scope.stop();
 output+=read_pipe(impl_->out.value,result_bound);
 require(output.size()<=result_bound,"copy worker protobuf result too large after exit");
 impl_->completed=true;
 impl_->drain_errors();impl_->emit_errors();
 if(impl_->diagnostics_overflow || impl_->errors.find("AddressSanitizer")!=std::string::npos ||
    impl_->errors.find("LeakSanitizer")!=std::string::npos || impl_->errors.find("UndefinedBehaviorSanitizer")!=std::string::npos ||
    impl_->errors.find("runtime error:")!=std::string::npos)
    throw WorldError(wire::STORAGE_ERROR,"Copy helper emitted sanitizer diagnostics");
 if(status!=0){const auto message="copy worker failed (exit_status="+std::to_string(status)+"); stage outcome requires inspection";throw WorldError(wire::STORAGE_ERROR,message.c_str());}
 require(output.size()>=4,"missing copy worker protobuf frame");
 auto u=[](unsigned char c){return static_cast<std::uint32_t>(c);};
 const std::uint32_t frame_size=(u(output[0])<<24)|(u(output[1])<<16)|(u(output[2])<<8)|u(output[3]);
 require(frame_size>0&&frame_size<=result_bound-4&&output.size()==4+frame_size,"invalid copy worker frame length");
 spatial::workspace::copyworker::v1::WorkerResult result;
 require(result.ParseFromArray(output.data()+4,static_cast<int>(frame_size))&&result.protocol_version()==impl_->expected_protocol&&result.outcome_case()!=spatial::workspace::copyworker::v1::WorkerResult::OUTCOME_NOT_SET&&result.GetReflection()->GetUnknownFields(result).field_count()==0,"invalid copy worker protobuf result");
 if(result.has_finalized()) {
  require(impl_->finalizing,"Copy-mode worker returned a finalization outcome");
  validate_message(result.finalized());
  require(result.finalized().domains_size()>=2 && result.finalized().domains_size()<=5 &&
          (result.finalized().head().size()==40 || result.finalized().head().size()==64) &&
          result.finalized().head().find_first_not_of("0123456789abcdef")==std::string::npos &&
          (impl_->derive_head || result.finalized().branch()==impl_->expected_branch),"Malformed finalization evidence");
  const auto& branch=result.finalized().branch();
  const auto kind=result.finalized().head_kind();
  require((kind==workspace::copyworker::v1::SYMBOLIC_BRANCH && !branch.empty() && branch.size()<=1024 &&
           branch.find_first_of("\r\n\0",0,3)==std::string::npos) ||
          (kind==workspace::copyworker::v1::DETACHED_COMMIT && branch.empty() && impl_->derive_head),
          "Finalization HEAD kind and branch disagree");
  CopyWorkerReceipt receipt;receipt.validation=result.finalized().SerializeAsString();
  std::set<int> returned_classes;
  for(const auto& domain:result.finalized().domains()) {
   require(returned_classes.insert(domain.data_class()).second,"Finalization repeated a data class");
   require(domain.content_digest().size()==32 && domain.actual_bytes()<=max_bytes-receipt.bytes &&
           domain.actual_entries()<=max_entries-receipt.entries,"Finalization exceeds its operation bounds");
   receipt.bytes+=domain.actual_bytes();receipt.entries+=domain.actual_entries();
  }
  require(returned_classes==impl_->expected_classes,"Finalization classes differ from the sealed job input");
  require(static_cast<std::size_t>(result.finalized().state_companions_size())==impl_->expected_state_validations.size(),
          "Finalization added or omitted a state companion");
  for(int index=0;index<result.finalized().state_companions_size();++index) {
   const auto& state=result.finalized().state_companions(index);
   require(state.SerializeAsString()==impl_->expected_state_validations[index] &&
           state.content_digest().size()==32 && state.actual_bytes()<=max_bytes-receipt.bytes &&
           state.actual_entries()<=max_entries-receipt.entries,"Finalization state receipt differs from the sealed job input or aggregate bounds");
   receipt.bytes+=state.actual_bytes();receipt.entries+=state.actual_entries();
  }
  return receipt;
 }
 if(result.has_failure()) {
  const auto reason=result.failure().reason();
  require(reason>=spatial::workspace::copyworker::v1::FAILURE_SYMLINK&&reason<=spatial::workspace::copyworker::v1::FAILURE_DESCRIPTOR_LIMIT&&result.failure().GetReflection()->GetUnknownFields(result.failure()).field_count()==0,"invalid copy worker failure enum");
  const auto code=reason==spatial::workspace::copyworker::v1::FAILURE_BYTE_LIMIT||reason==spatial::workspace::copyworker::v1::FAILURE_ENTRY_LIMIT||reason==spatial::workspace::copyworker::v1::FAILURE_DEPTH_LIMIT||reason==spatial::workspace::copyworker::v1::FAILURE_DESCRIPTOR_LIMIT?wire::RESOURCE_EXHAUSTED:
      reason==spatial::workspace::copyworker::v1::FAILURE_SYMLINK?wire::INVALID_ARGUMENT:wire::STORAGE_ERROR;
  throw WorldError(code,reason==spatial::workspace::copyworker::v1::FAILURE_SYMLINK?"Copy source contains a symbolic link":"Copy worker rejected source tree");
 }
 require(!impl_->finalizing && result.has_copied(),"Finalization-mode worker returned a copy outcome");
 require(result.copied().GetReflection()->GetUnknownFields(result.copied()).field_count()==0&&result.copied().content_digest().size()==32,"invalid typed copy receipt");
 CopyWorkerReceipt copied;copied.bytes=result.copied().actual_bytes();copied.entries=result.copied().actual_entries();copied.digest=result.copied().content_digest();
 require(copied.bytes<=max_bytes&&copied.entries<=max_entries,"copy worker exceeded declared bounds");return copied;
}
CopyWorkerReceipt run_copy_tree_worker(WorkspaceResources& resources,int source_fd,int destination_fd,
 std::uint64_t source_subvolume,std::uint64_t destination_subvolume,
 std::uint64_t max_bytes,std::uint32_t max_entries,const std::function<void()>& progress) {
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(300);
 CopyTreeJob job(resources,source_fd,destination_fd,source_subvolume,destination_subvolume,max_bytes,max_entries);
 for(;;) {
  if(std::chrono::steady_clock::now()>=deadline)throw WorldError(wire::TIMEOUT,"copy worker absolute deadline expired");
  if(progress)progress();
  if(auto result=job.poll())return *result;
  poll(nullptr,0,20);
 }
}
}
