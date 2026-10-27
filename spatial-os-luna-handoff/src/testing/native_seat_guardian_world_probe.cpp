// Exercises the same supervisor service and world client used by production.
#include "identity/native_seat_guardian_client.hpp"
#include "identity/native_seat_guardian_world_service.hpp"
#include "identity/native_seat_guardian.hpp"
#include "identity/native_seat_ledger.hpp"
#include "x11/input.hpp"
#include <sqlite3.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <chrono>
#include <cerrno>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
namespace g=spatial::identity::guardian::v1;
void need(bool value,const char* message){if(!value)throw std::runtime_error(message);}
int main(int argc,char** argv){
    try{
        need(argc==6,"Expected sealed compositor config, ledger path, native server ID, compositor credential ID, and seat ID");
        const int config=std::stoi(argv[1]);const auto parent=getpid();
        const int seat=std::stoi(argv[5]);need(seat>0,"Invalid native seat ID");
        int pair[2];need(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,pair)==0,"Cannot create guardian world channel");
        const auto child=fork();need(child>=0,"Cannot start guardian service process");
        if(child==0){
            close(pair[0]);
            try{
                spatial::NativeInput native(config);
                sqlite3* raw=nullptr;
                need(sqlite3_open_v2(argv[2],&raw,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_NOFOLLOW,nullptr)==SQLITE_OK,
                    "Cannot open guardian ledger");
                struct Db{sqlite3* value;~Db(){sqlite3_close(value);}} db{raw};
                char* error=nullptr;
                const int setup=sqlite3_exec(raw,"PRAGMA trusted_schema=OFF; PRAGMA foreign_keys=ON; PRAGMA synchronous=EXTRA; PRAGMA journal_mode=DELETE;",nullptr,nullptr,&error);
                if(error)sqlite3_free(error);
                need(setup==SQLITE_OK,"Cannot configure guardian ledger");
                spatial::identity::NativeSeatLedger ledger(raw);
                spatial::identity::NativeSeatGuardian guardian(ledger,native);
                spatial::identity::NativeSeatGuardianWorldService service(guardian,std::string(32,'i'),std::string(32,'s'),1,
                    pair[1],parent,parent);
                service.announce();
                for(;;){
                    try{service.pump();}
                    catch(...){return 0;} // Client close must close the guarded native connection.
                    pollfd event{service.descriptor(),POLLIN,0};
                    int result;do{result=poll(&event,1,20);}while(result<0&&errno==EINTR);
                    need(result>=0,"Guardian service poll failed");
                }
            }catch(const std::exception& error){std::cerr<<"guardian world service: "<<error.what()<<'\n';return 2;}
        }
        close(pair[1]);
        {
            spatial::identity::NativeSeatGuardianClient client(pair[0],parent,child);
            pair[0]=-1;
            g::NativeSeatBinding binding;binding.set_issuer_id(std::string(32,'i'));binding.set_actor_id(std::string(32,'a'));
            binding.set_credential_id(std::string(32,'c'));binding.set_actor_entity_id("identity:guardian-world-probe");
            binding.set_actor_session_instance(1);binding.set_service_incarnation(std::string(32,'s'));
            binding.set_authority_generation(1);binding.set_native_server_id(argv[3]);
            binding.set_compositor_credential_id(argv[4]);binding.set_native_seat_id(static_cast<std::uint64_t>(seat));
            const auto receipt=client.register_seat(binding,17);
            need(receipt.binding().SerializeAsString()==binding.SerializeAsString()&&receipt.issuer_security_revision()==17,
                "Guardian client returned a mismatched checked receipt");
        }
        if(pair[0]>=0)close(pair[0]);
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
        int status=0;
        while(std::chrono::steady_clock::now()<deadline){
            const auto result=waitpid(child,&status,WNOHANG);
            if(result==child){need(WIFEXITED(status)&&WEXITSTATUS(status)==0,"Guardian did not close cleanly after client loss");return 0;}
            if(result<0&&errno!=EINTR)throw std::runtime_error("Cannot wait for guardian service");
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        kill(child,SIGKILL);waitpid(child,&status,0);throw std::runtime_error("Guardian service did not stop after client loss");
    }catch(const std::exception& error){std::cerr<<"native guardian world probe: "<<error.what()<<'\n';return 2;}
}
