#include "workspace/broker_copy_cleanup.hpp"
#include "workspace/file_engine.hpp"
#include "workspace/resources.hpp"
#include "workspace/storage_devices.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/statfs.h>
#include <sys/ioctl.h>
#include <linux/btrfs.h>
#include <linux/magic.h>
#include <sys/wait.h>
#include <sys/prctl.h>
#include <poll.h>
#include <sys/syscall.h>
#include <signal.h>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <cstdlib>
#include <new>
#include <exception>
#include <functional>
#include <sstream>
#include <atomic>
#include <cstring>
#include <cstdio>
#include <cerrno>
#include <unistd.h>
#include <array>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <optional>
#include <set>
#include <stdexcept>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>
#include <openssl/evp.h>
#include "supervisor/broker_cleanup_bootstrap.hpp"
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <arpa/inet.h>

static void ignore_startup_signal(int) {}

// Executable-local interposition exercises malformed startup acknowledgements.
extern "C" ssize_t send(int socket_fd, const void* buffer, std::size_t size, int flags) noexcept {
  const char* mode=std::getenv("SPATIAL_TEST_START_ACK_MODE");
  if(mode && size==sizeof(int)) {
    if(std::strcmp(mode,"short")==0)
      return static_cast<ssize_t>(::syscall(SYS_sendto,socket_fd,buffer,size-1,flags,nullptr,0));
    if(std::strcmp(mode,"oversized")==0) {
      const auto sent=::syscall(SYS_sendto,socket_fd,buffer,size,flags,nullptr,0);
      if(sent==static_cast<long>(size)) { const unsigned char extra=0xA5; (void)::syscall(SYS_sendto,socket_fd,&extra,1,flags,nullptr,0); }
      return static_cast<ssize_t>(sent);
    }
    if(std::strcmp(mode,"zero")==0) {
      const int invalid=0;
      return static_cast<ssize_t>(::syscall(SYS_sendto,socket_fd,&invalid,sizeof(invalid),flags,nullptr,0));
    }
    if(std::strcmp(mode,"late")==0) {
      const pid_t waiter=::getppid();
      for(int n=0;n<30;++n) {
        const timespec pause{0,100000000};
        (void)::syscall(SYS_nanosleep,&pause,nullptr);
        (void)::syscall(SYS_kill,waiter,SIGUSR1);
      }
    }
  }
  return static_cast<ssize_t>(::syscall(SYS_sendto,socket_fd,buffer,size,flags,nullptr,0));
}

