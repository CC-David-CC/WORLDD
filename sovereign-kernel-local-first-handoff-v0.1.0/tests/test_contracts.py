import ast
import json
import re
import unittest
from pathlib import Path
from kernel.authority import FIELDS
from kernel.readiness import REQUIRED_GATES
from kernel.objects import verify_bytes

ROOT=Path(__file__).resolve().parents[1]

class ContractTests(unittest.TestCase):
    def test_CONTRACT_001_gate_names_match(self):
        gates=json.loads((ROOT/'tasks/gates.json').read_text())
        self.assertEqual({n['id'] for n in gates['nodes']},set(REQUIRED_GATES))
        self.assertTrue(all(n['delivered_status']=='PENDING_FULL_ACCEPTANCE' for n in gates['nodes']))
    def test_CONTRACT_002_gate_graph_acyclic(self):
        gates=json.loads((ROOT/'tasks/gates.json').read_text())
        adj={n['id']:[] for n in gates['nodes']}
        for e in gates['edges']:
            self.assertIn(e['target'],adj); adj[e['source']].append(e['target'])
        active=set(); done=set()
        def visit(n):
            self.assertNotIn(n,active)
            if n in done:return
            active.add(n)
            for d in adj[n]:visit(d)
            active.remove(n); done.add(n)
        for n in adj: visit(n)
    def test_CONTRACT_003_no_core_network_imports(self):
        banned={'socket','http','urllib','requests','httpx','aiohttp','ftplib','smtplib','websocket','websockets'}
        for path in (ROOT/'kernel').glob('*.py'):
            tree=ast.parse(path.read_text())
            for n in ast.walk(tree):
                names=[x.name for x in n.names] if isinstance(n,ast.Import) else ([n.module or ''] if isinstance(n,ast.ImportFrom) else [])
                self.assertFalse(any(x.split('.')[0] in banned for x in names),str(path))
    def test_CONTRACT_004_capability_schema_fields(self):
        schema=json.loads((ROOT/'schemas/capability.schema.json').read_text())
        self.assertEqual(set(schema['properties']['body']['required']),FIELDS)
        self.assertFalse(schema['properties']['body']['additionalProperties'])
    def test_CONTRACT_005_readiness_schema_gates(self):
        schema=json.loads((ROOT/'schemas/local-ready.schema.json').read_text())
        self.assertEqual(set(schema['properties']['body']['properties']['gates']['required']),set(REQUIRED_GATES))
    def test_CONTRACT_006_examples_verify(self):
        index=json.loads((ROOT/'examples/index.json').read_text())
        for entry in index['objects']:
            obj=verify_bytes(entry['id'],(ROOT/'examples'/entry['file']).read_bytes())
            self.assertEqual(obj['type'],entry['type'])
    def test_CONTRACT_007_inventory_covers_reference_methods(self):
        matrix=json.loads((ROOT/'tasks/acceptance.json').read_text())
        declared={r['method'] for r in matrix['reference_tests']}
        actual=set()
        for path in (ROOT/'tests').glob('test_*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.ClassDef):
                    for n in node.body:
                        if isinstance(n,ast.FunctionDef) and n.name.startswith('test_'):
                            actual.add(f'tests.{path.stem}.{node.name}.{n.name}')
        self.assertEqual(actual,declared)
    def test_CONTRACT_008_future_cases_not_marked_pass(self):
        matrix=json.loads((ROOT/'tasks/acceptance.json').read_text())
        self.assertTrue(matrix['target_acceptance'])
        self.assertTrue(all(r['status']=='SPECIFIED_NOT_IMPLEMENTED' for r in matrix['target_acceptance']))
    def test_CONTRACT_009_no_phase_autostart(self):
        tasks=json.loads((ROOT/'tasks/codex_tasks.json').read_text())
        self.assertEqual(tasks['default_phase'],'P00')
        self.assertTrue(tasks['default_stop_after_phase'])
        self.assertTrue(all(not t['peer_networking_authorized'] for t in tasks['tasks']))
    def test_CONTRACT_010_all_doc_target_ids_in_inventory(self):
        matrix=json.loads((ROOT/'tasks/acceptance.json').read_text())
        expected=set()
        for file in ['TESTING_SPEC.md','NETWORK_BOUNDARY.md']:
            expected.update(re.findall(r'\b([A-Z][A-Z0-9]*-\d{3})\b',(ROOT/'docs'/file).read_text()))
        self.assertEqual(expected,{r['id'] for r in matrix['target_acceptance']})
