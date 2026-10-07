import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from kernel.canonical import Invalid
from kernel.objects import make, identity, encode
from kernel.store import Store
from kernel.log import LocalLog
from kernel.simulation import FakeBroker
from tests.fixtures import *

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'objects.sqlite3'
        self.s=Store(self.path, STORE_KEY)
    def tearDown(self): self.s.close(); self.tmp.cleanup()
    def test_STORE_001_put_get(self): self.assertEqual(self.s.get(self.s.put(RESOURCE)),RESOURCE)
    def test_STORE_002_dedup(self):
        self.s.put(RESOURCE); self.s.put(RESOURCE); self.assertEqual(len(self.s.ids()),1)
    def test_STORE_003_wrong_key(self):
        cid=self.s.put(RESOURCE); other=Store(self.path,bytes([7])*32)
        try:
            with self.assertRaises(Invalid): other.get(cid)
        finally: other.close()
    def test_STORE_004_ciphertext_tamper(self):
        cid=self.s.put(RESOURCE)
        self.s.db.execute('UPDATE objects SET ciphertext=? WHERE cid=?',(bytes(64),cid))
        with self.assertRaises(Invalid): self.s.get(cid)
    def test_STORE_005_aad_substitution(self):
        cid=self.s.put(RESOURCE); other=identity(POLICY)
        self.s.db.execute('UPDATE objects SET cid=? WHERE cid=?',(other,cid))
        with self.assertRaises(Invalid): self.s.get(other)
    def test_STORE_006_no_plaintext_body(self):
        self.s.put(RESOURCE)
        self.assertNotIn(b'fixture-workspace',self.path.read_bytes())
    def test_STORE_007_inventory_deterministic(self):
        self.s.put(RESOURCE); self.assertEqual(self.s.verify_all(),self.s.verify_all())
    def test_STORE_008_inventory_changes(self):
        a=self.s.verify_all(); self.s.put(RESOURCE); self.assertNotEqual(a,self.s.verify_all())
    def test_STORE_009_fixture_export_import(self):
        self.s.put(RESOURCE); self.s.put(POLICY); raw=self.s.export()
        other=Store(Path(self.tmp.name)/'other.sqlite3',STORE_KEY)
        try:
            other.import_fixture(raw); self.assertEqual(raw,other.export())
        finally: other.close()
    def test_STORE_010_reachability(self):
        cid=self.s.put(RESOURCE); parent=self.s.put(make('snapshot',{}, {'resource':cid}))
        self.s.put(POLICY); self.assertEqual(self.s.reachable([parent]),{parent,cid})
    def test_STORE_011_dangling_reference(self):
        parent=self.s.put(make('snapshot',{}, {'resource':identity(RESOURCE)}))
        with self.assertRaises(Invalid): self.s.reachable([parent])
    def test_STORE_012_rollback_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.s.transaction():
                self.s.put(RESOURCE); raise RuntimeError('simulated failure')
        self.assertEqual(self.s.ids(),[])
    def test_STORE_013_no_silent_repair(self):
        cid=self.s.put(RESOURCE); self.s.db.execute('UPDATE objects SET ciphertext=? WHERE cid=?',(bytes(32),cid))
        with self.assertRaises(Invalid): self.s.put(RESOURCE)
    def test_STORE_014_crash_before_commit(self):
        root=str(Path(__file__).resolve().parents[1])
        code="""import sys,os
sys.path.insert(0,%r)
from kernel.store import Store
from tests.fixtures import STORE_KEY, RESOURCE
s=Store(%r,STORE_KEY)
s.db.execute('BEGIN IMMEDIATE')
s.put(RESOURCE)
os._exit(73)
""" % (root,str(self.path))
        p=subprocess.run([sys.executable,'-I','-c',code],check=False)
        self.assertEqual(p.returncode,73); self.assertEqual(self.s.ids(),[])
    def test_STORE_015_crash_after_commit(self):
        root=str(Path(__file__).resolve().parents[1])
        code="""import sys,os
sys.path.insert(0,%r)
from kernel.store import Store
from tests.fixtures import STORE_KEY, RESOURCE
s=Store(%r,STORE_KEY)
with s.transaction(): s.put(RESOURCE)
os._exit(74)
""" % (root,str(self.path))
        p=subprocess.run([sys.executable,'-I','-c',code],check=False)
        self.assertEqual(p.returncode,74); self.assertEqual(self.s.get(identity(RESOURCE)),RESOURCE)
    def test_STORE_017_boolean_export_version(self):
        with self.assertRaises(Invalid): self.s.import_fixture(b'{"objects":[],"v":true}')
    def test_STORE_016_missing_object(self):
        with self.assertRaises(Invalid): self.s.get(identity(RESOURCE))

