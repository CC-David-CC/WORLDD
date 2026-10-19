"""Pure behavior checks for explicit copy fixture resource profiles."""
import os
import json
import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path
import subprocess
import stat
import tempfile
import unittest
from unittest.mock import patch
from copy_fixture_profiles import consume_profile, manager_memory_mib, instrumentation_preflight, COPY_BINARIES
from copy_fixture_image_cleanup import (
    cleanup_engine_group, cleanup_file_action_group, cleanup_passed_fixture_images,
    engine_group_passed, file_action_group_passed, require_group_capacity, run_after_capacity_preflight,
    validate_candidate_facts, verify_previous_cleanup, _atomic,
)

class CopyFixtureProfileTests(unittest.TestCase):
    def test_explicit_profiles_preserve_arguments_and_budget(self):
        original=['runner','BUILD','SOURCE','EVIDENCE','test_exact']
        for profile,limit in [('Debug',256),('ASan-UBSan',512)]:
            arguments,actual=consume_profile(original+['--profile='+profile])
            self.assertEqual(arguments,original);self.assertEqual(actual,profile)
            self.assertEqual(manager_memory_mib(actual),limit)
            adapter,selected=consume_profile(['--copy-profile='+profile],'--copy-profile=')
            self.assertEqual(adapter,[]);self.assertEqual(selected,profile)

    def test_default_and_other_adapter_modes_keep_debug_envelope(self):
        arguments=['--','python3','fixture.py','/path/ASan-UBSan']
        with patch.dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1'):
            unchanged,profile=consume_profile(arguments,'--copy-profile=')
        self.assertEqual(unchanged,arguments);self.assertEqual(profile,'Debug')
        self.assertEqual(manager_memory_mib(profile),256)

    def test_unknown_duplicate_and_misplaced_profiles_are_rejected(self):
        for arguments in (['--profile=SAN'],['--profile='],
                          ['--profile=Debug','extra'],['--profile=Debug','--profile=ASan-UBSan']):
            with self.assertRaises(ValueError):consume_profile(arguments)
        with self.assertRaises(ValueError):manager_memory_mib('unknown')

    def test_san_preflight_rejects_mixed_and_records_actual_binaries(self):
        both=' (NEEDED) Shared library: [libasan.so.8]\n (NEEDED) Shared library: [libubsan.so.1]\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in COPY_BINARIES:(Path(directory)/name).write_bytes(name.encode())
            def result(argv,**kwargs):
                return subprocess.CompletedProcess(argv,0,both if Path(argv[-1]).name!='worldd' else '', '')
            with patch('copy_fixture_profiles.subprocess.run',side_effect=result):
                with self.assertRaisesRegex(ValueError,'worldd'):instrumentation_preflight(directory,'ASan-UBSan')
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,both,'')):
                proof=instrumentation_preflight(directory,'ASan-UBSan')
            self.assertEqual(set(proof['binaries']),set(COPY_BINARIES))
            self.assertEqual(proof['manager_memory_bytes'],512*2**20)
            for value in proof['binaries'].values():self.assertEqual(len(value['sha256']),64)

    def test_debug_preflight_rejects_san_and_mixed_executables(self):
        plain=' (NEEDED) Shared library: [libc.so.6]\n'
        sanitized=' (NEEDED) Shared library: [libasan.so.8]\n (NEEDED) Shared library: [libubsan.so.1]\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in COPY_BINARIES:(Path(directory)/name).write_bytes(name.encode())
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,sanitized,'')):
                with self.assertRaisesRegex(ValueError,'workspaced'):instrumentation_preflight(directory,'Debug')
            def mixed(argv,**kwargs):
                return subprocess.CompletedProcess(argv,0,sanitized if Path(argv[-1]).name=='worldd' else plain,'')
            with patch('copy_fixture_profiles.subprocess.run',side_effect=mixed):
                with self.assertRaisesRegex(ValueError,'worldd'):instrumentation_preflight(directory,'Debug')
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,plain,'')):
                proof=instrumentation_preflight(directory,'Debug')
            self.assertEqual(set(proof['binaries']),set(COPY_BINARIES))
            self.assertEqual(proof['manager_memory_bytes'],256*2**20)

    def test_supervisor_preflight_checks_the_added_actual_executable(self):
        plain=' (NEEDED) Shared library: [libc.so.6]\n'
        sanitized=' (NEEDED) Shared library: [libasan.so.8]\n (NEEDED) Shared library: [libubsan.so.1]\n'
        with tempfile.TemporaryDirectory() as directory:
            for name in (*COPY_BINARIES,'spatial-authority-supervisor'):(Path(directory)/name).write_bytes(name.encode())
            def mixed(argv,**kwargs):
                return subprocess.CompletedProcess(argv,0,sanitized if Path(argv[-1]).name=='spatial-authority-supervisor' else plain,'')
            with patch('copy_fixture_profiles.subprocess.run',side_effect=mixed):
                with self.assertRaisesRegex(ValueError,'spatial-authority-supervisor'):
                    instrumentation_preflight(directory,'Debug',additional_binaries=('spatial-authority-supervisor',))
            with patch('copy_fixture_profiles.subprocess.run',return_value=subprocess.CompletedProcess([],0,plain,'')):
                proof=instrumentation_preflight(directory,'Debug',additional_binaries=('spatial-authority-supervisor',))
            self.assertEqual(set(proof['binaries']),set(COPY_BINARIES)|{'spatial-authority-supervisor'})

    def test_file_based_runner_imports_find_adjacent_helper(self):
        tools=Path(__file__).resolve().parent
        for name in ('run_workspace_copy_engine','run_workspace_copy_faults'):
            with patch.object(sys,'path',[item for item in sys.path if Path(item or '.').resolve()!=tools]), \
                 patch.dict(sys.modules):
                sys.modules.pop('copy_fixture_profiles',None)
                spec=importlib.util.spec_from_file_location('profile_import_'+name,tools/(name+'.py'))
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                self.assertTrue(callable(module.main))
                self.assertEqual(module.consume_profile(['runner'])[1],'Debug')


