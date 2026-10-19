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
from inspect_workspace_copy_fault_fixture import validate_discovered_tests
import broker_pb2 as broker
import process_scope_pb2 as process_scope
from copy_fixture_profiles import (consume_profile, manager_memory_mib, broker_memory_mib,
    build_adapter_resources, bind_definition_resource_devices, require_exact_provisioned_resources,
    instrumentation_preflight, process_scope_budgets, set_resource_io_closure,
    validate_resource_io_closure, configure_copy_fixture_application_profiles, COPY_BINARIES)
from copy_fixture_image_cleanup import (
    cleanup_engine_group, cleanup_file_action_group, cleanup_passed_fixture_images,
    engine_group_passed, file_action_group_passed, require_group_capacity, run_after_capacity_preflight,
    validate_candidate_facts, verify_previous_cleanup, _atomic,
)

def _fixture_budget(devices, offset=0):
    budget=process_scope.ProcessScopeBudget(memory_bytes=128*2**20+offset,
        swap_bytes=offset,processes=8+offset,cpu_microseconds=25000+offset)
    for major,minor,read,write in devices:
        budget.io.add(major=major,minor=minor,read_bytes_per_second=read,
                      write_bytes_per_second=write)
    return budget

def _fixture_policy(policy, devices, offset, workspace=''):
    policy.workspace=workspace
    policy.total.CopyFrom(_fixture_budget(devices,offset))
    policy.actor_default.CopyFrom(_fixture_budget(devices,offset+1))
    policy.actors.add(fixture_actor='fixture-actor',
        maximum=_fixture_budget(devices,offset+2))
    policy.profiles.add(name='fixture-app',maximum=_fixture_budget(devices,offset+3))
    return policy

def _fixture_resource(devices):
    resource=broker.BrokerResourceConfig(protocol_version=2,delegated_cgroup='/fixture/delegated')
    resource.broker_budget.CopyFrom(_fixture_budget(devices,10))
    resource.manager_budget.CopyFrom(_fixture_budget(devices,20))
    _fixture_policy(resource.default_policy,devices,30)
    _fixture_policy(resource.workspaces.add(),devices,40,'explicit-workspace')
    copy_profile=resource.copy_profiles.add(name='fixture-copy',revision=1)
    _fixture_policy(copy_profile.policy,devices,50,'copy-workspace')
    return resource

def _non_io_budget_state(budget):
    copied=type(budget)();copied.CopyFrom(budget);copied.ClearField('io')
    return copied.SerializeToString(deterministic=True)

