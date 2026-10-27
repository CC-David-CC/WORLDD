#include "identity/native_seat_guardian.hpp"
#include "x11/input.hpp"
#include "core/protocol.hpp"
#include <sqlite3.h>
#include <fcntl.h>
#include <unistd.h>
#include <iostream>
#include <stdexcept>

namespace g=spatial::identity::guardian::v1;
namespace ni=spatial::input::v1;
void need(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
int main(int argc,char** argv){
    try {
        need(argc==3,"Expected two sealed input configurations");
        const int first=std::stoi(argv[1]),second=std::stoi(argv[2]);
        const int guardian_fd=fcntl(first,F_DUPFD_CLOEXEC,3);need(guardian_fd>=3,"Cannot duplicate guardian configuration");
        spatial::NativeInput owner(first),guard(guardian_fd),other(second);
        ni::Request open;open.mutable_open();const auto seat=owner.call(open),other_seat=other.call(open);
        need(seat.code()==ni::OK && seat.seat() && other_seat.code()==ni::OK && other_seat.seat(),"Cannot open test seats");
        char path[]="/tmp/spatial-native-guardian-XXXXXX";const int file=mkstemp(path);need(file>=0,"Cannot create private ledger");close(file);
        struct Cleanup {const char* path;~Cleanup(){unlink(path);}} cleanup{path};
        sqlite3* raw=nullptr;need(sqlite3_open(path,&raw)==SQLITE_OK,"Cannot open ledger");
        struct Db {sqlite3* value;~Db(){sqlite3_close(value);}} database{raw};
        spatial::identity::NativeSeatLedger ledger(raw);
        spatial::identity::NativeSeatGuardian guardian(ledger,guard),wrong(ledger,other);
        g::SeatRegistration reg;reg.set_operation_id(std::string(32,'o'));auto* b=reg.mutable_binding();
        b->set_issuer_id(std::string(32,'i'));b->set_actor_id(std::string(32,'a'));b->set_credential_id(std::string(32,'c'));
        b->set_actor_entity_id("identity:fixture");b->set_actor_session_instance(1);b->set_service_incarnation(std::string(32,'s'));
        b->set_authority_generation(1);b->set_native_server_id(guard.server_id());b->set_compositor_credential_id(guard.credential_id());
        b->set_native_seat_id(seat.seat());const auto registration=std::string(32,'r');
        bool rejected=false;try{wrong.register_seat(reg,registration);}catch(const std::exception&){rejected=true;}
        need(rejected,"Foreign guardian accepted binding");
        need(guardian.register_seat(reg,registration)==registration,"Guardian registration failed");
        g::NativeFenceIntent intent;intent.set_intent_id(std::string(32,'f'));intent.set_issuer_id(b->issuer_id());
        intent.set_target_kind(g::REVOCATION_ACTOR);intent.set_target_id(b->actor_id());
        rejected=false;try{wrong.fence_step(intent);}catch(const std::exception&){rejected=true;}
        need(rejected && ledger.outstanding(intent.intent_id()).size()==1,"Foreign guardian completed a fence");
        need(guardian.fence_step(intent)==0 && ledger.outstanding(intent.intent_id()).empty(),"Native fence was not confirmed");
        ni::Request key;key.mutable_key()->set_seat(seat.seat());key.mutable_key()->set_keycode(38);key.mutable_key()->set_pressed(true);
        need(owner.call(key).code()==ni::FORBIDDEN,"Fenced seat accepted later input");
        ni::Request status;status.mutable_status()->set_id(other_seat.seat());
        need(other.call(status).code()==ni::OK,"Unrelated seat was affected");
        spatial::wire::RenderResult result;result.set_protocol_version(1);result.mutable_ready();spatial::write_frame(std::cout,result);
        return 0;
    }catch(const std::exception& error){std::cerr << "native guardian probe: " << error.what() << '\n';return 2;}
}
