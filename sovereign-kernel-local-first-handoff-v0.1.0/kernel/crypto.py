"""Use cryptography's Ed25519; no hand-written signature or cipher implementation."""
from __future__ import annotations
import hashlib
import re
from copy import deepcopy
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from .canonical import Invalid
from .objects import encode, validate

SIGN_FRAME = b"sovereign-kernel/signature/sk-json-1/ed25519/v1\x00"
PRINCIPAL_FRAME = b"sovereign-kernel/principal/ed25519/v1\x00"
PRINCIPAL_RE = re.compile(r"skp1:ed25519:[0-9a-f]{64}\Z")

def public_bytes(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    if isinstance(key, Ed25519PrivateKey):
        key = key.public_key()
    return key.public_bytes_raw()

def principal(public: bytes) -> str:
    if type(public) is not bytes or len(public) != 32:
        raise Invalid("PUBLIC_KEY")
    return "skp1:ed25519:" + hashlib.sha256(PRINCIPAL_FRAME + public).hexdigest()

def sign(obj: dict, key: Ed25519PrivateKey) -> dict:
    validate(obj)
    if obj["proof"] is not None:
        raise Invalid("ALREADY_SIGNED")
    result = deepcopy(obj)
    public = public_bytes(key)
    signature = key.sign(SIGN_FRAME + public + encode(obj))
    result["proof"] = {"algorithm": "ed25519", "public_key": public.hex(),
                       "signature": signature.hex()}
    return result

def verify(obj: dict, *, kind: str, expected_principal: str | None = None) -> str:
    validate(obj)
    if obj["type"] != kind or obj["proof"] is None:
        raise Invalid("SIGNATURE_REQUIRED_OR_WRONG_TYPE")
    proof = obj["proof"]
    public = bytes.fromhex(proof["public_key"])
    actor = principal(public)
    if expected_principal is not None and actor != expected_principal:
        raise Invalid("WRONG_SIGNER")
    unsigned = deepcopy(obj)
    unsigned["proof"] = None
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            bytes.fromhex(proof["signature"]), SIGN_FRAME + public + encode(unsigned))
    except InvalidSignature as exc:
        raise Invalid("BAD_SIGNATURE") from exc
    return actor
