import hashlib,json,subprocess,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];local=r/'local';repo=r.parent;e=r/'evidence/participant-channel-2026-10-09'
for label in ['baseline','candidate']:
    tree=local/('participant-channel-'+label+'-review-01');tree.mkdir()
    with tarfile.open(local/'owned-participant-candidate-01.tar') as a:a.extractall(tree,filter='data')
    patch=e/('participant-channel-'+label+'-01.patch')
    command=['git','apply','--directory='+tree.relative_to(repo).as_posix(),str(patch)]
    subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True);subprocess.run(command,cwd=repo,check=True)
    manifest=json.loads((e/('participant-channel-'+label+'-01.json')).read_text())['files']
    for n,h in manifest.items():assert hashlib.sha256((tree/n).read_bytes()).hexdigest()==h,n
p=e/'source-review.json';d=json.loads(p.read_text());d['patch_reconstruction']='Both patches applied to the recorded base and reproduce every frozen file hash.';p.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8',newline='\n')
print('Both preserved patches reproduce all frozen source hashes.')
