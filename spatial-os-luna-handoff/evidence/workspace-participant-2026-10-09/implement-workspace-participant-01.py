from pathlib import Path
root=Path(__file__).resolve().parent/'participant-workspace-draft-01'
def edit(name,old,new):
    p=root/name;s=p.read_text(encoding='utf-8');assert s.count(old)==1,(name,old[:80],s.count(old));p.write_text(s.replace(old,new),encoding='utf-8',newline='\n')
edit('proto/spatial/workspace/v1/broker.proto','message WorkspaceAuthorityConfig {\n','message WorkspaceAuthorityConfig {\n  string participant_runtime_config = 4;\n')
edit('CMakeLists.txt','target_link_libraries(workspaced PRIVATE spatial-scene SQLite::SQLite3 spatial-authority spatial-issuer-watch-lib)','target_link_libraries(workspaced PRIVATE spatial-scene SQLite::SQLite3 spatial-authority spatial-participant-runtime-lib)')
edit('CMakeLists.txt','    add_test(NAME world-participant COMMAND', '''    add_test(NAME workspace-participant COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/core/test_workspace_participant.py"
        $<TARGET_FILE:identityd> $<TARGET_FILE:spatial-issuer-client> $<TARGET_FILE:worldd>)
    set_tests_properties(workspace-participant PROPERTIES TIMEOUT 180
        ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_BINARY_DIR}/python:${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME world-participant COMMAND''')
edit('src/workspace/authority.cpp','#include "identity/issuer_watch.hpp"','#include "identity/participant_runtime.hpp"')
edit('src/workspace/authority.cpp','std::unique_ptr<identity::IssuerWatcher> watcher;','std::unique_ptr<identity::ParticipantRuntime> participant;')
edit('src/workspace/authority.cpp','    bool ready = false, broken = false, changed = true;', '''    std::optional<std::uint64_t> consumed_revision;
    std::uint64_t acknowledged_revision = 0, generation = 0, command_sequence = 0, cancellation_sequence = 0;
    id::ParticipantCommand::OperationCase pending_operation = id::ParticipantCommand::OPERATION_NOT_SET;
    bool ready = false, broken = false, changed = true, closing = false, released = false;''')
edit('src/workspace/authority.cpp','        for (const auto& binding : config.bindings()) {','''        require(!config.participant_runtime_config().empty(), "Workspace participant configuration is required");
        std::vector<a::Domain> domains;
        for (const auto& binding : config.bindings()) {
            domains.push_back(domain(binding.workspace()));''')
edit('src/workspace/authority.cpp','        watcher = std::make_unique<identity::IssuerWatcher>(config.issuer_client_config());','        participant = std::make_unique<identity::ParticipantRuntime>(config.participant_runtime_config(), domains);')
edit('src/workspace/authority.cpp','''        while (!ready && !broken && Clock::now() < deadline) { tick(); std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
        require(ready && !broken, "Workspace revocation baseline is unavailable");''','''        // Construction has no owned jobs or control reservations to cancel.
        while (!ready && !broken && Clock::now() < deadline) {
            tick(); changed = false; settle(true); std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        require(ready && !broken, "Workspace participation is unavailable");''')