class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.s=Store(Path(self.tmp.name)/'log.sqlite3',STORE_KEY)
        self.log=LocalLog(self.s,ROOT_KEY)
    def tearDown(self): self.s.close(); self.tmp.cleanup()
    def seed(self): return [self.log.append({'value':i}) for i in range(3)]
    def test_LOG_001_replay(self):
        events=self.seed(); self.assertEqual(self.log.replay(expected_head=identity(events[-1])),events)
    def test_LOG_002_deleted_middle(self):
        self.seed(); self.s.db.execute('DELETE FROM events WHERE seq=1')
        with self.assertRaises(Invalid): self.log.replay()
    def test_LOG_003_truncated_with_anchor(self):
        events=self.seed(); self.s.db.execute('DELETE FROM events WHERE seq=2')
        with self.assertRaisesRegex(Invalid,'TRUNCATION_OR_FORK'): self.log.replay(expected_head=identity(events[-1]))
    def test_LOG_004_unanchored_prefix_is_valid(self):
        self.seed(); self.s.db.execute('DELETE FROM events WHERE seq=2')
        self.assertEqual(len(self.log.replay()),2)  # Explicit limitation, NOT an undetected-bug assertion.
    def test_LOG_005_wrong_signer(self):
        self.seed()
        with self.assertRaises(Invalid): LocalLog(self.s,OTHER_KEY).replay()
    def test_LOG_006_parent_fork(self):
        events=self.seed(); e=deepcopy(events[2]); e['refs']['previous']=identity(events[0]); e=resign(e,ROOT_KEY)
        cid=self.s.put(e); self.s.db.execute('UPDATE events SET cid=? WHERE seq=2',(cid,))
        with self.assertRaises(Invalid): self.log.replay()
    def test_LOG_007_atomic_receipt(self):
        with self.assertRaises(RuntimeError):
            with self.s.transaction():
                self.log.append_in_transaction({'value':1}); raise RuntimeError('crash')
        self.assertEqual(self.log.replay(),[]); self.assertEqual(self.s.ids(),[])
    def test_LOG_008_transaction_required(self):
        with self.assertRaises(Invalid): self.log.append_in_transaction({})
    def test_LOG_009_swapped_event_ids(self):
        events=self.seed()
        self.s.db.execute('DELETE FROM events')
        for i,e in enumerate(reversed(events)): self.s.db.execute('INSERT INTO events VALUES (?,?)',(i,identity(e)))
        with self.assertRaises(Invalid): self.log.replay()
    def test_LOG_011_boolean_sequence(self):
        e=self.log.append({'value':1}); e['body']['seq']=False; e=resign(e,ROOT_KEY)
        cid=self.s.put(e); self.s.db.execute('UPDATE events SET cid=? WHERE seq=0',(cid,))
        with self.assertRaises(Invalid): self.log.replay()
    def test_LOG_010_unique_receipt_index(self):
        events=self.seed()
        with self.assertRaises(sqlite3.IntegrityError): self.s.db.execute('INSERT INTO events VALUES (?,?)',(3,identity(events[0])))

