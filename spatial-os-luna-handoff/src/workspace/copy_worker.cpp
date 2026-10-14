#include "workspace/copy_worker.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_client.hpp"
#include "world/store.hpp"
#include "process_scope.pb.h"
#include <algorithm>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdexcept>
#include <string>
#include <vector>
namespace spatial {
namespace {
struct Fd { int value=-1; explicit Fd(int v=-1):value(v){} ~Fd(){if(value>=0)close(value);} void close_now(){if(value>=0){::close(value);value=-1;}} Fd(const Fd&)=delete; Fd& operator=(const Fd&)=delete; };
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
 const auto source=storage.object_path(source_state),destination=storage.object_path(destination_state);
 auto receipt=storage.begin_copy_stage(intent);
 if(receipt.state()==StorageCopyStageReceipt::SEALED)return receipt;
 StorageCopyStageStart claim;claim.set_operation_id(intent.operation_id());claim.set_request_digest(intent.request_digest());
 receipt=storage.start_copy_stage(claim);
 if(!receipt.claimed()) {
  if(receipt.state()==StorageCopyStageReceipt::SEALED)return receipt;
  throw WorldError(wire::UNAVAILABLE,"Copy stage is already active or uncertain; inspect receipt");
 }
 try {
  const auto copied=run_copy_tree_worker(resources,source,destination,intent.maximum_bytes(),intent.maximum_entries(),progress);
  StorageCopyStageSeal seal;*seal.mutable_snapshot_object()=intent.snapshot_object();*seal.mutable_destination_object()=intent.destination_object();
  seal.set_operation_id(intent.operation_id());seal.set_request_digest(intent.request_digest());seal.set_actual_bytes(copied.bytes);
  seal.set_actual_entries(copied.entries);seal.set_content_digest(copied.digest);return storage.seal_copy_stage(seal);
 } catch (...) {
  try { StorageCopyStageUncertain uncertain;uncertain.set_operation_id(intent.operation_id());uncertain.set_request_digest(intent.request_digest());(void)storage.mark_copy_stage_uncertain(uncertain); } catch (...) {}
  throw;
 }
}
CopyWorkerReceipt run_copy_tree_worker(WorkspaceResources& resources,const std::filesystem::path& source,
 const std::filesystem::path& destination,std::uint64_t max_bytes,std::uint32_t max_entries,
 const std::function<void()>& progress){
 require(source.is_absolute()&&destination.is_absolute()&&source!=destination&&max_bytes>0&&max_entries>0,"invalid copy worker request");
 const auto src=std::filesystem::canonical(source),dst=std::filesystem::canonical(destination);
 auto nested=[](const std::filesystem::path& x,const std::filesystem::path& y){auto i=x.begin(),j=y.begin();for(;i!=x.end()&&j!=y.end()&&*i==*j;++i,++j){}return i==x.end()||j==y.end();}; require(!nested(src,dst),"copy roots cannot be nested");
 struct stat a{},b{}; Fd sf(open(src.c_str(),O_PATH|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)),df(open(dst.c_str(),O_PATH|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));
 require(sf.value>=0&&df.value>=0&&fstat(sf.value,&a)==0&&fstat(df.value,&b)==0&&S_ISDIR(a.st_mode)&&S_ISDIR(b.st_mode),"cannot pin copy roots");
 require(a.st_dev!=b.st_dev||a.st_ino!=b.st_ino,"copy roots alias");
 const char* bwrap="/usr/local/libexec/spatial-bwrap"; const char* helper="/usr/local/libexec/spatial-copy-tree-worker";
 Fd bf(open(bwrap,O_PATH|O_NOFOLLOW|O_CLOEXEC)),hf(open(helper,O_PATH|O_NOFOLLOW|O_CLOEXEC));
 struct stat bs{},hs{}; require(bf.value>=0&&hf.value>=0&&fstat(bf.value,&bs)==0&&fstat(hf.value,&hs)==0&&S_ISREG(bs.st_mode)&&bs.st_uid==0&&(bs.st_mode&0022)==0&&S_ISREG(hs.st_mode)&&hs.st_uid==0&&(hs.st_mode&0022)==0&&(hs.st_mode&0111)!=0,"trusted worker binaries unavailable");
 std::array<int,2> handles{sf.value,df.value}; auto job=resources.prepare_git_job(handles); auto& scope=job->scope();
 int pipefd[2]; require(pipe2(pipefd,O_CLOEXEC)==0,"cannot create worker receipt pipe"); Fd out(pipefd[0]),writer(pipefd[1]); int flags=fcntl(out.value,F_GETFL);require(flags>=0&&fcntl(out.value,F_SETFL,flags|O_NONBLOCK)==0,"cannot make receipt reader nonblocking");
 Fd nullfd(open("/dev/null",O_RDWR|O_CLOEXEC)); require(nullfd.value>=0,"cannot open worker null device");
 std::vector<std::string> args{bwrap,"--unshare-all","--unshare-user","--disable-userns","--die-with-parent","--new-session","--cap-drop","ALL","--hostname","spatial-copy-worker","--clearenv","--setenv","PATH","/usr/bin:/bin","--setenv","HOME","/nonexistent","--setenv","TMPDIR","/tmp","--setenv","LC_ALL","C","--ro-bind","/usr/bin","/usr/bin","--ro-bind","/usr/lib","/usr/lib","--ro-bind","/usr/lib64","/usr/lib64","--ro-bind","/usr/share","/usr/share","--symlink","usr/bin","/bin","--symlink","usr/lib","/lib","--symlink","usr/lib64","/lib64","--proc","/proc","--dev","/dev","--tmpfs","/tmp","--dir","/source","--dir","/destination","--dir","/usr/local","--dir","/usr/local/libexec","--ro-bind-fd","3","/source","--bind-fd","4","/destination","--ro-bind-fd","5","/usr/local/libexec/spatial-copy-tree-worker","--remount-ro","/","--",helper,std::to_string(max_bytes),std::to_string(max_entries)};
 auto command=args;
 supervisor::v1::AuthorityProcessLaunch launch; launch.set_executable(bwrap);
 for(std::size_t i=1;i<command.size();++i)launch.add_arguments(command[i]);
 launch.add_inherited_fds(3);launch.add_inherited_fds(4);launch.add_inherited_fds(5);
 scope.start(launch,{nullfd.value,writer.value,nullfd.value},{sf.value,df.value,hf.value}); writer.close_now();
 try {
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(300); std::string output; int status=-1;
 while(status<0){if(progress)progress();if(std::chrono::steady_clock::now()>=deadline){(void)scope.stop();throw WorldError(wire::TIMEOUT,"copy worker deadline expired; inspect stage receipt");}
  pollfd p{out.value,POLLIN|POLLHUP,0};int ready=poll(&p,1,20);if(ready<0&&errno!=EINTR){(void)scope.stop();throw std::runtime_error("copy worker poll failed");}
  if(p.revents){auto part=read_pipe(out.value,128);output+=part;require(output.size()<=128,"copy worker receipt too large");}
  if(scope.state().direct_child_exited()){status=scope.exit_status();(void)scope.stop();}
 }
 if(status!=0)throw WorldError(wire::STORAGE_ERROR,"copy worker failed; stage outcome requires inspection");
 while(output.size()<68){if(progress)progress();auto part=read_pipe(out.value,128);output+=part;if(part.empty())break;}
 const auto first=output.find(' '),second=first==std::string::npos?first:output.find(' ',first+1);
 require(first!=std::string::npos&&second!=std::string::npos&&output.size()-second-1==64,"invalid copy worker receipt");
 CopyWorkerReceipt result;result.bytes=std::stoull(output.substr(0,first));result.entries=static_cast<std::uint32_t>(std::stoul(output.substr(first+1,second-first-1)));
 const auto hex=output.substr(second+1);result.digest.reserve(32);for(std::size_t i=0;i<hex.size();i+=2){unsigned value=static_cast<unsigned>(std::stoul(hex.substr(i,2),nullptr,16));result.digest.push_back(static_cast<char>(value));}
 require(result.bytes<=max_bytes&&result.entries<=max_entries,"copy worker exceeded declared bounds");return result;
 } catch (...) { if (!scope.state().direct_child_exited()) (void)scope.stop(); throw; }
}
}