edit('src/workspace/authority.cpp','    void fail() { broken = true; active.clear(); invalidate(); }','    void fail() { broken = true; ++cancellation_sequence; active.clear(); invalidate(); }')
p=root/'src/workspace/authority.cpp';s=p.read_text(encoding='utf-8');start=s.index('    void tick() {');end=s.index('    id::IssuerReply exchange',start)
s=s[:start]+'''    void tick() {
        if (broken || !participant) { observer(); return; }
        if (participant->failed()) { fail(); observer(); return; }
        while (auto update = participant->poll()) {
            if (update->has_authenticated()) {
                if (update->authenticated().issuer_id() != issuer) { fail(); observer(); return; }
                continue;
            }
            if (!update->has_revocations()) {
                if (pending_operation == id::ParticipantCommand::OPERATION_NOT_SET || update->request_id() != command_sequence) {
                    fail(); observer(); return;
                }
                const auto expected = pending_operation; pending_operation = id::ParticipantCommand::OPERATION_NOT_SET;
                if (update->has_error()) {
                    if (expected == id::ParticipantCommand::kActivate && update->error().code() == wire::REVISION_CONFLICT) continue;
                    fail(); observer(); return;
                }
                if (expected == id::ParticipantCommand::kActivate && update->has_activated()) {
                    generation = update->activated().generation(); acknowledged_revision = update->activated().applied_revision(); ready = true;
                } else if (expected == id::ParticipantCommand::kApplied && update->has_applied()) {
                    acknowledged_revision = update->applied().applied_revision();
                } else if (expected == id::ParticipantCommand::kRelease && update->has_released()) released = true;
                else { fail(); observer(); return; }
                continue;
            }
            const auto& batch = update->revocations();
            if (batch.issuer_id() != issuer) { fail(); observer(); return; }
            observed_revision = std::max(observed_revision, batch.head_revision());
            consumed_revision = batch.through_revision();
            for (const auto& event : batch.events()) {
                ++cancellation_sequence;
                if (bootstrap_credential && event.target().kind() == id::CREDENTIAL_TARGET &&
                        event.target().id() == *bootstrap_credential) bootstrap_revoked = true;
                if (event.target().kind() == id::ACTOR_TARGET) {
                    revoked.insert(event.target().id()); active[event.target().id()] = false; invalidate();
                }
            }
        }
        observer();
    }
    void settle(bool effects_settled) {
        // Called only after dispatch, process reconciliation, and control cancellation.
        // A wait callback can consume another revision after reconciliation. changed blocks that acknowledgement.
        if (!effects_settled || changed || broken || released || !consumed_revision ||
                pending_operation != id::ParticipantCommand::OPERATION_NOT_SET) return;
        id::ParticipantCommand command;
        if (!ready) {
            if (*consumed_revision < observed_revision) return;
            command.mutable_activate()->set_synchronized_revision(*consumed_revision);
        } else if (acknowledged_revision < *consumed_revision) {
            auto* applied = command.mutable_applied(); applied->set_generation(generation);
            applied->set_from_revision(acknowledged_revision); applied->set_through_revision(acknowledged_revision+1);
        } else if (closing && *consumed_revision >= observed_revision) {
            command.mutable_release()->set_generation(generation);
            command.mutable_release()->set_applied_revision(acknowledged_revision);
        } else return;
        require(command_sequence < UINT64_MAX, "Workspace participant sequence exhausted");
        command.set_request_id(++command_sequence); pending_operation = command.operation_case();
        participant->submit(std::move(command));
    }
    void shutdown(const std::function<void()>& reconcile) {
        closing = true;
        const auto deadline = Clock::now()+std::max(client->timeout()*2, std::chrono::milliseconds(2000));
        while (!released && !broken && Clock::now() < deadline) {
            tick(); reconcile(); settle(true); std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!released) throw std::runtime_error("Workspace participant release remains unresolved");
    }
'''+s[end:];p.write_text(s,encoding='utf-8',newline='\n')
edit('src/workspace/authority.cpp','return !broken && actor.issuer_id()', 'return ready && !broken && !closing && actor.issuer_id()')
edit('src/workspace/authority.cpp','void WorkspaceAuthority::tick() { impl_->tick(); }','''void WorkspaceAuthority::tick() { impl_->tick(); }
void WorkspaceAuthority::settle(bool effects_settled) { impl_->settle(effects_settled); }
void WorkspaceAuthority::shutdown(const std::function<void()>& reconcile) { impl_->shutdown(reconcile); }
std::uint64_t WorkspaceAuthority::cancellation_sequence() const { return impl_->cancellation_sequence; }''')
edit('src/workspace/authority.hpp','    void tick();','''    void tick();
    void settle(bool effects_settled);
    void shutdown(const std::function<void()>& reconcile);
    std::uint64_t cancellation_sequence() const;''')
edit('src/workspace/control.hpp','    void tick();','''    void tick();
    bool cancellation_settled() const;
    bool empty() const { return leases_.empty(); }''')
edit('src/workspace/control.cpp','void ControlLeases::tick() {','''bool ControlLeases::cancellation_settled() const {
    for (const auto& [id, reservation] : leases_) {
        (void)id;
        if (reservation.revoked) return false;
    }
    return true;
}
void ControlLeases::tick() {''')
edit('src/workspace/store.hpp','    void reap();','    void reap();\n    void stop_all();')
edit('src/workspace/store.cpp','void ExecutionStore::reap() {','''void ExecutionStore::stop_all() {
    // Checked destruction cannot return after unconfirmed process termination.
    impl_->jobs.clear();
    Query q(impl_->db.get(), "UPDATE processes SET state='uncertain' WHERE state='running'"); q.step();
}
void ExecutionStore::reap() {''')
edit('src/workspace/main.cpp','                    spatial::workspace::v1::Request request; spatial::workspace::v1::Reply reply;','''                    spatial::workspace::v1::Request request; spatial::workspace::v1::Reply reply;
                    const auto cancellation = authority ? authority->cancellation_sequence() : 0;''')
edit('src/workspace/main.cpp','''                    enforce();
                    input.erase''','''                    enforce();
                    if (authority && cancellation != authority->cancellation_sequence() &&
                            !request.has_management() && !request.has_control_release()) {
                        reply.Clear(); reply.set_protocol_version(1); reply.set_request_id(request.request_id());
                        reply.mutable_error()->set_code(spatial::wire::UNAUTHORIZED);
                        reply.mutable_error()->set_message("Issuer revocation invalidated the in-flight broker result");
                    }
                    input.erase''')
edit('src/workspace/main.cpp','''                return 0;
            }
            pollfd fds''','''                store.stop_all();
                if (authority) {
                    if (!controls.empty()) throw std::runtime_error("Workspace control release remains unresolved");
                    authority->shutdown(enforce);
                }
                return 0;
            }
            if (authority) authority->settle(output.empty() && controls.cancellation_settled());
            pollfd fds''')
print('Workspace participation draft implemented')
