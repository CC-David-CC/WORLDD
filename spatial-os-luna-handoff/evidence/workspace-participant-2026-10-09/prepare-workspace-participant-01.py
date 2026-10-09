import hashlib,json,pathlib
root=pathlib.Path(__file__).resolve().parents[1]
out=root/'local/participant-workspace-draft-01'
out.mkdir()
world=root/'local/participant-world-draft-01'
manifest=json.loads((root/'local/participant-world-candidate-03.json').read_text(encoding='utf-8'))['files']
for name,digest in manifest.items():
    data=(world/name).read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(data).hexdigest()==digest,name
    target=out/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
process=root/'local/process-termination-draft-01'
pm=json.loads((root/'local/process-termination-candidate-02.json').read_text(encoding='utf-8'))['files']
for name in ['src/workspace/process.cpp','src/workspace/process.hpp','src/testing/process_termination_probe.cpp','tests/core/test_process_termination.py']:
    data=(process/name).read_bytes().replace(b'\r\n',b'\n');assert hashlib.sha256(data).hexdigest()==pm[name]
    (out/name).write_bytes(data)
cmake=(out/'CMakeLists.txt').read_text(encoding='utf-8')
pc=(process/'CMakeLists.txt').read_text(encoding='utf-8')
start=pc.index('    add_executable(spatial-process-termination-probe ')
end=pc.index('    add_test(NAME identity-participant-barrier-client',start)
needle='    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)\n'
assert cmake.count(needle)==1
(out/'CMakeLists.txt').write_text(cmake.replace(needle,needle+pc[start:end]),encoding='utf-8',newline='\n')
print('Prepared isolated world03 + process02 source')
