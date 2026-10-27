// Test-only isolated worker. Its inherited pipes are not a production transport.
#include "identity/native_seat_guardian.hpp"
#include "x11/input.hpp"
#include "core/protocol.hpp"
#include <sqlite3.h>
#include <iostream>
#include <stdexcept>
namespace g=spatial::identity::guardian::v1;
void need(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
int main(int argc,char** argv){
    try {
        need(argc==3,"Expected sealed input configuration and test ledger path");
        spatial::NativeInput native(std::stoi(argv[1]));
        sqlite3* raw=nullptr;need(sqlite3_open_v2(argv[2],&raw,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_NOFOLLOW,nullptr)==SQLITE_OK,"Cannot open guardian test ledger");
        struct Db {sqlite3* value;~Db(){sqlite3_close(value);}} database{raw};
        spatial::identity::NativeSeatLedger ledger(raw);spatial::identity::NativeSeatGuardian guardian(ledger,native);
        std::string bytes;need(spatial::read_frame(std::cin,bytes),"Missing seat registration");
        g::SeatRegistration registration;need(registration.ParseFromString(bytes),"Invalid seat registration");
        guardian.register_seat(registration,std::string(32,'r'));
        spatial::wire::RenderResult reply;reply.set_protocol_version(1);reply.set_request_id(1);reply.mutable_ready();spatial::write_frame(std::cout,reply);
        need(spatial::read_frame(std::cin,bytes),"Missing native fence intent");
        g::NativeFenceIntent intent;need(intent.ParseFromString(bytes),"Invalid native fence intent");
        while(guardian.fence_step(intent)) {}
        reply.set_request_id(2);spatial::write_frame(std::cout,reply);return 0;
    }catch(const std::exception& error){std::cerr << "native guardian worker: " << error.what() << '\n';return 2;}
}
