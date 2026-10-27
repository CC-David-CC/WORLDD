#include "identity/native_seat_ledger.hpp"
#include <sqlite3.h>
#include <unistd.h>
#include <functional>
#include <stdexcept>
#include <string>
#include <barrier>
#include <thread>

using namespace spatial::identity;
namespace g=spatial::identity::guardian::v1;
void expect(bool v){if(!v)throw std::runtime_error("ledger assertion failed");}
void reject(const std::function<void()>& f){try{f();}catch(const std::exception&){return;}throw std::runtime_error("ledger accepted rejected operation");}
g::SeatRegistration registration(char operation,char actor,char credential){
    g::SeatRegistration r;r.set_operation_id(std::string(32,operation));r.set_issuer_security_revision(4);
    auto* b=r.mutable_binding();b->set_issuer_id(std::string(32,'i'));b->set_actor_id(std::string(32,actor));
    b->set_credential_id(std::string(32,credential));b->set_actor_entity_id("identity:actor");
    b->set_actor_session_instance(1);b->set_service_incarnation(std::string(32,'s'));b->set_authority_generation(2);
    b->set_native_server_id(std::string(32,'n'));b->set_compositor_credential_id(std::string(32,'c'));b->set_native_seat_id(operation);
    return r;
}
int main(){
    sqlite3* memory=nullptr;expect(sqlite3_open(":memory:",&memory)==SQLITE_OK);
    reject([&]{NativeSeatLedger invalid(memory);});expect(sqlite3_close(memory)==SQLITE_OK);
    char path[]="/tmp/spatial-guardian-ledger-XXXXXX";int fd=mkstemp(path);expect(fd>=0);close(fd);
    sqlite3* db=nullptr;expect(sqlite3_open(path,&db)==SQLITE_OK);
    expect(sqlite3_exec(db,"PRAGMA synchronous=OFF",nullptr,nullptr,nullptr)==SQLITE_OK);
    reject([&]{NativeSeatLedger invalid(db);});
    expect(sqlite3_exec(db,"PRAGMA synchronous=FULL; PRAGMA journal_mode=MEMORY",nullptr,nullptr,nullptr)==SQLITE_OK);
    reject([&]{NativeSeatLedger invalid(db);});
    expect(sqlite3_exec(db,"PRAGMA journal_mode=DELETE",nullptr,nullptr,nullptr)==SQLITE_OK);
    {
        NativeSeatLedger ledger(db);auto first=registration('1','a','x');auto second=registration('2','b','y');
        auto id1=std::string(32,'1'),id2=std::string(32,'2');
        expect(ledger.register_seat(first,id1)==id1);expect(ledger.register_seat(first,std::string(32,'z'))==id1);
        auto changed=first;changed.mutable_binding()->set_native_seat_id(99);reject([&]{ledger.register_seat(changed,std::string(32,'q'));});
        auto duplicate=registration('9','b','y');duplicate.mutable_binding()->set_native_seat_id(first.binding().native_seat_id());
        reject([&]{ledger.register_seat(duplicate,std::string(32,'9'));});
        expect(ledger.register_seat(second,id2)==id2);
        g::NativeFenceIntent intent;intent.set_intent_id(std::string(32,'f'));intent.set_issuer_id(std::string(32,'i'));
        intent.set_target_kind(g::REVOCATION_ACTOR);intent.set_target_id(std::string(32,'a'));intent.set_expected_security_revision(4);
        auto pending=ledger.begin_fence(intent);expect(pending.size()==1 && pending[0].registration_id==id1);
        reject([&]{ledger.register_seat(first,id1);});
        reject([&]{ledger.register_seat(registration('3','a','z'),std::string(32,'3'));});
        expect(ledger.register_seat(registration('4','d','z'),std::string(32,'4'))==std::string(32,'4'));
        auto conflict=intent;conflict.set_target_id(std::string(32,'b'));reject([&]{ledger.begin_fence(conflict);});
    }
    expect(sqlite3_close(db)==SQLITE_OK);db=nullptr;expect(sqlite3_open(path,&db)==SQLITE_OK);
    {
        NativeSeatLedger ledger(db);auto intents=ledger.pending_intents();expect(intents.size()==1);
        auto pending=ledger.outstanding(intents[0].intent_id());expect(pending.size()==1 && pending[0].binding.actor_id()==std::string(32,'a'));
        reject([&]{ledger.record_confirmed_fence(intents[0].intent_id(),std::string(32,'2'));});
        ledger.record_confirmed_fence(intents[0].intent_id(),std::string(32,'1'));
        expect(ledger.outstanding(intents[0].intent_id()).empty());
        reject([&]{ledger.register_seat(registration('5','a','z'),std::string(32,'5'));});
        expect(ledger.register_seat(registration('6','b','z'),std::string(32,'6'))==std::string(32,'6'));
        g::NativeFenceIntent credential;credential.set_intent_id(std::string(32,'g'));credential.set_issuer_id(std::string(32,'i'));
        credential.set_target_kind(g::REVOCATION_CREDENTIAL);credential.set_target_id(std::string(32,'y'));credential.set_expected_security_revision(4);
        auto credential_seats=ledger.begin_fence(credential);
        expect(credential_seats.size()==1 && credential_seats[0].registration_id==std::string(32,'2'));
        reject([&]{ledger.register_seat(registration('7','b','y'),std::string(32,'7'));});
        expect(ledger.register_seat(registration('8','b','z'),std::string(32,'8'))==std::string(32,'8'));
    }
    sqlite3* other=nullptr;expect(sqlite3_open(path,&other)==SQLITE_OK);
    sqlite3_busy_timeout(db,5000);sqlite3_busy_timeout(other,5000);
    {
        NativeSeatLedger registrar(db), fencer(other);
        for (int n=0;n<16;++n) {
            const char key=static_cast<char>(10+n), intent_key=static_cast<char>(120+n);
            const auto request=registration(key,key,'r');const auto id=std::string(32,key);
            g::NativeFenceIntent intent;intent.set_intent_id(std::string(32,intent_key));intent.set_issuer_id(std::string(32,'i'));
            intent.set_target_kind(g::REVOCATION_ACTOR);intent.set_target_id(std::string(32,key));intent.set_expected_security_revision(4);
            std::barrier start(2);bool accepted=false,fence_failed=false,unexpected_failure=false;
            std::thread a([&]{start.arrive_and_wait();try{registrar.register_seat(request,id);accepted=true;}
                catch(const std::exception& error){unexpected_failure=std::string(error.what())!="Seat registration fenced by pending revocation";}});
            std::thread b([&]{start.arrive_and_wait();try{fencer.begin_fence(intent);}catch(const std::exception&) {fence_failed=true;}});
            a.join();b.join();expect(!fence_failed && !unexpected_failure);
            const auto pending=fencer.outstanding(intent.intent_id());
            expect(pending.size()==(accepted?1U:0U));
            if(accepted)expect(pending[0].registration_id==id);
            reject([&]{registrar.register_seat(request,id);});
        }
        auto corrupted=registration('H','h','j');const auto corrupt_id=std::string(32,'H');
        registrar.register_seat(corrupted,corrupt_id);
        g::NativeFenceIntent corrupt_intent;corrupt_intent.set_intent_id(std::string(32,'S'));corrupt_intent.set_issuer_id(std::string(32,'i'));
        corrupt_intent.set_target_kind(g::REVOCATION_ACTOR);corrupt_intent.set_target_id(std::string(32,'h'));
        expect(registrar.begin_fence(corrupt_intent).size()==1);
        auto bytes=corrupted.SerializeAsString();bytes.append("\xa0\x06\x01",3); // unknown field 100
        sqlite3_stmt* stmt=nullptr;expect(sqlite3_prepare_v2(db,"UPDATE guardian_seat SET binding=? WHERE id=?",-1,&stmt,nullptr)==SQLITE_OK);
        expect(sqlite3_bind_blob(stmt,1,bytes.data(),static_cast<int>(bytes.size()),SQLITE_TRANSIENT)==SQLITE_OK);
        expect(sqlite3_bind_blob(stmt,2,corrupt_id.data(),static_cast<int>(corrupt_id.size()),SQLITE_TRANSIENT)==SQLITE_OK);
        expect(sqlite3_step(stmt)==SQLITE_DONE);expect(sqlite3_finalize(stmt)==SQLITE_OK);
        reject([&]{registrar.outstanding(corrupt_intent.intent_id());});
        reject([&]{registrar.first_outstanding(corrupt_intent.intent_id());});
        for(int n=0;n<64;++n){
            const char key=static_cast<char>(140+n);auto request=registration(key,'P','Q');
            request.mutable_binding()->set_native_seat_id(1000+n);
            registrar.register_seat(request,std::string(32,key));
        }
        g::NativeFenceIntent many;many.set_intent_id(std::string(32,'M'));many.set_issuer_id(std::string(32,'i'));
        many.set_target_kind(g::REVOCATION_ACTOR);many.set_target_id(std::string(32,'P'));
        registrar.prepare_fence(many);
        for(std::size_t count=64;count>0;--count){
            expect(registrar.outstanding_count(many.intent_id())==count);
            auto next=registrar.first_outstanding(many.intent_id());expect(next.has_value());
            registrar.record_confirmed_fence(many.intent_id(),next->registration_id);
        }
        expect(registrar.outstanding_count(many.intent_id())==0 && !registrar.first_outstanding(many.intent_id()));
        reject([&]{registrar.first_outstanding(std::string(32,'?'));});
        reject([&]{registrar.outstanding_count(std::string(32,'?'));});
    }
    expect(sqlite3_close(other)==SQLITE_OK);
    expect(sqlite3_exec(db,"DROP TABLE guardian_obligation",nullptr,nullptr,nullptr)==SQLITE_OK);
    reject([&]{NativeSeatLedger damaged(db);});
    expect(sqlite3_close(db)==SQLITE_OK);unlink(path);return 0;
}
