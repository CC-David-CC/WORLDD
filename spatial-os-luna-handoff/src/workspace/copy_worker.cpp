#include "workspace/copy_worker.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_client.hpp"
#include "world/store.hpp"
#include "process_scope.pb.h"
#include <algorithm>
#include <charconv>
#include <string_view>
#include <utility>
#include <linux/btrfs.h>
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
namespace spatial {
namespace {
struct Fd { int value=-1; explicit Fd(int v=-1):value(v){} ~Fd(){if(value>=0)close(value);} void close_now(){if(value>=0){::close(value);value=-1;}} Fd(const Fd&)=delete; Fd& operator=(const Fd&)=delete; Fd(Fd&& other) noexcept:value(std::exchange(other.value,-1)){} Fd& operator=(Fd&& other) noexcept {if(this!=&other){if(value>=0)close(value);value=std::exchange(other.value,-1);}return *this;} };
void require(bool v,const char*m){if(!v)throw std::invalid_argument(m);}
std::string read_pipe(int fd,std::size_t limit){
 std::string out; std::array<char,256>b{};
 for(;;){auto n=read(fd,b.data(),b.size());if(n==0)return out;if(n<0&&errno==EINTR)continue;if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return out;if(n<0)throw std::runtime_error("copy worker pipe read failed");require(out.size()+static_cast<size_t>(n)<=limit,"copy worker receipt too large");out.append(b.data(),n);}
}
}
workspace::v1::StorageCopyStageReceipt copy_registered_snapshot_to_staging(StorageClient& storage, WorkspaceResources& resources,
 const workspace::v1::StorageCopyStageBegin& intent,const std::function<void()>& progress) {
 using namespace workspace::v1;
 StorageOwnerRequest source_request; *source_request.mutable_inspect()->mutable_object()=intent.snapshot_object();
 const auto source_state=storage.call(std::move(source_request),progress);
 require(source_state.object().kind()==StorageObject::SNAPSHOT&&source_state.read_only(),"Copy source is not a registered immutable snapshot");
 StorageOwnerRequest destination_request; *destination_request.mutable_inspect()->mutable_object()=intent.destination_object();
 const auto destination_state=storage.call(std::move(destination_request),progress);
 require(destination_state.object().kind()==StorageObject::COPY_STAGING&&!destination_state.read_only(),"Copy destination is not private staging");
 Fd source_fd(storage.open_object(source_state)),destination_fd(storage.open_object(destination_state));
 auto receipt=storage.begin_copy_stage(intent);
 if(receipt.state()==StorageCopyStageReceipt::SEALED)return receipt;
 StorageCopyStageStart claim;claim.set_operation_id(intent.operation_id());claim.set_request_digest(intent.request_digest());
 receipt=storage.start_copy_stage(claim);
 if(!receipt.claimed()) {
  if(receipt.state()==StorageCopyStageReceipt::SEALED)return receipt;
  throw WorldError(wire::UNAVAILABLE,"Copy stage is already active or uncertain; inspect receipt");
 }
 try {
  const auto copied=run_copy_tree_worker(resources,source_fd.value,destination_fd.value,source_state.subvolume_id(),
      destination_state.subvolume_id(),intent.maximum_bytes(),intent.maximum_entries(),progress);
  StorageCopyStageSeal seal;*seal.mutable_snapshot_object()=intent.snapshot_object();*seal.mutable_destination_object()=intent.destination_object();
  seal.set_operation_id(intent.operation_id());seal.set_request_digest(intent.request_digest());seal.set_actual_bytes(copied.bytes);
  seal.set_actual_entries(copied.entries);seal.set_content_digest(copied.digest);return storage.seal_copy_stage(seal);
 } catch (...) {
  try { StorageCopyStageUncertain uncertain;uncertain.set_operation_id(intent.operation_id());uncertain.set_request_digest(intent.request_digest());(void)storage.mark_copy_stage_uncertain(uncertain); } catch (...) {}
  throw;
 }
}
CopyWorkerReceipt run_copy_tree_worker(WorkspaceResources& resources,int source_fd,int destination_fd,
 std::uint64_t expected_source_subvolume,std::uint64_t expected_destination_subvolume,
 std::uint64_t max_bytes,std::uint32_t max_entries,const std::function<void()>& progress){
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(300);
 auto tick=[&]{if(std::chrono::steady_clock::now()>=deadline)throw WorldError(wire::TIMEOUT,"copy worker absolute deadline expired");if(progress)progress();if(std::chrono::steady_clock::now()>=deadline)throw WorldError(wire::TIMEOUT,"copy worker absolute deadline expired");};
 require(source_fd>=0&&destination_fd>=0&&expected_source_subvolume>0&&expected_destination_subvolume>0&&
   expected_source_subvolume!=expected_destination_subvolume&&max_bytes>0&&max_entries>0,"invalid copy worker request");
 struct stat a{},b{}; btrfs_ioctl_get_subvol_info_args source_info{},destination_info{};
 require(fstat(source_fd,&a)==0&&fstat(destination_fd,&b)==0&&S_ISDIR(a.st_mode)&&S_ISDIR(b.st_mode)&&
   ioctl(source_fd,BTRFS_IOC_GET_SUBVOL_INFO,&source_info)==0&&ioctl(destination_fd,BTRFS_IOC_GET_SUBVOL_INFO,&destination_info)==0&&
   source_info.treeid==expected_source_subvolume&&destination_info.treeid==expected_destination_subvolume&&
   (source_info.flags&BTRFS_SUBVOL_RDONLY)!=0&&(destination_info.flags&BTRFS_SUBVOL_RDONLY)==0,
   "pinned copy roots do not match registered snapshot/staging subvolumes");
 auto ancestor=[](int root,int descendant){
  struct stat expected{};if(fstat(root,&expected)<0)return false;
  Fd current(openat(descendant,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC));if(current.value<0)return false;
  for(unsigned depth=0;depth<4096;++depth){struct stat now{};if(fstat(current.value,&now)<0)return false;
   if(now.st_dev==expected.st_dev&&now.st_ino==expected.st_ino)return true;
   Fd parent(openat(current.value,"..",O_RDONLY|O_DIRECTORY|O_CLOEXEC));if(parent.value<0)return false;
   struct stat up{};if(fstat(parent.value,&up)<0)return false;if(up.st_dev==now.st_dev&&up.st_ino==now.st_ino)return false;
   current=std::move(parent);
  }return true;
 };
 require(!ancestor(source_fd,destination_fd)&&!ancestor(destination_fd,source_fd),"copy roots cannot be nested");
 struct stat a_check{},b_check{};require(fstat(source_fd,&a_check)==0&&fstat(destination_fd,&b_check)==0&&
   (a_check.st_dev!=b_check.st_dev||a_check.st_ino!=b_check.st_ino),"copy roots alias");
 const char* bwrap="/usr/local/libexec/spatial-bwrap"; const char* helper="/usr/local/libexec/spatial-copy-tree-worker";
 Fd bf(open(bwrap,O_PATH|O_NOFOLLOW|O_CLOEXEC)),hf(open(helper,O_PATH|O_NOFOLLOW|O_CLOEXEC));
 struct stat bs{},hs{}; require(bf.value>=0&&hf.value>=0&&fstat(bf.value,&bs)==0&&fstat(hf.value,&hs)==0&&S_ISREG(bs.st_mode)&&bs.st_uid==0&&(bs.st_mode&0022)==0&&S_ISREG(hs.st_mode)&&hs.st_uid==0&&(hs.st_mode&0022)==0&&(hs.st_mode&0111)!=0,"trusted worker binaries unavailable");
 std::array<int,2> handles{source_fd,destination_fd}; auto job=resources.prepare_git_job(handles); auto& scope=job->scope();
 int pipefd[2]; require(pipe2(pipefd,O_CLOEXEC)==0,"cannot create worker receipt pipe"); Fd out(pipefd[0]),writer(pipefd[1]); int flags=fcntl(out.value,F_GETFL);require(flags>=0&&fcntl(out.value,F_SETFL,flags|O_NONBLOCK)==0,"cannot make receipt reader nonblocking");
 Fd nullfd(open("/dev/null",O_RDWR|O_CLOEXEC)); require(nullfd.value>=0,"cannot open worker null device");
 std::vector<std::string> args{bwrap,"--unshare-all","--unshare-user","--disable-userns","--die-with-parent","--new-session","--cap-drop","ALL","--hostname","spatial-copy-worker","--clearenv","--setenv","PATH","/usr/bin:/bin","--setenv","HOME","/nonexistent","--setenv","TMPDIR","/tmp","--setenv","LC_ALL","C","--ro-bind","/usr/bin","/usr/bin","--ro-bind","/usr/lib","/usr/lib","--ro-bind","/usr/lib64","/usr/lib64","--ro-bind","/usr/share","/usr/share","--symlink","usr/bin","/bin","--symlink","usr/lib","/lib","--symlink","usr/lib64","/lib64","--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/source","--dir","/destination","--dir","/usr/local","--dir","/usr/local/libexec","--ro-bind-fd","3","/source","--bind-fd","4","/destination","--ro-bind-fd","5","/usr/local/libexec/spatial-copy-tree-worker","--remount-ro","/","--",helper,std::to_string(max_bytes),std::to_string(max_entries)};
 auto command=args;
 supervisor::v1::AuthorityProcessLaunch launch; launch.set_executable("/proc/self/fd/6");
 for(std::size_t i=1;i<command.size();++i)launch.add_arguments(command[i]);
 launch.add_inherited_fds(6);launch.add_inherited_fds(3);launch.add_inherited_fds(4);launch.add_inherited_fds(5);
 tick();
 scope.start(launch,{nullfd.value,writer.value,nullfd.value},{bf.value,source_fd,destination_fd,hf.value});
 writer.close_now();
 try {
 tick(); std::string output; int status=-1;
 while(status<0){tick();
  pollfd p{out.value,POLLIN|POLLHUP,0};int ready=poll(&p,1,20);if(ready<0&&errno!=EINTR){(void)scope.stop();throw std::runtime_error("copy worker poll failed");}
  if(p.revents){auto part=read_pipe(out.value,128);output+=part;require(output.size()<=128,"copy worker receipt too large");}
  if(scope.state().direct_child_exited()){status=scope.exit_status();(void)scope.stop();}
 }
 if(status!=0)throw WorldError(wire::STORAGE_ERROR,"copy worker failed; stage outcome requires inspection");
 while(output.size()<68){tick();auto part=read_pipe(out.value,128);output+=part;if(part.empty())break;}
 const auto first=output.find(' '),second=first==std::string::npos?first:output.find(' ',first+1);
 require(first!=std::string::npos&&second!=std::string::npos&&output.find(' ',second+1)==std::string::npos&&output.size()-second-1==64,
         "invalid copy worker receipt");
 auto parse_unsigned=[](std::string_view text,std::uint64_t maximum){
  require(!text.empty(),"empty copy worker number");
  for(unsigned char c:text)require(c>='0'&&c<='9',"invalid copy worker number");
  std::uint64_t value=0;const auto [end,error]=std::from_chars(text.data(),text.data()+text.size(),value);
  require(error==std::errc{}&&end==text.data()+text.size()&&value<=maximum,"copy worker number out of range");return value;
 };
 CopyWorkerReceipt result;result.bytes=parse_unsigned(std::string_view(output).substr(0,first),max_bytes);
 result.entries=static_cast<std::uint32_t>(parse_unsigned(std::string_view(output).substr(first+1,second-first-1),max_entries));
 const auto hex=std::string_view(output).substr(second+1);result.digest.reserve(32);
 for(std::size_t i=0;i<hex.size();i+=2){auto digit=[](char c)->unsigned {if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;throw std::invalid_argument("invalid copy worker digest");};result.digest.push_back(static_cast<char>((digit(hex[i])<<4)|digit(hex[i+1])));}
 require(result.bytes<=max_bytes&&result.entries<=max_entries,"copy worker exceeded declared bounds");return result;
 } catch (...) { if (!scope.state().direct_child_exited()) (void)scope.stop(); throw; }
}
}
