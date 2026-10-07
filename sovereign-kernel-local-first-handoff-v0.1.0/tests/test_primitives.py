import hashlib
import json
import math
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path
from copy import deepcopy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.exceptions import InvalidTag
from kernel.canonical import dumps, loads, Invalid, MAX_BYTES, MAX_INTEGER
from kernel.objects import make, identity, encode, verify_bytes, OBJECT_FRAME
from kernel.crypto import sign, verify, principal, public_bytes
from tests.fixtures import ROOT_KEY, OTHER_KEY, RESOURCE, resign

HERE = Path(__file__).resolve().parents[1]

class CanonicalTests(unittest.TestCase):
    def test_CAN_001_golden_bytes(self):
        vectors = json.loads((HERE / "vectors/canonical.json").read_text())
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(dumps(vector["value"]).hex(), vector["utf8_hex"])

    def test_CAN_002_recursive_sort(self):
        self.assertEqual(dumps({"z": {"b": 1, "a": 2}, "a": 3}), b'{"a":3,"z":{"a":2,"b":1}}')

    def test_CAN_003_unicode_preserved(self):
        self.assertNotEqual(dumps({"x": "\u00e9"}), dumps({"x": "e\u0301"}))

    def test_CAN_004_duplicate_keys(self):
        with self.assertRaisesRegex(Invalid, "DUPLICATE_KEY"):
            loads(b'{"a":1,"a":2}')

    def test_CAN_005_reject_floats(self):
        for x in [1.0, -0.0, float("nan"), float("inf")]:
            with self.subTest(value=x), self.assertRaises(Invalid): dumps({"x": x})

    def test_CAN_006_reject_float_tokens(self):
        for raw in [b'1.0', b'1e2', b'NaN', b'Infinity', b'-0']:
            with self.subTest(raw=raw), self.assertRaises(Invalid): loads(raw, canonical=False)

    def test_CAN_007_integer_bounds(self):
        self.assertEqual(loads(dumps(MAX_INTEGER)), MAX_INTEGER)
        self.assertEqual(loads(dumps(-MAX_INTEGER)), -MAX_INTEGER)
        with self.assertRaises(Invalid): dumps(MAX_INTEGER + 1)

    def test_CAN_008_ascii_keys_only(self):
        for key in ["", "\u00e9", "\n", "a" * 129, 4]:
            with self.subTest(key=key), self.assertRaises(Invalid): dumps({key: 1})

    def test_CAN_009_surrogates(self):
        with self.assertRaises(Invalid): dumps({"x": "\ud800"})
        with self.assertRaises(Invalid): loads(b'{"x":"\\ud800"}')

    def test_CAN_010_null_not_absent(self):
        self.assertNotEqual(dumps({"x": None}), dumps({}))

    def test_CAN_011_array_order_semantic(self):
        self.assertNotEqual(dumps([1, 2]), dumps([2, 1]))

    def test_CAN_012_bool_not_integer(self):
        self.assertNotEqual(dumps(True), dumps(1))

    def test_CAN_013_noncanonical_wire(self):
        for raw in [b'{"b":2,"a":1}', b' {"a":1}', b'{"a":1}\n', b'{"a":"\\u0062"}']:
            with self.subTest(raw=raw), self.assertRaises(Invalid): loads(raw)

    def test_CAN_014_invalid_utf8(self):
        with self.assertRaises(Invalid): loads(b'"\xff"')

    def test_CAN_015_size_limit(self):
        with self.assertRaises(Invalid): loads(b' ' * (MAX_BYTES + 1))

    def test_CAN_016_depth_limit(self):
        x = None
        for _ in range(40): x = [x]
        with self.assertRaises(Invalid): dumps(x)

    def test_CAN_017_cycles(self):
        x = []; x.append(x)
        with self.assertRaises(Invalid): dumps(x)

    def test_CAN_018_cross_process(self):
        code = "import sys; sys.path.insert(0, %r); from kernel.canonical import dumps; print(dumps({'z':1,'a':'é'}).hex())" % str(HERE)
        a = subprocess.check_output([sys.executable, "-I", "-c", code])
        b = subprocess.check_output([sys.executable, "-I", "-c", code])
        self.assertEqual(a, b)
        self.assertEqual(a.strip().decode(), dumps({"a": "é", "z": 1}).hex())

    def test_CAN_019_seeded_roundtrips(self):
        rng = random.Random(1907)
        for _ in range(500):
            x = {"n": rng.randrange(-100000, 100000), "s": ''.join(rng.choice('abcé\\"\n') for _ in range(20)), "v": [True, False, None]}
            self.assertEqual(loads(dumps(x)), x)

    def test_CAN_020_unsupported_containers(self):
        for value in [(1,2), {1,2}, b'bytes']:
            with self.subTest(value=value), self.assertRaises(Invalid): dumps(value)