class CopyFixtureProfileTests(unittest.TestCase):
    def fixture_module(self):
        methods=('test_fault_a','test_fault_b','test_fault_c','test_fault_d','test_fault_e')
        def noop(self):pass
        fault_type=type('WorkspaceCopyFaultTests',(unittest.TestCase,),{name:noop for name in methods})
        auxiliary_names=('test_constructor_a','test_constructor_b')
        auxiliary_type=type('StorageBrokerConstructorFailureTests',(unittest.TestCase,),
                            {name:noop for name in auxiliary_names})
        return SimpleNamespace(WorkspaceCopyFaultTests=fault_type,
            StorageBrokerConstructorFailureTests=auxiliary_type),methods,auxiliary_names

    def test_fault_discovery_separates_five_faults_from_auxiliary_unit_cases(self):
        module,faults,auxiliary=self.fixture_module()
        suite=unittest.TestSuite([module.WorkspaceCopyFaultTests(name) for name in faults]+
            [module.StorageBrokerConstructorFailureTests(name) for name in auxiliary])
        self.assertEqual(validate_discovered_tests(suite,module,faults,auxiliary),(list(faults),list(auxiliary)))

    def test_fault_discovery_rejects_missing_duplicate_or_extra_fault_cases(self):
        module,faults,auxiliary=self.fixture_module()
        valid_aux=[module.StorageBrokerConstructorFailureTests(name) for name in auxiliary]
        invalid_suites=(
            unittest.TestSuite([module.WorkspaceCopyFaultTests(name) for name in faults[:-1]]+valid_aux),
            unittest.TestSuite([module.WorkspaceCopyFaultTests(name) for name in faults[:-1]]+
                [module.WorkspaceCopyFaultTests(faults[0])]+valid_aux),
            unittest.TestSuite([module.WorkspaceCopyFaultTests(name) for name in faults]+
                [module.WorkspaceCopyFaultTests('test_fault_a')]+valid_aux),
        )
        for suite in invalid_suites:
            with self.subTest(cases=suite.countTestCases()),self.assertRaises(AssertionError):
                validate_discovered_tests(suite,module,faults,auxiliary)

        def extra_fault(self):pass
        module.WorkspaceCopyFaultTests.test_fault_unexpected=extra_fault
        unexpected=unittest.TestSuite([module.WorkspaceCopyFaultTests(name) for name in faults]+
            [module.WorkspaceCopyFaultTests('test_fault_unexpected')]+valid_aux)
        with self.assertRaises(AssertionError):
            validate_discovered_tests(unexpected,module,faults,auxiliary)

    def test_fault_discovery_rejects_missing_or_unknown_auxiliary_cases(self):
        module,faults,auxiliary=self.fixture_module()
        fault_cases=[module.WorkspaceCopyFaultTests(name) for name in faults]
        missing=unittest.TestSuite(fault_cases+[module.StorageBrokerConstructorFailureTests(auxiliary[0])])
        extra=unittest.TestSuite(fault_cases+[module.StorageBrokerConstructorFailureTests(name) for name in auxiliary]+
            [unittest.FunctionTestCase(lambda:None)])
        for suite in (missing,extra):
            with self.subTest(cases=suite.countTestCases()),self.assertRaises(AssertionError):
                validate_discovered_tests(suite,module,faults,auxiliary)

    def test_copy_application_profiles_cover_selected_explicit_workspace(self):
        devices=[(7,22,64,16),(259,0,32,8)]
        for explicit in (False,True):
            with self.subTest(explicit=explicit):
                resource=_fixture_resource(devices)
                resource.default_policy.profiles[0].name='development'
                resource.workspaces[0].profiles[0].name='development'
                unrelated=resource.workspaces.add(workspace='unrelated')
                unrelated.CopyFrom(resource.workspaces[0]);unrelated.workspace='unrelated'
                untouched=unrelated.SerializeToString(deterministic=True)
                if not explicit:
                    del resource.workspaces[0]
                before=type(resource)();before.CopyFrom(resource)
                expected=2 if explicit else 1
                self.assertEqual(configure_copy_fixture_application_profiles(resource,'explicit-workspace'),expected)
                policies=[resource.default_policy,*[policy for policy in resource.workspaces
                            if policy.workspace=='explicit-workspace']]
                for policy in policies:
                    self.assertEqual([profile.name for profile in policy.profiles],
                                     ['development','copy-source','copy-app'])
                    for profile in policy.profiles[1:]:
                        maximum=profile.maximum
                        self.assertEqual((maximum.memory_bytes,maximum.processes,maximum.cpu_microseconds),
                                         (64*2**20,8,25000))
                        inherited=type(maximum)();inherited.CopyFrom(policy.profiles[0].maximum)
                        inherited.memory_bytes=64*2**20;inherited.processes=8;inherited.cpu_microseconds=25000
                        self.assertEqual(maximum.SerializeToString(deterministic=True),
                                         inherited.SerializeToString(deterministic=True))
                    del policy.profiles[1:]
                self.assertEqual(resource.SerializeToString(deterministic=True),before.SerializeToString(deterministic=True))
                self.assertEqual(resource.workspaces[-1].SerializeToString(deterministic=True),untouched)

    def test_copy_application_profile_setup_refuses_without_partial_mutation(self):
        for invalid in ('missing','duplicate','undersized'):
            resource=_fixture_resource([(7,22,64,16)])
            resource.default_policy.profiles[0].name='development'
            selected=resource.workspaces[0]
            selected.profiles[0].name='development'
            if invalid=='missing':selected.ClearField('profiles')
            elif invalid=='duplicate':selected.profiles.add(name='copy-source')
            else:selected.profiles[0].maximum.memory_bytes=1
            before=resource.SerializeToString(deterministic=True)
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                configure_copy_fixture_application_profiles(resource,'explicit-workspace')
            self.assertEqual(resource.SerializeToString(deterministic=True),before)

    def test_resource_io_closure_rebinds_every_nested_budget(self):
        ambient=[(7,21,48,12),(259,0,20,10)]
        own_volume=[(7,22,64,16),(259,0,64,16)]
        own_devices=_fixture_budget(own_volume).io
        resource=_fixture_resource(ambient)
        budgets=process_scope_budgets(resource)
        before={id(item):_non_io_budget_state(item) for item in budgets}
        workspace_budget=resource.workspaces[0].total
        workspace_rates=[(48,12),(20,10)]
        self.assertEqual(len(budgets),14)
        self.assertEqual(validate_resource_io_closure(_fixture_resource(own_volume),own_devices),14)
        self.assertEqual(set_resource_io_closure(resource,own_devices),14)
        expected={(major,minor) for major,minor,_,_ in own_volume}
        self.assertEqual({(item.major,item.minor) for item in workspace_budget.io},expected)
        self.assertEqual([(item.read_bytes_per_second,item.write_bytes_per_second)
                          for item in workspace_budget.io],workspace_rates)
        for item in process_scope_budgets(resource):
            self.assertEqual(_non_io_budget_state(item),before[id(item)])
            self.assertEqual({(value.major,value.minor) for value in item.io},expected)
            self.assertEqual({(value.major,value.minor):(value.read_bytes_per_second,
                value.write_bytes_per_second) for value in item.io},
                {(7,22):(48,12),(259,0):(20,10)})
        with self.assertRaisesRegex(ValueError,'fixture volume I/O device set'):
            validate_resource_io_closure(resource,_fixture_budget(ambient).io)

    def test_resource_io_closure_accepts_generic_single_device_fixture_config(self):
        ambient=[(259,0,32,12)]
        own_volume=[(7,22,64,16),(259,0,64,16)]
        own_devices=_fixture_budget(own_volume).io
        resource=_fixture_resource(ambient)
        budgets=process_scope_budgets(resource)
        before={id(item):_non_io_budget_state(item) for item in budgets}
        self.assertEqual(set_resource_io_closure(resource,own_devices),14)
        expected={(major,minor) for major,minor,_,_ in own_volume}
        for item in process_scope_budgets(resource):
            self.assertEqual({(value.major,value.minor) for value in item.io},expected)
            self.assertEqual(_non_io_budget_state(item),before[id(item)])
            self.assertTrue(all(value.read_bytes_per_second<=32 and
                                value.write_bytes_per_second<=12 for value in item.io))

    def test_resource_io_closure_rejects_bad_device_sets_without_mutation(self):
        ambient=[(7,21,48,12),(259,0,20,10)]
        invalid=([],_fixture_budget([(7,22,64,16),(7,22,32,8)]).io,
                 _fixture_budget([(7,22,0,16)]).io,_fixture_budget([(7,22,64,0)]).io)
        for devices in invalid:
            with self.subTest(devices=devices):
                resource=_fixture_resource(ambient)
                before=[[(item.major,item.minor,item.read_bytes_per_second,
                          item.write_bytes_per_second) for item in budget.io]
                        for budget in process_scope_budgets(resource)]
                with self.assertRaises(ValueError):set_resource_io_closure(resource,devices)
                after=[[(item.major,item.minor,item.read_bytes_per_second,
                         item.write_bytes_per_second) for item in budget.io]
                       for budget in process_scope_budgets(resource)]
                self.assertEqual(after,before)

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

    def test_adapter_broker_headroom_and_fallback_budgets_follow_profile(self):
        mib=2**20
        for profile,manager,broker_limit in (('Debug',256,1024),('ASan-UBSan',512,1280)):
            with self.subTest(profile=profile):
                self.assertEqual(manager_memory_mib(profile),manager)
                self.assertEqual(broker_memory_mib(profile),broker_limit)
                funded=(manager+256+128+128)
                self.assertEqual(broker_limit-funded,256)
                self.assertGreater(broker_memory_mib(profile),funded)
                config=build_adapter_resources(profile,259,7,broker,process_scope)
                self.assertEqual(config.broker_budget.memory_bytes,broker_limit*mib)
                self.assertEqual(config.broker_budget.processes,128)
                self.assertEqual(config.broker_budget.cpu_microseconds,400000)
                self.assertEqual(config.manager_budget.memory_bytes,manager*mib)
                policy=config.default_policy
                self.assertEqual(policy.total.memory_bytes,512*mib)
                self.assertEqual(policy.actor_default.memory_bytes,256*mib)
                self.assertEqual({p.name:p.maximum.memory_bytes for p in policy.profiles},
                                 {'development':128*mib,'read-only':128*mib})
                self.assertEqual(len(process_scope_budgets(config)),6)
                for budget in process_scope_budgets(config):
                    self.assertEqual(budget.swap_bytes,0)
                    self.assertEqual([(x.major,x.minor,x.read_bytes_per_second,x.write_bytes_per_second)
                                      for x in budget.io],[(259,7,64*mib,16*mib)])
        with self.assertRaises(ValueError):broker_memory_mib('unknown')
        with self.assertRaises(ValueError):build_adapter_resources('Debug',-1,7,broker,process_scope)

    def test_provisioned_resource_match_uses_exact_pinned_custom_policy(self):
        mib=2**20
        pinned=build_adapter_resources('Debug',1,0,broker,process_scope)
        for budget in process_scope_budgets(pinned):budget.ClearField('io')
        pinned.broker_budget.memory_bytes=1408*mib
        devices=process_scope.ProcessScopeBudget()
        devices.io.add(major=259,minor=8,read_bytes_per_second=32*mib,
                       write_bytes_per_second=8*mib)
        devices.io.add(major=259,minor=7,read_bytes_per_second=64*mib,
                       write_bytes_per_second=16*mib)
        actual=bind_definition_resource_devices(pinned,devices.io)
        self.assertEqual(require_exact_provisioned_resources(actual,pinned,devices.io),actual)
        self.assertEqual(actual.broker_budget.memory_bytes,1408*mib)
        self.assertEqual([(item.major,item.minor) for item in actual.broker_budget.io],
                         [(259,7),(259,8)])
        altered=type(actual)();altered.CopyFrom(actual)
        altered.default_policy.profiles[0].maximum.memory_bytes+=mib
        with self.assertRaisesRegex(ValueError,'differ from pinned'):
            require_exact_provisioned_resources(altered,pinned,devices.io)
        mismatched=process_scope.ProcessScopeBudget()
        mismatched.io.add(major=259,minor=8,read_bytes_per_second=64*mib,
                          write_bytes_per_second=16*mib)
        with self.assertRaisesRegex(ValueError,'differ from pinned'):
            require_exact_provisioned_resources(actual,pinned,mismatched.io)
        with self.assertRaisesRegex(ValueError,'must not prebind'):
            bind_definition_resource_devices(actual,devices.io)

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
