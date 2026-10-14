#!/usr/bin/env python3
"""Reject generated data in source commits. Also serves as a trusted receive hook."""
import argparse, os, subprocess, sys
from pathlib import PurePosixPath

MAX_BLOB=5*1024*1024
FORBIDDEN_DIRS={'evidence','local','build','jobs','handoff','__pycache__','node_modules','.venv','venv','.ssh','.aws'}
FORBIDDEN_SUFFIXES=('.log','.jsonl','.pyc','.pyo','.sqlite','.sqlite3','.sqlite-journal','.sqlite3-journal','.db','.img','.qcow2','.core','.tar','.tar.gz','.tgz','.zip','.bundle','.key','.pem')

def path_error(path):
    p=PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts: return 'unsafe path'
    for part in p.parts[:-1]:
        if part in FORBIDDEN_DIRS or part.startswith(('build-','build_','run-','run_','results-')): return 'generated or private directory'
    name=p.name
    if name in FORBIDDEN_DIRS: return 'generated or private path'
    if name=='.env' or (name.startswith('.env.') and name!='.env.example'): return 'credential configuration'
    if name.endswith(FORBIDDEN_SUFFIXES) or name.endswith(('_pb2.py','.pb.cc','.pb.h')): return 'generated data or credential file'
    return None

def git(*args):
    return subprocess.check_output(['git',*args])

def check_tree(tree):
    failures=[]
    for row in git('ls-tree','-rlz',tree).split(b'\0'):
        if not row: continue
        meta,name=row.split(b'\t',1); fields=meta.split(); path=name.decode()
        error=path_error(path)
        if fields[1]!=b'blob': error=error or 'submodules require a separately reviewed policy'
        elif int(fields[3])>MAX_BLOB: error=error or 'source blob exceeds 5 MiB'
        if error: failures.append(path+': '+error)
    if failures: raise ValueError('\n'.join(failures))

def receive():
    tips=[]
    for row in sys.stdin:
        old,new,ref=row.split()
        if new=='0'*40:
            if ref=='refs/heads/main': raise ValueError('Cannot delete main')
            continue
        if not ref.startswith(('refs/heads/','refs/tags/')): raise ValueError('Unsupported ref namespace')
        tips.append(new+'^{commit}')
    if not tips: return
    commits=git('rev-list',*tips,'--not','--all').decode().split()
    trees={git('rev-parse',c+'^{tree}').decode().strip() for c in commits}
    for tree in trees: check_tree(tree)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--receive',action='store_true'); p.add_argument('--tree'); a=p.parse_args()
    try:
        if a.receive: receive()
        else: check_tree(a.tree or git('write-tree').decode().strip())
    except (ValueError,subprocess.CalledProcessError) as error:
        print('SOURCE POLICY REJECTED: '+str(error),file=sys.stderr); return 1
    print('SOURCE POLICY PASS'); return 0

if __name__=='__main__': sys.exit(main())
