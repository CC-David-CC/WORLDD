import argparse,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1];dest=root/'evidence/world-remote-2026-10-09'
parser=argparse.ArgumentParser();parser.add_argument('host',choices=['sb16','sb16-2']);host=parser.parse_args().host
with tarfile.open(root/'local'/('world-remote-final-'+host+'-02.tar.gz')) as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts and '\\' not in member.name
        assert name.parts[:2]==('candidate-02',host) and name.suffix in ('.log','.json')
        target=dest.joinpath(*name.parts);data=archive.extractfile(member).read()
        if target.exists():assert target.read_bytes()==data,member.name
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
summary=json.loads((dest/'candidate-02'/host/'summary-final.json').read_text())
assert summary['source_files']==233 and summary['host']==host and summary['scope']=='final'
for profile in ('debug','runtime-sanitize'):
    v=summary[profile];assert (v['core_checks'],v['core_suites'],v['harness_checks'],v['P10_cases'],v['failed'],v['skipped'])==(1089,62,51,151,0,0)
prepared=json.loads((root/'local/world-remote-integration-prepared-02.json').read_text())
assert prepared['files']==json.loads((dest/'candidate-02'/host/'source-manifest.json').read_text())['files']
print(host,'both complete profiles verified against all233 proposed integration hashes')
