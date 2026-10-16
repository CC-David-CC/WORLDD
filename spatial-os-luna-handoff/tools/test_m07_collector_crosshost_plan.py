#!/usr/bin/env python3
"""Offline ordinary-file controls. Never connect to qualification hosts."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent

def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

p=load('m07_collector_crosshost_plan')
c=load('m07_collector_continuation')
gate=load('m06_deployed_gate')
m07=load('m07_deployed_gate')

class ContinuationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.root=Path(p.DEPLOY);self.host='sb16'
        self.out=self.root/'evidence'/'1e58e65d-sb16-local-01'
        self.artifacts=self.base/'canonical';self.artifacts.mkdir()
        self.tools=dict(p.TOOL_PINS)
        self.context_path=Path(p.CONTEXT_PATH)
        self.batch_id='1e58e65d-sb16-san-collector-fixture'
        self.manifests={profile:dict(sha256={'kept.log':('a' if profile=='Debug' else 'b')*64},exclusions=[],artifact_links=[dict(path='engine/retired/staging',kind='completed-execution-retirement-marker',target_bytes_hex=b'/fixture/never-created'.hex(),target_sha256=hashlib.sha256(b'/fixture/never-created').hexdigest())]) for profile in p.PROFILES}
        self.real_raw=c.raw_file;self.real_directory=c.real_directory
        self.space={};exec(p.CONTINUATION_CHECK,self.space)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(c,'raw_file',side_effect=lambda path:self.real_raw(self.map_path(path))))
        self.stack.enter_context(patch.object(c,'real_directory',side_effect=self.directory))
        self.stack.enter_context(patch.object(c,'ARTIFACT_ROOT',self.artifacts))
        self.stack.enter_context(patch.object(c.socket,'gethostname',return_value=self.host))
        self.stack.enter_context(patch.object(c.os,'kill',side_effect=ProcessLookupError))
        self.stack.enter_context(patch.dict(self.space,{'_fixed_collector':lambda:(c,gate,self.tools)}))
        self.source_check=self.stack.enter_context(patch.object(c,'_source_closure',return_value=({'runtime_manifest':{}},{'bundle_sha256':'bundle'})))
        self.profile_check=self.stack.enter_context(patch.object(c,'audit_profile',side_effect=self.audit_profile))
        self.original=dict(host=self.host,candidate=p.CANDIDATE,attempt=self.out.name,status='failed',phase_complete=False,source_sha256_rechecked=True,profiles={'Debug':{}},execution_error={'type':'RuntimeError','message':'gate artifact tree contains a file symlink: engine/retired/staging'},postrun_invariant_failures=[dict(profile='ASan-UBSan',check=key,reason='profile-not-run') for key in ('metadata_rechecked','storage_evidence_rechecked')],requirement_suites={'same':'mapping'},required_tests=['required'],dedicated_crosshost=['geometry-native-crosshost','workspace-geometry-native-crosshost'],bundle_sha256='bundle')
        for key in c.CHECKS:self.original[key]={'Debug':True}
        self.original_pin=self.put(self.out/'run.json',self.original)
        anchors={}
        for name in ('inventory.log','inventory.json','metadata-relocation.json','production-off-startup.log','harness.log','core.log','core.xml','P10.log','P10.json','bounded-storage-git.log','bounded-storage-git/review.json'):
            raw=('original '+name).encode();self.put(self.out/'Debug'/name,raw);anchors[name]=dict(size=len(raw),sha256=c.digest(raw))
        self.retention=dict(host=self.host,pane_retired=True,pane_pid=123456789,original_run_path=str(self.out/'run.json'),original_run_sha256=self.original_pin,original_run=self.original,symlinks=[dict(path='engine/retired/staging')],command_evidence_pins=anchors)
        retained_path=self.context_path.parent/'retained-original-failed-debug.json'
        retained_pin=self.put(retained_path,self.retention)
        self.origin=self.runner('1e58e65d-sb16-local-01',123456789,1,c._origin_argv(self.root,self.host))
        self.context=dict(schema=c.SCHEMA,host=self.host,candidate=p.CANDIDATE,deployment=str(self.root),batch_id=self.batch_id,capacity_floor_bytes=1,tools=self.tools,retention=dict(path=str(retained_path),sha256=retained_pin),origin_runner=self.origin)
        self.debug=dict(schema=c.SCHEMA,status='retained_debug_collector_passed',host=self.host,candidate=p.CANDIDATE,context_binding=c._binding(self.context),original_run_sha256=self.original_pin,manifest=self.manifests['Debug'])
        debug_path=self.out/'debug-collector-audit.json';debug_pin=self.put(debug_path,self.debug)
        self.context['debug_audit']=dict(path=str(debug_path),sha256=debug_pin)
        self.context_pin=self.put(self.context_path,self.context)
        self.new_runner=self.runner(self.batch_id,123456790,0,c._batch_argv(self.root,self.context_path,self.context_pin))
        launch=dict(schema='m07-collector-batch-launch-v1',host=self.host,candidate=p.CANDIDATE,batch_id=self.batch_id,context_sha256=self.context_pin,runner_path=self.new_runner['path'],pane_pid=self.new_runner['pid'],argv=c._batch_argv(self.root,self.context_path,self.context_pin))
        launch_path=self.context_path.parent/'sanitizer-primary-launch.json';launch_pin=self.put(launch_path,launch)
        self.new_runner['launch']=dict(path=str(launch_path),sha256=launch_pin)
        self.batch=dict(status='profile_batch_passed',report_kind='profile_batch',selected_profiles=['ASan-UBSan'],candidate=p.CANDIDATE,host=self.host,attempt=self.out.name,batch_id=self.batch_id,phase_complete=False,source_sha256_rechecked=True,postrun_invariant_failures=[],continuation={'context_sha256':self.context_pin},profiles={'ASan-UBSan':self.profile_row('ASan-UBSan')})
        for key in c.CHECKS:self.batch[key]={'ASan-UBSan':True}
        for key in ('requirement_suites','required_tests','dedicated_crosshost','bundle_sha256'):self.batch[key]=self.original[key]
        self.batch_pin=self.put(self.out/'sanitizer-batch.json',self.batch)
        self.report=copy.deepcopy(self.original)
        self.report.update(report_kind='combined_collector_amendment',status='local_passed_crosshost_pending',selected_profiles=list(p.PROFILES),profiles={profile:self.profile_row(profile) for profile in p.PROFILES},postrun_invariant_failures=[],collector_amendment=dict(context_path=str(self.context_path),context_sha256=self.context_pin,tools=self.tools,original_failed_run=str(self.out/'run.json'),original_failed_run_sha256=self.original_pin,batch_id=self.batch_id,debug_audit_sha256=debug_pin,sanitizer_batch_sha256=self.batch_pin,batch_runner=copy.deepcopy(self.new_runner)))
        for key in c.CHECKS:self.report[key]={profile:True for profile in p.PROFILES}
        self.report_pin=self.put(self.out/'combined-local.json',self.report)
        self.before=dict(schema=c.SCHEMA,status='primary_artifact_recheck_passed',phase='before-crosshost',phase_complete=False,host=self.host,candidate=p.CANDIDATE,combined_local_sha256=self.report_pin,context_sha256=self.context_pin,tools=self.tools,profiles={profile:self.summary(self.manifests[profile]) for profile in p.PROFILES})
        self.before_pin=self.put(self.out/'before-crosshost-artifact-audit.json',self.before)

    def map_path(self,path):
        path=Path(path)
        # Staged immutable tool modules may themselves live under spatial-artifacts.
        if path.is_relative_to(HERE):
            return path
        if any(path.is_relative_to(root) for root in (self.root,self.context_path.parent)):
            return self.base/'files'/path.relative_to('/home/dflanag3/spatial-artifacts')
        return path

    def directory(self,path):
        self.real_directory(self.map_path(path));return Path(path)

    def put(self,path,value):
        actual=self.map_path(path);actual.parent.mkdir(parents=True,exist_ok=True)
        raw=value if isinstance(value,bytes) else (json.dumps(value,sort_keys=True)+'\n').encode()
        actual.write_bytes(raw);return c.digest(raw)

    def runner(self,attempt,pid,exit_code,argv):
        directory=c.runner_directory(self.host,attempt);directory.mkdir(parents=True)
        values={name:b'' for name in c.RUNNER_FILES}
        values.update({'argv.nul':b''.join(x.encode()+b'\0' for x in argv),'command.exit':str(exit_code).encode()+b'\n','wrapper.exit':str(exit_code).encode()+b'\n','terminal-status.txt':b'COMPLETE\n'})
        for name,raw in values.items():(directory/name).write_bytes(raw)
        return dict(path=str(directory),pid=pid,files={name:c.digest(raw) for name,raw in values.items()})

    def profile_row(self,profile):
        m=self.manifests[profile]
        return dict(status='passed_needs_crosshost_and_review',artifact_sha256=m['sha256'],artifact_exclusions=m['exclusions'],artifact_links=m['artifact_links'])

    def summary(self,manifest):
        return dict(regular_files=len(manifest['sha256']),artifact_links=manifest['artifact_links'],manifest_sha256=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest())

    def audit_profile(self,gate_arg,root,provenance,report,profile,expected):
        self.assertEqual(expected,self.manifests[profile]);return report['profiles'][profile],copy.deepcopy(expected)

    def call(self,**changes):
        args=dict(root=self.root,host=self.host,report_path=self.out/'combined-local.json',context_sha=self.context_pin,report_sha=self.report_pin,before_sha=self.before_pin);args.update(changes)
        return self.space['continuation_preflight'](**args)

    def reseal_report(self):
        self.report_pin=self.put(self.out/'combined-local.json',self.report)
        self.before['combined_local_sha256']=self.report_pin;self.before_pin=self.put(self.out/'before-crosshost-artifact-audit.json',self.before)

    def test_real_staged_tool_closure_bypasses_fixture_mapping(self):
        before={name:(HERE/name).read_bytes() for name in p.TOOL_PINS}
        for name,raw in before.items():
            self.assertEqual(self.map_path(HERE/name),HERE/name)
            self.assertEqual(c.raw_file(HERE/name),raw)
        c._tools(gate,self.context)
        self.assertEqual(before,{name:(HERE/name).read_bytes() for name in p.TOOL_PINS})
        self.assertNotEqual(self.map_path(self.root/'evidence'),self.root/'evidence')
        self.assertNotEqual(self.map_path(self.context_path),self.context_path)
        self.assertEqual(c.raw_file(self.context_path),self.map_path(self.context_path).read_bytes())
        # Model the actual artifact staging location even when authored in a src checkout.
        staging=Path('/home/dflanag3/spatial-artifacts/collector-control-fixture/source')
        with patch.dict(globals(),{'HERE':staging}):
            for name in p.TOOL_PINS:
                self.assertEqual(self.map_path(staging/name),staging/name)
            self.assertNotEqual(self.map_path(self.context_path),self.context_path)
            unrelated=Path('/home/dflanag3/spatial-artifacts/unrelated/retained.json')
            self.assertEqual(self.map_path(unrelated),unrelated)

    def test_positive_complete_chain_rechecks_both_profiles_and_preserves_inputs(self):
        before={str(path):path.read_bytes() for path in self.base.rglob('*') if path.is_file()}
        result=self.call()
        self.assertEqual(result['status'],'CONTINUATION_READY_FOR_CROSSHOST')
        self.assertEqual(result['runner_dir'],self.new_runner['path'])
        self.assertEqual([call.args[4] for call in self.profile_check.call_args_list],list(p.PROFILES))
        self.assertEqual([call.args[5] for call in self.profile_check.call_args_list],[self.manifests[profile] for profile in p.PROFILES])
        self.assertEqual(before,{str(path):path.read_bytes() for path in self.base.rglob('*') if path.is_file()})

    def test_combined_identity_profiles_and_output_path_refusals(self):
        for key,value in (('host','sb16-2'),('candidate','wrong'),('report_kind','profile_batch'),('status','profile_batch_passed'),('selected_profiles',['ASan-UBSan']),('profiles',{'Debug':self.profile_row('Debug')}),('attempt','wrong')):
            saved=copy.deepcopy(self.report);self.report[key]=value;self.reseal_report()
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'complete combined local report required'):self.call()
            self.report=saved;self.reseal_report()
        with self.assertRaisesRegex(RuntimeError,'combined report path differs'):self.call(report_path=self.out/'run.json')
        with self.assertRaisesRegex(RuntimeError,'continuation deployment/host differs'):self.call(root=Path('/other'))
        with self.assertRaisesRegex(RuntimeError,'pinned input changed'):self.call(report_sha='0'*64)

    def test_missing_or_partial_sanitizer_and_failed_postchecks_refuse(self):
        path=self.map_path(self.out/'sanitizer-batch.json');raw=path.read_bytes();path.unlink()
        with self.assertRaises(FileNotFoundError):self.call()
        path.write_bytes(raw)
        for key,value in (('status','failed'),('profiles',{}),('selected_profiles',['Debug']),('source_sha256_rechecked',False)):
            changed=copy.deepcopy(self.batch);changed[key]=value
            self.report['collector_amendment']['sanitizer_batch_sha256']=self.put(self.out/'sanitizer-batch.json',changed);self.reseal_report()
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'complete actual sanitizer batch required'):self.call()
        self.put(self.out/'sanitizer-batch.json',self.batch);self.report['collector_amendment']['sanitizer_batch_sha256']=self.batch_pin;self.reseal_report()
        self.report['metadata_rechecked']['ASan-UBSan']=False;self.reseal_report()
        with self.assertRaisesRegex(RuntimeError,'combined profile postcheck failed'):self.call()

    def test_context_and_amendment_pins_refuse(self):
        with self.assertRaisesRegex(RuntimeError,'pinned input changed'):self.call(context_sha='0'*64)
        for key,value in (('context_sha256','0'*64),('context_path','/other/context.json'),('tools',{}),('original_failed_run_sha256','0'*64),('original_failed_run','/other/run.json'),('batch_id','wrong')):
            saved=copy.deepcopy(self.report);self.report['collector_amendment'][key]=value;self.reseal_report()
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'combined continuation chain differs'):self.call()
            self.report=saved;self.reseal_report()
        for key,value in (('host','sb16-2'),('candidate','wrong')):
            wrong=copy.deepcopy(self.context);wrong[key]=value;pin=self.put(self.context_path,wrong)
            with self.subTest(context=key),self.assertRaisesRegex(RuntimeError,'continuation host/candidate/schema mismatch'):self.call(context_sha=pin)
        wrong=copy.deepcopy(self.context);wrong['tools']['m07_collector_continuation.py']='0'*64
        pin=self.put(self.context_path,wrong)
        with self.assertRaisesRegex(RuntimeError,'amended tool changed'):self.call(context_sha=pin)

    def test_actual_failed_origin_terminal_pid_and_unchanged_bytes_required(self):
        path=Path(self.origin['path'])/'command.exit';old=path.read_bytes();path.write_bytes(b'0\n')
        with self.assertRaisesRegex(RuntimeError,'actual canonical runner pin changed'):self.call()
        path.write_bytes(old)
        self.put(self.out/'run.json',dict(self.original,status='passed'))
        with self.assertRaisesRegex(RuntimeError,'pinned input changed'):self.call()
        self.put(self.out/'run.json',self.original)
        altered=copy.deepcopy(self.retention);altered['pane_pid']+=1
        changed=copy.deepcopy(self.context);changed['retention']['sha256']=self.put(changed['retention']['path'],altered)
        pin=self.put(self.context_path,changed)
        with self.assertRaisesRegex(RuntimeError,'runner PID differs from retained launch'):self.call(context_sha=pin)

    def test_actual_new_runner_launch_argv_terminal_and_pid_refusals(self):
        for name,raw,expected in (('command.exit',b'1\n','canonical runner terminal/exit mismatch'),('wrapper.exit',b'1\n','canonical runner terminal/exit mismatch'),('terminal-status.txt',b'RUNNING\n','canonical runner terminal/exit mismatch'),('argv.nul',b'wrong\0','canonical runner argv differs')):
            path=Path(self.new_runner['path'])/name;old=path.read_bytes();path.write_bytes(raw)
            self.report['collector_amendment']['batch_runner']['files'][name]=c.digest(raw);self.reseal_report()
            with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,expected):self.call()
            path.write_bytes(old);self.report['collector_amendment']['batch_runner']['files'][name]=c.digest(old);self.reseal_report()
        saved=copy.deepcopy(self.report)
        self.report['collector_amendment']['batch_runner']['path']='/copied/runner';self.reseal_report()
        with self.assertRaisesRegex(RuntimeError,'sanitizer canonical runner path mismatch'):self.call()
        self.report=copy.deepcopy(saved);self.report['collector_amendment']['batch_runner']['pid']+=1;self.reseal_report()
        with self.assertRaisesRegex(RuntimeError,'actual primary sanitizer launch receipt mismatch'):self.call()
        self.report=copy.deepcopy(saved);self.reseal_report()
        def live_sanitizer(pid,signal):
            if pid==self.new_runner['pid']:return None
            raise ProcessLookupError
        with patch.object(c.os,'kill',side_effect=live_sanitizer),self.assertRaisesRegex(RuntimeError,'pane PID remains live'):self.call()

    def test_before_sidecar_typed_links_and_complete_manifest_refusals(self):
        path=self.map_path(self.out/'before-crosshost-artifact-audit.json');raw=path.read_bytes();path.unlink()
        with self.assertRaises(FileNotFoundError):self.call()
        path.write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError,'pinned input changed'):self.call(before_sha='0'*64)
        for key,value in (('phase','after-crosshost'),('host','sb16-2'),('context_sha256','0'*64),('combined_local_sha256','0'*64),('tools',{}),('profiles',{})):
            changed=copy.deepcopy(self.before);changed[key]=value;pin=self.put(self.out/'before-crosshost-artifact-audit.json',changed)
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'accepted before-crosshost artifact audit differs'):self.call(before_sha=pin)
        changed=copy.deepcopy(self.before);changed['profiles']['Debug']['artifact_links']=[];pin=self.put(self.out/'before-crosshost-artifact-audit.json',changed)
        with self.assertRaisesRegex(RuntimeError,'before audit full artifact/link manifest differs'):self.call(before_sha=pin)
        self.put(self.out/'before-crosshost-artifact-audit.json',self.before)
        with patch.object(c,'audit_profile',return_value=({},dict(self.manifests['Debug'],sha256={}))),self.assertRaisesRegex(RuntimeError,'fresh complete artifact manifest differs'):self.call()

    def test_accepted_debug_source_runtime_and_metadata_drift_refusals(self):
        self.map_path(self.out/'debug-collector-audit.json').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'pinned input changed'):self.call()
        self.put(self.out/'debug-collector-audit.json',self.debug)
        with patch.object(c,'_source_closure',side_effect=RuntimeError('frozen source closure changed')),self.assertRaisesRegex(RuntimeError,'frozen source closure changed'):self.call()
        for message in ('runtime/generated artifact changed','current CTest metadata changed','current CTest registrations differ from retained inventory','full regular/link/exclusion artifact manifest changed'):
            with self.subTest(boundary=message),patch.object(c,'audit_profile',side_effect=RuntimeError(message)),self.assertRaisesRegex(RuntimeError,message):self.call()

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_exact_pair_and_sha_inputs(self):
        valid=[('sb16','a'*64),('sb16-2','b'*64)]
        self.assertEqual(p.proof_pins(valid,'proofs'),dict(valid))
        for rows in (valid[:1],valid+[valid[0]],[valid[0],valid[0]],[('other','a'*64),valid[1]]):
            with self.assertRaisesRegex(RuntimeError,'exactly once'):p.proof_pins(rows,'proofs')
        with self.assertRaisesRegex(RuntimeError,'lowercase SHA256'):p.proof_pins([('sb16','bad'),valid[1]],'proofs')

    def test_fixed_dependencies_and_remote_code_have_closed_auditor_location(self):
        ast.parse(p.REMOTE_VALIDATE)
        self.assertIn("/attempt-01/tools",p.CONTINUATION_CHECK)
        self.assertNotIn('audit_artifacts(',p.CONTINUATION_CHECK)
        self.assertIn("m['_validate_local_run']",p.REMOTE_VALIDATE)
        self.assertIn('qualified_metadata(root,prov',p.REMOTE_VALIDATE)
        self.assertIn('PRIMARY_LOCAL_FULL_GATE_EVIDENCE_ACCEPTED',p.REMOTE_VALIDATE)
        self.assertNotIn('run.json',p.REPORTS['sb16'])
        self.assertEqual(set(p.BASELINE_PINS),{'prepare_crosshost_plan.py','execute_workspace_crosshost_serial.py','launch_m06_crosshost.sh'})
        directory=self.root/'deps';directory.mkdir();pins={}
        for name in p.BASELINE_PINS:
            (directory/name).write_bytes(name.encode());pins[name]=p.digest(directory/name)
        with patch.object(p,'BASELINE',directory),patch.object(p,'BASELINE_PINS',pins):
            self.assertEqual(len(p.verify_baseline()),3)
            (directory/'launch_m06_crosshost.sh').write_bytes(b'drift')
            with self.assertRaisesRegex(RuntimeError,'frozen baseline dependency changed'):p.verify_baseline()

    def generate(self,missing=None,bad_reply=None):
        source=self.root/'frozen-source';source.mkdir(exist_ok=True)
        artifact=self.root/'generated'
        pin=self.root/'package-pin.json';pin.write_text(json.dumps(dict(candidate=p.CANDIDATE,inner_provenance_sha256=p.INNER_SHA,outer_provenance_sha256=p.OUTER_SHA,archive_sha256=p.ARCHIVE_SHA,status='PRIMARY_PACKAGE_CONTENT_REVIEW_PASS')))
        argv=['generator','--source-root',str(source),'--artifact-root',str(artifact),'--attempt','offline-only']
        for host,address in (('sb16','10.0.7.49'),('sb16-2','10.0.7.94')):
            argv+=['--runner-dir',host+'=/fixture/'+host,'--audit-receipt',host+'=/fixture/audit/'+host+'.json','--address',host+'='+address]
            for name,sha in (('context-sha256','a'*64),('combined-sha256','b'*64),('before-audit-sha256','c'*64),('audit-sha256','d'*64)):argv+=['--'+name,host+'='+sha]
        calls=[]
        def remote(host,code,args):
            calls.append((host,code,args))
            if code!=p.REMOTE_VALIDATE:return {profile:{'xace_wrapper':{'path':'/external/'+host+'/'+profile+'/wrapper'}} for profile in p.PROFILES}
            if host==missing:raise RuntimeError('combined or before audit absent on '+host)
            self.assertEqual(args[2],p.REPORTS[host]);self.assertEqual(args[-4:],['a'*64,'b'*64,'c'*64,'d'*64])
            result=dict(host=host,candidate=p.CANDIDATE,source_integrity=True,postrun_integrity=True,profiles={profile:'passed_needs_crosshost_and_review' for profile in p.PROFILES},qualified_metadata={profile:{'registrations':199} for profile in p.PROFILES},runner_terminal='COMPLETE',local_status='local_passed_crosshost_pending',report_sha256='b'*64,primary_audit_sha256='d'*64,collector_continuation=dict(status='CONTINUATION_READY_FOR_CROSSHOST',context_sha256='a'*64,before_artifact_audit_sha256='c'*64,combined_sha256='b'*64,runner_dir='/fixture/'+host))
            if bad_reply:result.update(bad_reply)
            return result
        def transport(command,*args,**kwargs):
            self.assertEqual(command[0],'ssh');self.assertIn('test',command);self.assertIn('!',command)
            return subprocess.CompletedProcess(command,0,b'',b'')
        def production(path):return {'_validate_plan':gate._validate_plan} if Path(path).name=='m06_deployed_gate.py' else {'_role_map':m07._role_map}
        import runpy
        with patch.object(sys,'argv',argv),patch.object(p,'PIN_PATH',str(pin)),patch.object(p,'PIN_SHA',p.digest(pin)),patch.object(p,'source_pins',return_value={'frozen/schema.proto':'e'*64}),patch.object(p,'verify_baseline',return_value={'fixed-launcher':'f'*64}),patch.object(p.subprocess,'check_output',side_effect=lambda argv,**kw:p.CANDIDATE+'\n' if 'rev-parse' in argv else ''),patch.object(p,'ssh_json',side_effect=remote),patch.object(p.subprocess,'run',side_effect=transport),patch.object(runpy,'run_path',side_effect=production),contextlib.redirect_stdout(io.StringIO()):
            p.main()
        return artifact,calls

    def test_actual_main_produces_fixed_two_profile_four_role_plan(self):
        artifact,calls=self.generate();out=artifact/'plan-offline-only'
        plan=json.loads((out/'m06-crosshost-plan.json').read_bytes())
        self.assertEqual(plan['local_runs'],{h:{'path':p.REPORTS[h],'sha256':'b'*64} for h in p.HOSTS})
        self.assertEqual(plan['profiles'],list(p.PROFILES));self.assertEqual(len(plan['semantic_cases']),6)
        self.assertEqual(len(list((out/'roles').glob('*.json'))),4)
        summary=json.loads((out/'preparation-receipt.json').read_bytes())
        self.assertEqual(summary['runtime'],'UNRUN');self.assertEqual(summary['qualification'],'UNQUALIFIED')
        self.assertEqual(set(summary['required_after_crosshost_audits']),set(p.HOSTS))
        self.assertEqual([host for host,code,args in calls if code==p.REMOTE_VALIDATE],list(p.HOSTS))
        with self.assertRaisesRegex(RuntimeError,'plan output already exists'):self.generate()

    def test_main_refuses_missing_host_and_mismatched_returned_proofs_before_output(self):
        for host in p.HOSTS:
            with self.subTest(host=host),self.assertRaisesRegex(RuntimeError,'combined or before audit absent'):self.generate(missing=host)
            self.assertFalse((self.root/'generated').exists())
        for bad,message in ((dict(report_sha256='0'*64),'returned proof pins differ'),(dict(collector_continuation={}), 'continuation chain is incomplete'),(dict(local_status='profile_batch_passed'),'not terminal and ready'),(dict(host='other'),'returned authority/inventory summary differs'),(dict(candidate='wrong'),'returned authority/inventory summary differs'),(dict(qualified_metadata={}), 'returned authority/inventory summary differs')):
            with self.subTest(bad=bad),self.assertRaisesRegex(RuntimeError,message):self.generate(bad_reply=bad)
            self.assertFalse((self.root/'generated').exists())

class QualifiedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.build=self.root/'build/Debug';self.build.mkdir(parents=True)
        self.metadata=self.build/'CTestTestfile.cmake';self.metadata.write_bytes(b'# actual fixture CTest metadata\n')
        self.target=self.root/'evidence/original/Debug';self.target.mkdir(parents=True)
        self.report_path=self.target.parent/'combined-local.json'
        self.raw=json.dumps({'tests':[{'name':'case-%03d'%n} for n in range(199)]},sort_keys=True).encode()
        self.inventory=dict(names=['case-%03d'%n for n in range(199)],count=199,sha256=hashlib.sha256(self.raw).hexdigest())
        self.write('inventory.log',self.raw);self.write('inventory.json',self.inventory)
        self.write('metadata-relocation.json',{'after':{'CTestTestfile.cmake':p.digest(self.metadata)}})
        self.provenance={'profiles':{'Debug':{'ctest_metadata':{'CTestTestfile.cmake':'prepared-pin'}}}}
        self.report={'profiles':{'Debug':{'artifact_sha256':{name:p.digest(self.target/name) for name in ('metadata-relocation.json','inventory.json','inventory.log')}}}}
        self.namespace={'pathlib':__import__('pathlib')};exec(p.QUALIFIED_METADATA_CHECK,self.namespace)

    def write(self,name,value):
        (self.target/name).write_bytes(value if isinstance(value,bytes) else json.dumps(value,sort_keys=True).encode())

    def call(self,query=None):
        return self.namespace['qualified_metadata'](self.root,self.provenance,self.report,self.report_path,'Debug',query or (lambda root,profile:self.raw))

    def test_actual_199_inventory_and_metadata_positive(self):
        self.assertEqual(self.call()['registrations'],199)
        self.assertEqual(self.call()['metadata_files'],1)

    def test_current_metadata_inventory_and_artifact_drift_refusals(self):
        old=self.metadata.read_bytes();self.metadata.write_bytes(old+b'drift')
        with self.assertRaisesRegex(RuntimeError,'metadata bytes changed'):self.call()
        self.metadata.write_bytes(old)
        extra=self.build/'nested/CTestTestfile.cmake';extra.parent.mkdir();extra.write_bytes(b'extra')
        with self.assertRaisesRegex(RuntimeError,'metadata closure differs'):self.call()
        extra.unlink()
        with self.assertRaisesRegex(RuntimeError,'current inventory differs'):self.call(lambda root,profile:self.raw+b'\n')
        self.write('inventory.json',dict(self.inventory,count=198));self.report['profiles']['Debug']['artifact_sha256']['inventory.json']=p.digest(self.target/'inventory.json')
        with self.assertRaisesRegex(RuntimeError,'qualified inventory receipt differs'):self.call()
        self.write('inventory.json',self.inventory);self.report['profiles']['Debug']['artifact_sha256']['inventory.json']=p.digest(self.target/'inventory.json')
        self.write('inventory.log',b'drift')
        with self.assertRaisesRegex(RuntimeError,'qualified local artifact hash changed'):self.call()

if __name__=='__main__':unittest.main()