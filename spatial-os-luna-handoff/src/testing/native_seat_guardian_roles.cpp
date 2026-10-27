// Test-only driver for the reusable service. The fixture supplies peer IDs;
// production supervision and issuer authentication remain unimplemented.
#include "identity/native_seat_guardian_service.hpp"
#include "x11/input.hpp"
#include <sqlite3.h>
#include <chrono>
#include <poll.h>
#include <iostream>
#include <exception>
#include <string>
#include <stdexcept>
void need(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
int main(int argc,char** argv){
    try {
        need(argc==8 || (argc==9 && std::string(argv[8])=="--hold-after-failure"),
            "Expected native config, two channels, ledger, creator, two senders, and optional failure fixture");
        spatial::NativeInput native(std::stoi(argv[1]));
        sqlite3* raw=nullptr;need(sqlite3_open_v2(argv[4],&raw,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_NOFOLLOW,nullptr)==SQLITE_OK,"Cannot open guardian test ledger");
        struct Db {sqlite3* value;~Db(){sqlite3_close(value);}} database{raw};
        spatial::identity::NativeSeatLedger ledger(raw);spatial::identity::NativeSeatGuardian guardian(ledger,native);
        spatial::identity::NativeSeatGuardianService service(guardian,std::string(32,'i'),std::string(32,'s'),1,
            std::stoi(argv[2]),std::stoi(argv[5]),std::stoi(argv[6]),std::stoi(argv[3]),std::stoi(argv[7]));
        service.announce();bool completed=false;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
        while(std::chrono::steady_clock::now()<deadline){
            if(completed){service.flush();if(!service.pending_output())return 0;}
            else try {
                if(service.pump()==spatial::identity::NativeSeatGuardianService::Progress::fence_complete)completed=true;
            } catch (...) {
                auto original=std::current_exception();bool rejected=false;
                try {(void)service.pump();} catch(const std::logic_error&) {rejected=true;}
                if(!rejected)throw std::runtime_error("Failed guardian service allowed reuse");
                std::cerr << "failed guardian service rejected reuse\n";
                // Test-only: keep the failed service in scope so process exit
                // cannot stand in for its explicit native disconnect.
                if(argc==9)(void)poll(nullptr,0,2000);
                std::rethrow_exception(original);
            }
            pollfd fds[2]{{service.world_descriptor(),POLLIN,0},{service.issuer_descriptor(),POLLIN,0}};
            if(poll(fds,2,20)<0)throw std::runtime_error("Guardian channels poll failed");
        }
        throw std::runtime_error("Guardian channel deadline exceeded");
    }catch(const std::exception& error){std::cerr << "native guardian roles: " << error.what() << '\n';return 2;}
}
