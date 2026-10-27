// Test-only process-boundary transport. Production supervision and issuer
// authentication are not provided by this probe.
#include "identity/native_seat_guardian.hpp"
#include "platform/presentation_stream.hpp"
#include "x11/input.hpp"
#include "core/protocol.hpp"
#include <sqlite3.h>
#include <chrono>
#include <poll.h>
#include <iostream>
#include <stdexcept>
namespace g=spatial::identity::guardian::v1;
void need(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
std::string receive(spatial::PresentationStream& stream){
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(std::chrono::steady_clock::now()<deadline){
        if(auto frame=stream.receive()) {need(frame->descriptors.empty(),"Unexpected guardian descriptors");return frame->bytes;}
        pollfd fd{stream.descriptor(),POLLIN,0};if(poll(&fd,1,20)<0)throw std::runtime_error("Guardian channel poll failed");
    }
    throw std::runtime_error("Guardian channel deadline exceeded");
}
void send(spatial::PresentationStream& stream,std::uint64_t id){
    spatial::wire::RenderResult reply;reply.set_protocol_version(1);reply.set_request_id(id);reply.mutable_ready();
    stream.send(reply.SerializeAsString());
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while(stream.pending_output()){
        stream.flush();if(!stream.pending_output())break;
        need(std::chrono::steady_clock::now()<deadline,"Guardian reply deadline exceeded");
        pollfd fd{stream.descriptor(),POLLOUT,0};if(poll(&fd,1,20)<0)throw std::runtime_error("Guardian output poll failed");
    }
}
int main(int argc,char** argv){
    try {
        need(argc==6,"Expected sealed configuration, channel, ledger, creator, and sender");
        spatial::NativeInput native(std::stoi(argv[1]));
        spatial::PresentationStream channel(std::stoi(argv[2]),std::stoi(argv[4]),std::stoi(argv[5]));
        sqlite3* raw=nullptr;need(sqlite3_open_v2(argv[3],&raw,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_NOFOLLOW,nullptr)==SQLITE_OK,"Cannot open guardian test ledger");
        struct Db {sqlite3* value;~Db(){sqlite3_close(value);}} database{raw};
        spatial::identity::NativeSeatLedger ledger(raw);spatial::identity::NativeSeatGuardian guardian(ledger,native);
        send(channel,0);
        g::SeatRegistration registration;need(registration.ParseFromString(receive(channel)),"Invalid seat registration");
        guardian.register_seat(registration,std::string(32,'r'));send(channel,1);
        g::NativeFenceIntent intent;need(intent.ParseFromString(receive(channel)),"Invalid native fence intent");
        while(guardian.fence_step(intent)) {} send(channel,2);return 0;
    }catch(const std::exception& error){std::cerr << "native guardian channel: " << error.what() << '\n';return 2;}
}
