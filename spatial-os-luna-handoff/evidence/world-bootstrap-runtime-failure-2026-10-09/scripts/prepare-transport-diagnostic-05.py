"""Freeze passive transport diagnostics on the current294 candidate."""
import difflib
import hashlib
import json
from pathlib import Path
import tarfile
r=Path(__file__).resolve().parents[1]
base=r/'local/supervisor-crash-fencing-draft-01'
manifest=json.loads((r/'local/supervisor-crash-fencing-candidate-01.json').read_text())['files']
draft=r/'local/transport-diagnostic-draft-05';assert not draft.exists()
for name,digest in manifest.items():
    data=(base/name).read_bytes();assert hashlib.sha256(data).hexdigest()==digest,name
    (draft/name).parent.mkdir(parents=True,exist_ok=True);(draft/name).write_bytes(data)
def replace(name,old,new):
    p=draft/name;value=p.read_text(encoding='utf-8');assert value.count(old)==1,(name,old)
    p.write_bytes(value.replace(old,new).encode())
name='src/identity/participant_runtime.cpp'
replace(name,'''        } catch (const std::exception&) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) fail_locked(core::UNAVAILABLE, "Authority participant channel failed; reconciliation is required", current_request);
''','''        } catch (const std::exception& error) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) {
                std::cerr << "participant diagnostic: " << error.what() << " request=" << current_request << '\\n';
                fail_locked(core::UNAVAILABLE, "Authority participant channel failed; reconciliation is required", current_request);
            }
''')
name='src/identity/issuer_client.cpp'
replace(name,'#include <sstream>\n','#include <sstream>\n#include <iostream>\n#include <syncstream>\n')
replace(name,'    bool broken = false, busy = false;\n', '''    bool broken = false, busy = false;
    const char* diagnostic_stage = "startup";
    int diagnostic_operation = 0, diagnostic_ssl_error = 0, diagnostic_errno = 0;
    void diagnostic(const char* reason) {
        sockaddr_in local{}; socklen_t size = sizeof(local);
        const bool ipv4 = getsockname(socket.value, reinterpret_cast<sockaddr*>(&local), &size) == 0 && local.sin_family == AF_INET;
        const auto now = Clock::now();
        std::osyncstream(std::cerr) << "issuer-client diagnostic: reason=" << reason
            << " operation=" << diagnostic_operation << " sequence=" << sequence
            << " stage=" << diagnostic_stage << " ssl_error=" << diagnostic_ssl_error
            << " errno=" << diagnostic_errno << " local_port=" << (ipv4 ? ntohs(local.sin_port) : 0)
            << " late_ms=" << std::chrono::duration_cast<std::chrono::milliseconds>(now-deadline).count()
            << " time_ms=" << std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count()
            << " pending=" << (tls ? SSL_pending(tls.get()) : -1)
            << " buffered=" << (tls ? SSL_has_pending(tls.get()) : -1) << '\\n';
    }
''')
replace(name,'        require(Clock::now() < deadline, "Issuer client deadline exceeded");\n',
    '        if (Clock::now() >= deadline) diagnostic("deadline");\n'
    '        require(Clock::now() < deadline, "Issuer client deadline exceeded");\n')
replace(name,'        else throw std::runtime_error("Issuer TLS connection failed");\n',
    '''        else {
            diagnostic_ssl_error = error; diagnostic_errno = errno;
            diagnostic("tls-fatal");
            throw std::runtime_error("Issuer TLS connection failed");
        }
''')
replace(name,'        const auto sent = Clock::now();\n',
    '        diagnostic_operation = static_cast<int>(request.operation_case());\n'
    '        const auto sent = Clock::now();\n')
replace(name,'''        transfer(bytes.data(), bytes.size(), true);
        std::array<unsigned char, 4> header{}; transfer(reinterpret_cast<char*>(header.data()), header.size(), false);
''','''        diagnostic_stage = "write-request";
        transfer(bytes.data(), bytes.size(), true);
        diagnostic_stage = "read-header";
        std::array<unsigned char, 4> header{}; transfer(reinterpret_cast<char*>(header.data()), header.size(), false);
''')
replace(name,'        bytes.resize(length); transfer(bytes.data(), bytes.size(), false);\n',
    '        diagnostic_stage = "read-payload";\n'
    '        bytes.resize(length); transfer(bytes.data(), bytes.size(), false);\n'
    '        diagnostic_stage = "validate-reply";\n')
replace(name,'        } catch (...) { busy = false; poison(); throw; }\n',
    '''        } catch (const std::exception& error) {
            diagnostic(error.what()); busy = false; poison(); throw;
        } catch (...) { busy = false; poison(); throw; }
''')
name='src/identity/tls_server.cpp'
replace(name,'#include <sstream>\n','#include <sstream>\n#include <iostream>\n')
replace(name,'    std::size_t offset = 0;\n',
    '    std::size_t offset = 0;\n'
    '    int diagnostic_ssl_error = 0, diagnostic_errno = 0, diagnostic_peer_port = 0;\n')
