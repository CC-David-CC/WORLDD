#!/usr/bin/env python3
"""Reference conformance runner. It NEVER issues LOCAL_READY and never starts networking."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kernel.canonical import dumps
from kernel.objects import make, identity, encode
from kernel.crypto import sign, principal, public_bytes
from kernel.readiness import REQUIRED_GATES
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

INPUT_DIRS=('kernel','tests','vectors','tools','native','docs','schemas','tasks','prompts','examples')
INPUT_FILES=('README.md','AGENTS.md','START_CODEX.md','CURRENT_STATE.md','PHASES.md','requirements.txt','pyproject.toml','.gitignore')

def inventory() -> dict:
    paths=[]
    for name in INPUT_DIRS:
        paths.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    paths.extend(ROOT/name for name in INPUT_FILES if (ROOT/name).exists())
    entries=[]
    for p in sorted(set(paths)):
        if p.is_symlink(): raise RuntimeError('Symlink in measured inputs: '+str(p))
        entries.append({'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    return make('build-inputs',{'files':entries,'profile':'source-reference-v1'})

class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.records={}
    def addSuccess(self,test):
        super().addSuccess(test); self.records[test.id()]='PASS'
    def addFailure(self,test,err):
        super().addFailure(test,err); self.records[test.id()]='FAIL'
    def addError(self,test,err):
        super().addError(test,err); self.records[test.id()]='ERROR'
    def addSkip(self,test,reason):
        super().addSkip(test,reason); self.records[test.id()]='SKIP'
    def addExpectedFailure(self,test,err):
        super().addExpectedFailure(test,err); self.records[test.id()]='XFAIL'
    def addUnexpectedSuccess(self,test):
        super().addUnexpectedSuccess(test); self.records[test.id()]='XPASS'
    def addSubTest(self,test,subtest,err):
        super().addSubTest(test,subtest,err)
        if err is not None: self.records[test.id()]='FAIL'

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default='evidence/latest')
    p.add_argument('--native',action='store_true',help='Require actual native socket-denial observations; never a bypass flag')
    p.add_argument('--launcher',type=Path)
    args=p.parse_args()
    out=Path(args.out).resolve(); out.mkdir(parents=True,exist_ok=True)
    before=inventory()
    if args.native:
        from tools.check_network_guard import probe
        guard=probe()
        if args.launcher is None: raise RuntimeError('Native launcher artifact required')
        guard['launcher_sha256']=hashlib.sha256(args.launcher.read_bytes()).hexdigest()
    else:
        guard={'profile':'portable-reference-only','status':'UNQUALIFIED',
               'scope':'No network-silence claim; do not treat this mode as local readiness'}
    text=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py',top_level_dir=str(ROOT))
    expected=suite.countTestCases()
    result=unittest.TextTestRunner(stream=text,verbosity=2,resultclass=Result).run(suite)
    sys.stdout.write(text.getvalue())
    after=inventory()
    passed=(expected>0 and result.wasSuccessful() and len(result.records)==expected
            and all(s=='PASS' for s in result.records.values()) and before==after)
    tests=make('test-results',{'count':expected,'results':[{'test':t,'status':s} for t,s in sorted(result.records.items())],
                              'source_unchanged_during_run':before==after})
    environment=make('test-environment',{'python':platform.python_version(),'platform':platform.platform(),
           'machine':platform.machine(),'cryptography':importlib.metadata.version('cryptography'),
           'interpreter_sha256':hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
           'guard':guard,'dependency_closure':'NOT_YET_MEASURED'})
    requirements=make('readiness-requirements',{'required_gates':sorted(REQUIRED_GATES),
                    'definition_sha256':hashlib.sha256((ROOT/'docs/TESTING_SPEC.md').read_bytes()).hexdigest()})
    key=Ed25519PrivateKey.generate()  # Ephemeral fixture evidence key, not any user's authority root.
    run=sign(make('local-conformance-run',{
        'status':'REFERENCE_PASS' if passed else 'REFERENCE_FAIL',
        'readiness':'NOT_ISSUED','signer_purpose':'ephemeral-test-evidence-only',
        'required_gate_status':{g:'PENDING_FULL_ACCEPTANCE' for g in sorted(REQUIRED_GATES)},
        'network_started':False,
        'warning':'Fixture tests are not production conformance or remote attestation.'
    },{'build':identity(before),'tests':identity(tests),'environment':identity(environment),'requirements':identity(requirements)}),key)
    objects=[before,tests,environment,requirements,run]
    for obj in objects:
        (out/(identity(obj).split(':')[-1]+'.json')).write_bytes(encode(obj))
    (out/'run.json').write_bytes(encode(run))
    (out/'test-output.txt').write_text(text.getvalue())
    summary={'reference_tests':expected,'reference_passed':passed,'readiness':'NOT_ISSUED',
             'network_profile':guard['profile'],'run_id':identity(run),
             'fixture_signer':principal(public_bytes(key))}
    (out/'summary.json').write_bytes(dumps(summary))
    print(json.dumps(summary,indent=2))
    return 0 if passed else 1

if __name__=='__main__':
    raise SystemExit(main())
