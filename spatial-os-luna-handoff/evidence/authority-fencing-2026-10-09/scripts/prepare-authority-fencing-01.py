"""Create the isolated fencing draft; never mutate the live world candidate."""
import hashlib
import json
from pathlib import Path

r = Path(__file__).resolve().parents[1]
base = r / 'local/supervisor-world-handoff-draft-02'
draft = r / 'local/authority-fencing-draft-01'
manifest = json.loads((r / 'local/supervisor-world-handoff-candidate-02.json').read_text())['files']
assert len(manifest) == 290
draft.mkdir()
for name, digest in manifest.items():
    data = (base / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, name
    p = draft / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)

def edit(name, before, after):
    p = draft / name; text = p.read_text()
    assert text.count(before) == 1, (name, before[:80], text.count(before))
    p.write_bytes(text.replace(before, after).encode())

edit('proto/spatial/identity/v1/key.proto', '  AUTHORITY_LAUNCH_AUTHORIZATION = 6;',
     '  AUTHORITY_LAUNCH_AUTHORIZATION = 6;\n  AUTHORITY_FENCING = 7;')
messages = '''// Fencing stops issuer-side service mutations. It does not prove external cleanup.
message AuthorityFenceBegin {
  bytes participant_id = 1;
  uint64 registered_revision = 2;
  uint64 generation = 3;
  bytes service_incarnation = 4;
  bytes launch_id = 5;
  uint64 supervision_revision = 6;
  bytes authorization_id = 7;
}
message AuthorityFenceChallenge {
  uint32 protocol_version = 1;
  bytes issuer_id = 2;
  bytes issuer_incarnation = 3;
  bytes nonce = 4;
  bytes connection_binding = 5;
  AuthorityFenceBegin request = 6;
  bytes supervisor_public_key = 7;
  uint32 lifetime_ms = 8;
  string audience = 9;
}
message AuthorityFenceComplete { bytes nonce = 1; KeyProof proof = 2; }
message AuthorityFenceState {
  bytes issuer_id = 1;
  AuthorityParticipantRecord registration = 2;
  uint64 generation = 3;
  bytes service_incarnation = 4;
  bytes authorization_id = 5;
  bytes launch_id = 6;
  uint64 supervision_revision = 7;
  bytes fence_id = 8;
  uint64 security_revision = 9;
  bytes supervisor_public_key = 10;
}
'''
edit('proto/spatial/identity/v1/issuer.proto', 'message IssuerRequest {', messages + 'message IssuerRequest {')
edit('proto/spatial/identity/v1/issuer.proto', '    AuthorityLaunchComplete authority_launch_complete = 29;',
     '    AuthorityLaunchComplete authority_launch_complete = 29;\n    AuthorityFenceBegin authority_fence_begin = 30;\n    AuthorityFenceComplete authority_fence_complete = 31;')
edit('proto/spatial/identity/v1/issuer.proto', '    AuthorityLaunchAuthorization authority_launch_authorization = 27;',
     '    AuthorityLaunchAuthorization authority_launch_authorization = 27;\n    AuthorityFenceState authority_fence = 28;')
edit('src/identity/participants.hpp', 'inline void validate_registry_page(', '''inline void validate_fence_begin(const v1::AuthorityFenceBegin& request) {
    using participant_detail::require;
    validate_message(request);
    require(request.participant_id().size() == 32 && request.service_incarnation().size() == 32 &&
            request.launch_id().size() == 32 && request.authorization_id().size() == 32 &&
            request.registered_revision() > 0 && request.registered_revision() <= INT64_MAX &&
            request.generation() > 0 && request.generation() <= INT64_MAX &&
            request.supervision_revision() > 0 && request.supervision_revision() <= INT64_MAX,
            "Invalid authority fence context");
}
inline void validate_registry_page(''')
edit('src/identity/issuer.hpp', '    v1::AuthorityActivationState authority_activate(', '''    v1::ChallengeReply authority_fence_begin(const v1::AuthorityFenceBegin& request, IssuerConnection& connection);
    v1::AuthorityFenceState authority_fence_complete(const v1::AuthorityFenceComplete& request, IssuerConnection& connection);
    v1::AuthorityActivationState authority_activate(''')
edit('src/identity/protocol.cpp', '        else if (request.has_authority_launch_begin() ||', '''        else if (request.has_authority_fence_begin() || request.has_authority_fence_complete()) {
            if (!connection) throw spatial::WorldError(spatial::wire::FORBIDDEN, "Fencing requires a TLS connection context");
            if (request.has_authority_fence_begin()) *reply.mutable_challenge() = issuer.authority_fence_begin(request.authority_fence_begin(), *connection);
            else *reply.mutable_authority_fence() = issuer.authority_fence_complete(request.authority_fence_complete(), *connection);
        }
        else if (request.has_authority_launch_begin() ||''')
edit('src/identity/protocol.cpp', '            result->add_capabilities("identity.issuer-control/v1");',
     '            result->add_capabilities("identity.issuer-control/v1");\n            result->add_capabilities("identity.authority-fencing/v1");')