class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'budget.sqlite3'
        self.s=Store(self.path,STORE_KEY); self.log=LocalLog(self.s,ROOT_KEY)
        self.root=root_cap(max_calls=3,max_units=100)
        self.a=child(self.root); self.b=child(self.root,subject_key=OTHER_KEY)
        self.broker=FakeBroker(self.log,{identity(self.root)},POLICY)
    def tearDown(self): self.s.close(); self.tmp.cleanup()
    def test_BUD_001_charge_every_ancestor(self):
        self.broker.execute([self.root,self.a],intent(self.a,units=30),10)
        usage,_,_=self.broker.state()
        self.assertEqual(usage[identity(self.root)],{'calls':1,'units':30})
        self.assertEqual(usage[identity(self.a)],{'calls':1,'units':30})
    def test_BUD_002_sibling_double_spend(self):
        self.broker.execute([self.root,self.a],intent(self.a,units=60),10)
        with self.assertRaisesRegex(Invalid,'UNIT_BUDGET'):
            self.broker.execute([self.root,self.b],intent(self.b,key=OTHER_KEY,units=60),10)
        self.assertEqual(len(self.log.replay()),1)
    def test_BUD_003_idempotent_retry(self):
        i=intent(self.a,units=30); first=self.broker.execute([self.root,self.a],i,10)
        second=self.broker.execute([self.root,self.a],i,10)
        self.assertEqual(first,second); self.assertEqual(len(self.log.replay()),1)
    def test_BUD_004_request_conflict(self):
        self.broker.execute([self.root,self.a],intent(self.a,units=30),10)
        with self.assertRaisesRegex(Invalid,'IDEMPOTENCY_CONFLICT'):
            self.broker.execute([self.root,self.a],intent(self.a,units=31),10)
    def test_BUD_005_revocation_local(self):
        self.broker.revoke_fixture(identity(self.root))
        with self.assertRaisesRegex(Invalid,'REVOKED'):
            self.broker.execute([self.root,self.a],intent(self.a),10)
    def test_BUD_006_reconstruct_budget(self):
        self.broker.execute([self.root,self.a],intent(self.a,units=40),10)
        another=FakeBroker(LocalLog(self.s,ROOT_KEY),{identity(self.root)},POLICY)
        self.assertEqual(self.broker.state(),another.state())
    def test_BUD_007_concurrent_siblings(self):
        barrier=threading.Barrier(2); outcomes=[]
        def worker(cap,key):
            s=Store(self.path,STORE_KEY)
            try:
                broker=FakeBroker(LocalLog(s,ROOT_KEY),{identity(self.root)},POLICY)
                barrier.wait(timeout=5)
                try:
                    broker.execute([self.root,cap],intent(cap,key=key,units=60),10)
                    outcomes.append('ALLOW')
                except Invalid as exc: outcomes.append(str(exc))
            finally: s.close()
        threads=[threading.Thread(target=worker,args=(self.a,AGENT_KEY)),threading.Thread(target=worker,args=(self.b,OTHER_KEY))]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(sorted(outcomes),['ALLOW','UNIT_BUDGET'])
        self.assertEqual(len(self.log.replay()),1)
    def test_BUD_008_call_quota(self):
        for i in range(3): self.broker.execute([self.root,self.a],intent(self.a,request=f'request-{i}'),10)
        with self.assertRaisesRegex(Invalid,'CALL_BUDGET'):
            self.broker.execute([self.root,self.b],intent(self.b,key=OTHER_KEY,request='request-4'),10)
    def test_BUD_009_unknown_effect_closed(self):
        self.log.append({'kind':'future-effect'})
        with self.assertRaises(Invalid): self.broker.state()
    def test_BUD_010_expired_retry_no_execution(self):
        i=intent(self.a); self.broker.execute([self.root,self.a],i,10)
        with self.assertRaises(Invalid): self.broker.execute([self.root,self.a],i,100)
        self.assertEqual(len(self.log.replay()),1)