replace(name,'        SSL_set_accept_state(tls.get());\n',
    '''        SSL_set_accept_state(tls.get());
        sockaddr_in peer{}; socklen_t size = sizeof(peer);
        if (getpeername(fd, reinterpret_cast<sockaddr*>(&peer), &size) == 0 && peer.sin_family == AF_INET)
            diagnostic_peer_port = ntohs(peer.sin_port);
''')
replace(name,'        // SSL_free then close; do not call SSL_shutdown after fatal TLS errors.\n',
    '        diagnostic_ssl_error = error; diagnostic_errno = errno;\n'
    '        // SSL_free then close; do not call SSL_shutdown after fatal TLS errors.\n')
replace(name,'    void serve(Issuer& issuer) {\n        for (;;) {\n',
    '''    void serve(Issuer& issuer) {
        auto diagnostic_previous = Clock::now();
        std::int64_t diagnostic_max_gap = 0;
        for (;;) {
            const auto diagnostic_now = Clock::now();
            const auto diagnostic_gap = std::chrono::duration_cast<Milliseconds>(diagnostic_now-diagnostic_previous).count();
            diagnostic_max_gap = std::max(diagnostic_max_gap, diagnostic_gap);
            diagnostic_previous = diagnostic_now;
''')
replace(name,'                if (!keep) peers.erase(peers.begin()+static_cast<std::ptrdiff_t>(i-1));\n',
    '''                if (!keep) {
                    const auto now = Clock::now();
                    std::cerr << "issuer-server diagnostic: close phase=" << static_cast<int>(peer.phase)
                        << " peer_port=" << peer.diagnostic_peer_port << " events=" << events
                        << " wanted=" << peer.wanted << " offset=" << peer.offset
                        << " payload_size=" << peer.payload.size() << " ssl_error=" << peer.diagnostic_ssl_error
                        << " errno=" << peer.diagnostic_errno << " peers=" << peers.size()
                        << " late_ms=" << std::chrono::duration_cast<Milliseconds>(now-peer.deadline).count()
                        << " loop_gap_ms=" << diagnostic_gap << " max_loop_gap_ms=" << diagnostic_max_gap
                        << " time_ms=" << std::chrono::duration_cast<Milliseconds>(now.time_since_epoch()).count()
                        << " pending=" << SSL_pending(peer.tls.get()) << " buffered=" << SSL_has_pending(peer.tls.get()) << '\\n';
                    peers.erase(peers.begin()+static_cast<std::ptrdiff_t>(i-1));
                }
''')
replace(name,'                if (peers.size() >= limit) { close(fd); continue; }\n',
    '''                if (peers.size() >= limit) {
                    std::cerr << "issuer-server diagnostic: admission-limit peers=" << peers.size() << " limit=" << limit << '\\n';
                    close(fd); continue;
                }
''')
after={n:hashlib.sha256((draft/n).read_bytes()).hexdigest() for n in manifest}
changed=[n for n in manifest if after[n]!=manifest[n]]
assert set(changed)=={'src/identity/participant_runtime.cpp','src/identity/issuer_client.cpp','src/identity/tls_server.cpp'}
assert all(after[n]==v for n,v in manifest.items() if n.startswith(('tests/','proto/')))
record=dict(files=after,base_files=manifest,changed=changed,diagnostic_only=True,
    purpose='Classify transport failures without changing deadlines, retries, authorization, or test assertions.',
    logged_fields='Numeric operation, sequence, TLS error, I/O stage, socket port, deadline lag, peer state, loop gap, and buffering flags.',
    excludes='No credentials, actor IDs, payloads, private key paths, or model data are logged.',
    reference='https://docs.openssl.org/3.0/man3/SSL_pending/',
    caution='SSL_pending and SSL_has_pending answer different questions. Buffering is a hypothesis, not a confirmed cause.')
(r/'local/transport-diagnostic-05.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
with tarfile.open(r/'local/transport-diagnostic-05.tar','w') as archive:
    for n in after:archive.add(draft/n,arcname=n)
e=r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
(e/'transport-diagnostic-manifest-05.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
patch=[]
for n in changed:
    patch.extend(difflib.unified_diff((base/n).read_text(encoding='utf-8').splitlines(True),
        (draft/n).read_text(encoding='utf-8').splitlines(True),fromfile='a/'+n,tofile='b/'+n))
(e/'transport-diagnostic-only-05.patch').write_bytes(''.join(patch).encode())
print('Prepared passive three-file transport diagnostics on frozen294 source. Tests and schemas unchanged.')
