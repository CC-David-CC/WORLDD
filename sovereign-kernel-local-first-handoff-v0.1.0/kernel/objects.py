"""One immutable object envelope, with optional proof at a cryptographic boundary."""
from __future__ import annotations
import hashlib
import re
from copy import deepcopy
from typing import Any
from .canonical import Invalid, dumps, loads

CID_RE = re.compile(r"sk1:sha256:[0-9a-f]{64}\Z")
TYPE_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
OBJECT_FRAME = b"sovereign-kernel/object/sk-json-1/sha256/v1\x00"

def valid_cid(value: Any) -> bool:
    return type(value) is str and CID_RE.fullmatch(value) is not None

def validate(obj: dict) -> None:
    if type(obj) is not dict or set(obj) != {"v", "type", "body", "refs", "proof"}:
        raise Invalid("OBJECT_FIELDS")
    if type(obj["v"]) is not int or obj["v"] != 1:
        raise Invalid("OBJECT_VERSION")
    if type(obj["type"]) is not str or not TYPE_RE.fullmatch(obj["type"]):
        raise Invalid("OBJECT_TYPE")
    if type(obj["body"]) is not dict or type(obj["refs"]) is not dict:
        raise Invalid("OBJECT_SHAPE")
    if not all(valid_cid(cid) for cid in obj["refs"].values()):
        raise Invalid("REFERENCE")
    proof = obj["proof"]
    if proof is not None:
        if type(proof) is not dict or set(proof) != {"algorithm", "public_key", "signature"}:
            raise Invalid("PROOF_FIELDS")
        if proof["algorithm"] != "ed25519":
            raise Invalid("PROOF_ALGORITHM")
        for name, length in (("public_key", 64), ("signature", 128)):
            x = proof[name]
            if type(x) is not str or re.fullmatch(r"[0-9a-f]{%d}" % length, x) is None:
                raise Invalid("PROOF_ENCODING")
    dumps(obj)

def make(kind: str, body: dict, refs: dict[str, str] | None = None) -> dict:
    obj = {"v": 1, "type": kind, "body": deepcopy(body),
           "refs": deepcopy(refs or {}), "proof": None}
    validate(obj)
    return obj

def encode(obj: dict) -> bytes:
    validate(obj)
    return dumps(obj)

def identity(obj: dict) -> str:
    return "sk1:sha256:" + hashlib.sha256(OBJECT_FRAME + encode(obj)).hexdigest()

def verify_bytes(cid: str, raw: bytes) -> dict:
    if not valid_cid(cid):
        raise Invalid("CID")
    obj = loads(raw)
    if identity(obj) != cid:
        raise Invalid("HASH_MISMATCH")
    return obj
