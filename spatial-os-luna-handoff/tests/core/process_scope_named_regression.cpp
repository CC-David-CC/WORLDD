#include <fcntl.h>
#include "supervisor/process_scope.hpp"
#include <array>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
using spatial::supervisor::ProcessScope;
using namespace spatial::supervisor::v1;

void write_all(int fd, const void* data, std::size_t size) {
  const auto* bytes=static_cast<const char*>(data);
  while(size) {
    const auto count=write(fd,bytes,size);
    if(count<0 && errno==EINTR) continue;
    if(count<=0) throw std::runtime_error("identity pipe write failed");
    bytes+=count; size-=static_cast<std::size_t>(count);
  }
}
void read_all_bounded(int fd, void* data, std::size_t size,
                      std::chrono::steady_clock::time_point deadline) {
  auto* bytes=static_cast<char*>(data);
  while(size) {
    const auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(
        deadline-std::chrono::steady_clock::now()).count();
    if(remaining<=0) throw std::runtime_error("identity pipe read deadline");
    pollfd ready{fd,POLLIN,0};
    int rc;
    do { rc=poll(&ready,1,static_cast<int>(remaining)); } while(rc<0 && errno==EINTR);
    if(rc<=0 || !(ready.revents&(POLLIN|POLLHUP))) throw std::runtime_error("identity pipe read deadline");
    const auto count=read(fd,bytes,size);
    if(count<0 && errno==EINTR) continue;
    if(count<=0) throw std::runtime_error("identity pipe closed early");
    bytes+=count; size-=static_cast<std::size_t>(count);
  }
}
ProcessScopeIdentity owner_start_and_exit(const std::filesystem::path& root) {
  int pipefd[2];
  if(pipe2(pipefd,O_CLOEXEC)<0) throw std::runtime_error("identity pipe creation failed");
  const pid_t owner=fork();
  if(owner<0) { close(pipefd[0]);close(pipefd[1]);throw std::runtime_error("owner fork failed"); }
  if(owner==0) {
    close(pipefd[0]);
    try {
      auto process=ProcessScope::prepare_named(root,"copy-recovered-regression");
      AuthorityProcessLaunch launch; launch.set_executable("/usr/bin/python3");
      launch.add_arguments("-c"); launch.add_arguments("import time;time.sleep(30)");
      const int nullfd=open("/dev/null",O_RDWR|O_CLOEXEC);
      if(nullfd<0) _exit(91);
      process->start(launch,{nullfd,nullfd,nullfd}); close(nullfd);
      const std::string bytes=process->identity().SerializeAsString();
      if(bytes.empty() || bytes.size()>4096) _exit(92);
      const std::uint32_t size=static_cast<std::uint32_t>(bytes.size());
      const unsigned char header[]{static_cast<unsigned char>(size>>24),static_cast<unsigned char>(size>>16),
                                   static_cast<unsigned char>(size>>8),static_cast<unsigned char>(size)};
      write_all(pipefd[1],header,sizeof(header)); write_all(pipefd[1],bytes.data(),bytes.size());
      close(pipefd[1]);
      _exit(0); // Deliberately skip ProcessScope destruction to model owner death.
    } catch(...) { _exit(93); }
  }
  close(pipefd[1]);
  unsigned char header[4]{};
  const auto pipe_deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  try { read_all_bounded(pipefd[0],header,sizeof(header),pipe_deadline); }
  catch(...) { close(pipefd[0]);kill(owner,SIGKILL);waitpid(owner,nullptr,0);throw; }
  const std::uint32_t size=(std::uint32_t(header[0])<<24)|(std::uint32_t(header[1])<<16)|
                           (std::uint32_t(header[2])<<8)|std::uint32_t(header[3]);
  if(size==0 || size>4096) { close(pipefd[0]);kill(owner,SIGKILL);waitpid(owner,nullptr,0);throw std::runtime_error("invalid framed identity length"); }
  std::string bytes(size,'\0');
  try { read_all_bounded(pipefd[0],bytes.data(),bytes.size(),pipe_deadline); }
  catch(...) { close(pipefd[0]);kill(owner,SIGKILL);waitpid(owner,nullptr,0);throw; }
  close(pipefd[0]);
  int status=0;const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
  for(;;) {
    const pid_t waited=waitpid(owner,&status,WNOHANG);
    if(waited==owner) break;
    if(waited<0 && errno==EINTR) continue;
    if(waited<0 || std::chrono::steady_clock::now()>=deadline) {
      kill(owner,SIGKILL);waitpid(owner,nullptr,0);throw std::runtime_error("owner exit deadline");
    }
    poll(nullptr,0,5);
  }
  if(!WIFEXITED(status) || WEXITSTATUS(status)!=0) throw std::runtime_error("scope owner failed before exit");
  ProcessScopeIdentity identity;
  if(!identity.ParseFromString(bytes) || identity.SerializeAsString()!=bytes)
    throw std::runtime_error("owner pipe identity protobuf was not canonical");
  return identity;
}
}

