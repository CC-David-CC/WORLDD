"""Publicly known disposable TEST keys. Never use with real resources or real money."""
from copy import deepcopy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from kernel.objects import make, identity
from kernel.crypto import sign, principal, public_bytes

ROOT_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
ALICE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
AGENT_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(2, 34)))
OTHER_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(3, 35)))
STORE_KEY = bytes(range(32))
RESOURCE = make("resource", {"name": "fixture-workspace", "namespace": "fixture", "nonce": "public-test-only"})
RID = identity(RESOURCE)
POLICY = make("policy", {"engine": "finite-capability-v1", "actions": ["call", "read", "spend", "write"]})

def who(key):
    return principal(public_bytes(key))

def resign(obj, key):
    result = deepcopy(obj)
    result["proof"] = None
    return sign(result, key)

def root_cap(**updates):
    body = {"subject": who(ALICE_KEY), "scope": ["workspace"],
            "actions": ["call", "read", "spend", "write"], "destinations": ["fake-a", "fake-b"],
            "unit": "fixture-unit", "not_before": 0, "expires": 100,
            "depth": 3, "max_calls": 10, "max_units": 100}
    body.update(updates)
    return sign(make("capability", body, {"resource": RID}), ROOT_KEY)

def child(parent, subject_key=AGENT_KEY, issuer=ALICE_KEY, **updates):
    body = deepcopy(parent["body"])
    body.update(subject=who(subject_key), depth=max(0, body["depth"] - 1))
    body.update(updates)
    return sign(make("capability", body, {"parent": identity(parent), "resource": parent["refs"]["resource"]}), issuer)

def intent(cap, key=AGENT_KEY, **updates):
    body = {"request": "request-1", "scope": ["workspace", "file"],
            "action": "read", "destination": "fake-a", "units": 0, "unit": "fixture-unit"}
    body.update(updates)
    return sign(make("intent", body, {"capability": identity(cap), "resource": cap["refs"]["resource"]}), key)
