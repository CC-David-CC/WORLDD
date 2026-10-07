from copy import deepcopy
import random
import unittest
from kernel.canonical import Invalid
from kernel.objects import make, identity
from kernel.authority import verify_chain, decide, attenuates
from tests.fixtures import *

class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.root=root_cap(); self.child=child(self.root); self.chain=[self.root,self.child]
        self.roots={identity(self.root)}

    def ok(self, chain=None, now=10, revoked=None):
        return verify_chain(chain or self.chain,self.roots,revoked or set(),now)

    def denied_change(self, field, value):
        c=deepcopy(self.child); c['body'][field]=value; c=resign(c, ALICE_KEY)
        with self.assertRaises(Invalid): self.ok([self.root,c])

    def test_CAP_001_valid(self): self.assertEqual(self.ok().ids[-1],identity(self.child))
    def test_CAP_002_action_expansion(self): self.denied_change('actions',['admin','read'])
    def test_CAP_003_scope_escape(self): self.denied_change('scope',[])
    def test_CAP_004_sibling_scope(self): self.denied_change('scope',['workspace2'])
    def test_CAP_005_not_before(self):
        r=root_cap(not_before=5); c=child(r,not_before=0)
        with self.assertRaises(Invalid): verify_chain([r,c],{identity(r)},set(),10)
    def test_CAP_006_expiry_expansion(self): self.denied_change('expires',101)
    def test_CAP_007_depth_expansion(self): self.denied_change('depth',3)
    def test_CAP_008_call_expansion(self): self.denied_change('max_calls',11)
    def test_CAP_009_unit_expansion(self): self.denied_change('max_units',101)
    def test_CAP_010_currency_change(self): self.denied_change('unit','USD-cent')
    def test_CAP_011_destination_expansion(self): self.denied_change('destinations',['fake-c'])
    def test_CAP_012_parent_substitution(self):
        c=deepcopy(self.child); c['refs']['parent']=identity(RESOURCE); c=resign(c,ALICE_KEY)
        with self.assertRaises(Invalid): self.ok([self.root,c])
    def test_CAP_013_resource_substitution(self):
        c=deepcopy(self.child); c['refs']['resource']=identity(POLICY); c=resign(c,ALICE_KEY)
        with self.assertRaises(Invalid): self.ok([self.root,c])
    def test_CAP_014_wrong_issuer(self):
        with self.assertRaises(Invalid): self.ok([self.root,resign(self.child,OTHER_KEY)])
    def test_CAP_015_unpinned_root(self):
        with self.assertRaises(Invalid): verify_chain(self.chain,set(),set(),10)
    def test_CAP_016_duplicate_chain(self):
        with self.assertRaises(Invalid): self.ok([self.root,self.child,self.child])
    def test_CAP_017_missing_parent(self):
        with self.assertRaises(Invalid): self.ok([self.child])
    def test_CAP_018_empty_actions(self):
        c=child(self.root,actions=[]); self.ok([self.root,c])
        self.assertEqual(decide([self.root,c],intent(c),self.roots,set(),{},10,POLICY)['body']['outcome'],'DENY')
    def test_CAP_019_not_before_inclusive(self): self.ok(now=0)
    def test_CAP_020_expiry_exclusive(self):
        with self.assertRaises(Invalid): self.ok(now=100)
    def test_CAP_021_revoke_ancestor(self):
        with self.assertRaises(Invalid): self.ok(revoked={identity(self.root)})
    def test_CAP_022_revoke_leaf(self):
        with self.assertRaises(Invalid): self.ok(revoked={identity(self.child)})
    def test_CAP_023_revoked_sibling_not_leaf(self): self.ok(revoked={identity(RESOURCE)})
    def test_CAP_024_no_delegation_at_zero(self):
        c=child(self.root,depth=0); d=child(c,OTHER_KEY,AGENT_KEY,depth=0)
        with self.assertRaises(Invalid): self.ok([self.root,c,d])
    def test_CAP_025_boolean_quota(self): self.denied_change('max_calls',True)
    def test_CAP_026_path_components(self):
        for value in [['workspace','..'],['workspace/file'],['workspace','%2e%2e'],['workspace','']]:
            self.denied_change('scope',value)
    def test_CAP_027_set_canonicality(self):
        self.denied_change('actions',['write','read'])
        self.denied_change('actions',['read','read'])
    def test_CAP_028_unknown_constraint(self):
        c=deepcopy(self.child); c['body']['allow_all']=True; c=resign(c,ALICE_KEY)
        with self.assertRaises(Invalid): self.ok([self.root,c])
    def test_CAP_029_seeded_attenuation(self):
        rng=random.Random(2901)
        for _ in range(250):
            actions=sorted(rng.sample(self.root['body']['actions'],rng.randrange(5)))
            c=child(self.root,actions=actions,scope=['workspace','nested'],max_calls=rng.randrange(11),max_units=rng.randrange(101),depth=rng.randrange(3),not_before=rng.randrange(10),expires=rng.randrange(20,101))
            verify_chain([self.root,c],self.roots,set(),10)
            self.assertTrue(attenuates(self.root,c))
    def test_CAP_030_no_rights_union(self):
        a=child(self.root,actions=['read']); b=child(self.root,actions=['write'])
        with self.assertRaises(Invalid): self.ok([self.root,a,b])
    def test_CAP_031_economic_claim_not_authority(self):
        claim=sign(make('economic-claim',{'units':100,'subject':who(AGENT_KEY)}, {'resource':RID}),ROOT_KEY)
        with self.assertRaises(Invalid): verify_chain([claim],{identity(claim)},set(),10)
    def test_CAP_032_issuer_key_replacement(self):
        c=child(self.root); c['proof']['public_key']=public_bytes(OTHER_KEY).hex()
        with self.assertRaises(Invalid): self.ok([self.root,c])

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.root=root_cap(); self.cap=child(self.root); self.chain=[self.root,self.cap]; self.roots={identity(self.root)}
    def check(self, i=None, usage=None, policy=POLICY):
        return decide(self.chain,i or intent(self.cap),self.roots,set(),usage or {},10,policy)
    def test_POL_001_allow(self): self.assertEqual(self.check()['body']['outcome'],'ALLOW')
    def test_POL_002_unknown_action(self): self.assertEqual(self.check(intent(self.cap,action='admin'))['body']['outcome'],'DENY')
    def test_POL_003_other_subject(self): self.assertEqual(self.check(intent(self.cap,key=OTHER_KEY))['body']['outcome'],'DENY')
    def test_POL_004_scope(self): self.assertEqual(self.check(intent(self.cap,scope=['other']))['body']['outcome'],'DENY')
    def test_POL_005_destination(self): self.assertEqual(self.check(intent(self.cap,destination='fake-c'))['body']['outcome'],'DENY')
    def test_POL_006_negative_cost(self): self.assertEqual(self.check(intent(self.cap,units=-1))['body']['outcome'],'DENY')
    def test_POL_007_ancestor_budget(self):
        u={identity(self.root):{'calls':10,'units':0}}
        self.assertEqual(self.check(usage=u)['body']['reason'],'CALL_BUDGET')
    def test_POL_008_leaf_budget(self):
        u={identity(self.cap):{'calls':0,'units':100}}
        self.assertEqual(self.check(intent(self.cap,units=1),usage=u)['body']['reason'],'UNIT_BUDGET')
    def test_POL_009_deterministic(self): self.assertEqual(identity(self.check()),identity(self.check()))
    def test_POL_010_version_in_decision_id(self):
        p=make('policy',{'engine':'finite-capability-v1','actions':['read']})
        self.assertNotEqual(identity(self.check()),identity(self.check(policy=p)))
    def test_POL_011_unknown_engine(self):
        p=make('policy',{'engine':'future','actions':['read']})
        self.assertEqual(self.check(policy=p)['body']['outcome'],'DENY')
    def test_POL_012_wrong_currency(self): self.assertEqual(self.check(intent(self.cap,unit='USD-cent'))['body']['outcome'],'DENY')
    def test_POL_013_bool_cost(self): self.assertEqual(self.check(intent(self.cap,units=True))['body']['outcome'],'DENY')
    def test_POL_014_unknown_intent_field(self):
        i=intent(self.cap); i['body']['bypass']=True; i=resign(i,AGENT_KEY)
        self.assertEqual(self.check(i)['body']['outcome'],'DENY')
