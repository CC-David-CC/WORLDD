import difflib
import hashlib
import json
from pathlib import Path
import tarfile

r=Path(__file__).resolve().parents[1]
base=r/'local/supervisor-world-handoff-draft-02'
manifest=json.loads((r/'local/supervisor-world-handoff-candidate-02.json').read_text())['files']
draft=r/'local/world-bootstrap-diagnostic-01';assert not draft.exists()
for n,digest in manifest.items():
    data=(base/n).read_bytes();assert hashlib.sha256(data).hexdigest()==digest,n
    (draft/n).parent.mkdir(parents=True,exist_ok=True);(draft/n).write_bytes(data)
name='src/identity/participant_runtime.cpp'
before=(draft/name).read_text(encoding='utf-8')
old='''        } catch (const std::exception&) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) fail_locked(core::UNAVAILABLE, "Authority participant channel failed; reconciliation is required", current_request);
'''
new='''        } catch (const std::exception& error) {
            std::lock_guard lock(mutex);
            if (!stop.stop_requested()) {
                std::cerr << "participant diagnostic: " << error.what() << " request=" << current_request << '\\n';
                fail_locked(core::UNAVAILABLE, "Authority participant channel failed; reconciliation is required", current_request);
            }
'''
assert before.count(old)==1
after=before.replace(old,new);(draft/name).write_bytes(after.encode())
changed={n:hashlib.sha256((draft/n).read_bytes()).hexdigest() for n in manifest}
assert [n for n in manifest if changed[n]!=manifest[n]]==[name]
record=dict(files=changed,base_files=manifest,diagnostic_only=True,changed_file=name,
    purpose='Expose the swallowed transport exception without changing deadlines, retries, authority behavior, or tests.')
(r/'local/world-bootstrap-diagnostic-01.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
with tarfile.open(r/'local/world-bootstrap-diagnostic-01.tar','w') as archive:
    for n in changed:archive.add(draft/n,arcname=n)
e=r/'evidence/world-bootstrap-runtime-failure-2026-10-09';(e/'scripts').mkdir(parents=True,exist_ok=True)
(e/'diagnostic-manifest-01.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
(e/'diagnostic-only-01.patch').write_bytes(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name,tofile='b/'+name)).encode())
print('Prepared one-file diagnostic source; all test assertions and deadlines unchanged.')
