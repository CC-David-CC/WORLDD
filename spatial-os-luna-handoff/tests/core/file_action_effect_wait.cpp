#include "workspace/file_engine.hpp"
#include <cerrno>
#include <chrono>
#include <fcntl.h>
#include <future>
#include <stdexcept>
#include <thread>
#include <unistd.h>
#include <filesystem>
#include <fstream>
#include <sys/stat.h>
#include <cstring>
#include <cerrno>
#include <string>
#include <vector>


namespace {
namespace pb=spatial::workspace::v1;
namespace az=spatial::authority::v1;
struct Allow final:spatial::FileAuthority {
  pb::ScopedAuthorityCheckReply check_fresh(const pb::ScopedAuthorityCheckRequest&) override {
    pb::ScopedAuthorityCheckReply reply; reply.set_allowed(true); return reply;
  }
};
struct Executor final:spatial::FileEffectExecutor {
  std::function<void()> observer=[]{}; bool fail_read_open=false; int opened=-1;
  int browse_error=0; int edit_steps=0; int fail_step=0;
  spatial::FileIoResult run_step(const pb::FileActionRequest& request,az::Right,bool effectful,Step step) override {
    if(request.has_browse() && browse_error) { spatial::FileIoResult out; out.value=-1; out.error=browse_error; return out; }
    if(request.has_edit()) {
      ++edit_steps;
      if(edit_steps==5) { std::ofstream mutate(std::string(test_project)+"/read.txt",std::ios::binary|std::ios::app); mutate<<"racer"; }
      if(edit_steps==fail_step) { spatial::FileIoResult out; out.value=-1; out.error=EIO; return out; }
    }
    auto future=std::async(std::launch::async,[&] { auto value=step(); if(request.has_read() && value.owns_descriptor) opened=value.value; return value; });
    return spatial::wait_file_io_task(future,observer,effectful);
  }
  std::string test_project;
};
pb::FileActionRequest req(std::string op) {
  pb::FileActionRequest r; r.set_protocol_version(1); r.set_operation_id(std::move(op)); r.set_workspace_id("fixture-workspace");
  r.mutable_principal()->set_issuer_id(std::string(32,'i')); r.mutable_principal()->set_actor_id(std::string(32,'a')); r.add_authority_components("fixture-worktree"); return r;
}
int actual_paths() {
  char pattern[]="/tmp/spatial-file-effect-XXXXXX"; char* dir=mkdtemp(pattern); if(!dir)return 10;
  const std::string root=dir; const std::string file=root+"/read.txt";
  {std::ofstream out(file);out<<"payload";}
  const int fd=open(root.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC); if(fd<0)return 11;
  Allow authority; Executor executor; executor.test_project=root;
  spatial::FileEngine engine(fd,"fixture-workspace",{"fixture-worktree"},authority,
    []{return spatial::FileEngine::MonotonicClock::now();},&executor); close(fd);
  auto browse=req(std::string(16,'b')); browse.mutable_browse()->set_page_size(32);
  auto listed=engine.handle(browse); if(!listed.has_directory()) return 12;
  std::string handle; std::uint64_t generation=0;
  for(const auto& entry:listed.directory().entries()) if(entry.name()=="read.txt") {handle=entry.handle();generation=entry.generation();}
  if(handle.empty() || !generation)return 13;
  executor.fail_read_open=true;
  executor.observer=[] { throw std::runtime_error("observer failure after descriptor open"); };
  auto read=req(std::string(16,'r')); read.mutable_read()->set_file_handle(handle); read.mutable_read()->set_maximum_bytes(64);
  auto failed=engine.handle(read);
  if(!failed.has_error() || failed.error().code()!=pb::FileActionError::UNAVAILABLE || executor.opened<0)return 14;
  errno=0; if(fcntl(executor.opened,F_GETFD)>=0 || errno!=EBADF)return 15;
  executor.observer=[]{}; executor.edit_steps=0; executor.fail_step=8;
  auto edit=req(std::string(16,'e')); edit.mutable_edit()->set_file_handle(handle);
  edit.mutable_edit()->set_expected_generation(generation); edit.mutable_edit()->set_content("new-data");
  auto rollback=engine.handle(edit);
  if(executor.edit_steps!=8 || !rollback.has_error() || rollback.error().code()!=pb::FileActionError::UNCERTAIN)return 18;
  Executor denied; denied.browse_error=EACCES;
  const auto now=[] {return spatial::FileEngine::MonotonicClock::now();};
  const int denied_fd=open(root.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC); if(denied_fd<0)return 16;
  spatial::FileEngine denied_engine(denied_fd,"fixture-workspace",{"fixture-worktree"},authority,now,&denied); close(denied_fd);
  auto denied_reply=denied_engine.handle(browse); if(!denied_reply.has_error() || denied_reply.error().code()!=pb::FileActionError::UNAUTHORIZED)return 16;
  denied.browse_error=EIO;
  const int io_fd=open(root.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC); if(io_fd<0)return 17;
  spatial::FileEngine io_engine(io_fd,"fixture-workspace",{"fixture-worktree"},authority,now,&denied); close(io_fd);
  auto io_reply=io_engine.handle(browse); if(!io_reply.has_error() || io_reply.error().code()!=pb::FileActionError::UNAVAILABLE)return 17;
  std::filesystem::remove_all(root); return 0;
}
}

int main() {
  try {
    const int fd=open("/dev/null",O_RDONLY|O_CLOEXEC);
    if(fd<0) return 1;
    auto descriptor=std::async(std::launch::async,[fd] {
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
      spatial::FileIoResult result; result.value=fd; result.owns_descriptor=true; return result;
    });
    auto throwing_observer=[] { throw std::runtime_error("injected observer failure"); };
    auto closed=spatial::wait_file_io_task(descriptor,throwing_observer,false);
    if(closed.value>=0 || closed.error!=EIO || fcntl(fd,F_GETFD)>=0 || errno!=EBADF) return 2;
    auto effect=std::async(std::launch::async,[] {
      spatial::FileIoResult result; result.value=1; result.effect_may_have_occurred=true; return result;
    });
    auto uncertain=spatial::wait_file_io_task(effect,throwing_observer,true);
    if(uncertain.value>=0 || uncertain.error!=EIO || !uncertain.effect_may_have_occurred) return 3;
    const int paths=actual_paths(); if(paths)return paths;
    return 0;
  } catch(...) { return 4; }
}
