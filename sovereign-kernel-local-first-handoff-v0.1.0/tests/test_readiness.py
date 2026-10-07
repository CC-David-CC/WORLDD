from copy import deepcopy
import unittest
from kernel.canonical import Invalid
from kernel.objects import make, identity
from kernel.readiness import REQUIRED_GATES, verify_ready
from tests.fixtures import ROOT_KEY, OTHER_KEY, RESOURCE, POLICY, sign, who, resign

class ReadinessTests(unittest.TestCase):
    def setUp(self):
        refs={'build':identity(RESOURCE),'state':identity(POLICY),'requirements':identity(make('requirements',{})),'evidence':identity(make('evidence',{}))}
        self.statement=sign(make('local-ready',{'challenge':'synthetic-test-only','gates':{g:'PASS' for g in REQUIRED_GATES}},refs),ROOT_KEY)
        self.kw={'trusted_verifier':who(ROOT_KEY),'expected_build':refs['build'],'expected_state':refs['state'],'challenge':'synthetic-test-only','expected_requirements':refs['requirements'],'expected_evidence':refs['evidence']}
    def test_READY_001_synthetic_statement(self):
        self.assertEqual(verify_ready(self.statement,**self.kw).statement_id,identity(self.statement))
    def test_READY_002_wrong_signer(self):
        with self.assertRaises(Invalid): verify_ready(resign(self.statement,OTHER_KEY),**self.kw)
    def test_READY_003_wrong_build(self):
        self.kw['expected_build']=identity(POLICY)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_004_wrong_state(self):
        self.kw['expected_state']=identity(RESOURCE)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_005_stale_challenge(self):
        self.kw['challenge']='fresh'
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_006_pending_gate(self):
        self.statement['body']['gates']['agent-confinement']='PENDING'; self.statement=resign(self.statement,ROOT_KEY)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_007_missing_gate(self):
        del self.statement['body']['gates']['agent-confinement']; self.statement=resign(self.statement,ROOT_KEY)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_008_unknown_gate(self):
        self.statement['body']['gates']['bypass']='PASS'; self.statement=resign(self.statement,ROOT_KEY)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_009_boolean_not_pass(self):
        self.statement['body']['gates']['hashes']=True; self.statement=resign(self.statement,ROOT_KEY)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_010_reference_report_not_readiness(self):
        s=deepcopy(self.statement); s['type']='local-conformance-run'; s=resign(s,ROOT_KEY)
        with self.assertRaises(Invalid): verify_ready(s,**self.kw)
    def test_READY_011_requirements_pin(self):
        self.kw['expected_requirements']=identity(RESOURCE)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_012_evidence_pin(self):
        self.kw['expected_evidence']=identity(POLICY)
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_013_unsigned(self):
        self.statement['proof']=None
        with self.assertRaises(Invalid): verify_ready(self.statement,**self.kw)
    def test_READY_014_fail_or_skip_denied(self):
        for status in ['FAIL','SKIP','XFAIL','UNKNOWN','PASS_WITH_WARNINGS']:
            s=deepcopy(self.statement); s['body']['gates']['hashes']=status; s=resign(s,ROOT_KEY)
            with self.assertRaises(Invalid): verify_ready(s,**self.kw)
