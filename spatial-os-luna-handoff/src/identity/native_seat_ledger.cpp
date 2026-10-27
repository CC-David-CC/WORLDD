#include "identity/native_seat_ledger.hpp"
#include "identity/native_seat_binding.hpp"
#include <sqlite3.h>
#include <stdexcept>

namespace spatial::identity {
namespace {
void need(bool ok, const char* message) { if (!ok) throw std::invalid_argument(message); }
void check(int rc) { if (rc!=SQLITE_OK && rc!=SQLITE_ROW && rc!=SQLITE_DONE) throw std::runtime_error("Native seat ledger database error"); }
void sql(sqlite3* db,const char* text) {check(sqlite3_exec(db,text,nullptr,nullptr,nullptr));}
struct Query {
    sqlite3_stmt* s=nullptr;
    Query(sqlite3* db,const char* text) {check(sqlite3_prepare_v2(db,text,-1,&s,nullptr));}
    ~Query(){sqlite3_finalize(s);}
    void blob(int i,const std::string& v){check(sqlite3_bind_blob(s,i,v.data(),static_cast<int>(v.size()),SQLITE_TRANSIENT));}
    void number(int i,int v){check(sqlite3_bind_int(s,i,v));}
    int step(){int rc=sqlite3_step(s);check(rc);return rc;}
    std::string bytes(int i){const auto* p=static_cast<const char*>(sqlite3_column_blob(s,i));return p?std::string(p,sqlite3_column_bytes(s,i)):std::string{};}
    sqlite3_int64 integer(int i){return sqlite3_column_int64(s,i);}
};
struct Transaction {
    sqlite3* db;bool committed=false;
    explicit Transaction(sqlite3* value):db(value){sql(db,"BEGIN IMMEDIATE");}
    ~Transaction(){if(!committed)sqlite3_exec(db,"ROLLBACK",nullptr,nullptr,nullptr);}
    void commit(){sql(db,"COMMIT");committed=true;}
};
void valid_intent(const guardian::v1::NativeFenceIntent& v){
    validate_message(v);
    need(v.intent_id().size()==32 && v.issuer_id().size()==32 && v.target_id().size()==32 &&
         (v.target_kind()==guardian::v1::REVOCATION_ACTOR || v.target_kind()==guardian::v1::REVOCATION_CREDENTIAL) &&
         v.expected_security_revision()<=INT64_MAX,"Invalid native fence intent");
}
void valid_registration(const guardian::v1::SeatRegistration& request){
    validate_message(request);
    need(request.operation_id().size()==32 && request.has_binding(),"Invalid seat registration");
    validate_native_seat_binding(request.binding());
    need(request.issuer_security_revision()<=INT64_MAX,"Invalid registration revision");
}
}
NativeSeatLedger::NativeSeatLedger(sqlite3* database):db_(database){
    need(db_!=nullptr,"Missing native seat ledger database");
    const char* filename=sqlite3_db_filename(db_,"main");
    need(filename && *filename && sqlite3_db_readonly(db_,"main")==0 && sqlite3_get_autocommit(db_),
        "Native seat ledger requires an idle writable file database");
    { Query synchronous(db_,"PRAGMA synchronous");need(synchronous.step()==SQLITE_ROW && synchronous.integer(0)>=2,
        "Native seat ledger requires full synchronous durability"); }
    { Query journal(db_,"PRAGMA journal_mode");need(journal.step()==SQLITE_ROW,"Missing native seat ledger journal mode");
      const auto mode=journal.bytes(0);need(mode=="delete" || mode=="truncate" || mode=="persist" || mode=="wal",
        "Native seat ledger requires a persistent journal"); }
    Transaction transaction(db_);
    Query application(db_,"PRAGMA application_id");need(application.step()==SQLITE_ROW,"Missing native seat ledger application identity");
    const auto application_id=application.integer(0);
    Query version(db_,"PRAGMA user_version");need(version.step()==SQLITE_ROW,"Missing native seat ledger schema version");
    const auto schema_version=version.integer(0);
    if(application_id==0 && schema_version==0) {
        Query existing(db_,"SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1");
        need(existing.step()==SQLITE_DONE,"Cannot initialize a nonempty native seat ledger database");
    } else need(application_id==0x5350474c && schema_version==1,"Unsupported native seat ledger database");
    if(application_id==0) {
        sql(db_,"CREATE TABLE guardian_seat(operation BLOB PRIMARY KEY, id BLOB UNIQUE NOT NULL, binding BLOB NOT NULL, issuer BLOB NOT NULL, actor BLOB NOT NULL, credential BLOB NOT NULL, seat_key BLOB UNIQUE NOT NULL);"
                "CREATE TABLE guardian_intent(id BLOB PRIMARY KEY, intent BLOB NOT NULL, issuer BLOB NOT NULL, kind INTEGER NOT NULL, target BLOB NOT NULL);"
                "CREATE TABLE guardian_obligation(intent BLOB NOT NULL, seat BLOB NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)), PRIMARY KEY(intent,seat));"
                "PRAGMA application_id=0x5350474c; PRAGMA user_version=1");
    } else {
        // Do not recreate a missing table: that could erase an unresolved fence.
        Query seats(db_,"SELECT operation,id,binding,issuer,actor,credential,seat_key FROM guardian_seat LIMIT 0");
        Query intents(db_,"SELECT id,intent,issuer,kind,target FROM guardian_intent LIMIT 0");
        Query obligations(db_,"SELECT intent,seat,confirmed FROM guardian_obligation LIMIT 0");
        need(seats.step()==SQLITE_DONE && intents.step()==SQLITE_DONE && obligations.step()==SQLITE_DONE,
            "Invalid native seat ledger schema");
    }
    sql(db_,"CREATE INDEX IF NOT EXISTS guardian_seat_actor ON guardian_seat(issuer,actor);"
            "CREATE INDEX IF NOT EXISTS guardian_seat_credential ON guardian_seat(issuer,credential);"
            "CREATE INDEX IF NOT EXISTS guardian_intent_target ON guardian_intent(issuer,kind,target);"
            "CREATE INDEX IF NOT EXISTS guardian_obligation_pending ON guardian_obligation(intent,confirmed,seat)");
    transaction.commit();
}
std::string NativeSeatLedger::register_seat(const guardian::v1::SeatRegistration& request,const std::string& new_id){
    valid_registration(request); need(new_id.size()==32,"Invalid seat registration identity");
    const auto& b=request.binding();const auto encoded=request.SerializeAsString();Transaction transaction(db_);
    Query blocked(db_,"SELECT 1 FROM guardian_intent WHERE issuer=? AND ((kind=1 AND target=?) OR (kind=2 AND target=?)) LIMIT 1");
    blocked.blob(1,b.issuer_id());blocked.blob(2,b.actor_id());blocked.blob(3,b.credential_id());
    if(blocked.step()==SQLITE_ROW)throw std::runtime_error("Seat registration fenced by pending revocation");
    Query previous(db_,"SELECT id,binding FROM guardian_seat WHERE operation=?");previous.blob(1,request.operation_id());
    if(previous.step()==SQLITE_ROW){auto id=previous.bytes(0);need(previous.bytes(1)==encoded,"Seat registration operation conflict");transaction.commit();return id;}
    std::string seat_key=b.native_server_id();
    for(int shift=56;shift>=0;shift-=8)seat_key.push_back(static_cast<char>(b.native_seat_id()>>shift));
    Query insert(db_,"INSERT INTO guardian_seat(operation,id,binding,issuer,actor,credential,seat_key) VALUES(?,?,?,?,?,?,?)");
    insert.blob(1,request.operation_id());insert.blob(2,new_id);insert.blob(3,encoded);insert.blob(4,b.issuer_id());insert.blob(5,b.actor_id());insert.blob(6,b.credential_id());insert.blob(7,seat_key);insert.step();
    transaction.commit();return new_id;
}
std::vector<NativeSeatLedger::Obligation> NativeSeatLedger::begin_fence(const guardian::v1::NativeFenceIntent& intent){
    prepare_fence(intent);return outstanding(intent.intent_id());
}
void NativeSeatLedger::prepare_fence(const guardian::v1::NativeFenceIntent& intent){
    valid_intent(intent);Transaction transaction(db_);const auto encoded=intent.SerializeAsString();
    Query prior(db_,"SELECT intent FROM guardian_intent WHERE id=?");prior.blob(1,intent.intent_id());
    if(prior.step()==SQLITE_ROW)need(prior.bytes(0)==encoded,"Fence intent operation conflict");
    else {
        Query insert(db_,"INSERT INTO guardian_intent(id,intent,issuer,kind,target) VALUES(?,?,?,?,?)");
        insert.blob(1,intent.intent_id());insert.blob(2,encoded);insert.blob(3,intent.issuer_id());insert.number(4,int(intent.target_kind()));insert.blob(5,intent.target_id());insert.step();
        Query obligations(db_,"INSERT INTO guardian_obligation(intent,seat) SELECT ?,id FROM guardian_seat WHERE issuer=? AND ((?=1 AND actor=?) OR (?=2 AND credential=?))");
        obligations.blob(1,intent.intent_id());obligations.blob(2,intent.issuer_id());obligations.number(3,int(intent.target_kind()));obligations.blob(4,intent.target_id());obligations.number(5,int(intent.target_kind()));obligations.blob(6,intent.target_id());obligations.step();
    }
    transaction.commit();
}
std::optional<NativeSeatLedger::Obligation> NativeSeatLedger::first_outstanding(const std::string& id){
    need(id.size()==32,"Invalid fence intent identity");
    Query intent(db_,"SELECT 1 FROM guardian_intent WHERE id=?");intent.blob(1,id);need(intent.step()==SQLITE_ROW,"Unknown fence intent");
    Query q(db_,"SELECT o.seat,s.binding FROM guardian_obligation o LEFT JOIN guardian_seat s ON s.id=o.seat WHERE o.intent=? AND o.confirmed=0 ORDER BY o.seat LIMIT 1");q.blob(1,id);
    if(q.step()==SQLITE_DONE)return std::nullopt;
    guardian::v1::SeatRegistration registration;need(registration.ParseFromString(q.bytes(1)),"Invalid retained seat registration");valid_registration(registration);
    return Obligation{q.bytes(0),registration.binding()};
}
std::size_t NativeSeatLedger::outstanding_count(const std::string& id){
    need(id.size()==32,"Invalid fence intent identity");
    Query intent(db_,"SELECT 1 FROM guardian_intent WHERE id=?");intent.blob(1,id);need(intent.step()==SQLITE_ROW,"Unknown fence intent");
    Query q(db_,"SELECT count(*) FROM guardian_obligation WHERE intent=? AND confirmed=0");q.blob(1,id);need(q.step()==SQLITE_ROW,"Cannot count native fence obligations");
    return static_cast<std::size_t>(q.integer(0));
}
std::size_t NativeSeatLedger::obligation_count(const std::string& id){
    need(id.size()==32,"Invalid fence intent identity");
    Query intent(db_,"SELECT 1 FROM guardian_intent WHERE id=?");intent.blob(1,id);need(intent.step()==SQLITE_ROW,"Unknown fence intent");
    Query q(db_,"SELECT count(*) FROM guardian_obligation WHERE intent=?");q.blob(1,id);
    need(q.step()==SQLITE_ROW&&q.integer(0)>=0,"Cannot count native fence obligations");
    return static_cast<std::size_t>(q.integer(0));
}
std::vector<NativeSeatLedger::Obligation> NativeSeatLedger::outstanding(const std::string& id){
    need(id.size()==32,"Invalid fence intent identity");
    Query intent(db_,"SELECT 1 FROM guardian_intent WHERE id=?");intent.blob(1,id);need(intent.step()==SQLITE_ROW,"Unknown fence intent");
    Query q(db_,"SELECT o.seat,s.binding FROM guardian_obligation o LEFT JOIN guardian_seat s ON s.id=o.seat WHERE o.intent=? AND o.confirmed=0 ORDER BY o.seat");q.blob(1,id);
    std::vector<Obligation> result;
    while(q.step()==SQLITE_ROW){guardian::v1::SeatRegistration registration;need(registration.ParseFromString(q.bytes(1)),"Invalid retained seat registration");valid_registration(registration);result.push_back({q.bytes(0),registration.binding()});}
    return result;
}
void NativeSeatLedger::record_confirmed_fence(const std::string& intent,const std::string& seat){
    need(intent.size()==32 && seat.size()==32,"Invalid native fence confirmation");Transaction transaction(db_);
    Query q(db_,"UPDATE guardian_obligation SET confirmed=1 WHERE intent=? AND seat=?");q.blob(1,intent);q.blob(2,seat);q.step();
    need(sqlite3_changes(db_)==1,"Unknown native fence obligation");transaction.commit();
}
std::vector<guardian::v1::NativeFenceIntent> NativeSeatLedger::pending_intents(){
    Query q(db_,"SELECT intent FROM guardian_intent ORDER BY id");std::vector<guardian::v1::NativeFenceIntent> result;
    while(q.step()==SQLITE_ROW){guardian::v1::NativeFenceIntent v;need(v.ParseFromString(q.bytes(0)),"Invalid retained fence intent");valid_intent(v);result.push_back(std::move(v));}return result;
}
}
