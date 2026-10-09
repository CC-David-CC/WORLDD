import pathlib,shutil,tarfile
root=pathlib.Path(__file__).resolve().parents[1]
destination=root/'evidence/bootstrap-domain-2026-10-09'
with tarfile.open(root/'local/bootstrap-domain-evidence-sb16.tar.gz') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and name.parts[0]=='sb16' and not name.is_absolute() and '..' not in name.parts
        assert '\\' not in member.name
        target=destination.joinpath(*name.parts);data=archive.extractfile(member).read()
        if target.exists(): assert target.read_bytes()==data,member.name
        else: target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
for name in ('collect-domain-draft.py','sanitize-domain-draft.py','audit-domain-draft.py','import-domain-evidence.py'):
    shutil.copyfile(root/'local'/name,destination/name)
print('Imported immutable domain evidence, including 43+32 sanitizer checks')
