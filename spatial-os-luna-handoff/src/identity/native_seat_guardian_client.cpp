#include "identity/native_seat_guardian_client.hpp"
#include "identity/native_seat_binding.hpp"
#include <openssl/rand.h>
#include <poll.h>
#include <cerrno>
#include <climits>
#include <stdexcept>

namespace spatial::identity {
namespace {
void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
void flush(PresentationStream& stream){
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(stream.pending_output()){
        stream.flush();if(!stream.pending_output())return;
        pollfd event{stream.descriptor(),POLLOUT,0};
        int result;do{result=poll(&event,1,50);}while(result<0&&errno==EINTR);
        require(result>=0&&std::chrono::steady_clock::now()<deadline,"Guardian request write timed out");
    }
}
}
NativeSeatGuardianClient::NativeSeatGuardianClient(int fd,pid_t creator,pid_t sender)
    :fd_(fd),channel_(fd_.release(),creator,sender){
    const auto ready=receive();
    require(ready.request_id()==0&&ready.has_ready()&&ready.ready(),"Guardian did not announce readiness");
}
guardian::v1::GuardianReply NativeSeatGuardianClient::receive(){
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    for(;;){
        if(auto frame=channel_.receive()){
            require(frame->descriptors.empty(),"Unexpected guardian response descriptors");
            guardian::v1::GuardianReply reply;
            require(reply.ParseFromString(frame->bytes),"Malformed guardian response");
            validate_message(reply);
            require(reply.protocol_version()==1,"Unsupported guardian response version");
            return reply;
        }
        const auto remaining=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-std::chrono::steady_clock::now()).count();
        require(remaining>0,"Guardian response timed out");
        pollfd event{channel_.descriptor(),POLLIN,0};
        int result;do{result=poll(&event,1,static_cast<int>(std::min<std::int64_t>(remaining,50)));}while(result<0&&errno==EINTR);
        require(result>=0,"Guardian response poll failed");
    }
}
guardian::v1::SeatRegistrationReceipt NativeSeatGuardianClient::register_seat(
        const guardian::v1::NativeSeatBinding& binding,std::uint64_t issuer_revision){
    validate_native_seat_binding(binding);
    require(issuer_revision<=INT64_MAX&&sequence_<UINT64_MAX,
        "Seat registration differs from checked guardian context");
    guardian::v1::GuardianRequest request;request.set_protocol_version(1);request.set_request_id(++sequence_);
    auto* registration=request.mutable_register_seat();
    std::string operation_id(32,'\0');
    require(RAND_bytes(reinterpret_cast<unsigned char*>(operation_id.data()),operation_id.size())==1,
        "Cannot generate seat registration identity");
    registration->set_operation_id(operation_id);*registration->mutable_binding()=binding;
    registration->set_issuer_security_revision(issuer_revision);
    channel_.send(request.SerializeAsString());flush(channel_);
    const auto reply=receive();
    require(reply.request_id()==sequence_&&reply.has_registration_id()&&reply.has_registration_receipt(),
        "Guardian rejected seat registration");
    const auto& receipt=reply.registration_receipt();
    require(reply.registration_id()==receipt.registration_id()&&receipt.operation_id()==operation_id&&
        receipt.binding().SerializeAsString()==binding.SerializeAsString()&&
        receipt.guardian_incarnation().size()==32&&receipt.registration_id().size()==32&&
        receipt.issuer_security_revision()==issuer_revision,"Guardian registration receipt context mismatch");
    return receipt;
}
}
