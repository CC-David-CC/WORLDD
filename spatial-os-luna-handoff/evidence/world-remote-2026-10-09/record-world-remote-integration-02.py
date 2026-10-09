"""Write completion records only after both final profiles and guarded source integration."""
import hashlib,json,pathlib,shutil
root=pathlib.Path(__file__).resolve().parents[1];evidence=root/'evidence/world-remote-2026-10-09'
applied=json.loads((root/'local/world-remote-integration-applied-02.json').read_text())
for name,digest in applied['files'].items():
    assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
for host in ('sb16','sb16-2'):
    summary=json.loads((evidence/'candidate-02'/host/'summary-final.json').read_text())
    for profile in ('debug','runtime-sanitize'):
        v=summary[profile]
        assert tuple(v[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped'))==(1089,62,51,151,0,0)
section='''## 2026-10-09: Remote actor transport qualified and integrated

The world daemon now accepts explicitly configured actor connections through mutual TLS 1.3.
Signed actor proof, world admission, and scoped authority remain separate from transport access.
Local and remote actors use the same protobuf messages and production actor handlers.
The trusted management pipe stays private.
Remote observations omit live application pixels and metadata. Remote application input remains outside M1 scope.

Both hosts passed complete Debug and runtime ASan/UBSan qualification for candidate 02.
Each profile passed 1,089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
No final profile failed or skipped a case.
Each core total includes 46 remote checks across seven suites.
Direct cross-host TLS scenarios passed with each host acting as world authority in turn.
The scenarios used independent actor identities, real motion and inspection, and durable checkpoints.

All 233 integrated source hashes match both qualified host manifests.
The schema audit preserves 227 messages, 849 fields, and 111 enum values.
All 80 existing test files and 41 locked files remain unchanged.
Primary review corrected TLS read-retry deadline handling before qualification.
The original retry counterexamples and fixture failures remain preserved.
Commit and host adoption follow this guarded source integration.

The remote actor transport component is complete. Full M01, M02, and M05 remain incomplete.
Distributed cancellation acknowledgement is the next required component.
A real delayed-notification diagnostic confirms that issuer commit alone is not cancellation completion.
See tasks/m1/identity.revocation_barrier.json and evidence/revocation-barrier-2026-10-09.
Physical devices, native-server GLX sanitizers, GPU performance, and complete Spatial-M1 remain unqualified.
No operator authority database or normal login session changed.
See evidence/world-remote-2026-10-09/candidate-02 for all four complete profiles and source pins.

'''
p=root/'CURRENT_STATE.md';s=p.read_text();assert '## 2026-10-09: Remote actor transport qualified and integrated' not in s
p.write_text(s.replace('# Implementation status\n\n','# Implementation status\n\n'+section,1),encoding='utf-8',newline='\n')
p=root/'docs/MILESTONES.md';s=p.read_text();start=s.index('Remote actor candidate 02 passed complete Debug qualification');end=s.index('Shared worlds, world transitions',start)
s=s[:start]+'''Remote actor transport is qualified and integrated from candidate 02.
Both hosts passed complete Debug and runtime ASan/UBSan profiles.
Each profile passed 1,089 core checks across 62 suites, 51 harness checks, and 151 P10 cases.
No final case failed or skipped. Direct cross-host actor scenarios passed in both directions.
Distributed cancellation acknowledgement remains required. A real delayed-event diagnostic reproduces the gap.
'''+s[end:];p.write_text(s,encoding='utf-8',newline='\n')
p=root/'tasks/m1/identity.world_session.json';d=json.loads(p.read_text());d.update(status='complete',current_step='qualified_and_integrated_commit_and_host_adoption_pending')
d['current_evidence']=[
'Both hosts passed complete Debug and runtime ASan/UBSan:1089 core/62 suites,51 harness,151 P10 per profile;zero failed/skipped.',
'Both direct cross-host TLS actor scenarios passed with distinct identities, actual movement, and durable world checkpoints.',
'All233 integrated source hashes match both host manifests. All80 existing test files and41 locked files remain unchanged.',
'46 remote checks cover functional access, transport security, lifecycle, native observation, delayed issuer races, listener configuration, and TLS retry deadlines.'
];d['limitations']=[
'Remote transport completion does not complete M01, M02, M05, world transitions, or Spatial-M1.',
'Issuer commit replies do not prove distributed cancellation completion. The separate barrier requirement remains open.',
'Native application witnesses use the qualified private XLibre software display. Physical GPU and native-server GLX sanitizer qualification remain separate.',
'Remote live application streaming and remote application input remain outside M1 scope.'
];d['final_evidence']='evidence/world-remote-2026-10-09/candidate-02';p.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8',newline='\n')
p=root/'docs/WORLD_IDENTITY.md';s=p.read_text();s=s.replace('# Authenticated local world sessions','# Authenticated world sessions',1)
s=s.replace('It supplies local actor channels, fresh authentication, and live credential revocation handling.\nWorld networking, delegated grants, and full workspace and application authority remain separate required milestones.',
'It supplies local and remote actor channels, fresh authentication, and credential revocation handling.\nFull shared-world workflows, distributed cancellation acknowledgement, and complete workspace authority remain required milestones.')
s=s.replace('Remote actor transport remains required for shared-world networking.','Remote actor transport uses the explicit TLS listener described below.')
marker='## Fresh authentication\n'
remote='''## Remote actor channels

The supervisor supplies an inherited listening TCP socket through `--actor-listen-fd FD`.
It also supplies `--actor-tls-config ABSOLUTE_FILE` and the existing `--identity-config ABSOLUTE_FILE`.
The TLS listener may coexist with inherited local actor channels.
The listener cannot share a descriptor with an actor channel or the workspace broker.
Do not expose the trusted stdin/stdout management channel.

Use an owned WorldActorTlsConfig textproto file with protocol_version 1.
Specify absolute certificate_path, private_key_path, and client_ca_path values.
Keep each material file in an owned private directory.
The loader rejects unsafe ownership, symlink files, and certificates writable by other users.
It rejects private keys accessible by group or other users.
TLS 1.3 authenticates both transport peers.
Clients must verify the configured server identity and trust chain.
A valid transport certificate supplies no actor identity, world admission, or workspace grant.
Actors still complete the signed proof exchange below.

The optional io_timeout_ms accepts 50 through 60000 milliseconds. Its default is 5000.
The optional idle_timeout_ms accepts 50 through 300000 milliseconds. Its default is 60000.
The idle timeout applies only before actor authentication.
Authenticated idle actors remain connected without a periodic actor request.
The optional connection_limit accepts one through 128 connections. Its default is 32.
The total includes inherited local actor channels. Local inherited channels remain limited to 32.
Handshake, partial-frame, pending-write, and TLS read-retry deadlines remain bounded.
Repeated retries do not refresh a pending read-retry deadline.
The protobuf payload limit remains one MiB.
Slow or broken peers lose only their affected actor channel unless shared authority itself fails.

Remote channels use the existing WorldSessionRequest and WorldSessionReply messages.
They enter the same actor engine and authorization checks as local channels.
World entry does not create or select a workspace.
Remote geometry observation omits live application surfaces, pixels, and application metadata.
Remote application key, pointer, and scroll actions return UNIMPLEMENTED.
An authorized local reconnection restores local application observation through the normal checks.
M1 does not require remote live application streaming or remote application input.

'''
assert marker in s;s=s.replace(marker,remote+marker,1)
s += '''
## Remote transport qualification

Candidate 02 passed complete Debug and runtime ASan/UBSan profiles on sb16 and sb16-2.
Each profile passed 1,089 core checks, 51 harness checks, and all 151 P10 cases.
The seven remote suites include 46 checks. No final case failed or skipped.
Direct LAN scenarios passed with each host acting as authority for actors on both hosts.
See evidence/world-remote-2026-10-09/candidate-02 and the crosshost-02 and crosshost-03 records.
Run the focused suites with `ctest --test-dir BUILD -V -R '^world-remote($|-)'`.
Use the qualified private XLibre server through SPATIAL_TEST_XSERVER for native witnesses.
Focused results do not replace the complete profile or physical hardware acceptance.
'''
p.write_text(s,encoding='utf-8',newline='\n')
p=root/'docs/IDENTITY.md';s=p.read_text()
s=s.replace('Authenticated local world channels and native credential cancellation are qualified.',
    'Authenticated local and remote world channels and native credential cancellation are qualified.')
s=s.replace('Authenticated bootstrap delivery remains an isolated candidate under qualification. See BOOTSTRAP_DELIVERY.md for its current status.',
    'Authenticated bootstrap delivery is qualified, integrated, and adopted on both hosts. See BOOTSTRAP_DELIVERY.md.')
s=s.replace('Remote actor transport and distributed revocation acknowledgement remain required.',
    'Remote actor transport is qualified and integrated. Distributed revocation acknowledgement remains required.')
s=s.replace('Worldd now integrates these records through authenticated local actor channels.',
    'Worldd integrates these records through authenticated local and remote actor channels.')
s=s.replace('Authenticated bootstrap delivery must finish both-host qualification before production integration.',
    'Authenticated bootstrap delivery passed both-host qualification before production integration.')
p.write_text(s,encoding='utf-8',newline='\n')
p=root/'tasks/m1/identity.bootstrap_profile.json';d=json.loads(p.read_text())
d['limitations']=[
    'This completes authenticated enrollment delivery, not the full M01 identity milestone.',
    'Remote authenticated world transport is separately qualified and integrated. Distributed revocation acknowledgement remains required.',
    'M01, M02, native-server GLX sanitizers, and Spatial-M1 remain incomplete.'
]
p.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8',newline='\n')
p=root.parent/'M01_WORLD_REMOTE_DEBUG.txt';s=p.read_text();s=s.replace('Candidate 02 remains isolated. The production remote actor listener is not integrated yet.\nBoth hosts passed complete Debug qualification:',
'Candidate 02 is qualified and integrated. Commit and host adoption follow this integration.\nBoth hosts passed complete Debug and runtime ASan/UBSan qualification. Each profile passed:')
s=s.replace('No case failed or skipped.\nBoth hosts are running the existing runtime ASan/UBSan qualification jobs.','No final profile failed or skipped.')
s=s.replace('Complete and audit both runtime sanitizer profiles before production integration.','Complete the reviewed commit and verify both hosts adopt the exact qualified source.')
p.write_text(s,encoding='utf-8',newline='\n')
shutil.copyfile(root/'local/audit-integrated-world-remote-02.py',evidence/'audit-integrated-world-remote-02.py')
shutil.copyfile(root/'local/adopt-world-remote-02.py',evidence/'adopt-world-remote-02.py')
shutil.copyfile(root/'local/record-world-remote-integration-02.py',evidence/'record-world-remote-integration-02.py')
shutil.copyfile(root/'local/world-remote-integration-applied-02.json',evidence/'candidate-02/integration-applied.json')
p=evidence/'candidate-review-02.txt';s=p.read_text()
s=s.replace('Candidate 02 remains outside production. Both active qualification sources are immutable.',
    'Final status: candidate 02 passed both complete profiles on both hosts and is integrated.\nThe frozen qualification sources remain immutable. Commit and host adoption follow integration.')
s=s.replace('No Debug case failed or skipped. Runtime sanitizer qualification remains active through the original runner handles.\nThe runtime sanitizer profiles require the same totals before integration.',
    'Both runtime sanitizer profiles passed with the same totals. No final profile failed or skipped.')
s=s.replace('Finish and audit all complete profiles on both hosts.\nIntegrate only the exact reviewed and qualified candidate.',
    'All four complete profiles and both cross-host scenarios passed. Integration matched all 233 qualified source hashes.\nCommit and verify host adoption of that exact source.')
p.write_text(s,encoding='utf-8',newline='\n')
print('Recorded verified component integration; commit and host adoption remain pending')
