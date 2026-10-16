#!/usr/bin/env python3
"""Ordinary-file controls. No native stage, model, mount, or target is executed."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

c = load('m07_collector_continuation')
g = load('m06_deployed_gate')

class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(value, sort_keys=True) + '\n').encode() if not isinstance(value, bytes) else value
        path.write_bytes(raw)
        return path, c.digest(raw)

    def test_selection_is_closed(self):
        self.assertEqual(c.selected_profiles(g, None), g.PROFILES)
        self.assertEqual(c.selected_profiles(g, ['ASan-UBSan']), ('ASan-UBSan',))
        for value in ([], ['Debug'], ['unknown'], ['ASan-UBSan','Debug'], ['Debug','Debug'], ['Debug','ASan-UBSan','ASan-UBSan']):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                c.selected_profiles(g, value)

    def test_stage_argv_and_deadlines(self):
        source, build, production, target = map(Path, ('/source','/build','/production','/target'))
        rows = g.profile_stage_commands(source, build, production, target, {'geometry-native-crosshost','workspace-geometry-native-crosshost'})
        self.assertEqual([r[0] for r in rows], list(c.COMMANDS[1:]))
        self.assertEqual([r[2] for r in rows], [120,180,14400,300,600])
        self.assertEqual(rows[0][1], g.startup_packaging_command(source, production/'spatial-host-session', production/'python'))
        self.assertEqual(rows[1][1], ['python3','-m','unittest','discover','-s','tests/harness','-t','.','-v'])
        self.assertEqual(rows[2][1], ['ctest','--test-dir','/build','--parallel','1','-V','-E',g.ctest_exclusion_expression({'geometry-native-crosshost','workspace-geometry-native-crosshost'}),'--output-junit','/target/core.xml'])
        self.assertEqual(rows[3][1], ['python3','tools/with_resource_delegate.py','--native','--','python3','tools/with_adapter_resources.py','--','python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--sut',json.dumps(['/build/spatial-test-host']),'--report','/target/P10.json'])
        self.assertEqual(rows[4][1], ['sudo','-n','python3','tests/native/test_storage_git_integration.py','--production-owner','/build','/target/bounded-storage-git'])

    def test_environment_and_stage_policy_unchanged(self):
        original = {name:'unsafe' for name in ('DISPLAY','SPATIAL_TEST_DISPLAY','SPATIAL_SECURITY_TEST_CORE_ONLY','LD_LIBRARY_PATH','__EGL_VENDOR_LIBRARY_FILENAMES')}
        original['KEEP'] = 'yes'
        env = g.profile_environment(original, Path('/source'), Path('/build'), Path('/target'))
        self.assertEqual(original['DISPLAY'], 'unsafe')
        self.assertTrue(all(name not in env for name in original if name != 'KEEP'))
        self.assertEqual(env['KEEP'], 'yes')
        self.assertEqual(env['ASAN_OPTIONS'], 'detect_leaks=1:abort_on_error=1')
        self.assertEqual(env['UBSAN_OPTIONS'], 'halt_on_error=1:print_stacktrace=1')
        self.assertEqual(env['PYTHONPATH'], '/build/python:/source')
        for label in c.COMMANDS[1:]:
            with patch.object(g, 'create_p10_evidence_directory', return_value=Path('/target/P10-evidence')):
                actual = g.stage_environment(env, label, Path('/production'), Path('/source'), Path('/target'))
            self.assertEqual(actual['ASAN_OPTIONS'], env['ASAN_OPTIONS'])
            self.assertEqual(actual['UBSAN_OPTIONS'], env['UBSAN_OPTIONS'])
            if label == 'P10':
                self.assertEqual(actual['SPATIAL_EVIDENCE_DIR'], '/target/P10-evidence')

    def test_regular_hardlink_permitted_and_symlink_refused(self):
        path, pin = self.put('file', b'retained')
        other = self.root/'hardlink'; os.link(path, other)
        self.assertEqual(c.raw_file(other), b'retained')
        link = self.root/'link'; link.symlink_to(path)
        with self.assertRaises(RuntimeError): c.raw_file(link)

    def test_exclusive_output_preserves_existing_and_dangling(self):
        path = self.root/'new.json'; c.write_exclusive(g, path, {'status':'new'})
        before = path.read_bytes()
        with self.assertRaises(FileExistsError): c.write_exclusive(g, path, {})
        self.assertEqual(path.read_bytes(), before)
        dangling = self.root/'dangling'; dangling.symlink_to(self.root/'absent')
        with self.assertRaises(FileExistsError): c.write_exclusive(g, dangling, {})
        self.assertFalse((self.root/'absent').exists())

    def runner(self):
        directory = self.root/'runner'; directory.mkdir()
        argv = ['python3','-B','fixed.py']
        values = {name:b'' for name in c.RUNNER_FILES}
        values.update({'argv.nul': b'\0'.join(v.encode() for v in argv)+b'\0', 'command.exit':b'0\n','wrapper.exit':b'0\n','terminal-status.txt':b'COMPLETE\n'})
        for name, raw in values.items(): (directory/name).write_bytes(raw)
        return {'path':str(directory),'pid':123456789,'files':{name:c.digest(raw) for name,raw in values.items()}}, argv

    def test_actual_runner_positive_and_refusals(self):
        row, argv = self.runner()
        with patch.object(c, 'ARTIFACT_ROOT', self.root), patch.object(c.os, 'kill', side_effect=ProcessLookupError):
            self.assertEqual(c._runner(g,row,row['pid'],0,argv)['terminal'], 'COMPLETE')
            for name, raw in (('command.exit',b'1\n'),('wrapper.exit',b'1\n'),('terminal-status.txt',b'RUNNING\n'),('argv.nul',b'other\0')):
                with self.subTest(name=name):
                    path=Path(row['path'])/name; old=path.read_bytes(); path.write_bytes(raw)
                    altered=copy.deepcopy(row); altered['files'][name]=c.digest(raw)
                    with self.assertRaises(RuntimeError): c._runner(g,altered,row['pid'],0,argv)
                    path.write_bytes(old)
            with self.assertRaises(RuntimeError): c._runner(g,row,row['pid']+1,0,argv)
            invalid=copy.deepcopy(row); invalid['files'].pop('stdout.log')
            with self.assertRaises(RuntimeError): c._runner(g,invalid,row['pid'],0,argv)
            (Path(row['path'])/'stdout.log').write_bytes(b'drift')
            with self.assertRaises(RuntimeError): c._runner(g,row,row['pid'],0,argv)
        with patch.object(c.os,'kill', return_value=None), self.assertRaises(RuntimeError): c._pid_absent(row['pid'])
        for value in (True,0,-1,'123'):
            with self.assertRaises(RuntimeError): c._pid_absent(value)

    def prepared_fixture(self):
        out=self.root/'evidence'/'original'; (out/'Debug').mkdir(parents=True)
        (out/'run.json').write_bytes(b'original failed report')
        (out/'Debug'/'kept').write_bytes(b'original Debug bytes')
        context={'host':'sb16','batch_id':'unique-batch','capacity_floor_bytes':1}
        retained={'original_run_sha256':c.digest((out/'run.json').read_bytes())}
        original=dict(candidate=c.CANDIDATE, host='sb16', attempt=out.name, status='failed',
                      phase_complete=False, profiles={'Debug':{}})
        audit={'status':'retained_debug_collector_passed','original_run_sha256':retained['original_run_sha256'],'manifest':{'all':'pins'}}
        context['debug_audit']={'path':str(out/'debug-collector-audit.json')}
        audit['context_binding']=c._binding(context)
        path=out/'debug-collector-audit.json'; path.write_text(json.dumps(audit))
        context['debug_audit']['sha256']=c.digest(path.read_bytes())
        return out,context,retained,original,audit

    def prepare_patches(self, fixture):
        out,context,retained,original,audit=fixture
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch.object(c,'load_context',return_value=context))
        stack.enter_context(patch.object(c,'_source_closure',return_value=({},{})))
        stack.enter_context(patch.object(c,'_origin',return_value=(out,original,retained)))
        stack.enter_context(patch.object(c,'audit_profile',return_value=({},audit['manifest'])))
        stack.enter_context(patch.object(g,'STORAGE_EVIDENCE_ROOT',self.root/'storage'))
        return stack

    def test_preparation_positive_has_no_original_writes(self):
        fixture=self.prepared_fixture(); out=fixture[0]
        before={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
        with self.prepare_patches(fixture):
            result=c.prepare_batch(g,self.root,self.root/'inputs.json','f'*64)
        self.assertEqual(result['out'],out)
        self.assertEqual(result['report_path'],out/'sanitizer-batch.json')
        self.assertEqual(result['storage_attempt'],'unique-batch')
        self.assertEqual(before,{str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()})
        self.assertFalse((out/'ASan-UBSan').exists())

    def test_preparation_refuses_each_existing_or_dangling_output(self):
        fixture=self.prepared_fixture();out=fixture[0]
        for leaf in ('ASan-UBSan','sanitizer-batch.json','combined-local.json'):
            for kind in ('file','dangling'):
                with self.subTest(leaf=leaf,kind=kind):
                    path=out/leaf
                    if kind=='file': path.write_bytes(b'old')
                    else: path.symlink_to(out/'never-created')
                    with self.prepare_patches(fixture), self.assertRaisesRegex(RuntimeError,'continuation output already exists'):
                        c.prepare_batch(g,self.root,self.root/'inputs','f'*64)
                    path.unlink()

    def test_preparation_rejects_stale_audit_and_drift(self):
        fixture=self.prepared_fixture();out,context,retained,original,audit=fixture
        for key,value in (('status','failed'),('context_binding','stale'),('original_run_sha256','0'*64)):
            changed=copy.deepcopy(audit);changed[key]=value
            path=Path(context['debug_audit']['path']);path.write_text(json.dumps(changed));context['debug_audit']['sha256']=c.digest(path.read_bytes())
            with self.prepare_patches(fixture), self.assertRaisesRegex(RuntimeError,'accepted Debug audit is stale or unrelated'):
                c.prepare_batch(g,self.root,self.root/'inputs','f'*64)
        path.write_text(json.dumps(audit));context['debug_audit']['sha256']=c.digest(path.read_bytes())
        for boundary in ('_source_closure','_origin','audit_profile'):
            with self.prepare_patches(fixture), patch.object(c,boundary,side_effect=RuntimeError('drift')), self.assertRaisesRegex(RuntimeError,'drift'):
                c.prepare_batch(g,self.root,self.root/'inputs','f'*64)

    def test_postchecks_singleton_cannot_be_local_pass(self):
        report={'status':'profile_batch_passed','source_sha256_rechecked':True,'profiles':{'ASan-UBSan':{}}}
        for key in c.CHECKS: report[key]={'ASan-UBSan':True}
        g.finalize_postrun_audit(report,('ASan-UBSan',))
        self.assertEqual(report['status'],'profile_batch_passed')
        self.assertEqual(report['postrun_invariant_failures'],[])
        g.finalize_postrun_audit(report)
        self.assertEqual(report['status'],'failed')
        self.assertTrue(any(r.get('profile')=='Debug' for r in report['postrun_invariant_failures']))

class ComposeControls(unittest.TestCase):
    setUp = Controls.setUp
    prepared_fixture = Controls.prepared_fixture
    prepare_patches = Controls.prepare_patches
    def composer_fixture(self):
        fixture=self.prepared_fixture()
        out,context,retained,original,audit=fixture
        context['tools']={name:'a'*64 for name in c.TOOLS}
        # The accepted audit binds all inputs except its own later-added pin.
        audit['context_binding']=c._binding(context)
        path=Path(context['debug_audit']['path']);path.write_text(json.dumps(audit));context['debug_audit']['sha256']=c.digest(path.read_bytes())
        original.update(requirement_suites={'same':'mapping'},required_tests=['required'],dedicated_crosshost=['geometry-native-crosshost','workspace-geometry-native-crosshost'],bundle_sha256='bundle')
        manifest={'sha256':{'output':'b'*64},'exclusions':[],'artifact_links':[]}
        row={'artifact_sha256':manifest['sha256'],'artifact_exclusions':[],'artifact_links':[]}
        batch=dict(status='profile_batch_passed',report_kind='profile_batch',selected_profiles=['ASan-UBSan'],profiles={'ASan-UBSan':row},batch_id=context['batch_id'],attempt=out.name,candidate=c.CANDIDATE,host='sb16',phase_complete=False,continuation={'context_sha256':'f'*64},postrun_invariant_failures=[],source_sha256_rechecked=True)
        for key in c.CHECKS:batch[key]={'ASan-UBSan':True}
        for key in ('requirement_suites','required_tests','dedicated_crosshost','bundle_sha256'):batch[key]=original[key]
        (out/'sanitizer-batch.json').write_text(json.dumps(batch))
        return fixture,batch,row,manifest

    def compose_patches(self, fixture, row, manifest):
        stack=self.prepare_patches(fixture)
        stack.enter_context(patch.object(c,'_source_closure',return_value=({}, {'bundle_sha256':'bundle'})))
        stack.enter_context(patch.object(c,'audit_profile',return_value=(row,manifest)))
        stack.enter_context(patch.object(c,'_batch_runner',return_value={'pid':123,'files':{}}))
        return stack

    def test_composer_positive_appends_and_preserves_origin(self):
        fixture,batch,row,manifest=self.composer_fixture();out=fixture[0]
        before=(out/'run.json').read_bytes();debug=(out/'Debug'/'kept').read_bytes()
        with self.compose_patches(fixture,row,manifest):
            combined=c.compose(g,self.root,self.root/'inputs','f'*64,{'pid':123})
        self.assertEqual(combined['status'],'local_passed_crosshost_pending')
        self.assertEqual(set(combined['profiles']),set(g.PROFILES))
        self.assertEqual(combined['collector_amendment']['original_failed_run_sha256'],c.digest(before))
        self.assertEqual((out/'run.json').read_bytes(),before)
        self.assertEqual((out/'Debug'/'kept').read_bytes(),debug)
        self.assertTrue((out/'combined-local.json').is_file())

    def test_composer_rejects_otherwise_valid_reduced_or_failed_batch(self):
        fixture,batch,row,manifest=self.composer_fixture();out=fixture[0]
        changes=[('status','failed'),('report_kind','full_local_attempt'),('selected_profiles',['Debug','ASan-UBSan']),('profiles',{}),('batch_id','old'),('candidate','wrong'),('host','sb16-2'),('phase_complete',True),('source_sha256_rechecked',False),('postrun_invariant_failures',[{'check':'failed'}]),('requirement_suites',{}),('required_tests',[]),('bundle_sha256','wrong'),('continuation',{'context_sha256':'0'*64})]
        for key,value in changes:
            changed=copy.deepcopy(batch);changed[key]=value
            (out/'sanitizer-batch.json').write_text(json.dumps(changed))
            expected = ('sanitizer batch integrity failed' if key == 'source_sha256_rechecked' else 'batch requirement/bundle closure changed' if key in ('requirement_suites','required_tests','bundle_sha256') else 'sanitizer batch incomplete or unrelated')
            with self.subTest(key=key),self.compose_patches(fixture,row,manifest),self.assertRaisesRegex(RuntimeError,expected):
                c.compose(g,self.root,self.root/'inputs','f'*64,{'pid':123})
            self.assertFalse((out/'combined-local.json').exists())
        (out/'sanitizer-batch.json').write_text(json.dumps(batch))
        for boundary in ('_source_closure','_origin','_batch_runner','audit_profile'):
            with self.subTest(boundary=boundary),self.compose_patches(fixture,row,manifest),patch.object(c,boundary,side_effect=RuntimeError('drift')),self.assertRaisesRegex(RuntimeError,'drift'):
                c.compose(g,self.root,self.root/'inputs','f'*64,{'pid':123})
            self.assertFalse((out/'combined-local.json').exists())

class ProfileAuditControls(unittest.TestCase):
    setUp = Controls.setUp

    def profile_fixture(self):
        target=self.root/'evidence'/'original'/'Debug';target.mkdir(parents=True)
        source=self.root/'source';source.mkdir()
        names=['geometry-native-crosshost','workspace-geometry-native-crosshost']+['case-%03d'%i for i in range(197)]
        ids=['p10-%03d'%i for i in range(151)]
        (source/'acceptance.lock.json').write_text(json.dumps({'test_ids':ids}))
        query=json.dumps({'tests':[{'name':name} for name in names]}).encode()
        (target/'inventory.log').write_bytes(query)
        inventory=dict(names=sorted(names),count=199,dedicated_crosshost=names[:2],local_names=sorted(names[2:]),sha256=c.digest(query))
        (target/'inventory.json').write_text(json.dumps(inventory))
        for name in c.COMMANDS[1:]:(target/(name+'.log')).write_text('Ran '+('5' if name=='production-off-startup' else '51')+' tests in 0.1s\n\nOK\n')
        (target/'core.xml').write_text('<testsuite>'+''.join('<testcase name="'+name+'"/>' for name in names[2:])+'</testsuite>')
        p10=dict(status='passed',through='P10',selected=151,executed=151,total_baseline=151,not_selected=0,diagnostic_only=False,tests=[dict(test=ident,status='passed',phase='P10') for ident in ids])
        (target/'P10.json').write_text(json.dumps(p10))
        registrations={name:{'path':str(self.root/'storage'/name)} for name in g.STORAGE_LOCAL_EVIDENCE_TESTS}
        augmentation=dict(tests=list(g.STORAGE_LOCAL_EVIDENCE_TESTS),profile={'registrations':registrations},destinations={name:row['path'] for name,row in registrations.items()})
        (target/'metadata-relocation.json').write_text(json.dumps({'storage_local_evidence_augmentation':augmentation}))
        expected=[('inventory',['ctest','--test-dir',str(self.root/'build'/'Debug'),'--show-only=json-v1'])]+[(name,argv) for name,argv,_ in g.profile_stage_commands(source,self.root/'build'/'Debug',self.root/'production-off'/'Debug',target,set(names[:2]))]
        commands=[dict(name=name,command=argv,exit_code=0,assertions_passed=True) for name,argv in expected]
        commands[2]['expected_51']=True
        commands[3].update(junit_cases=197,all_registered_present=True,failed_or_skipped=False)
        commands[4].update(acceptance_rows_valid=True,acceptance={k:p10.get(k) for k in ('status','through','selected','executed','total_baseline','not_selected','diagnostic_only')})
        row=dict(commands=commands,storage_evidence_sha256={'storage':'pin'},storage_cleanup_receipts={'cleanup':'pin'},storage_evidence_exclusions=[])
        report=dict(host='sb16',attempt='original',required_tests=[],profiles={'Debug':row})
        manifest=dict(sha256={'kept':'pin'},exclusions=[],artifact_links=[])
        return target,report,manifest

    def boundaries(self,manifest):
        from contextlib import ExitStack
        stack=ExitStack()
        for name in ('_validate_geometry_inventory','validate_artifact_links','verify_storage_evidence_path'):
            stack.enter_context(patch.object(g,name))
        stack.enter_context(patch.object(c,'_runtime_profile'))
        stack.enter_context(patch.object(g,'verify_runtime',return_value={'xace_wrapper':{'path':'/fixed-wrapper'}}))
        query=(self.root/'evidence'/'original'/'Debug'/'inventory.log').read_bytes()
        stack.enter_context(patch.object(g,'run',return_value=(types.SimpleNamespace(returncode=0,stdout=query),0.01)))
        stack.enter_context(patch.object(g,'artifact_evidence_manifest',return_value=manifest))
        stack.enter_context(patch.object(g,'scan_storage_evidence',return_value=dict(files={'storage':'pin'},cleanups={'cleanup':'pin'},exclusions=[],logs_scanned=0)))
        return stack

    def test_complete_actual_profile_artifacts_and_exact_manifests(self):
        target,report,manifest=self.profile_fixture()
        with self.boundaries(manifest):
            row,actual=c.audit_profile(g,self.root,{'runtime_manifest':{}},report,'Debug',manifest)
        self.assertEqual(row['status'],'passed_needs_crosshost_and_review')
        self.assertEqual(actual,manifest)
        for key,value in (('sha256',{}),('artifact_links',[{'path':'extra'}]),('exclusions',[{'path':'extra'}])):
            expected=copy.deepcopy(manifest);expected[key]=value
            with self.subTest(key=key),self.boundaries(manifest),self.assertRaisesRegex(RuntimeError,'full regular/link/exclusion artifact manifest changed'):
                c.audit_profile(g,self.root,{'runtime_manifest':{}},report,'Debug',expected)

    def test_actual_reduced_failed_and_drifted_profile_refusals(self):
        target,report,manifest=self.profile_fixture()
        for index in range(6):
            changed=copy.deepcopy(report);changed['profiles']['Debug']['commands'][index]['exit_code']=1
            with self.subTest(command=index),self.boundaries(manifest),self.assertRaisesRegex(RuntimeError,'profile command failed or lacks assertions'):c.audit_profile(g,self.root,{'runtime_manifest':{}},changed,'Debug',manifest)
        changed=copy.deepcopy(report);changed['profiles']['Debug']['commands'].pop()
        with self.boundaries(manifest),self.assertRaisesRegex(RuntimeError,'complete ordered profile commands required'):c.audit_profile(g,self.root,{'runtime_manifest':{}},changed,'Debug',manifest)
        for name,value in (('core.xml',b'<testsuite/>'),('P10.json',b'{"status":"failed"}'),('harness.log',b'Ran 50 tests in 0.1s\n\nOK\n'),('inventory.log',b'{"tests":[]}')):
            path=target/name;old=path.read_bytes();path.write_bytes(value)
            expected = {'core.xml':'actual core JUnit is incomplete or failed', 'P10.json':'actual locked P10 is reduced or failed', 'harness.log':'startup/harness output incomplete', 'inventory.log':'inventory output pin changed'}[name]
            with self.subTest(path=name),self.boundaries(manifest),self.assertRaisesRegex(RuntimeError,expected):c.audit_profile(g,self.root,{'runtime_manifest':{}},report,'Debug',manifest)
            path.write_bytes(old)
        with self.boundaries(manifest),patch.object(c,'_runtime_profile',side_effect=RuntimeError('runtime drift')),self.assertRaisesRegex(RuntimeError,'runtime drift'):c.audit_profile(g,self.root,{'runtime_manifest':{}},report,'Debug',manifest)
        for result in (types.SimpleNamespace(returncode=1,stdout=(target/'inventory.log').read_bytes()),types.SimpleNamespace(returncode=0,stdout=b'changed current registrations')):
            with self.boundaries(manifest),patch.object(g,'run',return_value=(result,0.01)),self.assertRaisesRegex(RuntimeError,'current CTest registrations differ from retained inventory'):c.audit_profile(g,self.root,{'runtime_manifest':{}},report,'Debug',manifest)

class LaunchControls(unittest.TestCase):
    setUp = Controls.setUp
    put = Controls.put

    def test_batch_launch_receipt_pins_path_pid_argv_and_context(self):
        context={'host':'sb16','batch_id':'1e58e65d-sb16-san-collector-01'}
        inputs=self.root/'inputs';pin='f'*64
        with patch.object(c,'ARTIFACT_ROOT',self.root),patch.object(c.socket,'gethostname',return_value='sb16'):
            expected=c.runner_directory('sb16',context['batch_id'])
            argv=c._batch_argv(self.root,inputs,pin)
            launch=dict(schema='m07-collector-batch-launch-v1',host='sb16',candidate=c.CANDIDATE,batch_id=context['batch_id'],context_sha256=pin,runner_path=str(expected),pane_pid=123,argv=argv)
            path,sha=self.put('launch.json',launch)
            row=dict(path=str(expected),pid=123,files={},launch={'path':str(path),'sha256':sha})
            with patch.object(c,'_runner',return_value={'pid':123}):
                self.assertEqual(c._batch_runner(g,row,self.root,context,inputs,pin)['pid'],123)
                for key,value in (('pane_pid',124),('runner_path',str(self.root/'wrong')),('argv',['wrong']),('batch_id','old'),('context_sha256','0'*64),('candidate','wrong'),('host','sb16-2'),('schema','wrong')):
                    changed=copy.deepcopy(launch);changed[key]=value
                    path.write_text(json.dumps(changed));row['launch']['sha256']=c.digest(path.read_bytes())
                    with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'actual primary sanitizer launch receipt mismatch'):c._batch_runner(g,row,self.root,context,inputs,pin)
                row['path']=str(self.root/'copied-runner')
                with self.assertRaisesRegex(RuntimeError,'sanitizer canonical runner path mismatch'):c._batch_runner(g,row,self.root,context,inputs,pin)

class GateRoutingControls(unittest.TestCase):
    setUp = Controls.setUp

    def test_actual_gate_singleton_routes_only_new_sanitizer_outputs(self):
        from contextlib import ExitStack
        root=self.root;out=root/'evidence'/'original';(out/'Debug').mkdir(parents=True)
        (out/'Debug'/'kept').write_bytes(b'passed Debug')
        (out/'run.json').write_bytes(b'FAILED original')
        (root/'source').mkdir()
        build=root/'build'/'ASan-UBSan';build.mkdir(parents=True)
        old=root/'ctest-original'/'ASan-UBSan';old.mkdir(parents=True)
        metadata_name='CTestTestfile.cmake'
        (old/metadata_name).write_bytes(b'metadata');pin=c.digest(b'metadata')
        profiles={p:dict(runtime_files={},executables={},ctest_metadata={}) for p in g.PROFILES}
        profiles['ASan-UBSan'].update(ctest_metadata={metadata_name:pin},build='/prepared',xserver_placeholder='/xserver',evidence_root='/old-evidence',inventory=[])
        rec=dict(candidate=c.CANDIDATE,source_sha256={},runtime_manifest={},profiles=profiles,production_off={p:{'runtime_files':{}} for p in g.PROFILES},source_root='/prepared-source')
        receipt=dict(host='sb16',status='verified',bundle_sha256='bundle',metadata_baseline={'ASan-UBSan':{metadata_name:pin}})
        (root/'provenance.json').write_text(json.dumps(rec));(root/'deployment-receipt.json').write_text(json.dumps(receipt))
        info=dict(out=out,report_path=out/'sanitizer-batch.json',storage_attempt='new-storage-id',context_path=str(root/'inputs'),context_sha256='f'*64,original_sha256=c.digest(b'FAILED original'),debug_audit_sha256='a'*64)
        amended=types.SimpleNamespace(prepare_batch=lambda *a:info,write_exclusive=c.write_exclusive)
        loader=types.SimpleNamespace(exec_module=lambda module:None)
        attempt=dict(root=root/'storage',parents=[],attempt={})
        registrations={name:{'path':str(root/'storage'/name)} for name in g.STORAGE_LOCAL_EVIDENCE_TESTS}
        profile=dict(path=str(root/'storage'),registrations=registrations)
        args=types.SimpleNamespace(deployment=root,attempt='original',native_controller='host-actor-controller-native',real_world='actor-transition-real-world',recovery='actor-transition-recovery',host_session='host-session-production-native',held_input='host-actor-controller-native',selected_profiles=['ASan-UBSan'],collector_continuation=root/'inputs',collector_continuation_sha256='f'*64)
        import fcntl
        with ExitStack() as stack:
            stack.enter_context(patch.object(g.socket,'gethostname',return_value='sb16'))
            stack.enter_context(patch.object(fcntl,'flock'))
            real_open=Path.open
            def routed_open(path,*a,**kw):
                if str(path).endswith('/spatial-m06-gate.lock'):
                    return stack.enter_context(real_open(root/'lock',*a,**kw))
                return real_open(path,*a,**kw)
            stack.enter_context(patch.object(Path,'open',routed_open))
            stack.enter_context(patch.object(importlib.util,'spec_from_file_location',return_value=types.SimpleNamespace(loader=loader)))
            stack.enter_context(patch.object(importlib.util,'module_from_spec',return_value=amended))
            reserve=stack.enter_context(patch.object(g,'create_storage_evidence_attempt',return_value=attempt))
            stack.enter_context(patch.object(g,'create_storage_evidence_profile',return_value=profile))
            stack.enter_context(patch.object(g,'verify_runtime',return_value={'xace_wrapper':{'path':'/fixed'}}))
            stack.enter_context(patch.object(g,'replace_metadata',return_value=[]))
            stack.enter_context(patch.object(g,'add_startup_bindings',return_value=([],[])))
            stack.enter_context(patch.object(g,'storage_evidence_source',return_value=root/'old-storage'))
            stack.enter_context(patch.object(g,'relocate_storage_evidence_inventory',return_value=[]))
            stack.enter_context(patch.object(g,'relocate_storage_evidence_metadata',return_value=[]))
            stack.enter_context(patch.object(g,'verify_storage_evidence_path'))
            query=stack.enter_context(patch.object(g,'run',side_effect=RuntimeError('intentional inventory refusal before native commands')))
            self.assertEqual(g.gate(args),1)
        report=json.loads((out/'sanitizer-batch.json').read_text())
        self.assertEqual(report['execution_error']['message'],
                         'intentional inventory refusal before native commands')
        self.assertEqual(report['execution_error']['type'], 'RuntimeError')
        self.assertEqual(g.ctest_metadata(build), [build/metadata_name])
        self.assertEqual((build/metadata_name).read_bytes(), b'metadata')
        reserve.assert_called_once_with(root/'source','sb16','new-storage-id')
        self.assertEqual(query.call_count,1)
        self.assertEqual(query.call_args.args[0],['ctest','--test-dir',str(build),'--show-only=json-v1'])
        self.assertEqual(query.call_args.kwargs['timeout'],120)
        self.assertEqual((out/'run.json').read_bytes(),b'FAILED original')
        self.assertEqual((out/'Debug'/'kept').read_bytes(),b'passed Debug')
        report=json.loads((out/'sanitizer-batch.json').read_text())
        self.assertEqual(report['report_kind'],'profile_batch')
        self.assertEqual(report['selected_profiles'],['ASan-UBSan'])
        self.assertEqual(report['status'],'failed')
        self.assertTrue((out/'ASan-UBSan').is_dir())

class GateSelectionControls(unittest.TestCase):
    setUp = Controls.setUp

    def test_actual_gate_rejects_invalid_selection_before_stage_dispatch(self):
        import fcntl
        root=self.root;(root/'source').mkdir()
        (root/'deployment-receipt.json').write_text(json.dumps(dict(host='sb16',status='verified')))
        (root/'provenance.json').write_text(json.dumps(dict(source_sha256={},runtime_manifest={})))
        args=types.SimpleNamespace(deployment=root,attempt='original',native_controller='host-actor-controller-native',real_world='actor-transition-real-world',recovery='actor-transition-recovery',host_session='host-session-production-native',held_input='host-actor-controller-native')
        real_open=Path.open
        lock_handles=[]
        def routed_open(path,*a,**kw):
            if str(path).endswith('/spatial-m06-gate.lock'):
                handle=real_open(root/'lock',*a,**kw)
                lock_handles.append(handle)
                self.addCleanup(handle.close)
                return handle
            return real_open(path,*a,**kw)
        for selected in ([],['Debug'],['unknown'],['ASan-UBSan','Debug'],['Debug','Debug'],['Debug','ASan-UBSan','ASan-UBSan']):
            args.selected_profiles=selected
            with self.subTest(selected=selected),patch.object(g.socket,'gethostname',return_value='sb16'),patch.object(fcntl,'flock'),patch.object(Path,'open',routed_open),patch.object(g,'run') as run,self.assertRaisesRegex(RuntimeError,'only the full ordered profiles or guarded sanitizer singleton are allowed'):g.gate(args)
            run.assert_not_called()
            self.assertFalse((root/'evidence').exists())
        for handle in lock_handles:
            handle.close()
        self.assertTrue(all(handle.closed for handle in lock_handles))

class OriginControls(unittest.TestCase):
    setUp=Controls.setUp

    def test_actual_origin_guard_positive_and_otherwise_valid_refusals(self):
        host='sb16';out=self.root/'evidence'/'1e58e65d-sb16-local-01';(out/'Debug').mkdir(parents=True)
        marker='execution/retired/staging'
        report=dict(candidate=c.CANDIDATE,host=host,attempt=out.name,status='failed',phase_complete=False,execution_error={'type':'RuntimeError','message':'gate artifact tree contains a file symlink: '+marker},source_sha256_rechecked=True,profiles={'Debug':{}},postrun_invariant_failures=[dict(profile='ASan-UBSan',reason='profile-not-run',check=key) for key in ('metadata_rechecked','storage_evidence_rechecked')])
        for key in c.CHECKS:report[key]={'Debug':True}
        anchors={}
        for name in ('inventory.log','inventory.json','metadata-relocation.json','production-off-startup.log','harness.log','core.log','core.xml','P10.log','P10.json','bounded-storage-git.log','bounded-storage-git/review.json'):
            path=out/'Debug'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode());anchors[name]={'size':len(name.encode()),'sha256':c.digest(name.encode())}
        retained=dict(host=host,pane_retired=True,pane_pid=123,original_run_path=str(out/'run.json'),command_evidence_pins=anchors,symlinks=[{'path':marker}])
        retention=self.root/'retention.json'
        def seal(value,retained_value):
            (out/'run.json').write_text(json.dumps(value));retained_value['original_run']=value;retained_value['original_run_sha256']=c.digest((out/'run.json').read_bytes());retention.write_text(json.dumps(retained_value))
            return dict(host=host,retention={'path':str(retention),'sha256':c.digest(retention.read_bytes())},origin_runner={'path':str(c.runner_directory(host,out.name))})
        context=seal(report,copy.deepcopy(retained))
        with patch.object(c,'_runner'):
            self.assertEqual(c._origin(g,self.root,context)[0],out)
        for key,value in (('status','passed'),('candidate','wrong'),('host','sb16-2'),('phase_complete',True),('source_sha256_rechecked',False),('profiles',{}),('execution_error',{'type':'RuntimeError','message':'other failure'}),('postrun_invariant_failures',[dict(profile='Debug',reason='profile-incomplete',check='metadata_rechecked')])):
            changed=copy.deepcopy(report);changed[key]=value;context=seal(changed,copy.deepcopy(retained))
            expected = ('original failure is not the pinned postexecution collector failure' if key == 'execution_error' else 'original Debug execution/source was incomplete' if key in ('source_sha256_rechecked','profiles') else 'original failure includes an executed-profile invariant' if key == 'postrun_invariant_failures' else 'original failed report changed')
            with self.subTest(key=key),patch.object(c,'_runner'),self.assertRaisesRegex(RuntimeError,expected):c._origin(g,self.root,context)
        for key in c.CHECKS:
            changed=copy.deepcopy(report);changed[key]['Debug']=False;context=seal(changed,copy.deepcopy(retained))
            with self.subTest(check=key),patch.object(c,'_runner'),self.assertRaisesRegex(RuntimeError,'original Debug postcheck failed'):c._origin(g,self.root,context)
        for key,value in (('pane_retired',False),('host','sb16-2'),('original_run_path',str(out/'other.json')),('symlinks',[]),('command_evidence_pins',{})):
            changed=copy.deepcopy(retained);changed[key]=value;context=seal(copy.deepcopy(report),changed)
            expected = ('retained collector failure must have one known marker' if key == 'symlinks' else 'all eleven original command anchors required' if key == 'command_evidence_pins' else 'wrong retained Debug origin')
            with self.subTest(retention=key),patch.object(c,'_runner'),self.assertRaisesRegex(RuntimeError,expected):c._origin(g,self.root,context)
        context=seal(report,copy.deepcopy(retained));context['origin_runner']['path']=str(self.root/'copied-runner')
        with patch.object(c,'_runner'),self.assertRaisesRegex(RuntimeError,'original canonical runner path mismatch'):c._origin(g,self.root,context)
        context=seal(report,copy.deepcopy(retained));(out/'Debug'/'core.log').write_bytes(b'drift')
        with patch.object(c,'_runner'),self.assertRaisesRegex(RuntimeError,'original command artifact changed'):c._origin(g,self.root,context)

class ContextAndSourceControls(unittest.TestCase):
    setUp = Controls.setUp
    put = Controls.put

    def context_fixture(self):
        context=dict(schema=c.SCHEMA,host='sb16',candidate=c.CANDIDATE,deployment=str(self.root),batch_id='1e58e65d-sb16-san-collector-01',capacity_floor_bytes=1,tools={name:c.digest((HERE/name).read_bytes()) for name in c.TOOLS})
        return context

    def test_load_context_and_actual_tool_hashes_positive_and_refusals(self):
        context=self.context_fixture()
        with patch.object(c,'DEPLOYMENT',self.root),patch.object(c.socket,'gethostname',return_value='sb16'):
            path,pin=self.put('context.json',context)
            self.assertEqual(c.load_context(g,self.root,path,pin),context)
            for key,value in (('host','sb16-2'),('candidate','wrong'),('deployment',str(self.root/'wrong')),('schema','wrong'),('batch_id','1e58e65d-sb16-local-01'),('capacity_floor_bytes',0)):
                changed=copy.deepcopy(context);changed[key]=value;path,pin=self.put('context.json',changed)
                expected = {'host':'continuation host/candidate/schema mismatch','candidate':'continuation host/candidate/schema mismatch','deployment':'continuation deployment mismatch','schema':'continuation host/candidate/schema mismatch','batch_id':'invalid unique sanitizer batch identity','capacity_floor_bytes':'reviewed incremental capacity floor required'}[key]
                with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,expected):c.load_context(g,self.root,path,pin)
            for name in c.TOOLS:
                changed=copy.deepcopy(context);changed['tools'][name]='0'*64;path,pin=self.put('context.json',changed)
                with self.subTest(tool=name),self.assertRaisesRegex(RuntimeError,'amended tool changed'):c.load_context(g,self.root,path,pin)
            changed=copy.deepcopy(context);changed['tools'].pop(c.TOOLS[0]);path,pin=self.put('context.json',changed)
            with self.assertRaisesRegex(RuntimeError,'exact amended tool closure required'):c.load_context(g,self.root,path,pin)
            changed=copy.deepcopy(context);changed['tools']['extra.py']='a'*64;path,pin=self.put('context.json',changed)
            with self.assertRaisesRegex(RuntimeError,'exact amended tool closure required'):c.load_context(g,self.root,path,pin)
            path,pin=self.put('context.json',context)
            with self.assertRaisesRegex(RuntimeError,'pinned input changed'):c.load_context(g,self.root,path,'0'*64)
            with patch.object(c.socket,'gethostname',return_value='a5500'),self.assertRaisesRegex(RuntimeError,'continuation host/candidate/schema mismatch'):c.load_context(g,self.root,path,pin)
            other,pin_other=self.put('other.py',b'not the loaded gate')
            with self.assertRaisesRegex(RuntimeError,'loaded gate differs from amended tool closure'):c._tools(types.SimpleNamespace(__file__=str(other)),context)

    def test_real_1040_readonly_source_positive_and_drift_refusals(self):
        source=self.root/'source';source.mkdir()
        manifest={}
        for index in range(1040):
            name='file-%04d'%index;raw=name.encode();path=source/name;path.write_bytes(raw);path.chmod(0o444);manifest[name]=c.digest(raw)
        source.chmod(0o555)
        provenance=dict(candidate=c.CANDIDATE,source_sha256=manifest)
        receipt=dict(candidate=c.CANDIDATE,host='sb16',status='verified')
        provpath,provpin=self.put('provenance.json',provenance)
        recpath,recpin=self.put('deployment-receipt.json',receipt)
        context=dict(host='sb16',provenance_sha256=provpin,deployment_receipt_sha256=recpin)
        self.assertEqual(c._source_closure(g,self.root,context),(provenance,receipt))
        first=source/'file-0000';old=first.read_bytes()
        first.chmod(0o644);first.write_bytes(b'changed');first.chmod(0o444)
        with self.assertRaisesRegex(RuntimeError,'frozen source closure changed'):c._source_closure(g,self.root,context)
        first.chmod(0o644);first.write_bytes(old);first.chmod(0o444)
        first.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'deployed source linked or writable'):c._source_closure(g,self.root,context)
        first.chmod(0o444)
        source.chmod(0o755);first.unlink();source.chmod(0o555)
        with self.assertRaisesRegex(RuntimeError,'frozen source closure changed'):c._source_closure(g,self.root,context)
        source.chmod(0o755);first.symlink_to(source/'file-0001');source.chmod(0o555)
        with self.assertRaisesRegex(RuntimeError,'deployed source linked or writable'):c._source_closure(g,self.root,context)
        source.chmod(0o755);first.unlink();first.write_bytes(old);first.chmod(0o444);source.chmod(0o555)
        self.assertEqual(c._source_closure(g,self.root,context),(provenance,receipt))
        provpath.write_bytes(b'drift')
        with self.assertRaises((RuntimeError,ValueError)):c._source_closure(g,self.root,context)
        source.chmod(0o755)

class ExternalAuditorControls(unittest.TestCase):
    setUp = Controls.setUp
    prepared_fixture = Controls.prepared_fixture
    prepare_patches = Controls.prepare_patches
    composer_fixture = ComposeControls.composer_fixture
    compose_patches = ComposeControls.compose_patches

    def audit_fixture(self):
        fixture,batch,row,manifest=self.composer_fixture()
        row['status']='passed_needs_crosshost_and_review'
        with self.compose_patches(fixture,row,manifest):
            combined=c.compose(g,self.root,self.root/'inputs','f'*64,{'pid':123})
        self.assertEqual(combined['host'], fixture[1]['host'])
        self.assertEqual(combined['candidate'], c.CANDIDATE)
        self.assertEqual(combined['profiles']['Debug']['status'], 'passed_needs_crosshost_and_review')
        self.assertEqual(combined['profiles']['ASan-UBSan']['status'], 'passed_needs_crosshost_and_review')
        return fixture,batch,row,manifest,combined

    def seal_combined(self,out,combined):
        path=out/'combined-local.json';path.write_text(json.dumps(combined));return c.digest(path.read_bytes())

    def test_external_auditor_both_phases_and_exact_profile_manifests(self):
        fixture,batch,row,manifest,combined=self.audit_fixture();out=fixture[0]
        pin=c.digest((out/'combined-local.json').read_bytes())
        for phase in ('before-crosshost','after-crosshost'):
            with self.compose_patches(fixture,row,manifest),patch.object(c,'audit_profile',return_value=(row,manifest)) as profile_audit:
                result=c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,phase)
            self.assertEqual(result['status'],'primary_artifact_recheck_passed')
            self.assertEqual(set(result['profiles']),set(g.PROFILES))
            self.assertEqual([call.args[4] for call in profile_audit.call_args_list],list(g.PROFILES))
            self.assertEqual([call.args[5] for call in profile_audit.call_args_list],[manifest,manifest])
            self.assertTrue((out/(phase+'-artifact-audit.json')).is_file())
        with self.compose_patches(fixture,row,manifest),self.assertRaises(FileExistsError):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,'before-crosshost')

    def test_external_auditor_rejects_contradictory_or_stale_proofs(self):
        fixture,batch,row,manifest,combined=self.audit_fixture();out=fixture[0]
        for key,value in (('status','failed'),('source_sha256_rechecked',False),('postrun_invariant_failures',[{'check':'failed'}]),('selected_profiles',['Debug']),('profiles',{})):
            changed=copy.deepcopy(combined);changed[key]=value;pin=self.seal_combined(out,changed)
            with self.subTest(key=key),self.compose_patches(fixture,row,manifest),self.assertRaisesRegex(RuntimeError, 'combined local proof mismatch' if key == 'status' else 'combined local integrity contradicts success'):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,'before-crosshost')
        for check in c.CHECKS:
            changed=copy.deepcopy(combined);changed[check]['Debug']=False;pin=self.seal_combined(out,changed)
            with self.subTest(check=check),self.compose_patches(fixture,row,manifest),self.assertRaisesRegex(RuntimeError, 'combined local integrity contradicts success'):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,'before-crosshost')
        for key,value in (('original_failed_run_sha256','0'*64),('original_failed_run',str(out/'wrong')),('debug_audit_sha256','0'*64),('sanitizer_batch_sha256','0'*64),('batch_id','old'),('tools',{})):
            changed=copy.deepcopy(combined);changed['collector_amendment'][key]=value;pin=self.seal_combined(out,changed)
            expected = {'original_failed_run_sha256':'combined local proof mismatch', 'original_failed_run':'combined local integrity contradicts success', 'debug_audit_sha256':'combined Debug audit pin mismatch', 'sanitizer_batch_sha256':'pinned input changed', 'batch_id':'combined local integrity contradicts success', 'tools':'combined local proof mismatch'}[key]
            with self.subTest(amendment=key),self.compose_patches(fixture,row,manifest),self.assertRaisesRegex(RuntimeError,expected):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,'before-crosshost')
        pin=self.seal_combined(out,combined)
        with self.compose_patches(fixture,row,manifest),self.assertRaisesRegex(RuntimeError,'pinned input changed'):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,'0'*64,'before-crosshost')
        for boundary in ('_source_closure','_origin','_batch_runner','audit_profile'):
            with self.subTest(boundary=boundary),self.compose_patches(fixture,row,manifest),patch.object(c,boundary,side_effect=RuntimeError('source/origin/link/manifest drift')),self.assertRaisesRegex(RuntimeError,'source/origin/link/manifest drift'):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,'before-crosshost')
        # Re-pin a changed batch inside an otherwise valid combined proof: status must still refuse.
        changed_batch=copy.deepcopy(batch);changed_batch['status']='failed';(out/'sanitizer-batch.json').write_text(json.dumps(changed_batch))
        changed=copy.deepcopy(combined);changed['collector_amendment']['sanitizer_batch_sha256']=c.digest((out/'sanitizer-batch.json').read_bytes());pin=self.seal_combined(out,changed)
        with self.compose_patches(fixture,row,manifest),self.assertRaisesRegex(RuntimeError,'sanitizer batch integrity changed'):c.audit_artifacts(g,self.root,self.root/'inputs','f'*64,pin,'before-crosshost')
        self.assertFalse((out/'before-crosshost-artifact-audit.json').exists())

if __name__=='__main__': unittest.main()