edit('src/identity/issuer.cpp', '} // namespace\n\nstruct Issuer::Impl {', '''void create_fence_schema(sqlite3* db) {
    sql(db, "CREATE TABLE authority_fence("
            "participant_id BLOB NOT NULL CHECK(length(participant_id)=32),"
            "generation INTEGER NOT NULL CHECK(generation>0),"
            "fence_id BLOB NOT NULL UNIQUE CHECK(length(fence_id)=32),"
            "security_revision INTEGER NOT NULL CHECK(security_revision>=0),"
            "supervisor_public_key BLOB NOT NULL CHECK(length(supervisor_public_key)=32),"
            "request BLOB NOT NULL,state BLOB NOT NULL,PRIMARY KEY(participant_id,generation),"
            "FOREIGN KEY(participant_id,generation) REFERENCES authority_activation(participant_id,generation));"
            "PRAGMA user_version=9");
}
} // namespace

struct Issuer::Impl {''')
edit('src/identity/issuer.cpp', '        v1::AuthorityLaunchBegin launch{};',
     '        v1::AuthorityLaunchBegin launch{};\n        v1::AuthorityFenceBegin fence{};')
edit('src/identity/issuer.cpp', 'sqlite3_int64 schema = 8;', 'sqlite3_int64 schema = 9;')
edit('src/identity/issuer.cpp', '(schema == 8 || ((schema >= 1 && schema <= 7) && migrate))',
     '(schema == 9 || ((schema >= 1 && schema <= 8) && migrate))')
edit('src/identity/issuer.cpp', 'create_supervisor_schema(db.get()); create_launch_schema(db.get()); tx.commit();',
     'create_supervisor_schema(db.get()); create_launch_schema(db.get()); create_fence_schema(db.get()); tx.commit();')
edit('src/identity/issuer.cpp', 'if (schema < 8) {', 'if (schema < 9) {')
edit('src/identity/issuer.cpp', '            create_launch_schema(db.get()); migration.commit();',
     '            if (schema < 8) create_launch_schema(db.get());\n            create_fence_schema(db.get()); migration.commit();')
edit('src/identity/issuer.cpp', '    v1::AuthoritySupervisorBinding launch_context(', '''    bool fenced(const std::string& participant, std::uint64_t generation) {
        Query row(db.get(), "SELECT 1 FROM authority_fence WHERE participant_id=? AND generation=?");
        row.blob(1, participant); row.integer(2, generation);
        return row.step() == SQLITE_ROW;
    }
    v1::AuthoritySupervisorBinding fence_context(const v1::AuthorityFenceBegin& request) {
        validate_fence_begin(request);
        const auto& participant = request.participant_id();
        if (!supervised(participant)) throw WorldError(wire::FORBIDDEN, "Fencing requires a registered supervisor");
        const auto binding = supervision_record(participant);
        if (binding.registration().registered_revision() != request.registered_revision() ||
                binding.revision() != request.supervision_revision())
            throw WorldError(wire::REVISION_CONFLICT, "Fence registration or supervisor revision changed");
        const auto active = activation(participant, request.generation());
        if (!active.active() || active.service_incarnation() != request.service_incarnation() ||
                active.registration().SerializeAsString() != binding.registration().SerializeAsString())
            throw WorldError(wire::UNAUTHORIZED, "Fence does not match the active registered generation");
        const auto authorized = launch_authorization(participant, request.service_incarnation());
        if (!authorized || authorized->generation() != request.generation() ||
                authorized->authorization_id() != request.authorization_id() ||
                authorized->request().launch_id() != request.launch_id() ||
                authorized->request().supervision_revision() != request.supervision_revision() ||
                authorized->supervisor_public_key() != binding.public_key() ||
                authorized->request().service().registration().SerializeAsString() != active.registration().SerializeAsString() ||
                authorized->request().service().issuer_incarnation() != active.issuer_incarnation() ||
                authorized->request().service().connection_binding() != active.connection_binding())
            throw WorldError(wire::UNAUTHORIZED, "Fence does not match consumed launch authorization");
        return binding;
    }
    v1::AuthorityFenceState fence(const v1::AuthorityFenceBegin& request, const std::string& key) {
        const auto binding = fence_context(request);
        if (binding.public_key() != key) throw WorldError(wire::UNAUTHORIZED, "Fence supervisor key changed during proof");
        Query prior(db.get(), "SELECT fence_id,security_revision,supervisor_public_key,request,state "
                             "FROM authority_fence WHERE participant_id=? AND generation=?");
        prior.blob(1, request.participant_id()); prior.integer(2, request.generation());
        if (prior.step() == SQLITE_ROW) {
            v1::AuthorityFenceState result;
            storage(result.ParseFromString(prior.blob(4)), "Invalid durable fence state");
            try { validate_message(result); }
            catch (const std::invalid_argument&) { throw WorldError(wire::STORAGE_ERROR, "Invalid durable fence fields"); }
            storage(prior.blob(0).size() == 32 && prior.integer(1) >= 0 &&
                    static_cast<std::uint64_t>(prior.integer(1)) <= security_revision() && prior.blob(2) == key &&
                    prior.blob(3) == request.SerializeAsString() && result.issuer_id() == identity.issuer_id() &&
                    result.registration().SerializeAsString() == binding.registration().SerializeAsString() &&
                    result.generation() == request.generation() && result.service_incarnation() == request.service_incarnation() &&
                    result.authorization_id() == request.authorization_id() && result.launch_id() == request.launch_id() &&
                    result.supervision_revision() == request.supervision_revision() && result.fence_id() == prior.blob(0) &&
                    result.security_revision() == static_cast<std::uint64_t>(prior.integer(1)) && result.supervisor_public_key() == key,
                    "Inconsistent durable fence identity");
            return result;
        }
        v1::AuthorityFenceState result; result.set_issuer_id(identity.issuer_id());
        *result.mutable_registration() = binding.registration(); result.set_generation(request.generation());
        result.set_service_incarnation(request.service_incarnation()); result.set_authorization_id(request.authorization_id());
        result.set_launch_id(request.launch_id()); result.set_supervision_revision(request.supervision_revision());
        result.set_fence_id(random_id()); result.set_security_revision(security_revision()); result.set_supervisor_public_key(key);
        Query insert(db.get(), "INSERT INTO authority_fence VALUES(?,?,?,?,?,?,?)");
        insert.blob(1, request.participant_id()); insert.integer(2, request.generation()); insert.blob(3, result.fence_id());
        insert.integer(4, result.security_revision()); insert.blob(5, key); insert.blob(6, request.SerializeAsString());
        insert.blob(7, result.SerializeAsString()); insert.step();
        return result;
    }
    v1::AuthoritySupervisorBinding launch_context(''')
