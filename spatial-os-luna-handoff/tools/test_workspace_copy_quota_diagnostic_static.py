"""Pure source/proof checks. Run on a test host before diagnostic fixture execution."""
import ast
import importlib.util
from pathlib import Path
import sys
import unittest

SOURCE=Path(sys.argv.pop(1)).resolve()
RUNNER=SOURCE/'tools/run_workspace_copy_engine.py'
spec=importlib.util.spec_from_file_location('_quota_engine_runner_static',RUNNER)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
POSITIVE='test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch'
CONTROL='test_no_copy_live_writers_sixty_second_diagnostic'


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
        for method in (CONTROL,POSITIVE):
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
        normal=loader_methods(SOURCE/'tests/core/test_workspace_copy_engine.py')
        separate=loader_methods(SOURCE/'tests/core/diagnose_workspace_copy_quota.py')
        self.assertEqual(len(normal),13);self.assertEqual(len(set(normal)),13);self.assertNotIn(CONTROL,normal)
        self.assertEqual(set(separate),{CONTROL,POSITIVE});self.assertEqual(len(separate),2)
        fixture=ast.parse((SOURCE/'tests/core/diagnose_workspace_copy_quota.py').read_text())
        subclass=next(node for node in fixture.body if isinstance(node,ast.ClassDef) and node.name=='QuotaDiagnosticTests')
        self.assertNotIn(POSITIVE,{node.name for node in subclass.body if isinstance(node,ast.FunctionDef)})


if __name__=='__main__':unittest.main()
