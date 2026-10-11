from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from .rpc import Client, RpcError
from .oracles import pose, close_vec, same_rotation, PPM

def case(phase: str, primitive: str):
    def mark(fn):
        fn.phase, fn.primitive = phase, primitive
        return fn
    return mark

class Actor:
    def __init__(self, test, info):
        self.test, self.id, self.token, self.seq = test, info["id"], info["token"], 0
    def act(self, op, **args):
        self.seq += 1
        return self.test.call("actor.act", token=self.token, seq=self.seq, op=op, args=args)
    def info(self):
        return self.test.call("actor.inspect", token=self.token)

class Acceptance(unittest.TestCase):
    # No skips. tools/gate.py only selects the requested dependency prefix.
    def setUp(self):
        self.root = Path(os.environ.get("SPATIAL_EVIDENCE_DIR", "evidence/target-runs")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_dir = Path(tempfile.mkdtemp(prefix=self.id().replace(".", "_")+"-", dir=self.root))
        self.state_dir = self.run_dir / "state"
        self.argv = json.loads(os.environ["SPATIAL_SUT"])
        self.client = None
        self.addCleanup(self.cleanup)
        self.start()
    def start(self):
        self.client = Client(self.argv, self.state_dir, self.run_dir,
                             timeout=float(os.environ.get("SPATIAL_RPC_TIMEOUT", "10")))
        hello = self.call("system.hello")
        self.assertEqual(hello["protocol"], "spatial-protobuf/1")
        self.assertEqual(hello["implementation"], "production")
        self.assertTrue(hello["build_id"])
    def cleanup(self):
        if self.client:
            self.client.stop()
            self.client = None
    def restart(self, crash=False):
        self.client.stop(crash=crash)
        self.client = None
        self.start()
    def call(self, method, **params):
        return self.client.call(method, params)
    def error(self, code, method, **params):
        with self.assertRaises(RpcError) as ctx:
            self.call(method, **params)
        self.assertEqual(ctx.exception.code, code)
    def actor_error(self, code, actor, op, **args):
        with self.assertRaises(RpcError) as ctx:
            actor.act(op, **args)
        self.assertEqual(ctx.exception.code, code)
    def room(self, ident="room-a", **extra):
        return self.call("world.create", entity={"id":ident, "kind":"room", "label":ident, **extra})
    def obj(self, ident="object-a", room="room-a", kind="object", **extra):
        return self.call("world.create", entity={"id":ident,"kind":kind,"room":room,
                                                 "pose":pose(),"label":ident,**extra})
    def actor(self, ident="human", room="room-a", pose_=None, grants=None):
        info = self.call("actor.open", id=ident, room=room, pose=pose_ or pose(),
                         grants=grants if grants is not None else ["navigate","interact","world.write","git.write","process.launch"])
        return Actor(self, info)
    def get(self, ident):
        return self.call("world.get", id=ident)
    def patch(self, ident, patch):
        entity=self.get(ident)
        return self.call("world.update", id=ident, expected_revision=entity["revision"], patch=patch)
    def assertVec(self, actual, expected, tol=1e-6):
        self.assertTrue(close_vec(actual,expected,tol),f"{actual} != {expected} (tol={tol})")
    def assertPose(self, actual, expected, tol=1e-6):
        self.assertVec(actual["p"],expected["p"],tol)
        self.assertTrue(same_rotation(actual["q"],expected["q"],tol), f"{actual['q']} != ±{expected['q']}")
    def advance(self, seconds):
        return self.call("clock.advance", seconds=seconds)
    def capture(self, actor, width=128, height=128):
        result = self.call("render.capture", token=actor.token, width=width, height=height)
        p=Path(result["path"]).resolve()
        self.assertTrue(p.is_relative_to(self.state_dir.resolve()), "Capture escaped test state directory")
        image=PPM(p)
        self.assertEqual((image.width,image.height),(width,height))
        return image, result
    def assertColor(self, actual, expected, tol=3):
        self.assertVec(actual,expected,tol)
    def until(self, function, predicate, timeout=5.0):
        end=time.monotonic()+timeout
        last=None
        while time.monotonic()<end:
            last=function()
            if predicate(last):
                return last
            time.sleep(.01)  # bounded polling, not a correctness-dependent fixed delay
        self.fail(f"Condition deadline expired; last observation={last!r}")