edit('src/identity/issuer.cpp', 'v1::AuthorityActivationState Issuer::authority_activate(', '''v1::ChallengeReply Issuer::authority_fence_begin(const v1::AuthorityFenceBegin& request, IssuerConnection& connection) {
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Fence proof requires a TLS connection identity");
    Transaction transaction(impl_->db.get()); const auto binding = impl_->fence_context(request); transaction.commit();
    const auto nonce = impl_->nonce();
    v1::AuthorityFenceChallenge payload; payload.set_protocol_version(1); payload.set_issuer_id(impl_->identity.issuer_id());
    payload.set_issuer_incarnation(impl_->incarnation); payload.set_nonce(nonce); payload.set_connection_binding(connection.binding);
    *payload.mutable_request() = request; payload.set_supervisor_public_key(binding.public_key());
    payload.set_lifetime_ms(static_cast<std::uint32_t>(impl_->lifetime.count())); payload.set_audience("identityd/authority-fence/v1");
    v1::ChallengeReply result; result.set_nonce(nonce); result.mutable_signing_request()->set_purpose(v1::AUTHORITY_FENCING);
    result.mutable_signing_request()->set_payload(payload.SerializeAsString());
    Impl::Pending pending; pending.content = result.signing_request(); pending.expected_key = binding.public_key();
    pending.expires = Clock::now()+impl_->lifetime; pending.connection_binding = connection.binding; pending.fence = request;
    impl_->pending.emplace(nonce, std::move(pending)); return result;
}
v1::AuthorityFenceState Issuer::authority_fence_complete(const v1::AuthorityFenceComplete& request, IssuerConnection& connection) {
    validate_message(request); require(request.nonce().size() == 32 && request.has_proof(), "Fence nonce and proof are required");
    if (connection.binding.size() != 32) throw WorldError(wire::FORBIDDEN, "Fence proof requires a TLS connection identity");
    const auto pending = impl_->consume(request.nonce(), request.proof(), v1::AUTHORITY_FENCING);
    if (pending.connection_binding != connection.binding) throw WorldError(wire::UNAUTHORIZED, "Fence proof belongs to another connection");
    Transaction transaction(impl_->db.get()); const auto result = impl_->fence(pending.fence, pending.expected_key);
    transaction.commit(); return result;
}
v1::AuthorityActivationState Issuer::authority_activate(''')
edit('src/identity/issuer.cpp', '    if (!state.active()) throw WorldError(wire::UNAUTHORIZED, "Service generation is inactive");',
     '    if (!state.active() || impl_->fenced(context.registration().participant().id(), request.generation()))\n        throw WorldError(wire::UNAUTHORIZED, "Service generation is inactive or fenced");')
edit('src/identity/issuer.cpp', '    if (state.applied_revision() != request.applied_revision()) throw WorldError',
     '    if (impl_->fenced(context.registration().participant().id(), request.generation()))\n        throw WorldError(wire::UNAUTHORIZED, "Service generation is fenced");\n    if (state.applied_revision() != request.applied_revision()) throw WorldError')
print('Prepared isolated protocol, schema, issuer fencing, and service-mutation guards. Client and tests remain required.')