int main(int argc,char** argv) {
  try {
    if(argc!=2) throw std::invalid_argument("expected delegated root");
    const std::filesystem::path root=argv[1];
    auto named=ProcessScope::prepare_named(root,"copy-named-regression");
    AuthorityProcessLaunch launch; launch.set_executable("/usr/bin/python3");
    launch.add_arguments("-c"); launch.add_arguments("import time;time.sleep(30)");
    const int nullfd=open("/dev/null",O_RDWR|O_CLOEXEC);
    if(nullfd<0) throw std::runtime_error("open /dev/null failed");
    named->start(launch,{nullfd,nullfd,nullfd}); close(nullfd);
    const auto original=named->identity();
    const auto original_path=root/original.scope_name();
    struct stat original_stat{};if(lstat(original_path.c_str(),&original_stat)<0) throw std::runtime_error("cannot pin named scope");
    const pid_t original_pid=named->direct_child_pid();
    bool collision_rejected=false;
    try { (void)ProcessScope::prepare_named(root,"copy-named-regression"); }
    catch(const std::exception& error) {
      collision_rejected=std::string(error.what()).find("Cannot create authority scope")!=std::string::npos;
    }
    struct stat after_collision{};
    if(!collision_rejected || !named->diagnostic_populated() || kill(original_pid,0)!=0 ||
       lstat(original_path.c_str(),&after_collision)<0 || after_collision.st_dev!=original_stat.st_dev ||
       after_collision.st_ino!=original_stat.st_ino)
      throw std::runtime_error("duplicate named scope changed or killed the existing populated scope");
    bool acknowledged=false;
    const auto stopped=named->stop([&](const ProcessScopeIdentity& drained) {
      if(drained.SerializeAsString()!=original.SerializeAsString()) throw std::runtime_error("wrong drain identity");
      acknowledged=true;
    });
    if(!acknowledged || stopped.identity().SerializeAsString()!=original.SerializeAsString() ||
       std::filesystem::exists(original_path))
      throw std::runtime_error("named scope callback/removal contract failed");

    const auto recovered_identity=owner_start_and_exit(root);
    struct stat parent_stat{};
    if(stat(root.c_str(),&parent_stat)<0) throw std::runtime_error("cannot stat recovery parent");
    std::ifstream boot_file("/proc/sys/kernel/random/boot_id");std::string current_boot;std::getline(boot_file,current_boot);
    if(recovered_identity.launch_id().size()!=32 || recovered_identity.boot_id()!=current_boot)
      throw std::runtime_error("recovery fixture did not use the current 32-byte identity");
    bool recovery_ack=false;
    const bool recovered_stopped=ProcessScope::stop_recovered(root,
        static_cast<std::uint64_t>(parent_stat.st_dev),static_cast<std::uint64_t>(parent_stat.st_ino),
        recovered_identity,[&](const ProcessScopeIdentity& drained) {
          if(drained.SerializeAsString()!=recovered_identity.SerializeAsString())
            throw std::runtime_error("recovery acknowledged another identity");
          recovery_ack=true;
        });
    if(!recovered_stopped || !recovery_ack || std::filesystem::exists(root/"copy-recovered-regression"))
      throw std::runtime_error("32-byte recovered scope was not drained and removed");

    const std::string owned_name="authority-broker-0123456789abcdef0123456789abcdef";
    auto owned=ProcessScope::prepare_owned(root,owned_name);
    const auto owned_inode=owned->identity().inode();owned->stop();
    if(!std::filesystem::exists(root/owned_name)) throw std::runtime_error("owned scope was removed");
    auto reused=ProcessScope::prepare_owned(root,owned_name);
    if(reused->identity().inode()!=owned_inode) throw std::runtime_error("owned scope was not reused");
    reused->stop();
    std::cout<<"named_collision_preserved=1 drain_callback=1 named_removed=1 recovery32=1 owner_exit=1 owned_reused=1\n";
    return 0;
  } catch(const std::exception& error) { std::cerr<<error.what()<<'\n';return 1; }
}