class CopyFixtureImageCleanupTests(unittest.TestCase):
    def make_receipt(self, root, suffix='a'):
        root=Path(root); root.chmod(0o700)
        evidence=root/('case-'+suffix); evidence.mkdir(mode=0o700)
        name='m07-engine-'+(suffix*16)[:16]
        entry='/var/lib/spatial/volumes/'+name
        record=dict(name=name,entry=entry,volume=entry+'/mount',
            definition_hex='01',created=dict(filesystem_id='ab'*16),
            cleanup='unmounted',cleanup_failures=[])
        path=evidence/'volume.json'
        _atomic(path,record)
        build=root/'build'/'python'; build.mkdir(parents=True,mode=0o700,exist_ok=True)
        return path,build,evidence.parent

    def successful_root(self, paths):
        items=[]
        for path in paths:
            receipt=json.loads(Path(path).read_text())
            name=receipt['name']; entry=receipt['entry']; image=entry+'/volume.img'
            items.append(dict(receipt=str(Path(path).resolve()),proof=dict(
                status='removed',name=name,entry=entry,image=image,filesystem_id='ab'*16,
                image_device=2049,image_inode=98765,image_size=512*2**20,record_size=512*2**20,
                record_device=2049,record_inode=98765,mountpoint_absent=True,
                loop_detached=True,lock_acquired=True,removed_at=1.0)))
        report=dict(status='removed',items=items,recovered_bytes=len(items)*512*2**20)
        return lambda argv,**kwargs:subprocess.CompletedProcess(argv,0,json.dumps(report),'')

    def test_engine_group_success_cleans_only_after_recovery_and_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt,build,evidence=self.make_receipt(temporary)
            report=dict(runtime_status='focused-pass',selected_methods=1)
            result=cleanup_engine_group([receipt],build,evidence,report=report,
                completed_status='focused-pass',recovery=[dict(confirmed=True,inventory=str(receipt))],
                restoration=[dict(path='/usr/local/libexec/spatial-copy-tree-worker',restored=True),
                             dict(path='/usr/local/libexec/spatial-copy-finalize-worker',restored=True)],
                runner=self.successful_root([receipt]))
            self.assertEqual(result['status'],'removed')
            stored=json.loads(receipt.read_text())
            self.assertEqual(stored['image_cleanup']['status'],'removed')
            self.assertTrue((receipt.parent/'image-cleanup.json').is_file())
            verified=dict(status='verified-already-removed',name=stored['name'],
                          filesystem_id='ab'*16,image_inode=98765,image_size=512*2**20)
            def verify_root(argv,**kwargs):
                saved=json.loads(Path(argv[-1]).read_text())
                sidecar=json.loads((Path(argv[-1]).parent/'image-cleanup.json').read_text())
                self.assertEqual(saved['image_cleanup'],sidecar)
                self.assertEqual(saved['image_cleanup']['record_size'],saved['image_cleanup']['image_size'])
                return subprocess.CompletedProcess(argv,0,json.dumps(verified),'')
            self.assertEqual(verify_previous_cleanup(receipt,build,runner=verify_root),verified)

    def test_file_action_group_success_cleans_only_engine_derived_cases(self):
        class EngineCase(unittest.TestCase):
            def runTest(self): pass
        with tempfile.TemporaryDirectory() as temporary:
            receipt,build,evidence=self.make_receipt(temporary)
            case=EngineCase(); case.copy_evidence=receipt.parent
            suite=unittest.TestSuite([case])
            result=SimpleNamespace(wasSuccessful=lambda:True,testsRun=1,skipped=[])
            summary=cleanup_file_action_group(result,suite,EngineCase,build,
                runner=self.successful_root([receipt]))
            self.assertEqual(summary['status'],'removed')
            self.assertTrue(json.loads(receipt.read_text())['image_cleanup'])

    def test_body_and_teardown_failures_retain_images_after_real_unittest_results(self):
        class EngineCase(unittest.TestCase):
            def runTest(self): pass
        class BodyFailure(EngineCase):
            def runTest(self): self.fail('body failed')
        class TeardownFailure(EngineCase):
            def tearDown(self): self.fail('teardown failed')
        for suffix,case_type in (('b',BodyFailure),('c',TeardownFailure)):
            with self.subTest(case=case_type.__name__),tempfile.TemporaryDirectory() as temporary:
                receipt,build,evidence=self.make_receipt(temporary,suffix)
                case=case_type(); case.copy_evidence=receipt.parent
                suite=unittest.TestSuite([case]); cases=list(suite)
                result=unittest.TextTestRunner(stream=__import__('io').StringIO()).run(suite)
                self.assertFalse(result.wasSuccessful())
                def forbidden(*args,**kwargs): self.fail('failed suite must not call root cleanup')
                outcome=cleanup_file_action_group(result,cases,EngineCase,build,runner=forbidden)
                self.assertEqual(outcome['status'],'retained-suite-failed')
                self.assertNotIn('image_cleanup',json.loads(receipt.read_text()))

    def test_capacity_preflight_sizes_each_group_and_does_not_run_on_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch('copy_fixture_image_cleanup.os.statvfs',
                       return_value=SimpleNamespace(f_bavail=1024,f_frsize=1024)):
                with self.assertRaisesRegex(ValueError,'insufficient free space'):
                    run_after_capacity_preflight(temporary,12,lambda:self.fail("fixture body ran"))
            with patch('copy_fixture_image_cleanup.os.statvfs',
                       return_value=SimpleNamespace(f_bavail=1024,f_frsize=1024)):
                with self.assertRaisesRegex(ValueError,'insufficient free space'):
                    run_after_capacity_preflight(temporary,13,lambda:self.fail("fixture body ran"))
            with patch('copy_fixture_image_cleanup.os.statvfs',
                       return_value=SimpleNamespace(f_bavail=10*2**30,f_frsize=1)):
                cap12,_=run_after_capacity_preflight(temporary,12,lambda:"ran")
                cap13,_=run_after_capacity_preflight(temporary,13,lambda:"ran")
            self.assertEqual(cap12['required_bytes'],7*2**30)
            self.assertEqual(cap13['required_bytes'],(15*2**30)//2)

    def test_engine_group_rejects_empty_short_duplicate_and_bad_restoration_lists(self):
        report=dict(runtime_status='focused-pass',selected_methods=3)
        recovery=[dict(confirmed=True,inventory='one'),dict(confirmed=True,inventory='two'),
                  dict(confirmed=True,inventory='three')]
        restoration=[dict(path='/usr/local/libexec/spatial-copy-tree-worker',restored=True),
                     dict(path='/usr/local/libexec/spatial-copy-finalize-worker',restored=True)]
        self.assertTrue(engine_group_passed(report,'focused-pass',recovery,restoration))
        self.assertFalse(engine_group_passed(report,'focused-pass',[],restoration))
        self.assertFalse(engine_group_passed(report,'focused-pass',recovery[:2],restoration))
        duplicate=list(recovery);duplicate[2]=dict(confirmed=True,inventory='two')
        self.assertFalse(engine_group_passed(report,'focused-pass',duplicate,restoration))
        self.assertFalse(engine_group_passed(report,'focused-pass',recovery,restoration[:1]))
        wrong=[dict(path='/tmp/other-helper',restored=True),restoration[1]]
        self.assertFalse(engine_group_passed(report,'focused-pass',recovery,wrong))

    def test_root_cleanup_proof_contains_actual_record_size_field(self):
        expected=dict(path='/tmp/volume.json',name='m07-engine-'+'a'*16,
            entry='/var/lib/spatial/volumes/m07-engine-'+'a'*16,
            volume='/var/lib/spatial/volumes/m07-engine-'+'a'*16+'/mount',
            image='/var/lib/spatial/volumes/m07-engine-'+'a'*16+'/volume.img',
            filesystem_id='ab'*16)
        facts=dict(record_device=2049,record_inode=1234,record_size=512*2**20,
            image=dict(device=2049,inode=1234,size=512*2**20))
        candidate=dict(expected=expected,facts=facts,lockfd=12,imagefd=10,entryfd=11)
        before=SimpleNamespace(st_dev=2049,st_ino=1234,st_size=512*2**20,st_nlink=1)
        after=SimpleNamespace(st_dev=2049,st_ino=1234,st_size=512*2**20,st_nlink=0)
        with patch('copy_fixture_image_cleanup.os.getuid',return_value=0), \
             patch('copy_fixture_image_cleanup.socket.gethostname',return_value='sb16'), \
             patch('copy_fixture_image_cleanup.ROOT',new=SimpleNamespace(lstat=lambda:SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0))), \
             patch('copy_fixture_image_cleanup._root_candidate',return_value=candidate), \
             patch('copy_fixture_image_cleanup._mount_absent',return_value=True), \
             patch('copy_fixture_image_cleanup._loop_detached',return_value=True), \
             patch('copy_fixture_image_cleanup.os.stat',return_value=before), \
             patch('copy_fixture_image_cleanup.os.fstat',side_effect=[before,after]), \
             patch('copy_fixture_image_cleanup.os.unlink'), \
             patch('copy_fixture_image_cleanup.os.path.lexists',return_value=False), \
             patch('copy_fixture_image_cleanup.os.close'):
            result=__import__('copy_fixture_image_cleanup').root_cleanup(['/receipt'],1000,'/bindings')
        proof=result['items'][0]['proof']
        self.assertEqual(proof['image_size'],512*2**20)
        self.assertEqual(proof['record_size'],512*2**20)

    def test_engine_group_refuses_unconfirmed_volume_recovery_or_helper_restore(self):
        report=dict(runtime_status='focused-pass')
        self.assertFalse(engine_group_passed(report,'focused-pass',[dict(confirmed=False)],[dict(restored=True)]))
        self.assertFalse(engine_group_passed(report,'focused-pass',[dict(confirmed=True)],[dict(restored=False)]))

    def test_unknown_or_teardown_failed_receipt_is_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt,build,evidence=self.make_receipt(temporary)
            value=json.loads(receipt.read_text()); value['cleanup']='unconfirmed'
            receipt.write_text(json.dumps(value)); receipt.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'teardown did not confirm clean unmount'):
                cleanup_passed_fixture_images([receipt],build,evidence,suite_success=True,
                    expected_count=1,runner=lambda *a,**k:self.fail('invalid receipt reached root'))
        with tempfile.TemporaryDirectory() as temporary:
            receipt,build,evidence=self.make_receipt(temporary)
            value=json.loads(receipt.read_text()); value['cleanup_failures']=['broker close failed']
            receipt.write_text(json.dumps(value)); receipt.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'teardown did not confirm clean unmount'):
                cleanup_passed_fixture_images([receipt],build,evidence,suite_success=True,
                    expected_count=1,runner=lambda *a,**k:self.fail('failed teardown reached root'))

    def test_mount_loop_inode_and_lock_failures_are_retained(self):
        expected=dict(filesystem_id='ab'*16)
        base=dict(mountpoint_absent=True,loop_detached=True,lock_acquired=True,record_valid=True,
            record_device=2049,record_inode=98765,record_size=512*2**20,
            image=dict(regular=True,uid=0,nlink=1,mode=0o600,device=2049,inode=98765,
                       size=512*2**20,filesystem_id='ab'*16))
        for field,value,message in (
            ('mountpoint_absent',False,'mount is live'),
            ('loop_detached',False,'loop'),
            ('lock_acquired',False,'lock'),
        ):
            facts=dict(base); facts[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,message):
                validate_candidate_facts(expected,facts)
        facts=dict(base); facts['image']=dict(base['image'],inode=123)
        with self.assertRaisesRegex(ValueError,'identity or capacity'):
            validate_candidate_facts(expected,facts)

    def test_root_proof_must_match_exact_fixture_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt,build,evidence=self.make_receipt(temporary)
            def altered(argv,**kwargs):
                report=json.loads(self.successful_root([receipt])(argv,**kwargs).stdout)
                report['items'][0]['proof']['image_inode']+=1
                return subprocess.CompletedProcess(argv,0,json.dumps(report),'')
            with self.assertRaisesRegex(ValueError,'proof'):
                cleanup_passed_fixture_images([receipt],build,evidence,suite_success=True,
                    expected_count=1,runner=altered)
            self.assertNotIn('image_cleanup',json.loads(receipt.read_text()))


    def test_partial_root_cleanup_records_only_confirmed_unlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            first,build,evidence=self.make_receipt(temporary,'d')
            second,_,_=self.make_receipt(temporary,'e')
            def partial(argv,**kwargs):
                report=json.loads(self.successful_root([first,second])(argv,**kwargs).stdout)
                report["status"]="incomplete";report["items"]=report["items"][:1]
                report["recovered_bytes"]=report["items"][0]["proof"]["image_size"]
                return subprocess.CompletedProcess(argv,2,json.dumps(report),"")
            with self.assertRaisesRegex(RuntimeError,"cleanup incomplete"):
                cleanup_passed_fixture_images([first,second],build,evidence,suite_success=True,
                    expected_count=2,runner=partial)
            self.assertEqual(json.loads(first.read_text())["image_cleanup"]["status"],"removed")
            self.assertNotIn("image_cleanup",json.loads(second.read_text()))

if __name__=='__main__':unittest.main(verbosity=2)
