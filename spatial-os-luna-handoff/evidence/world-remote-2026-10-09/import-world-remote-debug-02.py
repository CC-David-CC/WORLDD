import json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1]
dest=root/'evidence/world-remote-2026-10-09'
expected=json.loads((root/'local/world-remote-source-02.json').read_text())['files']
for host in ('sb16','sb16-2'):
    with tarfile.open(root/'local'/('world-remote-debug-'+host+'-02.tar.gz')) as archive:
        for member in archive.getmembers():
            name=pathlib.PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts and '\\' not in member.name
            assert name.parts[:2]==('candidate-02',host) and name.suffix in ('.log','.json')
            target=dest.joinpath(*name.parts);data=archive.extractfile(member).read()
            if target.exists():assert target.read_bytes()==data,member.name
            else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    folder=dest/'candidate-02'/host
    summary=json.loads((folder/'summary-debug.json').read_text())
    assert summary['host']==host and summary['scope']=='debug' and summary['source_files']==233
    result=summary['debug']
    assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped'))==(1089,62,51,151,0,0)
    assert expected==json.loads((folder/'source-manifest.json').read_text())['files']
    print(host,'complete Debug verified:1089 core,51 harness,151 P10;233 source hashes')