class ObjectTests(unittest.TestCase):
    def test_HASH_001_sha256_known_vector(self):
        self.assertEqual(hashlib.sha256(b'abc').hexdigest(), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_HASH_002_fixed_object_vectors(self):
        for vector in json.loads((HERE / 'vectors/objects.json').read_text()):
            with self.subTest(vector=vector['id']):
                self.assertEqual(encode(vector['object']).hex(), vector['utf8_hex'])
                self.assertEqual(identity(vector['object']), vector['cid'])

    def test_HASH_003_roundtrip(self):
        self.assertEqual(verify_bytes(identity(RESOURCE), encode(RESOURCE)), RESOURCE)

    def test_HASH_004_each_bit_rejected(self):
        raw = encode(RESOURCE)
        for i in range(len(raw)):
            for bit in range(8):
                changed = raw[:i] + bytes([raw[i] ^ (1 << bit)]) + raw[i+1:]
                with self.assertRaises((Invalid, ValueError)):
                    verify_bytes(identity(RESOURCE), changed)

    def test_HASH_005_merkle_parent_changes(self):
        x, y = make('blob', {'s':'one'}), make('blob', {'s':'two'})
        p = make('snapshot', {}, {'child': identity(x)})
        q = make('snapshot', {}, {'child': identity(y)})
        self.assertNotEqual(identity(p), identity(q))

    def test_HASH_006_type_domain_separation(self):
        self.assertNotEqual(identity(make('resource', {'a':1})), identity(make('policy', {'a':1})))

    def test_HASH_007_unknown_version(self):
        obj = deepcopy(RESOURCE); obj['v'] = 2
        with self.assertRaises(Invalid): identity(obj)

    def test_HASH_008_provider_independent(self):
        raw = encode(RESOURCE)
        local = {'disk': raw}; cloud = {'opaque-provider-id-37': raw}
        self.assertEqual(verify_bytes(identity(RESOURCE), local['disk']), verify_bytes(identity(RESOURCE), cloud['opaque-provider-id-37']))

    def test_HASH_009_unknown_algorithm(self):
        with self.assertRaises(Invalid): verify_bytes(identity(RESOURCE).replace('sha256', 'md5'), encode(RESOURCE))

    def test_HASH_010_bad_reference(self):
        with self.assertRaises(Invalid): make('resource', {}, {'parent':'provider-name'})

    def test_HASH_011_no_boolean_version(self):
        obj = deepcopy(RESOURCE); obj['v'] = True
        with self.assertRaises(Invalid): identity(obj)

    def test_HASH_012_copy_is_not_alias(self):
        body = {'a':[1]}; obj = make('test', body); body['a'].append(2)
        self.assertEqual(obj['body'], {'a':[1]})

class CryptoTests(unittest.TestCase):
    def test_KEY_001_rfc8032_vector(self):
        v = json.loads((HERE/'vectors/ed25519-rfc8032.json').read_text())
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(v['seed']))
        self.assertEqual(public_bytes(key).hex(), v['public_key'])
        self.assertEqual(key.sign(bytes.fromhex(v['message'])).hex(), v['signature'])
        key.public_key().verify(bytes.fromhex(v['signature']), bytes.fromhex(v['message']))

    def test_KEY_002_sign_verify(self):
        obj = sign(RESOURCE, ROOT_KEY)
        self.assertEqual(verify(obj, kind='resource'), principal(public_bytes(ROOT_KEY)))

    def test_KEY_003_payload_tamper(self):
        obj=sign(RESOURCE, ROOT_KEY); obj['body']['name']='evil'
        with self.assertRaises(Invalid): verify(obj, kind='resource')

    def test_KEY_004_wrong_key(self):
        with self.assertRaises(Invalid): verify(sign(RESOURCE, ROOT_KEY), kind='resource', expected_principal=principal(public_bytes(OTHER_KEY)))

    def test_KEY_005_wrong_domain(self):
        obj = sign(RESOURCE, ROOT_KEY); obj['type']='intent'
        with self.assertRaises(Invalid): verify(obj, kind='intent')

    def test_KEY_006_proof_in_content_id(self):
        self.assertNotEqual(identity(sign(RESOURCE, ROOT_KEY)), identity(RESOURCE))

    def test_KEY_007_signature_determinism(self):
        self.assertEqual(sign(RESOURCE, ROOT_KEY), sign(RESOURCE, ROOT_KEY))

    def test_KEY_008_no_double_sign(self):
        with self.assertRaises(Invalid): sign(sign(RESOURCE, ROOT_KEY), ROOT_KEY)

    def test_KEY_009_unsigned_rejected(self):
        with self.assertRaises(Invalid): verify(RESOURCE, kind='resource')

    def test_KEY_010_malformed_encoding(self):
        obj = sign(RESOURCE, ROOT_KEY); obj['proof']['signature']='ff'
        with self.assertRaises(Invalid): verify(obj, kind='resource')

    def test_KEY_011_unknown_suite(self):
        obj = sign(RESOURCE, ROOT_KEY); obj['proof']['algorithm']='ed25519ph'
        with self.assertRaises(Invalid): verify(obj, kind='resource')

    def test_KEY_012_aead_roundtrip(self):
        c=ChaCha20Poly1305(bytes(32)); n=bytes(12)
        self.assertEqual(c.decrypt(n,c.encrypt(n,b'data',b'aad'),b'aad'),b'data')

    def test_KEY_013_aead_tamper(self):
        c=ChaCha20Poly1305(bytes(32)); n=bytes(12); b=c.encrypt(n,b'data',b'aad')
        with self.assertRaises(InvalidTag): c.decrypt(n,b[:-1]+bytes([b[-1]^1]),b'aad')

    def test_KEY_014_aead_wrong_aad(self):
        c=ChaCha20Poly1305(bytes(32)); n=bytes(12)
        with self.assertRaises(InvalidTag): c.decrypt(n,c.encrypt(n,b'data',b'aad'),b'other')
