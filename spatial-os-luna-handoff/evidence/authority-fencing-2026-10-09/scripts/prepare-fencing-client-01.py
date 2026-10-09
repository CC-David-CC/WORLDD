"""Add independently checked fencing responses to the isolated production client."""
from pathlib import Path
import json

r = Path(__file__).resolve().parents[1]
d = r / 'local/authority-fencing-draft-01'
def edit(name, before, after):
    p = d / name; t = p.read_text(); assert t.count(before) == 1, (name, before[:80])
    p.write_bytes(t.replace(before, after).encode())

edit('src/identity/issuer_client.cpp', 'request.has_authority_authentication_begin() || request.has_authority_launch_begin())',
     'request.has_authority_authentication_begin() || request.has_authority_launch_begin() || request.has_authority_fence_begin())')
edit('src/identity/issuer_client.cpp', '        (request.has_authority_launch_complete() && reply.has_authority_launch_authorization()) ||',
     '        (request.has_authority_launch_complete() && reply.has_authority_launch_authorization()) ||\n        (request.has_authority_fence_complete() && reply.has_authority_fence()) ||')
edit('src/identity/issuer_client.cpp', '    struct LaunchChallenge {', '''    struct FenceChallenge {
        v1::SignRequest signing_request;
        v1::AuthorityFenceChallenge context;
        Clock::time_point expires;
    };
    std::map<std::string, FenceChallenge> fence_challenges;
    void remember_fence_challenge(const v1::AuthorityFenceBegin& request, const v1::ChallengeReply& reply, Clock::time_point sent) {
        require(reply.nonce().size() == 32 && reply.has_signing_request() &&
                reply.signing_request().purpose() == v1::AUTHORITY_FENCING && reply.signing_request().has_payload(),
                "Invalid fence challenge envelope");
        FenceChallenge pending; pending.signing_request = reply.signing_request(); auto& value = pending.context;
        require(value.ParseFromString(pending.signing_request.payload()), "Invalid fence challenge payload");
        try { validate_message(value); validate_fence_begin(request); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid fence challenge fields"); }
        require(value.protocol_version() == 1 && value.issuer_id() == config.issuer_id() &&
                value.issuer_incarnation().size() == 32 && value.nonce() == reply.nonce() &&
                value.connection_binding().size() == 32 && value.has_request() &&
                value.request().SerializeAsString() == request.SerializeAsString() && value.supervisor_public_key().size() == 32 &&
                value.supervisor_public_key() != config.issuer_public_key() && value.lifetime_ms() >= 50 && value.lifetime_ms() <= 60000 &&
                value.audience() == "identityd/authority-fence/v1", "Invalid fence challenge context");
        require((issuer_incarnation.empty() || issuer_incarnation == value.issuer_incarnation()) &&
                (launch_binding.empty() || launch_binding == value.connection_binding()) &&
                (service_binding.empty() || service_binding == value.connection_binding()), "Fence challenge changed connection context");
        require(!fence_challenges.contains(reply.nonce()), "Duplicate fence challenge nonce");
        std::erase_if(fence_challenges, [](const auto& item) { return item.second.expires <= Clock::now(); });
        require(fence_challenges.size() < 256, "Too many fence challenges");
        pending.expires = sent + std::chrono::milliseconds(value.lifetime_ms());
        require(Clock::now() < pending.expires, "Fence challenge expired in transit");
        issuer_incarnation = value.issuer_incarnation(); launch_binding = value.connection_binding();
        fence_challenges.emplace(reply.nonce(), std::move(pending));
    }
    void check_fence_result(const v1::AuthorityFenceComplete& request, const v1::AuthorityFenceState& result,
                            const std::optional<FenceChallenge>& pending) {
        require(pending && Clock::now() < pending->expires, "Unrequested or expired fence success");
        const auto& expected = pending->context;
        const auto& target = expected.request();
        try { validate_participant_record(result.registration(), config.issuer_id()); }
        catch (const std::invalid_argument&) { throw std::runtime_error("Invalid fence registration"); }
        require(result.issuer_id() == config.issuer_id() && result.registration().participant().id() == target.participant_id() &&
                result.registration().registered_revision() == target.registered_revision() && result.generation() == target.generation() &&
                result.service_incarnation() == target.service_incarnation() && result.authorization_id() == target.authorization_id() &&
                result.launch_id() == target.launch_id() && result.supervision_revision() == target.supervision_revision() &&
                result.fence_id().size() == 32 && result.security_revision() <= INT64_MAX &&
                result.supervisor_public_key() == expected.supervisor_public_key() &&
                result.supervisor_public_key() != result.registration().participant().public_key(), "Invalid fence result context");
        try {
            v1::VerifyRequest verification; *verification.mutable_content() = pending->signing_request;
            *verification.mutable_proof() = request.proof(); verification.set_expected_public_key(expected.supervisor_public_key());
            require(verify(verification), "Fence accepted an invalid supervisor proof");
        } catch (const std::invalid_argument&) { throw std::runtime_error("Invalid fence proof"); }
    }
    struct LaunchChallenge {''')
edit('src/identity/issuer_client.cpp', '        std::optional<LaunchChallenge> launch_pending;', '''        std::optional<FenceChallenge> fence_pending;
        if (request.has_authority_fence_complete()) {
            auto node = fence_challenges.extract(request.authority_fence_complete().nonce());
            if (!node.empty()) fence_pending = std::move(node.mapped());
        }
        std::optional<LaunchChallenge> launch_pending;''')
edit('src/identity/issuer_client.cpp', '            if (request.has_authority_launch_begin()) remember_launch_challenge',
     '            if (request.has_authority_fence_begin()) remember_fence_challenge(request.authority_fence_begin(), reply.challenge(), sent);\n            if (request.has_authority_fence_complete()) check_fence_result(request.authority_fence_complete(), reply.authority_fence(), fence_pending);\n            if (request.has_authority_launch_begin()) remember_launch_challenge')

# These existing migration fixtures intentionally reconstruct older schemas.
# The new table must be removed before its referenced activation and authorization tables.
changed = []
for p in (d / 'tests/core').glob('test_*.py'):
    t = p.read_text()
    if 'DROP TABLE authority_launch_authorization;' not in t:
        continue
    t = t.replace('DROP TABLE authority_launch_authorization;', 'DROP TABLE authority_fence; DROP TABLE authority_launch_authorization;')
    # Existing assertions about the latest successful issuer schema follow the explicit version change.
    t = t.replace("fetchone()[0],8)", "fetchone()[0],9)")
    p.write_bytes(t.encode()); changed.append(p.relative_to(d).as_posix())
assert len(changed) == 7, changed
(r / 'local/fencing-migration-fixture-changes-01.json').write_bytes((json.dumps(dict(
    files=changed, reason='Explicit version9 schema adds authority_fence. Historical fixtures remove that table before reconstructing versions1 through7.',
    preserved='All existing test methods and row/key/rollback assertions. Latest successful version assertion changes8 to9.',
    acceptance_lock_changed=False), indent=2) + '\n').encode())
print('Prepared fencing client validation and recorded seven historical migration-fixture updates.')
