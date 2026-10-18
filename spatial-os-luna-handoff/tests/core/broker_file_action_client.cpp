#include "platform/broker.hpp"
#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

using namespace spatial;
using namespace spatial::workspace::v1;

namespace {
void exact_read(int fd,char* out,std::size_t size) {
  while(size) { auto n=::read(fd,out,size); if(n<=0) throw std::runtime_error("peer read failed"); out+=n; size-=static_cast<std::size_t>(n); }
}
void exact_write(int fd,const char* data,std::size_t size) {
  while(size) { auto n=::write(fd,data,size); if(n<=0) throw std::runtime_error("peer write failed"); data+=n; size-=static_cast<std::size_t>(n); }
}
template<class Message> Message receive(int fd) {
  std::array<unsigned char,4> h{}; exact_read(fd,reinterpret_cast<char*>(h.data()),4);
  const std::uint32_t n=(std::uint32_t(h[0])<<24)|(std::uint32_t(h[1])<<16)|(std::uint32_t(h[2])<<8)|h[3];
  std::string bytes(n,'\0'); exact_read(fd,bytes.data(),bytes.size()); Message value;
  if(!value.ParseFromString(bytes)) throw std::runtime_error("peer parse failed");
  return value;
}
template<class Message> void send_message(int fd,const Message& value) {
  const auto bytes=value.SerializeAsString(); const std::uint32_t n=bytes.size();
  const std::array<char,4> h{char(n>>24),char(n>>16),char(n>>8),char(n)};
  exact_write(fd,h.data(),h.size()); exact_write(fd,bytes.data(),bytes.size());
}
enum class Fault { good, wrong_shape, unknown_error, wrong_recovery, empty_recovery, missing_ack, truncated_ack };
void run(Fault fault,bool should_pass) {
  int pair[2]; if(socketpair(AF_UNIX,SOCK_STREAM,0,pair)) throw std::runtime_error("socketpair failed");
  std::exception_ptr peer_error;
  std::thread peer([&] {
    try {
      struct PeerFd { int value; ~PeerFd(){ if(value>=0) ::close(value); } } peer_fd{pair[1]};
      auto first=receive<broker_wire::Request>(pair[1]);
      broker_wire::Reply ready; ready.set_protocol_version(1); ready.set_request_id(first.request_id());
      ready.mutable_management()->set_protocol_version(1); ready.mutable_management()->set_request_id(first.request_id());
      ready.mutable_management()->mutable_identities()->set_workspaced_pid(getpid()); send_message(pair[1],ready);
      auto request=receive<broker_wire::Request>(pair[1]);
      if(fault==Fault::missing_ack) return;
      if(fault==Fault::truncated_ack) {
        const std::array<char,4> header{0,0,0,10};
        const std::array<char,2> prefix{char(8),char(1)};
        exact_write(pair[1],header.data(),header.size());
        exact_write(pair[1],prefix.data(),prefix.size());
        return;
      }
      broker_wire::Reply reply; reply.set_protocol_version(1); reply.set_request_id(request.request_id());
      auto* result=reply.mutable_file_action(); result->set_protocol_version(1);
      result->set_operation_id(request.file_action().operation_id());
      if(fault==Fault::good) result->mutable_directory();
      else if(fault==Fault::wrong_shape) result->mutable_content();
      else if(fault==Fault::unknown_error) result->mutable_error()->set_code(static_cast<FileActionError::Code>(77));
      else { auto* error=result->mutable_error(); error->set_code(FileActionError::UNCERTAIN); if(fault==Fault::wrong_recovery) error->set_recovery_operation_id(std::string(16,'x')); }
      send_message(pair[1],reply);
    } catch(...) { peer_error=std::current_exception(); }
  });
  bool passed=false;
  try {
    BrokerClient client(pair[0],getpid());
    FileActionRequest request; request.set_protocol_version(1); request.set_operation_id(std::string(16,'o'));
    request.set_workspace_id("workspace-one"); request.mutable_principal()->set_issuer_id(std::string(32,'i'));
    request.mutable_principal()->set_actor_id(std::string(32,'a')); request.mutable_browse()->set_page_size(10);
    auto reply=client.file_action(request);
    if(fault==Fault::missing_ack || fault==Fault::truncated_ack)
      passed=reply.has_error() && reply.error().code()==FileActionError::UNCERTAIN &&
        reply.operation_id()==request.operation_id() && reply.error().recovery_operation_id()==request.operation_id();
    else if(fault==Fault::good) passed=reply.has_directory();
    else passed=reply.has_error() && reply.error().code()==FileActionError::UNCERTAIN &&
      reply.operation_id()==request.operation_id() && reply.error().recovery_operation_id()==request.operation_id();
  } catch(const std::exception&) { passed=false; }
  peer.join();
  if(peer_error) std::rethrow_exception(peer_error);
  if(passed!=should_pass) throw std::runtime_error("BrokerClient file reply validation regression");
}
}
int main() {
  try {
    // The peer closes as soon as it sends the reply; repeat this race-sensitive case.
    for(int i=0;i<16;++i) run(Fault::good,true);
    run(Fault::wrong_shape,true); run(Fault::unknown_error,true); run(Fault::wrong_recovery,true); run(Fault::empty_recovery,true); run(Fault::missing_ack,true); run(Fault::truncated_ack,true);
    return 0;
  }
  catch(...) { return 1; }
}
