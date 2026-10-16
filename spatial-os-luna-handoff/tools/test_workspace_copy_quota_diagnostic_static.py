"""Pure source/proof checks. Run on a test host before diagnostic fixture execution."""
import ast
import importlib.util
from pathlib import Path
import sys
import runpy
from types import SimpleNamespace
from unittest.mock import patch
import unittest

SOURCE=Path(sys.argv.pop(1)).resolve()
RUNNER=SOURCE/'tools/run_workspace_copy_engine.py'
spec=importlib.util.spec_from_file_location('_quota_engine_runner_static',RUNNER)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
POSITIVE='test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch'
CONTROL='test_no_copy_live_writers_sixty_second_diagnostic'
NEGATIVE='test_small_source_quota_rejects_actual_writer_and_preserves_peer_application'


def constants(statements):
    return {target.id:node.value.value for node in statements if isinstance(node,ast.Assign)
            and isinstance(node.value,ast.Constant) for target in node.targets if isinstance(target,ast.Name)}


def loader_methods(path):
    module=ast.parse(path.read_text());load=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='load_tests')
    return [node.value for node in ast.walk(load) if isinstance(node,ast.Constant)
            and isinstance(node.value,str) and node.value.startswith('test_')]


class QuotaDiagnosticStaticTests(unittest.TestCase):
    def test_default_selected_method_proof_stays_exact(self):
        line=f'{POSITIVE} (__main__.WorkspaceCopyEngineTests.{POSITIVE}) ... ok'
        self.assertTrue(runner.selected_method_passed(line,POSITIVE))
        self.assertFalse(runner.selected_method_passed(line,CONTROL))
        self.assertFalse(runner.selected_method_passed(line.replace(' ... ok',' ... ERROR'),POSITIVE))

    def test_diagnostic_class_requires_its_explicit_method_proof(self):
        for method in (CONTROL,POSITIVE,NEGATIVE):
            line=f'{method} (__main__.QuotaDiagnosticTests.{method}) ... ok'
            self.assertFalse(runner.selected_method_passed(line,method))
            self.assertTrue(runner.selected_method_passed(line,method,'QuotaDiagnosticTests'))
            other=CONTROL if method==POSITIVE else POSITIVE
            self.assertFalse(runner.selected_method_passed(line,other,'QuotaDiagnosticTests'))

    def test_diagnostic_labels_and_loader_cannot_be_a_gate_pass(self):
        module=ast.parse(RUNNER.read_text());main=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        defaults=constants(main.body)
        self.assertEqual(defaults['fixture_class'],'WorkspaceCopyEngineTests');self.assertEqual(defaults['fixture_count'],13)
        self.assertEqual(defaults['completed_status'],'focused-pass')
        diagnostic=next(node for node in main.body if isinstance(node,ast.If) and ast.unparse(node.test)=='fixture_spec is not None')
        selected=constants(diagnostic.body)
        self.assertEqual(selected['qualification'],'UNQUALIFIED_DIAGNOSTIC')
        self.assertEqual(selected['completed_status'],'diagnostic-complete');self.assertNotEqual(selected['completed_status'],'focused-pass')
        env_guard=next(node for node in ast.walk(main) if isinstance(node,ast.If)
            and ast.unparse(node.test)=='fixture_spec is not None'
            and any(isinstance(child,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='fixture_index'
                for target in child.targets) for child in node.body))
        index=env_guard.body[0]
        self.assertEqual(ast.unparse(index.value), "command.index(str(source / 'tools/with_adapter_resources.py')) + 2")
        self.assertEqual(ast.unparse(env_guard.body[1].test), "command[fixture_index - 1] == '--' and command[fixture_index] == sys.executable")
        insertion=env_guard.body[2]
        self.assertEqual(ast.unparse(insertion.targets[0]),'command[fixture_index:fixture_index]')
        self.assertEqual(insertion.value.elts[0].value,'env')
        self.assertEqual(insertion.value.elts[1].left.value,'SPATIAL_COPY_QUOTA_DIAGNOSTIC_DIR=')
        self.assertEqual(insertion.value.elts[2].left.value,'SPATIAL_COPY_QUOTA_DIAGNOSTIC_MODE=')
        normal=loader_methods(SOURCE/'tests/core/test_workspace_copy_engine.py')
        separate=loader_methods(SOURCE/'tests/core/diagnose_workspace_copy_quota.py')
        self.assertEqual(len(normal),13);self.assertEqual(len(set(normal)),13);self.assertNotIn(CONTROL,normal)
        self.assertEqual(set(separate),{CONTROL,POSITIVE,NEGATIVE});self.assertEqual(len(separate),3)
        self.assertIn(NEGATIVE,normal)
        self.assertEqual(selected['fixture_count'],3)
        fixture=ast.parse((SOURCE/'tests/core/diagnose_workspace_copy_quota.py').read_text())
        subclass=next(node for node in fixture.body if isinstance(node,ast.ClassDef) and node.name=='QuotaDiagnosticTests')
        self.assertFalse({POSITIVE,NEGATIVE}&{node.name for node in subclass.body if isinstance(node,ast.FunctionDef)})

    def test_private_driver_selects_exact_methods_and_preserves_bounds(self):
        driver=SOURCE/'tools/run_workspace_copy_quota_diagnostic.py'
        expected={'nocopy':(CONTROL,120),'copy':(POSITIVE,360),'smallquota':(NEGATIVE,120)}
        for mode,(method,bound) in expected.items():
            calls=[]
            def capture(**kwargs):calls.append((list(sys.argv),kwargs['fixture_spec']))
            with patch.dict(sys.modules,{'run_workspace_copy_engine':SimpleNamespace(main=capture)}), \
                 patch.object(sys,'argv',[str(driver),'BUILD','SOURCE','EVIDENCE',mode]):
                runpy.run_path(str(driver),run_name='__main__')
            self.assertEqual(len(calls),1)
            argv,spec=calls[0]
            self.assertEqual(argv,[str(driver),'BUILD','SOURCE','EVIDENCE',method])
            self.assertEqual(spec,{'fixture':'diagnose_workspace_copy_quota.py','discovery':'inspect_workspace_copy_quota_diagnostic.py',
                 'class':'QuotaDiagnosticTests','mode':mode,'runtime_bound':bound})
        calls=[]
        with patch.dict(sys.modules,{'run_workspace_copy_engine':SimpleNamespace(main=lambda **kwargs:calls.append(kwargs))}), \
             patch.object(sys,'argv',[str(driver),'BUILD','SOURCE','EVIDENCE','unknown']):
            with self.assertRaises(AssertionError):runpy.run_path(str(driver),run_name='__main__')
        self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
