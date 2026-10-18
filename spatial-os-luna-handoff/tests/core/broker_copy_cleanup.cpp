#include "workspace/broker_copy_cleanup.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <sys/stat.h>
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
#include <cstdlib>
#include <new>
#include <exception>
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
#include <stdexcept>
#include <thread>
#include <type_traits>
#include <vector>
#include <openssl/evp.h>

using spatial::FileOperationJournal;
using spatial::BrokerCopyCleanupParticipant;
using spatial::supervisor::ProcessScope;
using namespace spatial::workspace::v1;
using namespace spatial::supervisor::v1;
namespace {
// Interpose the real callee syscall only in this executable. No production hook.
std::atomic<bool> disappear_at_open{false},disappeared_at_open{false};
std::uint64_t disappearance_parent_device=0,disappearance_parent_inode=0;
thread_local long allocation_budget=-1;
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

void callback_controls(const std::filesystem::path& delegated){
  static_assert(!std::is_constructible_v<spatial::supervisor::OwnedDrainedTree,int,const ProcessScopeIdentity&>);
  auto root=ProcessScope::prepare(delegated);const auto path=delegated/root->identity().scope_name();bool first=true,called=false;
  std::filesystem::create_directory(path/"preserved-child");
  root->set_pre_prune_cleanup([&](const auto& view){check(view.identity().SerializeAsString()==root->identity().SerializeAsString(),"view root mismatch");check(std::filesystem::exists(path/"preserved-child"),"hook ran after prune");called=true;if(first){first=false;throw std::runtime_error("planned callback failure");}});
  rejects([&]{root->stop();},"callback failure returned complete receipt");check(called&&std::filesystem::exists(path/"preserved-child"),"failed callback pruned child");root->stop();check(!std::filesystem::exists(path),"retry did not finish cleanup");
  // Real direct-child termination/reap precedes the hook, and late installation rejects.
  auto live=ProcessScope::prepare(delegated);bool saw_dead=false;live->set_pre_prune_cleanup([&](const auto&){saw_dead=live->diagnostic_child_reaped();check(!live->diagnostic_populated(),"callback saw live population");});
  AuthorityProcessLaunch request;request.set_executable("/usr/bin/sleep");request.add_arguments("60");int null=open("/dev/null",O_RDWR|O_CLOEXEC);live->start(request,{null,null,null});close(null);
  rejects([&]{live->set_pre_prune_cleanup([](const auto&){});},"late callback installed");live->stop();check(saw_dead,"callback preceded direct-child reap");
  bool destructor_called=false;{auto scope=ProcessScope::prepare(delegated);scope->set_pre_prune_cleanup([&](const auto&){destructor_called=true;});}check(destructor_called,"destructor skipped callback");
  auto retry=ProcessScope::prepare(delegated);const auto retry_path=delegated/retry->identity().scope_name();
  std::vector<std::filesystem::path> deep;auto nested=retry_path;
  for(int depth=0;depth<10;++depth){nested/="nested";std::filesystem::create_directory(nested);deep.push_back(nested);}
  unsigned observations=0;
  retry->set_pre_prune_cleanup([&](const auto&){++observations;if(observations==2)check(std::filesystem::exists(retry_path/"changed-between-attempts"),"retry skipped changed descendant");});
  rejects([&]{retry->stop();},"excessive nesting did not preserve prune failure");
  for(auto at=deep.rbegin();at!=deep.rend();++at)std::filesystem::remove(*at);
  std::filesystem::create_directory(retry_path/"changed-between-attempts");
  retry->stop();check(observations==2,"successful callback was cached across failed prune");
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
void participant_case(const std::filesystem::path& delegated,const std::string& mode){
  State state;auto root=ProcessScope::prepare(delegated);const auto path=delegated/root->identity().scope_name();
  root->enable_children();const bool provisional=mode=="reserved";const auto ids=create_descendants(path,!provisional);
  auto job=record(state,root->identity(),ids,provisional);seed(state,root->identity(),path,ids,job);
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
  root->stop();check(!std::filesystem::exists(path),"root not removed");
  if(mode=="missing-at-open") {
    disappear_at_open.store(false,std::memory_order_release);
    check(disappeared_at_open.load(std::memory_order_acquire),"actual callee openat race was not reached");
  }
  FileOperationJournal recovered(state.path);auto after=recovered.find_copy_job(job.workspace_id(),job.job_id(),{});check(bool(after),"job lost");
  const auto outcome=recovered.scope_cleanup_outcome(job.workspace_id(),job.job_id(),{});
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
  if(mode=="reserved"){check(outcome&&outcome->kind()==FileCopyScopeCleanupOutcome::RESERVED_SCOPE_RECONCILED&&!after->has_process_scope(),"reserved outcome invented proof");}
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
  check(argc==2,"expected delegated cgroup root");const std::filesystem::path root=argv[1];journal_controls();constructor_allocation_controls();substitution_during_open_control();pin_lock_controls();alias_transaction_controls();hot_journal_controls();alias_header_orphan_controls();btrfs_layout_controls();callback_controls(root);for(const auto* mode:{"exact","missing","missing-at-open","replaced","old-proof-replaced","uncertain","reserved","foreign-view","commit-failure","prepared","missing-parent","replaced-parent","old-proof-parent-replaced"})participant_case(root,mode);std::cout<<"BROKER_COPY_CLEANUP_PASS\n";return 0;}catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