using spatial::FileOperationJournal;
using spatial::BrokerCopyCleanupParticipant;
using spatial::supervisor::ProcessScope;
using namespace spatial::workspace::v1;
using namespace spatial::supervisor::v1;
namespace {
unsigned test_device_major(dev_t value) { return major(value); }
unsigned test_device_minor(dev_t value) { return minor(value); }
#undef major
#undef minor
class RecoveryTestAuthority final : public spatial::FileAuthority {
 public:
  spatial::workspace::v1::ScopedAuthorityCheckReply check_fresh(
      const spatial::workspace::v1::ScopedAuthorityCheckRequest&) override {
    spatial::workspace::v1::ScopedAuthorityCheckReply reply;
    reply.set_allowed(true); reply.set_revision(1); reply.set_incarnation(std::string(16, 'i'));
    reply.set_epoch(1); return reply;
  }
};
// Interpose the real callee syscall only in this executable. No production hook.
std::atomic<bool> disappear_at_open{false},disappeared_at_open{false};
std::uint64_t disappearance_parent_device=0,disappearance_parent_inode=0;
thread_local long allocation_budget=-1;
std::string active_cleanup_mode="startup";
}
void* operator new(std::size_t size) {
  if(allocation_budget==0) throw std::bad_alloc();
  if(allocation_budget>0)--allocation_budget;
  if(void* value=std::malloc(size?size:1))return value;
  throw std::bad_alloc();
}
void* operator new[](std::size_t size){return ::operator new(size);}
void operator delete(void* value) noexcept {std::free(value);}
void operator delete[](void* value) noexcept {std::free(value);}
void operator delete(void* value,std::size_t) noexcept {std::free(value);}
void operator delete[](void* value,std::size_t) noexcept {std::free(value);}
extern "C" int openat(int directory,const char* name,int flags,...) {
  mode_t mode=0;
  if((flags&O_CREAT) || (flags&O_TMPFILE)==O_TMPFILE) {
    va_list arguments;va_start(arguments,flags);mode=va_arg(arguments,mode_t);va_end(arguments);
  }
  if(disappear_at_open.load(std::memory_order_acquire) && std::strcmp(name,"copy-job")==0) {
    struct stat held{};
    if(fstat(directory,&held)==0 && static_cast<std::uint64_t>(held.st_dev)==disappearance_parent_device &&
        static_cast<std::uint64_t>(held.st_ino)==disappearance_parent_inode &&
        disappear_at_open.exchange(false,std::memory_order_acq_rel)) {
      // complete_stop has drained the ancestor. Remove the actual empty recorded child
      // after participant fstatat, immediately before stop_recovered's actual openat.
      if(unlinkat(directory,name,AT_REMOVEDIR)==0) disappeared_at_open.store(true,std::memory_order_release);
    }
  }
  return static_cast<int>(syscall(SYS_openat,directory,name,flags,mode));
}
namespace {
void check(bool ok,const char* text){if(!ok)throw std::runtime_error(text);}
template<class F> void rejects(F f,const char* text){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed,text);}
template<class F> void deadline_rejects(F f,const char* text){bool expired=false;try{f();}catch(const std::exception& error){expired=std::string(error.what()).find("deadline")!=std::string::npos;}check(expired,text);}
void cleanup_phase(const std::string& mode,const char* phase,std::chrono::steady_clock::time_point began,const std::string& detail={}){
  const auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-began).count();
  std::cerr<<"cleanup_phase mode="<<mode<<" phase="<<phase<<" elapsed_ms="<<elapsed;
  if(!detail.empty())std::cerr<<" detail="<<detail;
  std::cerr<<std::endl;
}
void write_file(const std::filesystem::path& path,const std::string& value){std::ofstream file(path);file<<value;file.close();check(!file.fail(),"fixture write failed");}
std::string read_file(const std::filesystem::path& path){std::ifstream file(path);return {std::istreambuf_iterator<char>(file),{}};}
void wait_for(auto predicate){const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);while(!predicate()){check(std::chrono::steady_clock::now()<until,"fixture deadline");std::this_thread::sleep_for(std::chrono::milliseconds(5));}}
void execute(sqlite3* db,const char* sql){check(sqlite3_exec(db,sql,nullptr,nullptr,nullptr)==SQLITE_OK,"fixture SQL failed");}
struct State {
  std::filesystem::path path;
  int fd=-1;
  State(){
    const auto pattern=(std::filesystem::temp_directory_path()/"spatial-preprune-XXXXXX").string();
    std::vector<char> name(pattern.begin(),pattern.end());name.push_back('\0');
    check(mkdtemp(name.data()),"mkdtemp failed");path=name.data();
    {FileOperationJournal journal(path);}
    sqlite3* db=nullptr;check(sqlite3_open((path/"execution.db").c_str(),&db)==SQLITE_OK,"registry open");
    execute(db,"CREATE TABLE workspaces(id TEXT PRIMARY KEY,worktree TEXT);CREATE TABLE worktrees(id TEXT PRIMARY KEY,state TEXT,path TEXT);"
               "INSERT INTO workspaces VALUES('one','tree-one');");sqlite3_close(db);
    std::filesystem::create_directories(path/"project/.spatial-copy-staging/.spatial-copy-stage-job");
    check(chmod((path/"project/.spatial-copy-staging").c_str(),0700)==0,"staging root mode setup failed");
    write_file(path/"project/source","real source");
    check(sqlite3_open((path/"execution.db").c_str(),&db)==SQLITE_OK,"registry seed");
    const auto insert="INSERT INTO worktrees VALUES('tree-one','ready','"+(path/"project").string()+"');";
    execute(db,insert.c_str());sqlite3_close(db);
    chmod((path/"execution.db").c_str(),0600);write_file(path/"execution.lock","");chmod((path/"execution.lock").c_str(),0600);
    fd=open(path.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);check(fd>=0,"root fd");
  }
  ~State(){
    if(fd>=0)close(fd);
    if(std::uncaught_exceptions()>0){std::fprintf(stderr,"retained_fixture_state=%s\n",path.c_str());return;}
    std::filesystem::remove_all(path);
  }
};
void transfer(int fd,const std::string& value){uint32_t size=value.size();check(write(fd,&size,sizeof(size))==sizeof(size),"header write");check(write(fd,value.data(),value.size())==static_cast<ssize_t>(value.size()),"identity write");}
std::string receive(int fd){uint32_t size=0;check(read(fd,&size,sizeof(size))==sizeof(size)&&size>0&&size<4096,"header read");std::string result(size,'\0');size_t at=0;while(at<size){auto n=read(fd,result.data()+at,size-at);check(n>0,"identity read");at+=n;}return result;}
struct Descendants {ProcessScopeIdentity parent,worker;};
Descendants create_descendants(const std::filesystem::path& root,bool worker){
  int channel[2];check(pipe2(channel,O_CLOEXEC)==0,"pipe");auto owner=fork();check(owner>=0,"fork");
  if(owner==0){close(channel[0]);try{
    auto parent=ProcessScope::prepare_named(root,"spatial-git-workers");parent->enable_children();transfer(channel[1],parent->identity().SerializeAsString());
    std::unique_ptr<ProcessScope> child; // Keep its owner alive until deliberate _exit.
    if(worker){
      child=ProcessScope::prepare_named(root/"spatial-git-workers","copy-job");
      AuthorityProcessLaunch request;request.set_executable(std::filesystem::read_symlink("/proc/self/exe").string());
      request.add_arguments("--orphan-worker");
      int ready[2];check(pipe2(ready,O_CLOEXEC)==0,"worker readiness pipe");
      int null=open("/dev/null",O_RDWR|O_CLOEXEC);check(null>=0,"null");
      child->start(request,{null,ready[1],null});close(null);close(ready[1]);
      pollfd event{ready[0],POLLIN,0};int result=0;
      const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
      for(;;){
        const auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-std::chrono::steady_clock::now()).count();
        if(remaining<=0)break;
        result=poll(&event,1,static_cast<int>(remaining));
        if(result>=0||errno!=EINTR)break;
      }
      char byte=0;check(result==1&&(event.revents&POLLIN)&&read(ready[0],&byte,1)==1&&byte=='R',"live orphan readiness");
      close(ready[0]);transfer(channel[1],child->identity().SerializeAsString());
    }
    close(channel[1]);_exit(0); // Retain real orphan scopes. Deliberately skip their owners' destructors.
  }catch(...){_exit(91);}}
  close(channel[1]);Descendants result;check(result.parent.ParseFromString(receive(channel[0])),"parent proto");
  if(worker) { check(result.worker.ParseFromString(receive(channel[0])),"worker proto"); }
  close(channel[0]);int status=0;bool reaped=false;
  try {wait_for([&]{const auto waited=waitpid(owner,&status,WNOHANG);check(waited>=0,"orphan waitpid");return reaped=waited==owner;});}
  catch(...){
    if(!reaped){
      kill(owner,SIGKILL);
      const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
      while(std::chrono::steady_clock::now()<deadline){
        const auto waited=waitpid(owner,&status,WNOHANG);
        if(waited==owner){reaped=true;break;}
        if(waited<0&&errno!=EINTR)break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
      }
      if(!reaped)std::fprintf(stderr,"orphan_owner_reap_timeout pid=%ld\n",static_cast<long>(owner));
    }
    throw;
  }
  check(WIFEXITED(status)&&WEXITSTATUS(status)==0,"orphan owner");
  const auto parent_path=root/"spatial-git-workers";struct stat actual{};
  check(lstat(parent_path.c_str(),&actual)==0&&S_ISDIR(actual.st_mode)&&
      static_cast<std::uint64_t>(actual.st_dev)==result.parent.device()&&static_cast<std::uint64_t>(actual.st_ino)==result.parent.inode(),"orphan parent identity missing");
  const auto worker_path=parent_path/"copy-job";
  if(worker){
    check(lstat(worker_path.c_str(),&actual)==0&&S_ISDIR(actual.st_mode)&&
        static_cast<std::uint64_t>(actual.st_dev)==result.worker.device()&&static_cast<std::uint64_t>(actual.st_ino)==result.worker.inode(),"live orphan worker identity missing");
    check(read_file(worker_path/"cgroup.events").find("populated 1")!=std::string::npos,"orphan worker is not live");
    std::cerr<<"live_orphan_verified dev="<<actual.st_dev<<" ino="<<actual.st_ino<<" populated=1\n";
  }else{
    check(lstat(worker_path.c_str(),&actual)<0&&errno==ENOENT,"reservation fixture unexpectedly has a worker");
    std::cerr<<"reservation_precondition=absent_worker present_reservation_control=UNRUN\n";
  }
  return result;
}
FileOperationJournal::CopyJobRecord record(const State& state,const ProcessScopeIdentity& broker,const Descendants& ids,bool provisional){
  FileOperationJournal::CopyJobRecord job;job.set_format_version(2);job.set_workspace_id("one");job.set_worktree_id("tree-one");
  job.set_job_id(std::string(16,'j'));job.set_initiating_operation_id(std::string(16,'o'));job.set_initiating_request_sha256(std::string(32,'d'));
  job.set_principal_issuer_id(std::string(32,'i'));job.set_principal_actor_id(std::string(32,'a'));job.set_exact_right(spatial::authority::v1::FILE_COPY);
  struct stat root_info{};check(stat((state.path/"project").c_str(),&root_info)==0,"project stat");
  job.set_worktree_root_device(root_info.st_dev);job.set_worktree_root_inode(root_info.st_ino);job.add_source_path_components("source");
  auto file=[&](FileCopyFilesystemIdentity* value,const std::filesystem::path& path){struct stat info{};check(stat(path.c_str(),&info)==0,"file identity stat");value->set_device(info.st_dev);value->set_inode(info.st_ino);value->set_type(S_ISDIR(info.st_mode)?FileCopyFilesystemIdentity::DIRECTORY:FileCopyFilesystemIdentity::REGULAR_FILE);};
  file(job.mutable_source(),state.path/"project/source");file(job.mutable_destination_directory(),state.path/"project");
  file(job.mutable_staging_root(),state.path/"project/.spatial-copy-staging");
  job.set_destination_basename("copy");job.set_staging_name(".spatial-copy-stage-job");job.set_maximum_bytes(1024);job.set_maximum_entries(8);
  *job.mutable_process_scope_parent()=ids.parent;job.set_broker_root_device(broker.device());job.set_broker_root_inode(broker.inode());job.set_broker_root_boot_id(broker.boot_id());
  job.set_reserved_process_scope_name("copy-job");job.set_created_at_unix_ms(1);job.set_updated_at_unix_ms(1);
  if(provisional)job.set_state(FILE_COPY_JOB_JOURNAL_START_INTENT);
  else{file(job.mutable_staging(),state.path/"project/.spatial-copy-staging/.spatial-copy-stage-job");*job.mutable_process_scope()=ids.worker;job.set_state(FILE_COPY_JOB_JOURNAL_QUEUED);}
  return job;
}
void seed(State& state,const ProcessScopeIdentity& broker,const std::filesystem::path& root,const Descendants& ids,const FileOperationJournal::CopyJobRecord& job){
  struct HeldProject {int fd=-1;~HeldProject(){if(fd>=0)close(fd);}} project;
  project.fd=openat(state.fd,"project",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  struct stat actual{};
  check(project.fd>=0 && fstat(project.fd,&actual)==0 && S_ISDIR(actual.st_mode),"fixture project root open");
  check(job.worktree_id()=="tree-one" && job.worktree_root_device()==static_cast<std::uint64_t>(actual.st_dev) &&
      job.worktree_root_inode()==static_cast<std::uint64_t>(actual.st_ino),"fixture job differs from actual registered project");
  FileOperationJournal journal(state.path);journal.reserve_git_worker_parent(broker.device(),broker.inode(),root,broker.boot_id(),{});
  journal.bind_git_worker_parent(broker.device(),broker.inode(),root,broker.boot_id(),ids.parent.SerializeAsString(),{});
  const auto rejects_root=[&](const auto& candidate) {
    bool rejected=false;
    try {journal.begin_copy_job(candidate,{});}
    catch(const std::runtime_error& error) {
      check(std::strcmp(error.what(),"Copy job root identity is not registered")==0,"fixture root rejected for a different reason");
      rejected=true;
    }
    check(rejected,"copy job root registration guard was bypassed");
    check(!journal.find_copy_job(candidate.workspace_id(),candidate.job_id(),{}),"rejected root inserted a copy job");
  };
  rejects_root(job);
  // Match Store's normal file-engine root registration using the actual opened inode.
  check(journal.bind_root("tree-one",actual.st_dev,actual.st_ino,{}),"fixture actual root registration failed");
  check(journal.bind_root("tree-one",actual.st_dev,actual.st_ino,{}),"fixture root registration is not idempotent");
  check(!journal.bind_root("tree-one",actual.st_dev,static_cast<std::uint64_t>(actual.st_ino)+1,{}),"fixture changed root rebind succeeded");
  auto mismatched=job;mismatched.set_worktree_root_inode(static_cast<std::uint64_t>(actual.st_ino)+1);
  rejects_root(mismatched);
  journal.begin_copy_job(job,{});
}
std::vector<std::filesystem::path> aliases(const std::filesystem::path& parent) {
  std::vector<std::filesystem::path> result;
  for(const auto& entry:std::filesystem::directory_iterator(parent))
    if(entry.path().filename().string().starts_with(".spatial-pinned-sqlite-")) result.push_back(entry.path());
  return result;
}
struct LiveVfs {sqlite3_vfs* api=nullptr;std::string logical;};
LiveVfs find_live_vfs(const std::filesystem::path& database) {
  for(unsigned id=1;id<=4096;++id) {
    const auto name="spatial-pinned-journal-"+std::to_string(id);auto* vfs=sqlite3_vfs_find(name.c_str());if(!vfs)continue;
    for(const auto& entry:std::filesystem::directory_iterator("/proc/self/fd")) {
      std::error_code error;const auto target=std::filesystem::read_symlink(entry.path(),error);
      if(error || target!=database.parent_path())continue;
      const auto logical=entry.path().string()+"/"+database.filename().string();
      std::vector<char> output(static_cast<std::size_t>(vfs->mxPathname)+1);
      if(vfs->xFullPathname(vfs,logical.c_str(),static_cast<int>(output.size()),output.data())==SQLITE_OK)
        return {vfs,logical};
    }
  }
  throw std::runtime_error("live private VFS not found");
}
sqlite3* open_live(const LiveVfs& vfs) {
  sqlite3* db=nullptr;const int result=sqlite3_open_v2(vfs.logical.c_str(),&db,SQLITE_OPEN_READWRITE|SQLITE_OPEN_NOFOLLOW,vfs.api->zName);
  if(result!=SQLITE_OK){if(db)sqlite3_close(db);throw std::runtime_error("real alias connection failed");}
  return db;
}
void close_test_db(sqlite3* db) {check(sqlite3_close(db)==SQLITE_OK,"test connection close failed");}
bool child_can_lock(const std::filesystem::path& database) {
  auto child=fork();check(child>=0,"lock witness fork");
  if(!child) {
    const int fd=open(database.c_str(),O_RDWR|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0)_exit(92);
    struct flock request{};request.l_type=F_WRLCK;request.l_whence=SEEK_SET;request.l_start=0x40000001;request.l_len=1;
    const int result=fcntl(fd,F_SETLK,&request);const int error=errno;close(fd);
    _exit(result==0?0:(error==EAGAIN || error==EACCES?1:93));
  }
  int status=0;check(waitpid(child,&status,0)==child&&WIFEXITED(status),"lock witness wait");
  check(WEXITSTATUS(status)<=1,"lock witness could not inspect database");return WEXITSTATUS(status)==0;
}
struct HeldExclusiveTransaction {
  pid_t child=-1;int release_fd=-1;
  HeldExclusiveTransaction(pid_t process,int release):child(process),release_fd(release){}
  HeldExclusiveTransaction(const HeldExclusiveTransaction&)=delete;
  HeldExclusiveTransaction& operator=(const HeldExclusiveTransaction&)=delete;
  HeldExclusiveTransaction(HeldExclusiveTransaction&& other) noexcept
      :child(std::exchange(other.child,-1)),release_fd(std::exchange(other.release_fd,-1)){}
  HeldExclusiveTransaction& operator=(HeldExclusiveTransaction&&)=delete;
  ~HeldExclusiveTransaction() {
    if(release_fd>=0)close(release_fd);
    if(child>0) { (void)kill(child,SIGKILL);int status=0;while(waitpid(child,&status,0)<0&&errno==EINTR){} }
  }
};
int hold_exclusive_transaction_child(const char* database,int ready_fd,int release_fd) {
  sqlite3* db=nullptr;check(sqlite3_open(database,&db)==SQLITE_OK,"transaction holder open");
  execute(db,"BEGIN EXCLUSIVE");const char held='H';
  check(write(ready_fd,&held,1)==1,"transaction holder readiness");close(ready_fd);
  char resume=0;check(read(release_fd,&resume,1)==1,"transaction holder release read");close(release_fd);
  execute(db,"ROLLBACK");close_test_db(db);return 0;
}
HeldExclusiveTransaction hold_exclusive_transaction(const std::filesystem::path& database) {
  int ready[2],release[2];check(pipe(ready)==0&&pipe(release)==0,"transaction pipes");
  const auto ready_arg=std::to_string(ready[1]),release_arg=std::to_string(release[0]);
  const auto self=std::filesystem::read_symlink("/proc/self/exe").string();
  const auto child=fork();check(child>=0,"transaction holder fork");
  if(child==0) {
    close(ready[0]);close(release[1]);
    execl(self.c_str(),self.c_str(),"--hold-journal-lock",database.c_str(),ready_arg.c_str(),release_arg.c_str(),nullptr);
    _exit(127);
  }
  close(ready[1]);close(release[0]);char held=0;pollfd wait_ready{ready[0],POLLIN,0};
  const bool is_ready=poll(&wait_ready,1,5000)==1&&read(ready[0],&held,1)==1&&held=='H';
  close(ready[0]);
  if(!is_ready) { kill(child,SIGKILL);int status=0;(void)waitpid(child,&status,0);close(release[1]);
    throw std::runtime_error("transaction holder did not acquire lock"); }
  return {child,release[1]};
}
void release_exclusive_transaction(HeldExclusiveTransaction& held) {
  const char resume='R';check(write(held.release_fd,&resume,1)==1,"transaction holder release failed");
  close(held.release_fd);held.release_fd=-1;int status=0;
  check(waitpid(held.child,&status,0)==held.child&&WIFEXITED(status)&&WEXITSTATUS(status)==0,
        "transaction holder did not finish cleanly");held.child=-1;
}
struct SwapDuringOpenVfs {
  sqlite3_vfs api{};sqlite3_vfs* base=nullptr;
  std::string original,saved;
  std::atomic<bool> armed{true},swapped{false},opened_original{false};
  static inline SwapDuringOpenVfs* active=nullptr;
  explicit SwapDuringOpenVfs(const std::filesystem::path& database):original(database.string()),saved(original+".before-open") {
    base=sqlite3_vfs_find(nullptr);check(base,"default Unix VFS missing");api=*base;
    api.zName="spatial-test-swap-before-unix-open";api.pNext=nullptr;
    api.xOpen=[](sqlite3_vfs*,const char* path,sqlite3_file* file,int flags,int* out) {
      auto* self=active;
      if(path && std::strlen(path)>=9 && std::strcmp(path+std::strlen(path)-9,"/database")==0 && self->armed.exchange(false)) {
        if(rename(self->original.c_str(),self->saved.c_str())!=0) {file->pMethods=nullptr;return SQLITE_CANTOPEN;}
        const int replacement=open(self->original.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
        if(replacement<0){file->pMethods=nullptr;return SQLITE_CANTOPEN;}
        constexpr char sentinel[]="foreign replacement sentinel";
        const auto written=write(replacement,sentinel,sizeof(sentinel)-1);close(replacement);
        if(written!=static_cast<ssize_t>(sizeof(sentinel)-1)){file->pMethods=nullptr;return SQLITE_CANTOPEN;}
        self->swapped.store(true);
      }
      const int result=self->base->xOpen(self->base,path,file,flags,out);
      if(result==SQLITE_OK && self->swapped.load() && (flags&SQLITE_OPEN_MAIN_DB)) {
        char signature[16]{};
        if(file->pMethods->xRead(file,signature,sizeof(signature),0)==SQLITE_OK &&
            std::memcmp(signature,"SQLite format 3",sizeof(signature))==0) self->opened_original.store(true);
      }
      return result;
    };
    active=this;check(sqlite3_vfs_register(&api,1)==SQLITE_OK,"swap VFS registration failed");
  }
  ~SwapDuringOpenVfs(){sqlite3_vfs_register(base,1);sqlite3_vfs_unregister(&api);active=nullptr;}
};
void substitution_during_open_control(){
  State state;auto pin=FileOperationJournal::pin_existing(state.fd);const auto database=state.path/"file-operations/file-operations.db";
  {
    SwapDuringOpenVfs swap(database);
    rejects([&]{auto journal=FileOperationJournal::open_existing(pin);},"during-open main replacement accepted");
    check(swap.swapped.load()&&swap.opened_original.load(),"real Unix open did not reach exact alias after substitution");
    check(read_file(database)=="foreign replacement sentinel","during-open replacement was touched");
    check(aliases(database.parent_path()).empty(),"failed constructor retained a closed alias");
    std::filesystem::remove(database);std::filesystem::rename(swap.saved,database);
  }
  {auto recovered=FileOperationJournal::open_existing(pin);}
  std::cout<<"during_open_exact_inode_and_failure_unwind=PASS\n";
}
void pin_lock_controls(){
  for(bool registry:{false,true}) {
    State state;const auto database=state.path/(registry?"execution.db":"file-operations/file-operations.db");
    sqlite3* db=nullptr;check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"lock transaction open");
    execute(db,"BEGIN EXCLUSIVE");check(!child_can_lock(database),"child did not observe SQLite transaction lock");
    if(registry) {auto pin=FileOperationJournal::pin_existing(state.fd);}
    else rejects([&]{auto pin=FileOperationJournal::pin_existing(state.fd);},"same-process exclusive query unexpectedly succeeded");
    check(!child_can_lock(database),"O_PATH pin cleanup dropped SQLite locks");
    // Isolated negative control proves the child detects the ordinary-close hazard.
    const int ordinary=open(database.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);check(ordinary>=0,"ordinary control open");close(ordinary);
    check(child_can_lock(database),"ordinary-close negative control did not drop locks");
    execute(db,"ROLLBACK");close_test_db(db);
  }
  std::cout<<"opath_sqlite_transaction_child_lock_controls=PASS\n";
}
void alias_transaction_controls(){
  State state;auto pin=FileOperationJournal::pin_existing(state.fd);const auto database=state.path/"file-operations/file-operations.db";
  auto owner=FileOperationJournal::open_existing(pin);const auto vfs=find_live_vfs(database);
  sqlite3* db=open_live(vfs);execute(db,"CREATE TABLE alias_probe(value INTEGER);INSERT INTO alias_probe VALUES(1);");
  execute(db,"BEGIN EXCLUSIVE;UPDATE alias_probe SET value=2;");
  check(sqlite3_db_cacheflush(db)==SQLITE_OK,"alias cache flush");
  check(std::filesystem::file_size(database.string()+"-journal")>0,"canonical rollback journal missing");
  check(!child_can_lock(database),"canonical child bypassed alias transaction lock");
  rejects([&]{auto overlapping=FileOperationJournal::pin_existing(state.fd);},"exclusive alias allowed unrelated reader query");
  check(!child_can_lock(database),"overlapping pin close dropped alias transaction lock");
  sqlite3* second=open_live(vfs);check(sqlite3_exec(second,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr)==SQLITE_BUSY,"second alias bypassed writer exclusion");close_test_db(second);
  auto child=fork();check(child>=0,"canonical exclusion fork");
  if(!child){execl("/proc/self/exe","broker-copy-cleanup-test","--canonical-writer-probe",database.c_str(),static_cast<char*>(nullptr));_exit(94);}
  int status=0;check(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==0,"canonical writer bypassed alias writer");
  int moved=-1;check(sqlite3_file_control(db,"main",SQLITE_FCNTL_HAS_MOVED,&moved)==SQLITE_OK&&moved==0,"live alias reported moved");
  const auto live=aliases(database.parent_path());check(live.size()==1,"unexpected live alias count");
  for(const auto& path:live){check(std::filesystem::exists(path/"database"),"alias inode missing");check(std::distance(std::filesystem::directory_iterator(path),std::filesystem::directory_iterator{})==1,"side file escaped into alias directory");}
  execute(db,"ROLLBACK");
  sqlite3_stmt* abandoned=nullptr;check(sqlite3_prepare_v2(db,"SELECT value FROM alias_probe",-1,&abandoned,nullptr)==SQLITE_OK,"busy close statement");
  check(sqlite3_close(db)==SQLITE_BUSY&&std::filesystem::exists(live.front()/"database"),"busy close lost live alias");
  sqlite3_finalize(abandoned);close_test_db(db);
  // An early-unlink counterexample is safe only after all write transactions ended.
  sqlite3* early=open_live(vfs);check(std::filesystem::remove(live.front()/"database"),"early unlink control");
  moved=0;check(sqlite3_file_control(early,"main",SQLITE_FCNTL_HAS_MOVED,&moved)==SQLITE_OK&&moved==1,"early unlink was not detected");close_test_db(early);
  owner.reset();check(std::filesystem::exists(live.front()),"suspicious empty alias was silently removed at close");
  {auto clean=FileOperationJournal::pin_existing(state.fd);}
  check(aliases(database.parent_path()).empty(),"verified empty orphan was not reclaimed");
  std::cout<<"alias_transaction_journal_lock_lifetime=PASS\n";
}
void hot_journal_controls(){
  for(bool alias_recovery:{false,true}) {
    State state;auto pin=FileOperationJournal::pin_existing(state.fd);const auto database=state.path/"file-operations/file-operations.db";
    {sqlite3* db=nullptr;check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"hot seed open");execute(db,"CREATE TABLE hot_probe(value INTEGER);INSERT INTO hot_probe VALUES(1)");close_test_db(db);}
    auto writer=fork();check(writer>=0,"hot journal fork");
    if(!writer){try{
      auto journal=FileOperationJournal::open_existing(pin);auto vfs=find_live_vfs(database);sqlite3* db=open_live(vfs);
      execute(db,"PRAGMA cache_size=2;BEGIN IMMEDIATE;UPDATE hot_probe SET value=2");
      check(sqlite3_db_cacheflush(db)==SQLITE_OK,"hot journal cache flush");
      const auto rollback=read_file(database.string()+"-journal");
      const std::string magic("\xd9\xd5\x05\xf9\x20\xa1\x63\xd7",8);
      check(rollback.size()>512 && rollback.compare(0,8,magic)==0,"hot rollback journal header not durable");
      for(const auto& path:aliases(database.parent_path()))check(std::distance(std::filesystem::directory_iterator(path),std::filesystem::directory_iterator{})==1,"private alias journal created");
      kill(getpid(),SIGKILL);_exit(97); // Real crash with uncommitted pages and a hot canonical journal.
    }catch(...){_exit(96);}}
    int status=0;check(waitpid(writer,&status,0)==writer&&WIFSIGNALED(status)&&WTERMSIG(status)==SIGKILL,"hot writer did not reach SIGKILL boundary");
    check(aliases(database.parent_path()).size()==1,"crash did not retain exact alias");
    if(alias_recovery){auto recovered=FileOperationJournal::open_existing(pin);}
    sqlite3* db=nullptr;check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"canonical recovery open");
    sqlite3_stmt* row=nullptr;check(sqlite3_prepare_v2(db,"SELECT value FROM hot_probe",-1,&row,nullptr)==SQLITE_OK&&sqlite3_step(row)==SQLITE_ROW&&sqlite3_column_int(row,0)==1,"hot journal rollback failed");sqlite3_finalize(row);close_test_db(db);
    {auto cleanup=FileOperationJournal::pin_existing(state.fd);}
    check(aliases(database.parent_path()).empty(),"crash orphan alias was not reclaimed");
  }
  std::cout<<"canonical_and_alias_hot_journal_recovery=PASS\n";
}
void alias_header_orphan_controls(){
  State state;const auto database=state.path/"file-operations/file-operations.db";const auto parent=database.parent_path();
  auto pin=FileOperationJournal::pin_existing(state.fd);
  for(const auto* mode:{"wal","mixed","unknown","short","signature"}) {
    const auto bytes=read_file(database);auto changed=bytes;
    if(std::strcmp(mode,"short")==0)changed.resize(30);
    else if(std::strcmp(mode,"signature")==0)changed[0]='X';
    else {changed[18]=std::strcmp(mode,"unknown")==0?7:2;if(std::strcmp(mode,"wal")==0)changed[19]=2;}
    write_file(database,changed);
    rejects([&]{auto opened=FileOperationJournal::open_existing(pin);},"unsupported header reached SQLite queries");
    check(!std::filesystem::exists(database.string()+"-shm")&&!std::filesystem::exists(database.string()+"-wal"),"rejected header created WAL state");
    check(aliases(parent).empty(),"header rejection leaked alias lifetime");write_file(database,bytes);
  }
  // Actual WAL format is also refused, even after its writer checkpoint removes sides.
  {sqlite3* db=nullptr;check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"WAL seed open");execute(db,"PRAGMA journal_mode=WAL;CREATE TABLE wal_probe(value);INSERT INTO wal_probe VALUES(1);");close_test_db(db);}
  rejects([&]{auto opened=FileOperationJournal::open_existing(pin);},"real WAL database was accepted");
  check(!std::filesystem::exists(database.string()+"-shm"),"WAL rejection created shared memory");
  {sqlite3* db=nullptr;check(sqlite3_open(database.c_str(),&db)==SQLITE_OK,"restore DELETE open");execute(db,"PRAGMA journal_mode=DELETE");close_test_db(db);}
  const auto orphan=parent/(".spatial-pinned-sqlite-"+std::string(32,'a'));
  std::filesystem::create_directory(orphan);chmod(orphan.c_str(),0700);write_file(orphan/"foreign","retain");
  rejects([&]{auto opened=FileOperationJournal::open_existing(pin);},"foreign orphan contents were removed");check(read_file(orphan/"foreign")=="retain","orphan cleanup touched foreign data");std::filesystem::remove_all(orphan);
  std::filesystem::create_directory(orphan);chmod(orphan.c_str(),0700);write_file(orphan/"database","foreign inode");
  rejects([&]{auto opened=FileOperationJournal::open_existing(pin);},"foreign orphan inode was accepted");
  check(read_file(orphan/"database")=="foreign inode","foreign orphan inode was removed");std::filesystem::remove_all(orphan);
  std::filesystem::create_directory(orphan);chmod(orphan.c_str(),0700);std::filesystem::create_symlink(database,orphan/"database");
  rejects([&]{auto opened=FileOperationJournal::open_existing(pin);},"symlink orphan was accepted");check(std::filesystem::is_symlink(orphan/"database"),"symlink evidence removed");std::filesystem::remove_all(orphan);
  for(unsigned i=0;i<65;++i){char suffix[33]{};std::snprintf(suffix,sizeof(suffix),"%032x",i);const auto path=parent/(".spatial-pinned-sqlite-"+std::string(suffix));std::filesystem::create_directory(path);chmod(path.c_str(),0700);}
  rejects([&]{auto opened=FileOperationJournal::open_existing(pin);},"orphan namespace exceeded bound");check(aliases(parent).size()==65,"overflow scan removed evidence");for(const auto& path:aliases(parent))std::filesystem::remove(path);
  {auto opened=FileOperationJournal::open_existing(pin);}
  check(aliases(parent).empty(),"normal close leaked alias");
  std::cout<<"precore_header_and_bounded_orphan_controls=PASS\n";
}

