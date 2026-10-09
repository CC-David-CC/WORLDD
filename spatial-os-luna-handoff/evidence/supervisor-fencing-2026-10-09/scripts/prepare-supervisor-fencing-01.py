"""Prepare an isolated coordinator correction without changing frozen candidates."""
import hashlib
import json
from pathlib import Path

r=Path(__file__).resolve().parents[1]
base=r/'local/authority-fencing-draft-01'
manifest=json.loads((r/'local/authority-fencing-candidate-01.json').read_text())['files']
draft=r/'local/supervisor-fencing-draft-01'
assert len(manifest)==292 and not draft.exists()
for n,digest in manifest.items():
    data=(base/n).read_bytes();assert hashlib.sha256(data).hexdigest()==digest,n
    (draft/n).parent.mkdir(parents=True,exist_ok=True);(draft/n).write_bytes(data)
def replace(name,before,after):
    path=draft/name;s=path.read_text(encoding='utf-8');assert s.count(before)==1,(name,before)
    path.write_bytes(s.replace(before,after).encode('utf-8'))
replace('proto/spatial/supervisor/v1/launch.proto',
    '  spatial.identity.v1.AuthorityLaunchAuthorization authorization = 9;\n',
    '  spatial.identity.v1.AuthorityLaunchAuthorization authorization = 9;\n  spatial.identity.v1.AuthorityFenceState fence = 10;\n')
replace('proto/spatial/supervisor/v1/launch.proto',
    '    AuthorityLaunchQuery pump_channel = 19;\n',
    '    AuthorityLaunchQuery pump_channel = 19;\n    AuthorityLaunchQuery fence = 20;\n')
replace('src/supervisor/launch.hpp',
    '    v1::AuthorityLaunchRecord stop();\n',
    '    v1::AuthorityLaunchRecord stop();\n    // Fence the exact authorized generation. This does not retire issuer obligations.\n    v1::AuthorityLaunchRecord fence();\n')
replace('src/supervisor/launch.cpp',
    '    require(!binding.supervised() || value.stage() != v1::START_ATTEMPTED || value.has_authorization(),',
    '''    if (value.has_fence()) {
        const auto& fence = value.fence();
        const auto& approval = value.authorization();
        const auto& request = approval.request();
        require(value.has_authorization() && binding.supervised() && value.stage() >= v1::START_ATTEMPTED &&
            request.previous_generation() < INT64_MAX && fence.issuer_id() == binding.issuer_id() &&
            fence.registration().SerializeAsString() == request.service().registration().SerializeAsString() &&
            fence.generation() == request.previous_generation()+1 &&
            fence.service_incarnation() == binding.service_incarnation() &&
            fence.authorization_id() == approval.authorization_id() && fence.launch_id() == process.launch_id() &&
            fence.supervision_revision() == request.supervision_revision() &&
            fence.supervisor_public_key() == approval.supervisor_public_key() &&
            fence.fence_id().size() == 32 && fence.security_revision() <= INT64_MAX,
            "Invalid durable authority fence");
    }
    require(!binding.supervised() || value.stage() != v1::START_ATTEMPTED || value.has_authorization(),''')
replace('src/supervisor/launch.cpp',
    '    std::optional<identity::v1::AuthorityAuthenticationResult> authenticated;\n',
    '    std::optional<identity::v1::AuthorityAuthenticationResult> authenticated;\n    std::optional<v1::AuthorityLaunchAuthorize> authorization_config;\n')
replace('src/supervisor/launch.cpp',
    '    impl_->persist(next); // The remote approval alone never releases child execution.\n',
    '    impl_->persist(next); // The remote approval alone never releases child execution.\n    impl_->authorization_config = request; // Private paths stay in this live supervisor only.\n')
replace('src/supervisor/launch.cpp',
    'void AuthorityLaunch::submit_participant(identity::v1::ParticipantCommand command) {',
    '''v1::AuthorityLaunchRecord AuthorityLaunch::fence() {
    require(impl_->record.binding().supervised() && impl_->record.has_authorization() &&
        impl_->record.stage() >= v1::START_ATTEMPTED && impl_->process && impl_->native &&
        impl_->authorization_config, "Fencing requires retained authorized launch ownership");
    if (impl_->record.has_fence()) return impl_->record;
    impl_->stopping = true;
    impl_->relay.reset(); impl_->bootstrap.clear();
    if (impl_->relay_child.fd >= 0) close(std::exchange(impl_->relay_child.fd, -1));
    impl_->participant.reset();
    const auto& approval = impl_->record.authorization();
    const auto& authorized = approval.request();
    require(authorized.previous_generation() < INT64_MAX, "Fence candidate generation overflows");
    auto key = identity::KeyPair::load(impl_->authorization_config->private_key_path());
    require(key.public_key() == approval.supervisor_public_key(), "Fence signing key differs from owned authorization");
    identity::IssuerClient client(impl_->authorization_config->issuer_client_config());
    identity::v1::IssuerRequest begin; begin.set_protocol_version(1);
    auto* request = begin.mutable_authority_fence_begin();
    const auto& binding = impl_->record.binding();
    request->set_participant_id(binding.participant_id());
    request->set_registered_revision(binding.registered_revision());
    request->set_generation(authorized.previous_generation()+1);
    request->set_service_incarnation(binding.service_incarnation());
    request->set_launch_id(impl_->record.process().launch_id());
    request->set_supervision_revision(authorized.supervision_revision());
    request->set_authorization_id(approval.authorization_id());
    auto result = client.call(std::move(begin));
    require(result.has_challenge(), "Issuer rejected owned authority fence challenge");
    identity::v1::IssuerRequest complete; complete.set_protocol_version(1);
    auto* proof = complete.mutable_authority_fence_complete();
    proof->set_nonce(result.challenge().nonce());
    *proof->mutable_proof() = key.sign(result.challenge().signing_request());
    result = client.call(std::move(complete));
    require(result.has_authority_fence(), "Issuer did not fence the owned authority launch");
    auto next = impl_->record; *next.mutable_fence() = result.authority_fence();
    impl_->persist(next); // Full binding validation precedes the durable local write.
    return impl_->record;
}
void AuthorityLaunch::submit_participant(identity::v1::ParticipantCommand command) {''')
replace('src/supervisor/main.cpp',
    '                    require(request.has_inspect() || request.has_stop(), "Supervisor supports only inspect and stop");\n                    stop = request.has_stop();',
    '                    require(request.has_inspect() || request.has_stop() || request.has_fence(), "Supervisor supports only inspect, stop, and fence");\n                    stop = request.has_stop() || request.has_fence();')
replace('src/supervisor/main.cpp',
    '                    // Cleanup errors are fatal and cannot resume child routing.\n',
    '                    // Cleanup errors are fatal and cannot resume child routing.\n                    if (request.has_fence()) launch->fence();\n')
replace('src/testing/authority_launch_probe.cpp',
    '                else if (request.has_stop()) *reply.mutable_view()->mutable_record() = launch.stop();\n',
    '                else if (request.has_stop()) *reply.mutable_view()->mutable_record() = launch.stop();\n                else if (request.has_fence()) *reply.mutable_view()->mutable_record() = launch.fence();\n')
print('Prepared isolated coordinator fencing implementation. Tests and qualification remain required.')