void btrfs_layout_controls(){
  State state;struct statfs filesystem{};check(fstatfs(state.fd,&filesystem)==0,"layout filesystem stat");
  if(filesystem.f_type!=BTRFS_SUPER_MAGIC) {
    check(!std::getenv("SPATIAL_TEST_REQUIRE_BTRFS_ALIAS"),"Btrfs alias layout required; set TMPDIR to private Btrfs fixture");
    std::cout<<"btrfs_alias_same_subvolume_and_cross_subvolume=UNRUN\n";return;
  }
  auto pin=FileOperationJournal::pin_existing(state.fd);{auto opened=FileOperationJournal::open_existing(pin);}
  const auto parent=state.path/"file-operations";const int parent_fd=open(parent.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
  check(parent_fd>=0,"Btrfs alias parent open");
  btrfs_ioctl_vol_args subvolume{};std::strcpy(subvolume.name,"negative-cross-subvolume");
  check(ioctl(parent_fd,BTRFS_IOC_SUBVOL_CREATE,&subvolume)==0,"Btrfs negative subvolume create");
  const int other=openat(parent_fd,subvolume.name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  const int source=openat(parent_fd,"file-operations.db",O_PATH|O_NOFOLLOW|O_CLOEXEC);
  check(other>=0&&source>=0,"Btrfs exact source/negative target open");
  const auto from="/proc/self/fd/"+std::to_string(source);
  const int result=linkat(AT_FDCWD,from.c_str(),other,"database",AT_SYMLINK_FOLLOW);const int error=errno;
  close(source);close(other);
  check(result<0&&error==EXDEV,"cross-subvolume alias did not fail closed");
  check(ioctl(parent_fd,BTRFS_IOC_SNAP_DESTROY,&subvolume)==0,"Btrfs negative subvolume remove");close(parent_fd);
  std::cout<<"btrfs_alias_same_subvolume_and_cross_subvolume=PASS\n";
}
void constructor_allocation_controls(){
  State state;unsigned failed=0;
  for(long budget:{0L,4L,12L,24L,40L,80L}) {
    allocation_budget=budget;
    try {auto pin=FileOperationJournal::pin_existing(state.fd);}
    catch(const std::bad_alloc&){++failed;}
    catch(...){allocation_budget=-1;throw;}
    allocation_budget=-1;
    {auto recovered=FileOperationJournal::pin_existing(state.fd);}
    check(aliases(state.path/"file-operations").empty(),"allocation failure lost bounded alias ownership");
  }
  check(failed>0,"allocation fault control was not reached");
  std::cout<<"constructor_allocation_unwind_and_recovery=PASS\n";
}
void live_vfs_rejection_controls(){
  unsigned checked=0;
  // Exercise registered private adapters through SQLite's real C callback ABI.
  // This runs during the first open, before later per-job adapters are constructed.
  for(unsigned id=1;id<=64;++id) {
    const auto name="spatial-pinned-journal-"+std::to_string(id);
    auto* vfs=sqlite3_vfs_find(name.c_str());if(!vfs)continue;
    std::vector<std::max_align_t> storage((vfs->szOsFile+sizeof(std::max_align_t)-1)/sizeof(std::max_align_t));
    auto* file=reinterpret_cast<sqlite3_file*>(storage.data());
    for(const char* path:{static_cast<const char*>(nullptr),"/outside-journal-anchor","/proc/self/fd/0/foreign"}) {
      file->pMethods=reinterpret_cast<const sqlite3_io_methods*>(std::uintptr_t{1});
      int flags=0;
      int opened=0,accessed=0,deleted=0,accessible=1;
      allocation_budget=0;
      try {
        opened=vfs->xOpen(vfs,path,file,SQLITE_OPEN_READONLY|SQLITE_OPEN_MAIN_DB,&flags);
        accessed=vfs->xAccess(vfs,path,SQLITE_ACCESS_EXISTS,&accessible);
        deleted=vfs->xDelete(vfs,path,0);
      } catch(...) {allocation_budget=-1;throw;}
      allocation_budget=-1;
      check(opened==SQLITE_CANTOPEN && file->pMethods==nullptr,"rejected xOpen did not clear pMethods");
      check(accessed==SQLITE_OK && !accessible,"VFS accepted unrelated access");
      check(deleted==SQLITE_IOERR_DELETE,"VFS accepted unrelated delete");
    }
    ++checked;
  }
  check(checked>0,"no live pinned VFS was tested");
}
void journal_controls(){
  State state;auto pin=FileOperationJournal::pin_existing(state.fd);auto binding=pin.binding();check(binding.journal_id().size()==32,"journal identity missing");
  {auto journal=FileOperationJournal::open_existing(pin);
    live_vfs_rejection_controls();
    const auto active=aliases(state.path/"file-operations");check(active.size()==1,"active alias missing");
    auto concurrent=FileOperationJournal::pin_existing(state.fd);check(concurrent.binding().SerializeAsString()==binding.SerializeAsString(),"concurrent VFS changed binding");
    check(std::filesystem::exists(active.front()/"database"),"orphan scan removed live alias");
    rejects([&]{auto duplicate=FileOperationJournal::open_existing(pin);},"second cleanup acquired execution.lock");}
  const auto db=state.path/"file-operations/file-operations.db";
  const auto outside=state.path/"outside-sentinel";write_file(outside,"unchanged");
  auto side=db;side+="-journal";std::filesystem::create_symlink(outside,side);
  rejects([&]{auto journal=FileOperationJournal::open_existing(pin);},"SQLite side file escaped its anchor");
  check(read_file(outside)=="unchanged","foreign side target changed");std::filesystem::remove(side);
  for(const auto* suffix:{"-wal","-shm"}) {
    const auto forbidden=std::filesystem::path(db.string()+suffix);std::filesystem::create_symlink(outside,forbidden);
    rejects([&]{auto journal=FileOperationJournal::open_existing(pin);},"unsupported sidefile symlink accepted");
    check(read_file(outside)=="unchanged","unsupported side target changed");std::filesystem::remove(forbidden);
  }
  const auto registry_side=state.path/"execution.db-journal";std::filesystem::create_symlink(outside,registry_side);
  rejects([&]{auto journal=FileOperationJournal::open_existing(pin);},"registry sidefile symlink accepted");std::filesystem::remove(registry_side);
  auto main_saved=db;main_saved+=".saved";std::filesystem::rename(db,main_saved);std::filesystem::create_symlink(main_saved,db);
  rejects([&]{auto wrong=FileOperationJournal::pin_existing(state.fd);},"O_PATH symlink main was accepted");
  std::filesystem::remove(db);std::filesystem::rename(main_saved,db);
  {
    auto opened=FileOperationJournal::open_existing(pin);auto replaced=db;replaced+=".held";
    std::filesystem::rename(db,replaced);write_file(db,"foreign database sentinel");
    rejects([&]{opened->scope_cleanup_outcome("one",std::string(16,'x'),{});},"after-open inode substitution accepted");
    check(read_file(db)=="foreign database sentinel","foreign replacement database was modified");
    std::filesystem::remove(db);std::filesystem::rename(replaced,db);
  }
  auto old=db;old+=".old";std::filesystem::rename(db,old);write_file(db,"replacement");
  rejects([&]{pin.verify();},"replaced database accepted");std::filesystem::remove(db);std::filesystem::rename(old,db);pin.verify();
  auto root_old=state.path;root_old+="-renamed";std::filesystem::rename(state.path,root_old);std::filesystem::create_directory(state.path);
  rejects([&]{pin.verify();},"foreign execution root accepted");std::filesystem::remove(state.path);std::filesystem::rename(root_old,state.path);
  sqlite3* raw=nullptr;check(sqlite3_open(db.c_str(),&raw)==SQLITE_OK,"version open");execute(raw,"PRAGMA user_version=7;");sqlite3_close(raw);
  rejects([&]{auto journal=FileOperationJournal::open_existing(pin);},"existing-only opener migrated old schema");
  check(read_file(db).size()>0,"existing db removed");
  check(sqlite3_open(db.c_str(),&raw)==SQLITE_OK,"version reopen");execute(raw,"PRAGMA user_version=8;");sqlite3_close(raw);
  std::filesystem::remove(db);rejects([&]{auto journal=FileOperationJournal::open_existing(pin);},"existing-only opener created missing DB");check(!std::filesystem::exists(db),"cleanup created DB");
  std::cout<<"journal_binding_controls=PASS\n";
}
void root_replacement_control(const std::filesystem::path& delegated){
  int channel[2];check(pipe2(channel,O_CLOEXEC|O_NONBLOCK)==0,"root replacement evidence pipe");
  const auto child=fork();check(child>=0,"root replacement control fork");
  if(child==0) {
    close(channel[0]);
    // This one destructive counterexample invalidates its owner on purpose.
    // Keep the object outside the try scope and end with _exit, never a fake stop.
    // Ordinary controls above still require normal stop/destructor behavior.
    std::unique_ptr<ProcessScope> scope;
    int held=-1;
    try {
      scope=ProcessScope::prepare(delegated);
      const auto original=scope->identity();const auto named=delegated/original.scope_name();
      const auto descendant=named/"must-survive";
      struct stat replacement{},replacement_child{};
      bool mutation_complete=false,receipt_returned=false,guard_rejected=false;
      ProcessScopeError rejection;
      const auto matches=[](const struct stat& actual,std::uint64_t device,std::uint64_t inode) {
        return S_ISDIR(actual.st_mode) && actual.st_uid==geteuid() &&
            static_cast<std::uint64_t>(actual.st_dev)==device && static_cast<std::uint64_t>(actual.st_ino)==inode;
      };
      scope->set_pre_prune_cleanup([&](const auto& view){
        struct stat before{};
        check(lstat(named.c_str(),&before)==0 && matches(before,original.device(),original.inode()),"root replacement setup original identity");
        held=fcntl(view.directory_fd(),F_DUPFD_CLOEXEC,3);check(held>=0,"root replacement held descriptor");
        // cgroup v2 forbids rename. Replace only this actual empty named cgroup.
        check(rmdir(named.c_str())==0,"root replacement setup rmdir failed");
        check(mkdir(named.c_str(),0700)==0,"root replacement setup mkdir failed");
        check(lstat(named.c_str(),&replacement)==0 && S_ISDIR(replacement.st_mode) &&
            replacement.st_uid==geteuid() && replacement.st_dev==before.st_dev && replacement.st_ino!=before.st_ino,
            "root replacement setup did not create a new identity");
        check(mkdir(descendant.c_str(),0700)==0 && lstat(descendant.c_str(),&replacement_child)==0 &&
            S_ISDIR(replacement_child.st_mode) && replacement_child.st_uid==geteuid(),"root replacement setup descendant failed");
        mutation_complete=true;
      });
      try { (void)scope->stop();receipt_returned=true; }
      catch(const std::invalid_argument& error) {
        check(std::strcmp(error.what(),"Owned drained tree identity or population changed")==0,
            "root replacement rejected for a different reason");
        guard_rejected=true;rejection.set_code(ProcessScopeError::INVALID_ARGUMENT);rejection.set_message(error.what());
      }
      check(mutation_complete && guard_rejected && !receipt_returned,"root replacement did not reach the identity guard");
      struct stat held_after{},named_after{},child_after{};
      check(fstat(held,&held_after)==0 && matches(held_after,original.device(),original.inode()),"original held root identity changed");
      check(lstat(named.c_str(),&named_after)==0 && matches(named_after,replacement.st_dev,replacement.st_ino),"unrelated replacement root was pruned");
      check(lstat(descendant.c_str(),&child_after)==0 && matches(child_after,replacement_child.st_dev,replacement_child.st_ino),"unrelated replacement descendant was pruned");
      // Assertions precede manual removal of only the exact empty test-owned replacement.
      check(lstat(descendant.c_str(),&child_after)==0 && matches(child_after,replacement_child.st_dev,replacement_child.st_ino) &&
          rmdir(descendant.c_str())==0,"cannot remove exact test replacement child");
      check(lstat(named.c_str(),&named_after)==0 && matches(named_after,replacement.st_dev,replacement.st_ino) &&
          rmdir(named.c_str())==0,"cannot remove exact test replacement root");
      close(held);held=-1;
      FileCopyFilesystemIdentity root_identity,child_identity;
      root_identity.set_device(replacement.st_dev);root_identity.set_inode(replacement.st_ino);root_identity.set_type(FileCopyFilesystemIdentity::DIRECTORY);
      child_identity.set_device(replacement_child.st_dev);child_identity.set_inode(replacement_child.st_ino);child_identity.set_type(FileCopyFilesystemIdentity::DIRECTORY);
      std::string packet;
      for(const auto& message:{original.SerializeAsString(),root_identity.SerializeAsString(),child_identity.SerializeAsString(),rejection.SerializeAsString()}) {
        const auto size=static_cast<std::uint32_t>(message.size());packet.append(reinterpret_cast<const char*>(&size),sizeof(size));packet+=message;
      }
      check(packet.size()<=4096 && write(channel[1],packet.data(),packet.size())==static_cast<ssize_t>(packet.size()),"root replacement success evidence write");
      close(channel[1]);_exit(0);
    } catch(const std::exception& error) {
      const auto report=std::string("root replacement control setup/assertion failure: ")+error.what()+"\n";
      (void)write(STDERR_FILENO,report.data(),report.size());
      // Preserve unexpected paths. The deliberately invalid owner cannot stop again.
      _exit(91);
    }
  }
  close(channel[1]);
  int status=0;bool retired=false;
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
  while(std::chrono::steady_clock::now()<deadline) {
    const auto result=waitpid(child,&status,WNOHANG);
    if(result==child){retired=true;break;}
    if(result<0 && errno!=EINTR){close(channel[0]);throw std::runtime_error("root replacement child wait failed");}
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  if(!retired) {
    // The child is owned and unreaped, so its PID cannot have been reused.
    (void)kill(child,SIGKILL);
    const auto reap_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while(std::chrono::steady_clock::now()<reap_deadline) {
      const auto result=waitpid(child,&status,WNOHANG);
      if(result==child){retired=true;break;}
      if(result<0 && errno!=EINTR)break;
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    close(channel[0]);throw std::runtime_error(retired?"root replacement child timed out and was killed":"root replacement child timed out; retirement unconfirmed");
  }
  std::string packet;char bytes[1024]{};
  for(;;) {
    const auto count=read(channel[0],bytes,sizeof(bytes));
    if(count==0)break;
    if(count<0 && errno==EINTR)continue;
    if(count<0){close(channel[0]);throw std::runtime_error("root replacement evidence pipe incomplete");}
    packet.append(bytes,static_cast<std::size_t>(count));
    if(packet.size()>4096){close(channel[0]);throw std::runtime_error("root replacement evidence exceeds bound");}
  }
  close(channel[0]);
  check(WIFEXITED(status)&&WEXITSTATUS(status)==0,"root replacement child reported setup/assertion failure");
  std::size_t cursor=0;
  const auto next=[&]() {
    check(packet.size()-cursor>=sizeof(std::uint32_t),"root replacement success evidence missing");
    std::uint32_t size=0;std::memcpy(&size,packet.data()+cursor,sizeof(size));cursor+=sizeof(size);
    check(size>0 && size<=1024 && packet.size()-cursor>=size,"root replacement evidence frame invalid");
    auto message=packet.substr(cursor,size);cursor+=size;return message;
  };
  ProcessScopeIdentity original;FileCopyFilesystemIdentity replacement,descendant;ProcessScopeError rejection;
  check(original.ParseFromString(next()) && replacement.ParseFromString(next()) && descendant.ParseFromString(next()) &&
      rejection.ParseFromString(next()) && cursor==packet.size(),"root replacement protobuf evidence invalid");
  check(original.device()>0 && original.inode()>0 && original.launch_id().size()==32 &&
      replacement.type()==FileCopyFilesystemIdentity::DIRECTORY && replacement.device()==original.device() && replacement.inode()!=original.inode() &&
      descendant.type()==FileCopyFilesystemIdentity::DIRECTORY && descendant.device()==original.device() && descendant.inode()>0 &&
      rejection.code()==ProcessScopeError::INVALID_ARGUMENT && rejection.message()=="Owned drained tree identity or population changed",
      "root replacement evidence did not prove exact guard rejection");
  std::cout<<"root_replacement_exact_identity_no_prune=PASS old_inode="<<original.inode()<<" replacement_inode="<<replacement.inode()<<" child_inode="<<descendant.inode()<<'\n';
}

void retirement_ack_failure_retry(const std::filesystem::path& delegated){
  const std::string name="authority-broker-"+std::string(32,'5');
  auto owned=ProcessScope::prepare_owned(delegated,name);
  const auto owned_path=delegated/name;
  const auto original=owned->identity();
  unsigned retirement_callbacks=0,drained_callbacks=0;
  owned->set_pre_prune_cleanup_with_retirement([&](const auto& tree) {
    ++retirement_callbacks;
    check(tree.child_exited_successfully(),"retry retirement callback lost clean child proof");
    return retirement_callbacks==1;
  });
  AuthorityProcessLaunch child_request;child_request.set_executable("/bin/true");
  const int child_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(child_null>=0,"ack retry child null");
  owned->start(child_request,{child_null,child_null,child_null});close(child_null);
  const int child_handle=owned->duplicate_child_pidfd();check(child_handle>=0,"ack retry child pidfd");
  pollfd child_done{child_handle,POLLIN,0};const int child_ready=poll(&child_done,1,5000);close(child_handle);
  check(child_ready==1&&(child_done.revents&POLLIN),"ack retry child did not exit before cleanup");
  rejects([&]{owned->stop([&](const auto& acknowledged) {
    ++drained_callbacks;
    check(acknowledged.SerializeAsString()==original.SerializeAsString(),"drain acknowledgment identity changed");
    throw std::runtime_error("planned drain acknowledgment failure");
  });},"failed final drain acknowledgment returned a stop receipt");
  struct stat after_failure{};
  check(lstat(owned_path.c_str(),&after_failure)==0&&S_ISDIR(after_failure.st_mode)&&
      after_failure.st_dev==static_cast<dev_t>(original.device())&&
      after_failure.st_ino==static_cast<ino_t>(original.inode()),
      "failed drain acknowledgment removed or replaced stable broker root");
  owned->stop();
  struct stat after_retry{};
  check(retirement_callbacks==2&&drained_callbacks==1&&
      lstat(owned_path.c_str(),&after_retry)==0&&S_ISDIR(after_retry.st_mode)&&
      after_retry.st_dev==static_cast<dev_t>(original.device())&&
      after_retry.st_ino==static_cast<ino_t>(original.inode()),
      "retry cached retirement authorization or changed stable broker root");
  check(std::filesystem::remove(owned_path),"explicit acknowledgment retry fixture teardown failed");
}

void callback_controls(const std::filesystem::path& delegated){
  static_assert(!std::is_constructible_v<spatial::supervisor::OwnedDrainedTree,int,const ProcessScopeIdentity&>);
  auto root=ProcessScope::prepare(delegated);const auto path=delegated/root->identity().scope_name();bool first=true,called=false;
  std::filesystem::create_directory(path/"preserved-child");
  root->set_pre_prune_cleanup([&](const auto& view){check(view.identity().SerializeAsString()==root->identity().SerializeAsString(),"view root mismatch");check(!view.execution_may_have_run(),"prepared root falsely marked executable");check(std::filesystem::exists(path/"preserved-child"),"hook ran after prune");called=true;if(first){first=false;throw std::runtime_error("planned callback failure");}});
  rejects([&]{root->stop();},"callback failure returned complete receipt");check(called&&std::filesystem::exists(path/"preserved-child"),"failed callback pruned child");root->stop();check(!std::filesystem::exists(path),"retry did not finish cleanup");
  // Real direct-child termination/reap precedes the hook, and late installation rejects.
  auto live=ProcessScope::prepare(delegated);bool saw_dead=false;live->set_pre_prune_cleanup([&](const auto& tree){saw_dead=live->diagnostic_child_reaped();check(!live->diagnostic_populated(),"callback saw live population");check(tree.execution_may_have_run(),"released child gate did not set sticky execution proof");});
  AuthorityProcessLaunch request;request.set_executable("/usr/bin/sleep");request.add_arguments("60");int null=open("/dev/null",O_RDWR|O_CLOEXEC);live->start(request,{null,null,null});close(null);
  rejects([&]{live->set_pre_prune_cleanup([](const auto&){});},"late callback installed");live->stop();check(saw_dead,"callback preceded direct-child reap");
  // A complete private errno proves the requested program never ran.
  auto failed_exec=ProcessScope::prepare(delegated);const auto failed_exec_path=delegated/failed_exec->identity().scope_name();bool failed_exec_checked=false;
  failed_exec->set_pre_prune_cleanup([&](const auto& tree){
    check(tree.execution_may_have_run(),"failed exec cleared sticky execution possibility");
    check(tree.trusted_preexec_failure(),"complete pre-exec errno proof was lost");
    failed_exec_checked=true;
  });
  AuthorityProcessLaunch missing;missing.set_executable("/nonexistent-trusted-preexec-test");int missing_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(missing_null>=0,"failed exec null");
  bool missing_rejected=false;try{failed_exec->start(missing,{missing_null,missing_null,missing_null});}catch(const std::exception& error){missing_rejected=std::string(error.what()).find("Authority exec failed")!=std::string::npos;}close(missing_null);
  check(missing_rejected,"missing executable did not report its trusted exec failure");
  failed_exec->stop();check(failed_exec_checked&&!std::filesystem::exists(failed_exec_path),"trusted pre-exec failure did not complete checked stop");
  auto outer=ProcessScope::prepare_named(delegated,"spatial-git-workers");outer->enable_children();const auto outer_path=delegated/outer->identity().scope_name();
  // Malformed acknowledgements remain ambiguous and cannot mint the pre-exec proof.
  const auto check_ambiguous_ack=[&](const char* mode){
    int ack_pipe[2];check(pipe2(ack_pipe,O_CLOEXEC)==0,"ambiguous ack evidence pipe");const pid_t ack_probe=fork();check(ack_probe>=0,"ambiguous ack child fork");
    if(ack_probe==0){close(ack_pipe[0]);try{auto uncertain=ProcessScope::prepare(outer_path);const auto identity=uncertain->identity();transfer(ack_pipe[1],identity.SerializeAsString());
        uncertain->set_pre_prune_cleanup([](const auto& tree){check(tree.execution_may_have_run(),"gate release lost sticky execution possibility");check(!tree.trusted_preexec_failure(),"malformed gate response minted pre-exec proof");throw std::runtime_error("unbound cleanup must remain uncertain");});
        AuthorityProcessLaunch bad_ack;bad_ack.set_executable("/nonexistent-malformed-ack-test");int child_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(child_null>=0,"malformed ack null");
        if(std::strcmp(mode,"late")==0){struct sigaction action{};action.sa_handler=ignore_startup_signal;sigemptyset(&action.sa_mask);check(sigaction(SIGUSR1,&action,nullptr)==0,"install startup interrupt handler");}
        check(setenv("SPATIAL_TEST_START_ACK_MODE",mode,1)==0,"set malformed ack mode");bool ambiguous=false;
        const auto start_began=std::chrono::steady_clock::now();
        const auto expected=std::strcmp(mode,"late")==0?"Authority exec deadline exceeded":"Cannot confirm authority exec";
        try{uncertain->start(bad_ack,{child_null,child_null,child_null});}catch(const std::exception& error){ambiguous=std::string(error.what()).find(expected)!=std::string::npos;}
        const auto start_elapsed=std::chrono::steady_clock::now()-start_began;
        close(child_null);unsetenv("SPATIAL_TEST_START_ACK_MODE");check(ambiguous,"malformed or late gate response was accepted as a trusted exec failure");
        if(std::strcmp(mode,"late")==0)check(start_elapsed>=std::chrono::milliseconds(1500)&&start_elapsed<std::chrono::milliseconds(2800),"interrupted startup wait did not honor one absolute deadline");
        try{uncertain->stop();_exit(95);}catch(const std::exception& error){_exit(std::string(error.what())=="unbound cleanup must remain uncertain"?0:96);}
      }catch(...){_exit(97);}}
    close(ack_pipe[1]);pollfd ready{ack_pipe[0],POLLIN,0};check(poll(&ready,1,5000)==1&&(ready.revents&POLLIN),"ambiguous ack identity deadline");
    ProcessScopeIdentity identity;check(identity.ParseFromString(receive(ack_pipe[0])),"ambiguous ack identity");close(ack_pipe[0]);
    int status=0;wait_for([&]{const auto waited=waitpid(ack_probe,&status,WNOHANG);check(waited>=0,"ambiguous ack waitpid");return waited==ack_probe;});
    check(WIFEXITED(status)&&WEXITSTATUS(status)==0,"ambiguous cleanup rejection was not contained");
    const auto child_path=outer_path/identity.scope_name();struct stat retained{};
    check(lstat(child_path.c_str(),&retained)==0&&S_ISDIR(retained.st_mode)&&static_cast<std::uint64_t>(retained.st_dev)==identity.device()&&static_cast<std::uint64_t>(retained.st_ino)==identity.inode(),"ambiguous scope was pruned or replaced");
    check(ProcessScope::remove_reserved_empty(outer_path,outer->identity(),identity.scope_name()),"ambiguous exact child did not permit parent-bound recovery");
    check(!std::filesystem::exists(child_path),"ambiguous exact child remained after recovery");
  };
  check_ambiguous_ack("short");check_ambiguous_ack("oversized");check_ambiguous_ack("zero");check_ambiguous_ack("late");
  // Isolate the fail-closed post-gate case: the callback rejects after the child exits,
  // and the parent verifies that the exact scope remains for recovery.
  int evidence[2];check(pipe2(evidence,O_CLOEXEC)==0,"predispatch evidence pipe");const pid_t probe=fork();check(probe>=0,"predispatch child fork");
  if(probe==0){close(evidence[0]);try{auto uncertain=ProcessScope::prepare(outer_path);const auto identity=uncertain->identity();transfer(evidence[1],identity.SerializeAsString());
      uncertain->set_pre_prune_cleanup([](const auto& tree){check(tree.execution_may_have_run(),"released gate lost execution possibility");check(!tree.trusted_preexec_failure(),"successful exec minted pre-exec proof");throw std::runtime_error("unbound cleanup must remain uncertain");});
      AuthorityProcessLaunch short_request;short_request.set_executable("/bin/true");int child_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(child_null>=0,"predispatch null");uncertain->start(short_request,{child_null,child_null,child_null});close(child_null);
      try{uncertain->stop();_exit(92);}catch(const std::exception& error){_exit(std::string(error.what())=="unbound cleanup must remain uncertain"?0:94);}
    }catch(...){_exit(93);}}
  close(evidence[1]);pollfd evidence_ready{evidence[0],POLLIN,0};check(poll(&evidence_ready,1,5000)==1&&(evidence_ready.revents&POLLIN),"predispatch identity deadline");
  ProcessScopeIdentity uncertain_identity;check(uncertain_identity.ParseFromString(receive(evidence[0])),"predispatch identity");close(evidence[0]);
  int probe_status=0;bool probe_reaped=false;wait_for([&]{const auto waited=waitpid(probe,&probe_status,WNOHANG);check(waited>=0,"predispatch waitpid");return probe_reaped=waited==probe;});
  check(probe_reaped&&WIFEXITED(probe_status)&&WEXITSTATUS(probe_status)==0,"uncertain cleanup child did not contain rejection");
  const auto uncertain_path=outer_path/uncertain_identity.scope_name();struct stat retained_child{};
  check(lstat(uncertain_path.c_str(),&retained_child)==0&&S_ISDIR(retained_child.st_mode)&&
      static_cast<std::uint64_t>(retained_child.st_dev)==uncertain_identity.device()&&
      static_cast<std::uint64_t>(retained_child.st_ino)==uncertain_identity.inode(),"unbound post-gate exact child was pruned or replaced");
  check(ProcessScope::remove_reserved_empty(outer_path,outer->identity(),uncertain_identity.scope_name()),"retained test child did not permit exact parent-bound recovery");
  check(!std::filesystem::exists(uncertain_path),"exact child remained after parent-bound recovery");outer->stop();
  bool destructor_called=false;{auto scope=ProcessScope::prepare(delegated);scope->set_pre_prune_cleanup([&](const auto&){destructor_called=true;});}check(destructor_called,"destructor skipped callback");
  auto expired=ProcessScope::prepare(delegated);const auto expired_path=delegated/expired->identity().scope_name();bool expired_called=false;
  expired->set_pre_prune_cleanup([&](const auto&){expired_called=true;});expired->request_stop();
  rejects([&]{expired->complete_stop(std::chrono::steady_clock::now());},"expired deadline returned stopped receipt");
  check(!expired_called&&std::filesystem::exists(expired_path),"expired callback pruned or entered participant");
  expired->stop();check(expired_called&&!std::filesystem::exists(expired_path),"expired callback retry did not finish");
  auto reap_retry=ProcessScope::prepare(delegated);unsigned reap_callbacks=0;bool reap_persisted=false;
  reap_retry->set_pre_prune_cleanup([&](const auto& tree){++reap_callbacks;if(reap_callbacks==1){while(std::chrono::steady_clock::now()<tree.deadline())std::this_thread::sleep_for(std::chrono::milliseconds(1));}else reap_persisted=reap_retry->diagnostic_child_reaped();});
  AuthorityProcessLaunch retry_request;retry_request.set_executable("/usr/bin/sleep");retry_request.add_arguments("60");int retry_null=open("/dev/null",O_RDWR|O_CLOEXEC);reap_retry->start(retry_request,{retry_null,retry_null,retry_null});close(retry_null);
  reap_retry->request_stop();rejects([&]{reap_retry->complete_stop(std::chrono::steady_clock::now()+std::chrono::milliseconds(150));},"expiry after direct-child reap returned receipt");
  check(reap_retry->diagnostic_child_reaped(),"deadline expiry forgot successful waitpid reap");reap_retry->stop();
  check(reap_callbacks==2&&reap_persisted,"reap retry hit ECHILD or skipped callback");
  auto retry=ProcessScope::prepare(delegated);const auto retry_path=delegated/retry->identity().scope_name();
  std::vector<std::filesystem::path> deep;auto nested=retry_path;
  for(int depth=0;depth<10;++depth){nested/="nested";std::filesystem::create_directory(nested);deep.push_back(nested);}
  unsigned observations=0;
  retry->set_pre_prune_cleanup([&](const auto&){++observations;if(observations==2)check(std::filesystem::exists(retry_path/"changed-between-attempts"),"retry skipped changed descendant");});
  rejects([&]{retry->stop();},"excessive nesting did not preserve prune failure");
  for(auto at=deep.rbegin();at!=deep.rend();++at)std::filesystem::remove(*at);
  std::filesystem::create_directory(retry_path/"changed-between-attempts");
  retry->stop();check(observations==2,"successful callback was cached across failed prune");
  const auto run_owned_retirement=[&](const std::string& suffix,int exit_code,bool permit_retirement) {
    const auto name=std::string("authority-broker-")+suffix;
    auto owned=ProcessScope::prepare_owned(delegated,name);
    const auto owned_path=delegated/name;
    bool observed=false;
    owned->set_pre_prune_cleanup_with_retirement([&](const auto& tree) {
      observed=true;
      const bool clean=tree.child_exited_successfully();
      check(clean==(exit_code==0),"retirement callback received the wrong direct-child exit proof");
      return permit_retirement&&clean;
    });
    AuthorityProcessLaunch child_request;child_request.set_executable("/bin/sh");
    child_request.add_arguments("-c");child_request.add_arguments("exit "+std::to_string(exit_code));
    const int child_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(child_null>=0,"retirement child null");
    owned->start(child_request,{child_null,child_null,child_null});close(child_null);
    const int child_handle=owned->duplicate_child_pidfd();check(child_handle>=0,"retirement child pidfd");
    pollfd child_done{child_handle,POLLIN,0};
    const int child_ready=poll(&child_done,1,5000);close(child_handle);
    check(child_ready==1&&(child_done.revents&POLLIN),"retirement child did not exit before cleanup request");
    owned->stop();
    check(observed,"retirement callback did not observe reaped child");
    return std::filesystem::exists(owned_path);
  };
  check(run_owned_retirement(std::string(32,'1'),17,true),"nonzero child exit retired stable broker root");
  check(!run_owned_retirement(std::string(32,'1'),0,true),"clean child and authorized cleanup did not retire stable root");
  check(run_owned_retirement(std::string(32,'2'),0,false),"cleanup refusal retired stable broker root");
  check(!run_owned_retirement(std::string(32,'2'),0,true),"checked retry did not retire stable broker root");
  retirement_ack_failure_retry(delegated);
  {
    State state;
    const std::string name="authority-broker-"+std::string(32,'3');
    auto owned=ProcessScope::prepare_owned(delegated,name);
    const auto owned_path=delegated/name;
    auto prior_parent=ProcessScope::prepare_named(owned_path,"spatial-git-workers");
    const auto prior_identity=prior_parent->identity();prior_parent->stop();
    FileOperationJournal journal(state.path);
    const auto root_path=std::filesystem::canonical(owned_path);
    journal.reserve_git_worker_parent(owned->identity().device(),owned->identity().inode(),root_path.string(),owned->identity().boot_id(),{});
    journal.bind_git_worker_parent(owned->identity().device(),owned->identity().inode(),root_path.string(),owned->identity().boot_id(),prior_identity.SerializeAsString(),{});
    auto participant=BrokerCopyCleanupParticipant::install(*owned,state.fd);
    check(std::filesystem::create_directory(owned_path/"spatial-git-workers"),"replacement Git parent setup failed");
    AuthorityProcessLaunch child_request;child_request.set_executable("/bin/true");
    const int child_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(child_null>=0,"identity-mismatch child null");
    owned->start(child_request,{child_null,child_null,child_null});close(child_null);
    const int child_handle=owned->duplicate_child_pidfd();check(child_handle>=0,"identity-mismatch child pidfd");
    pollfd child_done{child_handle,POLLIN,0};const int child_ready=poll(&child_done,1,5000);close(child_handle);
    check(child_ready==1&&(child_done.revents&POLLIN),"identity-mismatch child did not exit");
    const auto before=owned->identity();
    bool identity_rejected=false;
    try { owned->stop(); }
    catch(const std::exception& error) { identity_rejected=std::string(error.what()).find("Recovered Git worker parent identity changed")!=std::string::npos; }
    check(identity_rejected,"replaced Git parent identity was accepted for root retirement");
    struct stat retained{};
    check(lstat(owned_path.c_str(),&retained)==0&&retained.st_dev==static_cast<dev_t>(before.device())&&
        retained.st_ino==static_cast<ino_t>(before.inode()),"identity mismatch retired or replaced stable broker root");
    check(std::filesystem::remove(owned_path/"spatial-git-workers"),"mismatched empty Git parent cleanup failed");
    owned->stop();check(!std::filesystem::exists(owned_path),"exact CAS retry did not retire repaired stable root");
  }
  {
    State state;
    const std::string name="authority-broker-"+std::string(32,'4');
    auto owned=ProcessScope::prepare_owned(delegated,name);
    const auto owned_path=delegated/name;
    auto participant=BrokerCopyCleanupParticipant::deferred(*owned);
    AuthorityProcessLaunch child_request;child_request.set_executable("/bin/true");
    const int child_null=open("/dev/null",O_RDWR|O_CLOEXEC);check(child_null>=0,"uncertain retirement child null");
    owned->start(child_request,{child_null,child_null,child_null});close(child_null);
    const int child_handle=owned->duplicate_child_pidfd();check(child_handle>=0,"uncertain retirement child pidfd");
    pollfd child_done{child_handle,POLLIN,0};const int child_ready=poll(&child_done,1,5000);close(child_handle);
    check(child_ready==1&&(child_done.revents&POLLIN),"uncertain retirement child did not exit");
    // enable_children() intentionally requires an unstarted scope. This fixture
    // enables the real cgroup controls only after its retained child has exited.
    {
      std::ofstream controllers(owned_path/"cgroup.subtree_control");
      check(bool(controllers),"uncertain retirement subtree control open failed");
      controllers<<"+cpu +memory +pids +io\n";controllers.close();
      check(!controllers.fail(),"uncertain retirement child controllers were not enabled");
    }
    const auto ids=create_descendants(owned_path,true);
    auto job=record(state,owned->identity(),ids,false);
    seed(state,owned->identity(),owned_path,ids,job);
    job.set_state(FILE_COPY_JOB_JOURNAL_UNCERTAIN);job.set_updated_at_unix_ms(2);
    auto* uncertain_receipt=job.mutable_terminal_receipt();
    uncertain_receipt->set_job_id(job.job_id());uncertain_receipt->set_state(FileCopyJobReceipt::UNCERTAIN);
    uncertain_receipt->set_detail("retained root regression");
    FileOperationJournal journal(state.path);journal.update_copy_job(job,{});
    participant->bind(FileOperationJournal::pin_existing(state.fd));
    owned->stop();
    struct stat retained{};
    check(lstat(owned_path.c_str(),&retained)==0&&retained.st_dev==static_cast<dev_t>(owned->identity().device())&&
        retained.st_ino==static_cast<ino_t>(owned->identity().inode()),"uncertain copy job retired the stable broker root");
    const auto active=journal.find_git_worker_parent({});
    check(active.state==spatial::GitWorkerParentRecord::State::bound&&active.root_device==owned->identity().device()&&
        active.root_inode==owned->identity().inode()&&active.root_path==std::filesystem::canonical(owned_path).string(),
        "uncertain copy job cleared its active Git parent binding");
    const auto saved=journal.scope_cleanup_outcome(job.workspace_id(),job.job_id(),{});
    check(saved&&saved->kind()==FileCopyScopeCleanupOutcome::IMMUTABLE_UNCERTAINTY,
        "uncertain copy job lost its immutable cleanup evidence");
    const auto worker_path=owned_path/"spatial-git-workers"/ids.worker.scope_name();
    struct stat worker{};check(lstat(worker_path.c_str(),&worker)<0&&errno==ENOENT,
        "uncertain copy worker remained live after checked drain");
    check(std::filesystem::remove(owned_path),"isolated retained-root fixture teardown failed");
  }
  root_replacement_control(delegated);
  std::cout<<"callback_order_failure_retry_destructor=PASS\n";
}
void diagnostic_scope(std::ostream& out,const char* label,const ProcessScopeIdentity& identity){
  out<<' '<<label<<"_dev="<<identity.device()<<' '<<label<<"_ino="<<identity.inode()
      <<' '<<label<<"_boot="<<identity.boot_id()<<' '<<label<<"_name="<<identity.scope_name()
      <<' '<<label<<"_launch_bytes="<<identity.launch_id().size();
}
void diagnostic_job(std::ostream& out,const FileOperationJournal::CopyJobRecord& job){
  out<<" state="<<static_cast<int>(job.state())<<" job_root_dev="<<job.broker_root_device()
      <<" job_root_ino="<<job.broker_root_inode()<<" job_root_boot="<<job.broker_root_boot_id()
      <<" project_dev="<<job.worktree_root_device()<<" project_ino="<<job.worktree_root_inode()
      <<" staging_root_dev="<<job.staging_root().device()<<" staging_root_ino="<<job.staging_root().inode()
      <<" has_worker="<<job.has_process_scope()<<" has_drain="<<job.has_drained_process_scope();
  if(job.has_process_scope())diagnostic_scope(out,"worker",job.process_scope());
  diagnostic_scope(out,"parent",job.process_scope_parent());
  if(job.has_drained_process_scope())diagnostic_scope(out,"drained",job.drained_process_scope());
}
void emit_participant_diagnostic(std::ostringstream& out){
  auto line=out.str();if(line.size()>4095)line.resize(4095);line+='\n';
  (void)write(STDERR_FILENO,line.data(),line.size());
}

using TestStorageDevice = std::pair<unsigned, unsigned>;
using TestStorageDevices = std::vector<TestStorageDevice>;
struct TestScopedFd {
  int value=-1;
  explicit TestScopedFd(int descriptor):value(descriptor){}
  ~TestScopedFd(){if(value>=0)close(value);}
  TestScopedFd(const TestScopedFd&)=delete;
  TestScopedFd& operator=(const TestScopedFd&)=delete;
};
TestStorageDevices test_storage_devices(int descriptor) {
  std::set<TestStorageDevice> devices;
  std::function<void(int,unsigned)> filesystem_device;
  std::function<void(dev_t,unsigned)> block_device;
  const auto read_device=[](const std::filesystem::path& node) {
    std::ifstream file(node/"dev");unsigned major_number=0,minor_number=0;char colon=0;
    check(static_cast<bool>(file>>major_number>>colon>>minor_number)&&colon==':',
          "consumer storage block identity unavailable");
    return TestStorageDevice{major_number,minor_number};
  };
  block_device=[&](dev_t number,unsigned depth) {
    check(depth<8,"consumer storage device nesting exceeds supported bound");
    const auto link=std::filesystem::path("/sys/dev/block")/
        (std::to_string(test_device_major(number))+":"+std::to_string(test_device_minor(number)));
    auto device=std::filesystem::canonical(link);
    if(std::filesystem::exists(device/"partition"))device=device.parent_path();
    const auto identity=read_device(device);
    if(!devices.insert(identity).second)return;
    // Match production coverage: charge a loop queue and its real backing filesystem.
    const auto backing_file=device/"loop/backing_file";
    if(std::filesystem::exists(backing_file)) {
      std::ifstream backing(backing_file);std::string path;std::getline(backing,path);
      check((backing.good()||backing.eof())&&std::filesystem::path(path).is_absolute(),
            "consumer loop backing identity unavailable");
      TestScopedFd backing_fd(open(path.c_str(),O_PATH|O_NOFOLLOW|O_CLOEXEC));
      check(backing_fd.value>=0,"consumer loop backing filesystem pin failed");
      filesystem_device(backing_fd.value,depth+1);
    }
    const auto slaves=device/"slaves";
    if(std::filesystem::exists(slaves)) {
      for(const auto& child:std::filesystem::directory_iterator(slaves))
        devices.insert(read_device(child.path()));
    }
  };
  filesystem_device=[&](int held,unsigned depth) {
    check(depth<8,"consumer filesystem device nesting exceeds supported bound");
    struct stat info{};struct statfs filesystem{};
    check(held>=0&&fstat(held,&info)==0&&fstatfs(held,&filesystem)==0,
          "consumer storage filesystem identity unavailable");
    if(filesystem.f_type==BTRFS_SUPER_MAGIC) {
      const auto pinned_path="/proc/self/fd/"+std::to_string(held);
      TestScopedFd query(open(pinned_path.c_str(),O_RDONLY|O_CLOEXEC));
      struct stat pinned{};
      check(query.value>=0&&fstat(query.value,&pinned)==0&&pinned.st_dev==info.st_dev&&pinned.st_ino==info.st_ino,
            "consumer Btrfs query authority could not be pinned");
      btrfs_ioctl_fs_info_args volume{};
      check(ioctl(query.value,BTRFS_IOC_FS_INFO,&volume)>=0,
            "consumer Btrfs volume device query failed");
      check(volume.num_devices==1,"consumer Btrfs fixture must be single-device");
      btrfs_ioctl_dev_info_args selected{};selected.devid=volume.max_id;
      check(ioctl(query.value,BTRFS_IOC_DEV_INFO,&selected)>=0,
            "consumer Btrfs block device query failed");
      const auto length=strnlen(reinterpret_cast<const char*>(selected.path),sizeof(selected.path));
      check(length>0&&length<sizeof(selected.path),"consumer Btrfs block device path invalid");
      const std::filesystem::path block_path(std::string(reinterpret_cast<const char*>(selected.path),length));
      struct stat block{};
      check(block_path.is_absolute()&&stat(block_path.c_str(),&block)==0&&S_ISBLK(block.st_mode),
            "consumer Btrfs block device unavailable");
      block_device(block.st_rdev,depth+1);
    } else {
      block_device(info.st_dev,depth+1);
    }
  };
  filesystem_device(descriptor,0);
  return TestStorageDevices(devices.begin(),devices.end());
}
void test_storage_device_coverage_controls(int directory,const TestStorageDevices& devices) {
  check(directory>=0&&!devices.empty(),"consumer storage device closure is unavailable");
  const auto coverage=[&](std::optional<std::size_t> omitted) {
    ProcessScopeBudget budget;
    for(std::size_t index=0;index<devices.size();++index) {
      if(omitted&&*omitted==index)continue;
      auto* io=budget.add_io();io->set_major(devices[index].first);io->set_minor(devices[index].second);
      io->set_read_bytes_per_second(64ULL*1024*1024);io->set_write_bytes_per_second(16ULL*1024*1024);
    }
    return budget;
  };
  spatial::validate_storage_device_handles(coverage(std::nullopt),std::span<const int>(&directory,1));
  for(std::size_t index=0;index<devices.size();++index) {
    bool rejected=false;
    try { spatial::validate_storage_device_handles(coverage(index),std::span<const int>(&directory,1)); }
    catch(const std::exception& error) {
      const auto reason=std::string(error.what());
      rejected=reason=="Workspace storage device has no I/O budget"||
          reason=="Stacked storage device has no I/O budget";
    }
    check(rejected,"production storage validator did not reject the intended missing-device budget");
  }
}

spatial::supervisor::v1::ProcessScopeBudget test_budget(std::uint64_t memory,std::uint64_t processes,
    std::uint64_t cpu,const TestStorageDevices& devices) {
  spatial::supervisor::v1::ProcessScopeBudget value;
  value.set_memory_bytes(memory);value.set_swap_bytes(0);value.set_processes(processes);
  value.set_cpu_microseconds(cpu);
  for(const auto& device:devices) {
    auto* io=value.add_io();io->set_major(device.first);io->set_minor(device.second);
    io->set_read_bytes_per_second(64ULL*1024*1024);io->set_write_bytes_per_second(16ULL*1024*1024);
  }
  return value;
}
spatial::workspace::v1::BrokerResourceConfig test_resource_config(
    const std::filesystem::path& storage) {
  TestScopedFd directory(open(storage.c_str(),O_PATH|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW));
  check(directory.value>=0,"consumer storage directory pin failed");
  const auto devices=test_storage_devices(directory.value);
  test_storage_device_coverage_controls(directory.value,devices);
  const auto budget=[&](std::uint64_t memory,std::uint64_t processes,std::uint64_t cpu) {
    return test_budget(memory,processes,cpu,devices);
  };
  spatial::workspace::v1::BrokerResourceConfig config;config.set_protocol_version(2);
  *config.mutable_broker_budget()=budget(1024ULL*1024*1024,128,400000);
  *config.mutable_manager_budget()=budget(256ULL*1024*1024,32,200000);
  auto* policy=config.mutable_default_policy();
  *policy->mutable_total()=budget(512ULL*1024*1024,64,200000);
  *policy->mutable_actor_default()=budget(256ULL*1024*1024,32,100000);
  auto* profile=policy->add_profiles();profile->set_name("development");
  *profile->mutable_maximum()=budget(128ULL*1024*1024,16,100000);
  const auto check_devices=[&](const auto& value) {
    check(static_cast<std::size_t>(value.io_size())==devices.size(),
          "consumer resource budget omitted storage closure devices");
    for(std::size_t index=0;index<devices.size();++index)
      check(value.io(static_cast<int>(index)).major()==devices[index].first&&
          value.io(static_cast<int>(index)).minor()==devices[index].second,
          "consumer resource budgets do not share one storage closure");
  };
  check_devices(config.broker_budget());check_devices(config.manager_budget());
  check_devices(policy->total());check_devices(policy->actor_default());check_devices(profile->maximum());
  return config;
}
int consumer_recovery_child(const char* state_path,const char* affected_project,
                            const char* workspace,const char* worktree,const char* job_id,const char* independent_project) {
  try {
    const std::filesystem::path state=state_path, project=affected_project, independent=independent_project;
    int state_fd=open(state.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    check(state_fd>=0,"consumer state directory open failed");
    FileOperationJournal journal(state);
    auto pin=FileOperationJournal::pin_existing(state_fd);
    std::ifstream cgroup_file("/proc/self/cgroup"); std::string cgroup_line;
    std::getline(cgroup_file,cgroup_line);
    check(cgroup_line.starts_with("0::/"),"consumer cgroup path unavailable");
    const auto cgroup=std::filesystem::path("/sys/fs/cgroup")/cgroup_line.substr(4);
    const int resource_config=spatial::seal_resource_config(test_resource_config(project),cgroup);
    spatial::WorkspaceResources resources(resource_config,true);
    const auto saved=journal.scope_cleanup_outcome(workspace, job_id, {});
    check(saved&&saved->kind()==spatial::workspace::v1::FileCopyScopeCleanupOutcome::REPLACED_SCOPE,
          "consumer did not load the retained old-generation disposition");
    const auto current_identity=resources.broker_root_identity();
    check(saved->broker_root().device()!=current_identity.first || saved->broker_root().inode()!=current_identity.second,
          "cleanup outcome was rebound to the new generation root");
    RecoveryTestAuthority authority;
    int project_fd=open(project.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    int stage_fd=fcntl(project_fd,F_DUPFD_CLOEXEC,3);
    check(project_fd>=0&&stage_fd>=0,"affected consumer project descriptors open failed");
    bool quarantined=false;
    try {
      spatial::FileEngine rejected(project_fd,workspace,{worktree},authority,
          [] { return spatial::FileEngine::MonotonicClock::now(); },nullptr,&resources,stage_fd,
          ".spatial-copy-staging",&journal,{},&pin);
    } catch(const std::exception& error) {
      quarantined=std::string(error.what())=="Copy worktree quarantined by durable scope cleanup conflict";
    }
    close(project_fd); close(stage_fd);
    check(quarantined,"resource-backed FileEngine did not quarantine stale proof after scope replacement");
    resources.finish_initialization();
    std::filesystem::create_directories(independent/".spatial-copy-staging");
    chmod((independent/".spatial-copy-staging").c_str(),0700);
    project_fd=open(independent.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    stage_fd=fcntl(project_fd,F_DUPFD_CLOEXEC,3);
    check(project_fd>=0&&stage_fd>=0,"independent consumer project descriptors open failed");
    {
      spatial::FileEngine engine(project_fd,"independent-workspace",{"tree-independent"},authority,
          [] { return spatial::FileEngine::MonotonicClock::now(); },nullptr,&resources,stage_fd,
          ".spatial-copy-staging",&journal,{},&pin);
      spatial::workspace::v1::FileActionRequest request;
      request.set_protocol_version(1); request.set_operation_id(std::string(16,'b'));
      request.set_workspace_id("independent-workspace");
      request.mutable_principal()->set_issuer_id(std::string(32,'i'));
      request.mutable_principal()->set_actor_id(std::string(32,'a'));
      request.add_authority_components("tree-independent"); request.mutable_browse()->set_page_size(32);
      const auto reply=engine.handle(request);
      check(reply.has_directory(),"authorized independent browse did not succeed");
    }
    close(project_fd); close(stage_fd); close(state_fd);
    const char success[]="STALE_PROOF_QUARANTINED INDEPENDENT_BROWSE_OK\n";
    check(write(STDOUT_FILENO,success,sizeof(success)-1)==static_cast<ssize_t>(sizeof(success)-1),"consumer result write failed");
    return 0;
  } catch(const std::exception& error) {
    const std::string failure=std::string("CONSUMER_ERROR ")+error.what()+"\n";
    (void)write(STDOUT_FILENO,failure.data(),failure.size()); return 1;
  }
}

void bootstrap_controls(){
  State state;
  using Bootstrap=spatial::supervisor::BrokerCleanupBootstrap;
  auto socket_pair=[](){int pair[2];check(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,pair)==0,"bootstrap socketpair");
    Bootstrap::require_credentials(pair[0]);Bootstrap::require_credentials(pair[1]);return std::array<int,2>{pair[0],pair[1]};};
  auto send_raw=[&](int fd,const std::string& payload,const std::vector<int>& descriptors,bool raw_packet=false){
    std::string bytes=payload;
    if(!raw_packet){
      const std::uint32_t size=htonl(static_cast<std::uint32_t>(payload.size()));
      bytes.assign(reinterpret_cast<const char*>(&size),sizeof(size));bytes+=payload;
    }
    iovec part{bytes.data(),bytes.size()};msghdr msg{};msg.msg_iov=&part;msg.msg_iovlen=1;
    alignas(cmsghdr)char control[CMSG_SPACE(sizeof(int)*16)]{};
    if(!descriptors.empty()){
      msg.msg_control=control;msg.msg_controllen=CMSG_SPACE(sizeof(int)*descriptors.size());
      auto* c=CMSG_FIRSTHDR(&msg);c->cmsg_level=SOL_SOCKET;c->cmsg_type=SCM_RIGHTS;
      c->cmsg_len=CMSG_LEN(sizeof(int)*descriptors.size());std::memcpy(CMSG_DATA(c),descriptors.data(),sizeof(int)*descriptors.size());
    }
    check(sendmsg(fd,&msg,MSG_NOSIGNAL)==static_cast<ssize_t>(bytes.size()),"raw bootstrap frame send");
  };
  auto encode_frame=[](spatial::supervisor::v1::BrokerCleanupBootstrapFrame frame){
    std::string payload;check(frame.SerializeToString(&payload),"bootstrap protobuf serialize");return payload;
  };
  auto expect_rejection_and_closed_rights=[&](const std::string& payload,std::size_t descriptor_count,
                                                const char* expected,const char* why,bool raw_packet=false){
    int channel[2];check(pipe2(channel,O_CLOEXEC)==0,"FD closure pipe");
    std::vector<int> descriptors(descriptor_count,channel[1]);
    auto pair=socket_pair();send_raw(pair[1],payload,descriptors,raw_packet);close(channel[1]);
    bool intended=false;std::string rejection_reason;
    try{(void)Bootstrap::receive_request(pair[0],getpid(),getuid(),std::chrono::seconds(1));}
    catch(const std::exception& error){rejection_reason=error.what();intended=rejection_reason.find(expected)!=std::string::npos;}
    if(!intended)throw std::runtime_error(std::string(why)+"; actual rejection: "+(rejection_reason.empty()?"<none>":rejection_reason));
    pollfd event{channel[0],static_cast<short>(POLLIN|POLLHUP),0};
    check(poll(&event,1,250)==1&&(event.revents&(POLLIN|POLLHUP)),"rejected bootstrap leaked received FD");
    char byte=1;check(read(channel[0],&byte,1)==0,"rejected bootstrap retained pipe writer");
    close(channel[0]);close(pair[0]);close(pair[1]);
  };
  {
    spatial::supervisor::v1::BrokerCleanupBootstrapFrame frame;
    frame.set_protocol_version(2);frame.set_correlation_id(std::string(32,'v'));frame.set_kind(frame.BIND_REQUEST);
    expect_rejection_and_closed_rights(encode_frame(frame),1,"Invalid broker cleanup bootstrap frame",
        "unsupported bootstrap version was not the rejection reason");
  }
  {
    spatial::supervisor::v1::BrokerCleanupBootstrapFrame frame;
    frame.set_protocol_version(1);frame.set_correlation_id(std::string(32,'u'));frame.set_kind(frame.BIND_REQUEST);
    auto payload=encode_frame(frame);payload.append("\x98\x06\x01",3);
    expect_rejection_and_closed_rights(payload,1,"Invalid broker cleanup bootstrap frame",
        "unknown bootstrap protobuf field was not the rejection reason");
  }
  {
    expect_rejection_and_closed_rights(std::string(1,static_cast<char>(0xff)),1,"Malformed broker cleanup bootstrap protobuf",
        "malformed bootstrap protobuf was not the rejection reason");
  }
  {
    std::string oversized(5000,'z');
    expect_rejection_and_closed_rights(oversized,1,"truncated or oversized",
        "oversized bootstrap frame was not the rejection reason");
  }
  {
    auto pair=socket_pair();Bootstrap::send_ack(pair[0],std::string(32,'a'));
    rejects([&]{(void)Bootstrap::receive_ack(pair[1],getpid(),getuid(),std::chrono::seconds(1),std::string(32,'b'));},
        "mismatched bootstrap acknowledgment correlation accepted");close(pair[0]);close(pair[1]);
  }
  {
    auto pair=socket_pair();const int flags=fcntl(pair[1],F_GETFL);check(flags>=0&&fcntl(pair[1],F_SETFL,flags|O_NONBLOCK)==0,"set bootstrap send nonblocking");
    const char fill='q';while(send(pair[1],&fill,1,MSG_NOSIGNAL)>=0){}
    check(errno==EAGAIN||errno==EWOULDBLOCK,"bootstrap send queue did not fill");
    struct sigaction action{},previous{};action.sa_handler=+[](int){};sigemptyset(&action.sa_mask);
    check(sigaction(SIGALRM,&action,&previous)==0,"install blocked-send interrupt handler");
    const auto began=std::chrono::steady_clock::now();const auto deadline=Bootstrap::deadline_after(std::chrono::milliseconds(50));
    ualarm(5000,5000);
    rejects([&]{Bootstrap::send_request(pair[1],std::string(32,'s'),state.fd,deadline);},
        "blocked bootstrap send exceeded its absolute deadline");
    ualarm(0,0);sigaction(SIGALRM,&previous,nullptr);
    check(std::chrono::steady_clock::now()-began<std::chrono::milliseconds(150),"blocked send deadline reset across EINTR");
    close(pair[0]);close(pair[1]);
  }
  {
    struct sigaction action{},previous{};action.sa_handler=+[](int){};sigemptyset(&action.sa_mask);
    check(sigaction(SIGALRM,&action,&previous)==0,"install bootstrap interrupt handler");
    ualarm(5000,5000);auto pair=socket_pair();const auto began=std::chrono::steady_clock::now();
    const auto deadline=Bootstrap::deadline_after(std::chrono::milliseconds(50));
    rejects([&]{(void)Bootstrap::receive_request(pair[0],getpid(),getuid(),deadline);},
        "interrupted bootstrap receive accepted an empty peer");
    ualarm(0,0);close(pair[0]);close(pair[1]);sigaction(SIGALRM,&previous,nullptr);
    check(std::chrono::steady_clock::now()-began<std::chrono::milliseconds(150),"signal restarted bootstrap absolute deadline");
  }
  {
    auto pair=socket_pair();
    const pid_t child=fork();check(child>=0,"bootstrap fork");
    if(child==0){close(pair[0]);try{Bootstrap::send_request(pair[1],std::string(32,'c'),state.fd);
        Bootstrap::receive_ack(pair[1],getppid(),getuid(),std::chrono::seconds(2),std::string(32,'c'));_exit(0);}catch(...){_exit(81);}}
    close(pair[1]);
    auto request=Bootstrap::receive_request(pair[0],child,getuid(),std::chrono::seconds(2));
    check(request.pid==child&&request.uid==getuid()&&request.frame.correlation_id()==std::string(32,'c'),"valid child bootstrap identity");
    Bootstrap::validate_state_root(request.descriptor,state.path);
    Bootstrap::send_ack(pair[0],request.frame.correlation_id());close(pair[0]);int status=0;
    check(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==0,"valid child bootstrap completion");
  }
  auto rejected_sender=[&](bool wrong_uid){
    auto pair=socket_pair();const pid_t child=fork();check(child>=0,"negative bootstrap fork");
    if(child==0){close(pair[0]);try{Bootstrap::send_request(pair[1],std::string(32,'d'),state.fd);_exit(0);}catch(...){_exit(82);}}
    close(pair[1]);const auto uid=wrong_uid?static_cast<uid_t>(getuid()+1):getuid();
    rejects([&]{(void)Bootstrap::receive_request(pair[0],wrong_uid?child:child+1,uid,std::chrono::seconds(2));},
        wrong_uid?"wrong bootstrap UID accepted":"wrong bootstrap PID accepted");
    close(pair[0]);int status=0;check(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==0,"negative bootstrap child failed");
  };
  rejected_sender(false);rejected_sender(true);
  {
    spatial::supervisor::v1::BrokerCleanupBootstrapFrame frame;
    frame.set_protocol_version(1);frame.set_correlation_id(std::string(32,'t'));frame.set_kind(frame.BIND_REQUEST);
    expect_rejection_and_closed_rights(encode_frame(frame),16,"truncated or oversized",
        "truncated ancillary data was not the rejection reason");
  }
  {
    spatial::supervisor::v1::BrokerCleanupBootstrapFrame frame;
    frame.set_protocol_version(1);frame.set_correlation_id(std::string(32,'m'));frame.set_kind(frame.BIND_REQUEST);
    int channel[2];check(pipe2(channel,O_CLOEXEC)==0,"allocation failure FD pipe");
    auto pair=socket_pair();send_raw(pair[1],encode_frame(frame),{channel[1]});close(channel[1]);
    allocation_budget=0;bool allocation_failed=false;
    try{(void)Bootstrap::receive_request(pair[0],getpid(),getuid(),std::chrono::seconds(1));}
    catch(const std::bad_alloc&){allocation_failed=true;}
    allocation_budget=-1;check(allocation_failed,"bootstrap receive allocation failure was not exercised");
    pollfd event{channel[0],static_cast<short>(POLLIN|POLLHUP),0};
    check(poll(&event,1,250)==1&&(event.revents&(POLLIN|POLLHUP)),"allocation failure leaked received FD");
    char byte=1;check(read(channel[0],&byte,1)==0,"allocation failure retained pipe writer");
    close(channel[0]);close(pair[0]);close(pair[1]);
  }
  {
    auto pair=socket_pair();const pid_t child=fork();check(child>=0,"missing descriptor fork");
    if(child==0){close(pair[0]);try{Bootstrap::send_request(pair[1],std::string(32,'e'),-1);_exit(0);}catch(...){_exit(83);}}
    close(pair[1]);rejects([&]{(void)Bootstrap::receive_request(pair[0],child,getuid(),std::chrono::seconds(2));},"bytes-only bootstrap accepted");
    close(pair[0]);int status=0;check(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==0,"bytes-only sender failed");
  }
  {
    spatial::supervisor::v1::BrokerCleanupBootstrapFrame frame;
    frame.set_protocol_version(1);frame.set_correlation_id(std::string(32,'f'));frame.set_kind(frame.BIND_REQUEST);
    expect_rejection_and_closed_rights(encode_frame(frame),2,"requires exactly one directory descriptor",
        "extra bootstrap FD count was not the rejection reason");
  }
  {
    expect_rejection_and_closed_rights(std::string("xy",2),1,"frame is incomplete",
        "truncated bootstrap frame was not the rejection reason",true);
  }
  {
    auto pair=socket_pair();close(pair[1]);
    rejects([&]{(void)Bootstrap::receive_request(pair[0],getpid(),getuid(),std::chrono::milliseconds(20));},"closed bootstrap peer accepted");close(pair[0]);
    pair=socket_pair();rejects([&]{(void)Bootstrap::receive_request(pair[0],getpid(),getuid(),std::chrono::milliseconds(20));},"bootstrap timeout accepted");close(pair[0]);close(pair[1]);
  }
  {
    State other;rejects([&]{Bootstrap::validate_state_root(other.fd,state.path);},"substituted cleanup state root accepted");
    auto pin=FileOperationJournal::pin_existing(state.fd);const auto database=state.path/"file-operations/file-operations.db";
    const auto saved=state.path/"file-operations/file-operations.db.saved";std::filesystem::rename(database,saved);
    write_file(database,"substituted database inode");chmod(database.c_str(),0600);
    rejects([&]{pin.verify();},"substituted pinned database inode accepted");
    std::filesystem::remove(database);std::filesystem::rename(saved,database);
  }
}

void participant_case(const std::filesystem::path& delegated,const std::string& mode){
  active_cleanup_mode=mode;
  const auto case_began=std::chrono::steady_clock::now();
  cleanup_phase(mode,"case_begin",case_began);
  State state;auto root=ProcessScope::prepare(delegated);const auto path=delegated/root->identity().scope_name();
  root->enable_children();const bool provisional=mode=="reserved"||mode=="reserved-empty"||mode=="deadline-multirow";const auto ids=create_descendants(path,!provisional);
  if(mode=="reserved-empty")check(mkdir((path/"spatial-git-workers/copy-job").c_str(),0755)==0,"present reserved child creation failed");
  auto job=record(state,root->identity(),ids,provisional);
  cleanup_phase(mode,"base_seed_begin",case_began);
  seed(state,root->identity(),path,ids,job);
  cleanup_phase(mode,"base_seed_done",case_began);
  constexpr std::uint64_t kDeadlineRows=128;
  if(mode=="deadline-multirow") {
    cleanup_phase(mode,"multirow_seed_begin",case_began,"rows="+std::to_string(kDeadlineRows));
    FileOperationJournal rows(state.path);
    for(std::uint64_t i=1;i<kDeadlineRows;++i) {
      auto extra=job;std::string suffix(16,'j');for(unsigned byte=0;byte<8;++byte)suffix[15-byte]=static_cast<char>((i>>(byte*8))&0xff);
      extra.set_job_id(suffix);suffix.assign(16,'o');for(unsigned byte=0;byte<8;++byte)suffix[15-byte]=static_cast<char>((i>>(byte*8))&0xff);
      extra.set_initiating_operation_id(suffix);rows.begin_copy_job(extra,{});
      if(i%250==0)cleanup_phase(mode,"multirow_seed_progress",case_began,"rows="+std::to_string(i+1));
    }
    cleanup_phase(mode,"multirow_seed_done",case_began,"rows="+std::to_string(kDeadlineRows));
  }
  const auto worker=path/"spatial-git-workers/copy-job";
  if(mode=="old-proof-replaced"||mode=="old-proof-parent-replaced"){
    FileOperationJournal journal(state.path);
    ProcessScope::stop_recovered(path/"spatial-git-workers",ids.parent.device(),ids.parent.inode(),ids.worker,[&](const auto& actual){
      *job.mutable_drained_process_scope()=actual;journal.update_copy_job(job,{});
    });
    if(mode=="old-proof-replaced") std::filesystem::create_directory(worker);
    else {std::filesystem::remove(path/"spatial-git-workers");std::filesystem::create_directory(path/"spatial-git-workers");}
  }
  if(mode=="prepared") {
    const auto output=state.path/"project/.spatial-copy-staging/.spatial-copy-stage-job/.spatial-copy-output-prepared";
    write_file(output,"prepared");struct stat info{};check(stat(output.c_str(),&info)==0,"prepared identity");
    auto* prepared=job.mutable_prepared_result();prepared->set_staging_output_name(output.filename().string());
    prepared->mutable_output()->set_device(info.st_dev);prepared->mutable_output()->set_inode(info.st_ino);
    prepared->mutable_output()->set_type(FileCopyFilesystemIdentity::REGULAR_FILE);prepared->set_bytes_copied(8);prepared->set_entries_copied(1);
    std::string digest(32,'\0');unsigned size=0;check(EVP_Digest("prepared",8,reinterpret_cast<unsigned char*>(digest.data()),&size,EVP_sha256(),nullptr)==1&&size==32,"prepared digest");
    prepared->set_content_sha256(digest);job.set_state(FILE_COPY_JOB_JOURNAL_RUNNING);
    FileOperationJournal journal(state.path);journal.update_copy_job(job,{});
  }
  if(mode=="uncertain"){
    FileOperationJournal journal(state.path);job.set_state(FILE_COPY_JOB_JOURNAL_UNCERTAIN);auto* receipt=job.mutable_terminal_receipt();receipt->set_job_id(job.job_id());receipt->set_state(FileCopyJobReceipt::UNCERTAIN);receipt->set_detail("preexisting uncertainty");journal.update_copy_job(job,{});
  }
  if(mode=="missing"||mode=="replaced"||mode=="missing-parent"||mode=="replaced-parent"){
    write_file(worker/"cgroup.kill","1\n");wait_for([&]{return read_file(worker/"cgroup.events").find("populated 0")!=std::string::npos;});std::filesystem::remove(worker);
    if(mode=="replaced") std::filesystem::create_directory(worker);
    if(mode=="missing-parent"||mode=="replaced-parent") {
      std::filesystem::remove(path/"spatial-git-workers");
      if(mode=="replaced-parent") std::filesystem::create_directory(path/"spatial-git-workers");
    }
  }
  const auto marker=state.path/"project/.spatial-copy-staging/.spatial-copy-stage-job/stage-evidence";write_file(marker,"preserve marker");struct stat before{};check(stat(marker.c_str(),&before)==0,"marker stat");
  const auto original=job.SerializeAsString();auto participant=BrokerCopyCleanupParticipant::install(*root,state.fd);
  rejects([&]{participant->bind(FileOperationJournal::pin_existing(state.fd));},"duplicate deferred journal bind accepted");
  if(mode=="deadline-multirow") {
    root->request_stop();const auto began=std::chrono::steady_clock::now();
    cleanup_phase(mode,"first_attempt_begin",case_began);
    std::string first_rejection;
    try {root->complete_stop(began+std::chrono::milliseconds(300));}
    catch(const std::exception& error){first_rejection=error.what();cleanup_phase(mode,"first_attempt_rejected",case_began,first_rejection);}
    check(first_rejection.find("deadline")!=std::string::npos,"multi-row cleanup failed for a reason other than the shared deadline");
    const auto elapsed=std::chrono::steady_clock::now()-began;
    check(elapsed<std::chrono::seconds(2),"multi-row cleanup restarted or ignored its absolute budget");
    check(std::filesystem::exists(path),"multi-row expiry returned or removed root");
    cleanup_phase(mode,"partial_outcome_query_begin",case_began);
    sqlite3* raw=nullptr;check(sqlite3_open((state.path/"file-operations/file-operations.db").c_str(),&raw)==SQLITE_OK,"multi-row outcome inspection");
    sqlite3_stmt* count=nullptr;check(sqlite3_prepare_v2(raw,"SELECT count(*) FROM copy_scope_cleanup",-1,&count,nullptr)==SQLITE_OK&&sqlite3_step(count)==SQLITE_ROW,"multi-row outcome query");
    const auto partial=sqlite3_column_int64(count,0);sqlite3_finalize(count);close_test_db(raw);
    cleanup_phase(mode,"partial_outcome_query_done",case_began,"partial="+std::to_string(partial)+" total="+std::to_string(kDeadlineRows));
    check(partial>0&&partial<static_cast<sqlite3_int64>(kDeadlineRows),"multi-row deadline did not preserve bounded partial outcomes");
    cleanup_phase(mode,"exact_retry_begin",case_began);
    try {root->complete_stop(std::chrono::steady_clock::now()+std::chrono::seconds(30));}
    catch(const std::exception& error){cleanup_phase(mode,"exact_retry_rejected",case_began,error.what());throw;}
    cleanup_phase(mode,"exact_retry_done",case_began);
    std::cout<<"multirow_single_deadline_partial_retry=PASS partial="<<partial<<" total="<<kDeadlineRows<<"\n";
  }
  if(mode=="deadline-lock") {
    sqlite3* lock=nullptr;check(sqlite3_open((state.path/"file-operations/file-operations.db").c_str(),&lock)==SQLITE_OK,"deadline lock open");
    execute(lock,"BEGIN IMMEDIATE");root->request_stop();const auto began=std::chrono::steady_clock::now();
    deadline_rejects([&]{root->complete_stop(began+std::chrono::milliseconds(250));},"held SQLite writer lock did not expire through its shared deadline");
    const auto elapsed=std::chrono::steady_clock::now()-began;
    check(elapsed<std::chrono::seconds(2),"held lock exceeded inherited cleanup budget");
    check(std::filesystem::exists(worker)&&std::filesystem::exists(marker)&&read_file(marker)=="preserve marker","expired lock cleanup removed worker or stage evidence");
    execute(lock,"ROLLBACK");close_test_db(lock);
    root->complete_stop(std::chrono::steady_clock::now()+std::chrono::seconds(10));
    std::cout<<"held_sqlite_deadline_retry=PASS elapsed_ms="<<std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count()<<"\n";
  }
  {auto pinned=FileOperationJournal::pin_existing(state.fd);pinned.verify_workspace_binding(job);
    auto foreign=job;foreign.set_workspace_id("foreign-workspace");rejects([&]{pinned.verify_workspace_binding(foreign);},"foreign workspace accepted");
    foreign=job;foreign.set_worktree_root_inode(job.worktree_root_inode()+1);rejects([&]{pinned.verify_workspace_binding(foreign);},"foreign worktree inode accepted");}
  if(mode=="foreign-view"){
    auto other=ProcessScope::prepare(delegated);bool rejected=false;other->set_pre_prune_cleanup([&](const auto& view){try{participant->pre_prune(view);}catch(const std::exception&){rejected=true;}});other->stop();check(rejected,"foreign root view accepted");
  }
  if(mode=="commit-failure") {
    sqlite3* raw=nullptr;check(sqlite3_open((state.path/"file-operations/file-operations.db").c_str(),&raw)==SQLITE_OK,"fault DB");
    execute(raw,"CREATE TRIGGER reject_drain BEFORE UPDATE ON file_copy_jobs BEGIN SELECT RAISE(ABORT,'planned commit failure'); END;");sqlite3_close(raw);
    rejects([&]{root->stop();},"journal failure returned root cleanup receipt");
    check(std::filesystem::exists(worker),"failed durable callback removed exact worker");
    {FileOperationJournal journal(state.path);auto current=journal.find_copy_job(job.workspace_id(),job.job_id(),{});check(current&&current->SerializeAsString()==job.SerializeAsString(),"failed callback changed job");}
    check(sqlite3_open((state.path/"file-operations/file-operations.db").c_str(),&raw)==SQLITE_OK,"fault reopen");
    execute(raw,"DROP TRIGGER reject_drain;");sqlite3_close(raw);
  }
  if(mode=="missing-at-open") {
    disappearance_parent_device=ids.parent.device();disappearance_parent_inode=ids.parent.inode();
    disappeared_at_open.store(false,std::memory_order_release);disappear_at_open.store(true,std::memory_order_release);
  }
  {
    FileOperationJournal inspection(state.path);const auto records=inspection.recovery_copy_jobs_all({});
    std::ostringstream diagnostic;diagnostic<<"copy_cleanup_diag mode="<<mode<<" phase=before recovery_count="<<records.size();
    diagnostic_scope(diagnostic,"owner",root->identity());unsigned matched=0;
    for(const auto& saved:records)if(saved.workspace_id()==job.workspace_id()&&saved.job_id()==job.job_id()){
      ++matched;if(matched==1)diagnostic_job(diagnostic,saved);
    }
    diagnostic<<" matched="<<matched;emit_participant_diagnostic(diagnostic);
  }
  if(mode!="deadline-lock"&&mode!="deadline-multirow") { root->stop(); }
  check(!std::filesystem::exists(path),"root not removed");
  if(mode=="missing-at-open") {
    disappear_at_open.store(false,std::memory_order_release);
    check(disappeared_at_open.load(std::memory_order_acquire),"actual callee openat race was not reached");
  }
  auto recovered=std::make_unique<FileOperationJournal>(state.path);auto after=recovered->find_copy_job(job.workspace_id(),job.job_id(),{});check(bool(after),"job lost");
  const auto outcome=recovered->scope_cleanup_outcome(job.workspace_id(),job.job_id(),{});
  {
    std::ostringstream diagnostic;diagnostic<<"copy_cleanup_diag mode="<<mode<<" phase=after";
    diagnostic_job(diagnostic,*after);diagnostic<<" has_outcome="<<bool(outcome);
    if(outcome){
      diagnostic<<" outcome_kind="<<static_cast<int>(outcome->kind())
          <<" outcome_execution_dev="<<outcome->journal().execution_device()<<" outcome_execution_ino="<<outcome->journal().execution_inode()
          <<" outcome_database_dev="<<outcome->journal().journal_database_device()<<" outcome_database_ino="<<outcome->journal().journal_database_inode()
          <<" outcome_journal_id_bytes="<<outcome->journal().journal_id().size();
      diagnostic_scope(diagnostic,"outcome_root",outcome->broker_root());
    }
    emit_participant_diagnostic(diagnostic);
  }
  check(after->prepared_result().SerializeAsString()==job.prepared_result().SerializeAsString(),"prepared identity changed");
  if(mode=="reserved"||mode=="reserved-empty") {
    check(outcome&&outcome->kind()==FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED&&!after->has_process_scope(),"reserved outcome invented proof");
    struct stat reserved_info{};
    check(lstat(worker.c_str(),&reserved_info)<0&&errno==ENOENT,"reserved child remained after reconciliation");

    if(mode=="reserved") {

      check(outcome&&outcome->kind()==FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED&&
            !after->has_process_scope()&&!after->has_drained_process_scope(),"reserved outcome invented proof");
      auto pin=FileOperationJournal::pin_existing(state.fd);
      auto cancelled=*after;
      cancelled.set_state(FILE_COPY_JOB_JOURNAL_CANCELLED);
      cancelled.set_updated_at_unix_ms(after->updated_at_unix_ms()+1);
      auto* receipt=cancelled.mutable_terminal_receipt();
      receipt->set_job_id(cancelled.job_id());receipt->set_state(FileCopyJobReceipt::CANCELLED);
      receipt->set_bytes_copied(cancelled.bytes_progress());receipt->set_entries_copied(cancelled.entries_progress());
      recovered->update_copy_job(cancelled,{});
      const auto consumed=recovered->find_copy_job(cancelled.workspace_id(),cancelled.job_id(),{});
      check(consumed&&consumed->state()==FILE_COPY_JOB_JOURNAL_CANCELLED&&
            !consumed->has_process_scope()&&!consumed->has_drained_process_scope(),
            "reconciled reservation did not become a proof-free cancellation");
      const auto retained=recovered->validated_scope_cleanup_outcome(*consumed,nullptr,pin,{});
      check(retained&&retained->kind()==FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED,
            "consumed reservation could not authorize its exact cancelled receipt");
    }
  }
  else if(mode=="deadline-multirow") {
    check(outcome&&outcome->kind()==FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED&&!after->has_process_scope(),"multi-row retry invented worker proof");
    sqlite3* raw=nullptr;check(sqlite3_open((state.path/"file-operations/file-operations.db").c_str(),&raw)==SQLITE_OK,"multi-row final inspection");
    sqlite3_stmt* count=nullptr;check(sqlite3_prepare_v2(raw,"SELECT count(*) FROM copy_scope_cleanup",-1,&count,nullptr)==SQLITE_OK&&sqlite3_step(count)==SQLITE_ROW,"multi-row final count");
    check(sqlite3_column_int64(count,0)==static_cast<sqlite3_int64>(kDeadlineRows),"exact retry did not finish every outcome");sqlite3_finalize(count);close_test_db(raw);
  }
  else if(mode=="old-proof-replaced") {
    auto pin=FileOperationJournal::pin_existing(state.fd);
    const auto parent=recovered->find_git_worker_parent({});
    const auto* expected_parent=parent.state==spatial::GitWorkerParentRecord::State::absent?nullptr:&parent;
    const auto queued_workspace=after->workspace_id();const auto queued_job=after->job_id();
    const auto phase=[](const char* name){std::cerr<<"copy_cleanup_phase="<<name<<std::endl;};
    auto held=[&] {
      auto queued_expected=*after;auto queued_parent=parent;
      // Pin establishment reads SQLite, so finish it before the external writer
      // acquires BEGIN EXCLUSIVE. Its wrapper dies before that lock is released.
      auto queued_pin=FileOperationJournal::pin_existing(state.fd);
      phase("observer_pin_ready");
      auto transaction=hold_exclusive_transaction(state.path/"file-operations/file-operations.db");
      phase("exclusive_lock_ready");
      rejects([&]{recovered->validated_scope_cleanup_outcome(queued_expected,&queued_parent,queued_pin,[] {
        throw std::runtime_error("planned observer interruption");
      });},"cleanup lookup observer did not interrupt while queued behind SQLite");
      phase("observer_threw_while_lookup_queued");
      return transaction;
    }();
    release_exclusive_transaction(held);phase("exclusive_lock_released");
    const auto flushed=recovered->find_copy_job(queued_workspace,queued_job,{});
    phase("queued_lookup_drained");
    check(flushed&&flushed->SerializeAsString()==after->SerializeAsString(),
          "observer-abandoned cleanup lookup did not drain with owned captures");
    const auto disposition=recovered->validated_scope_cleanup_outcome(*after,expected_parent,pin,{});
    phase("saved_disposition_validated");
    check(disposition&&disposition->kind()==FileCopyScopeCleanupOutcome::REPLACED_SCOPE &&
          after->has_drained_process_scope() &&
          after->drained_process_scope().SerializeAsString()==after->process_scope().SerializeAsString(),
          "stale old worker proof overrode the durable replacement disposition");
    recovered.reset(); // The new supervised process must acquire the managed execution lock.
    const auto independent_path=state.path/"independent-project";
    std::filesystem::create_directories(independent_path);
    auto generation=ProcessScope::prepare(delegated);
    AuthorityProcessLaunch request;request.set_executable(std::filesystem::read_symlink("/proc/self/exe").string());
    request.add_arguments("--consumer-recovery");request.add_arguments(state.path.string());
    request.add_arguments((state.path/"project").string());request.add_arguments(job.workspace_id());
    request.add_arguments(job.worktree_id());request.add_arguments(job.job_id());
    request.add_arguments(independent_path.string());
    struct ConsumerStreams {
      int input=-1,output=-1,null=-1;
      ~ConsumerStreams(){if(input>=0)close(input);if(output>=0)close(output);if(null>=0)close(null);}
    } streams;
    int output[2];check(pipe2(output,O_CLOEXEC)==0,"consumer result pipe");
    streams.input=output[0];streams.output=output[1];
    streams.null=open("/dev/null",O_RDWR|O_CLOEXEC);check(streams.null>=0,"consumer null stream");
    check((delegated/generation->identity().scope_name()).parent_path()==path.parent_path(),
          "recovery generation did not retain the same immediate delegated parent");
    generation->start(request,{streams.null,streams.output,streams.output});
    phase("recovery_generation_started");
    close(streams.null);streams.null=-1;close(streams.output);streams.output=-1;
    std::string result;char buffer[256];
    const auto read_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(15);
    for(;;){
      const auto now=std::chrono::steady_clock::now();
      check(now<read_deadline,"consumer result deadline expired");
      const auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(read_deadline-now).count();
      pollfd event{streams.input,static_cast<short>(POLLIN|POLLHUP),0};
      const int ready=poll(&event,1,static_cast<int>(std::max<std::int64_t>(1,remaining)));
      if(ready<0&&errno==EINTR)continue;
      check(ready>0&&(event.revents&(POLLIN|POLLHUP)),"consumer result deadline or channel failure");
      const auto count=read(streams.input,buffer,sizeof(buffer));if(count<0&&errno==EINTR)continue;
      check(count>=0,"consumer result read failed");if(count==0)break;
      result.append(buffer,static_cast<std::size_t>(count));check(result.size()<=4096,"consumer result exceeded bound");
    }
    close(streams.input);streams.input=-1;
    const auto child_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(15);
    while(!generation->state().direct_child_exited()) {
      check(std::chrono::steady_clock::now()<child_deadline,
            "supervised consumer child exit deadline expired");
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    phase("supervised_consumer_child_exited");
    const bool child_success=generation->exited_successfully();
    (void)generation->stop();
    phase("supervised_consumer_scope_stopped");
    if(!child_success||result!="STALE_PROOF_QUARANTINED INDEPENDENT_BROWSE_OK\n")
      throw std::runtime_error("supervised resource-backed recovery consumer failed: "+result);
  }
  else if(mode=="uncertain"){check(after->SerializeAsString()==original&&outcome&&outcome->kind()==FileCopyScopeCleanupOutcome::IMMUTABLE_UNCERTAINTY,"terminal uncertainty mutated");}
  else if(mode=="missing-parent"||mode=="replaced-parent"||mode=="old-proof-parent-replaced") {
    check(outcome&&outcome->kind()==FileCopyScopeCleanupOutcome::INVALID_PARENT&&after->state()==FILE_COPY_JOB_JOURNAL_UNCERTAIN,"parent conflict was not durable");
    check(after->has_drained_process_scope()==(mode=="old-proof-parent-replaced"),"parent conflict manufactured proof");
  }
  else if(mode=="missing"||mode=="missing-at-open"||mode=="replaced"||mode=="old-proof-replaced"){
    check(outcome&&after->state()==FILE_COPY_JOB_JOURNAL_UNCERTAIN,"missing conflict disposition");
    check(after->has_drained_process_scope()==(mode=="old-proof-replaced"),"conflict manufactured exact proof");
    check(outcome->kind()==((mode=="missing"||mode=="missing-at-open")?FileCopyScopeCleanupOutcome::MISSING_SCOPE:FileCopyScopeCleanupOutcome::REPLACED_SCOPE),"wrong conflict kind");
  }else check(after->drained_process_scope().SerializeAsString()==ids.worker.SerializeAsString(),"genuine exact proof missing");
  if(mode=="missing") {
    auto pin=FileOperationJournal::pin_existing(state.fd);
    auto parent=recovered->find_git_worker_parent({});
    const auto* expected_parent=parent.state==spatial::GitWorkerParentRecord::State::absent?nullptr:&parent;
    const auto validated=recovered->validated_scope_cleanup_outcome(*after,expected_parent,pin,{});
    check(validated && validated->kind()==FileCopyScopeCleanupOutcome::MISSING_SCOPE,
        "exact saved cleanup disposition did not validate against current journal binding");
    State foreign_state;
    auto foreign_pin=FileOperationJournal::pin_existing(foreign_state.fd);
    rejects([&]{recovered->validated_scope_cleanup_outcome(*after,expected_parent,foreign_pin,{});},
        "cleanup disposition accepted a different current journal binding");
    auto wrong_job=*after;wrong_job.set_worktree_id("another-worktree");
    rejects([&]{recovered->validated_scope_cleanup_outcome(wrong_job,expected_parent,pin,{});},
        "cleanup disposition accepted a different worktree job identity");
    auto wrong_root=*after;wrong_root.set_broker_root_inode(wrong_root.broker_root_inode()+1);
    rejects([&]{recovered->validated_scope_cleanup_outcome(wrong_root,expected_parent,pin,{});},
        "cleanup disposition accepted a different saved broker root");
    check(recovered->prune_terminal_copy_jobs(after->workspace_id(),static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()),1,{})==0 &&
          recovered->scope_cleanup_outcome(after->workspace_id(),after->job_id(),{}).has_value(),
        "uncertain cleanup evidence was pruned");
    auto writer=FileOperationJournal::open_existing(pin);
    writer->record_scope_cleanup_outcome(job,*outcome,true,{});
    auto changed=*outcome;changed.mutable_journal()->set_execution_inode(changed.journal().execution_inode()+1);
    rejects([&]{writer->record_scope_cleanup_outcome(job,changed,true,{});},"foreign execution outcome accepted");
    changed=*outcome;changed.set_job_identity_sha256(std::string(32,'x'));
    rejects([&]{writer->record_scope_cleanup_outcome(job,changed,true,{});},"wrong identity digest accepted");
    changed=*outcome;changed.mutable_broker_root()->set_inode(changed.broker_root().inode()+1);
    rejects([&]{writer->record_scope_cleanup_outcome(job,changed,true,{});},"foreign broker root accepted");
    changed=*outcome;changed.set_kind(FileCopyScopeCleanupOutcome::REPLACED_SCOPE);
    rejects([&]{writer->record_scope_cleanup_outcome(job,changed,true,{});},"changed idempotent outcome accepted");
    auto changed_job=job;changed_job.set_updated_at_unix_ms(job.updated_at_unix_ms()+10);
    rejects([&]{writer->record_scope_cleanup_outcome(changed_job,*outcome,true,{});},"stale expected-old CAS accepted");
  }
  struct stat final_info{};check(stat(marker.c_str(),&final_info)==0&&before.st_dev==final_info.st_dev&&before.st_ino==final_info.st_ino&&read_file(marker)=="preserve marker","participant changed stage evidence");
  std::cout<<mode<<"=PASS\n";
}
}
int main(int argc,char** argv){try{
  if(argc==5 && std::strcmp(argv[1],"--hold-journal-lock")==0)
    return hold_exclusive_transaction_child(argv[2],std::stoi(argv[3]),std::stoi(argv[4]));
  if(argc==8 && std::strcmp(argv[1],"--consumer-recovery")==0)
    return consumer_recovery_child(argv[2],argv[3],argv[4],argv[5],argv[6],argv[7]);
  if(argc==2 && std::strcmp(argv[1],"--orphan-worker")==0){
    // Adversarial descendants may clear PDEATHSIG. The real cgroup must still drain them.
    check(prctl(PR_SET_PDEATHSIG,0)==0,"orphan worker clear parent-death signal");
    const char ready='R';check(write(STDOUT_FILENO,&ready,1)==1,"orphan worker readiness write");
    for(;;)pause();
  }
  if(argc==3 && std::strcmp(argv[1],"--canonical-writer-probe")==0) {
    sqlite3* db=nullptr;check(sqlite3_open_v2(argv[2],&db,SQLITE_OPEN_READWRITE|SQLITE_OPEN_NOFOLLOW,nullptr)==SQLITE_OK,"canonical witness open");
    const int result=sqlite3_exec(db,"BEGIN IMMEDIATE",nullptr,nullptr,nullptr);close_test_db(db);
    return result==SQLITE_BUSY?0:95;
  }
  const bool only_mode=argc==4&&std::strcmp(argv[2],"--only-participant-mode")==0;
  const bool mode_set=argc==4&&std::strcmp(argv[2],"--participant-mode-set")==0;
  check(argc==2||only_mode||mode_set,"expected delegated cgroup root");
  const std::filesystem::path root=argv[1];
  if(only_mode){
    if(std::strcmp(argv[3],"retirement-ack-retry")==0){
      retirement_ack_failure_retry(root);std::cout<<"BROKER_COPY_CLEANUP_SELECTED_PASS\n";return 0;
    }
    check(std::strcmp(argv[3],"deadline-multirow")==0,"unsupported isolated participant mode");
    participant_case(root,argv[3]);std::cout<<"BROKER_COPY_CLEANUP_SELECTED_PASS\n";return 0;
  }
  if(mode_set)check(std::strcmp(argv[3],"controls-plus-15")==0,"unsupported participant mode set");
  if(argc==2||mode_set)bootstrap_controls();
  journal_controls();constructor_allocation_controls();substitution_during_open_control();pin_lock_controls();alias_transaction_controls();hot_journal_controls();alias_header_orphan_controls();btrfs_layout_controls();callback_controls(root);
  for(const auto* mode:{"exact","missing","missing-at-open","replaced","old-proof-replaced","uncertain","reserved","reserved-empty","deadline-lock","deadline-multirow","foreign-view","commit-failure","prepared","missing-parent","replaced-parent","old-proof-parent-replaced"}){
    if(mode_set&&std::strcmp(mode,"deadline-multirow")==0)continue;
    participant_case(root,mode);
  }
  if(mode_set)std::cout<<"BROKER_COPY_CLEANUP_CONTROLS_PLUS_15_PASS\n";
  else std::cout<<"BROKER_COPY_CLEANUP_PASS\n";
  return 0;}catch(const std::exception& error){std::cerr<<"cleanup_phase mode="<<active_cleanup_mode<<" phase=probe_unwind detail="<<error.what()<<std::endl;std::cerr<<error.what()<<'\n';return 1;}}
