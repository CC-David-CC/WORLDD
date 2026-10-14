"""Evidence verification, not remote attestation and not a network enable switch."""
from __future__ import annotations
from dataclasses import dataclass
from .canonical import Invalid
from .objects import identity, valid_cid
from .crypto import verify

REQUIRED_GATES = frozenset({
    "canonical", "hashes", "signatures", "authority", "accounting", "store",
    "replay", "revocation", "policy", "agent-confinement", "execution",
    "recovery", "rollback-anchor", "build-identity", "network-zero",
    "independent-vectors", "crash-consistency", "readiness-supervisor"
})

@dataclass(frozen=True)
class VerifiedReadiness:
    statement_id: str
    build_id: str
    state_id: str
    challenge: str

def verify_ready(statement: dict, *, trusted_verifier: str, expected_build: str,
                 expected_state: str, challenge: str, expected_requirements: str,
                 expected_evidence: str) -> VerifiedReadiness:
    """Must run in the future trusted supervisor, not an agent's Python interpreter.

The caller must reconstruct/pin all expected roots and validate the complete evidence
closure first. A signature authenticates the assertion, not the truth of its claims.
"""
    verify(statement, kind="local-ready", expected_principal=trusted_verifier)
    if set(statement["body"]) != {"challenge", "gates"}:
        raise Invalid("READINESS_FIELDS")
    expected = {"build": expected_build, "state": expected_state,
                "requirements": expected_requirements, "evidence": expected_evidence}
    if not all(valid_cid(x) for x in expected.values()) or statement["refs"] != expected:
        raise Invalid("STALE_OR_WRONG_ROOT")
    if type(challenge) is not str or not 1 <= len(challenge) <= 128 or statement["body"]["challenge"] != challenge:
        raise Invalid("STALE_CHALLENGE")
    gates = statement["body"]["gates"]
    if type(gates) is not dict or set(gates) != REQUIRED_GATES:
        raise Invalid("GATE_SET")
    if any(value != "PASS" for value in gates.values()):
        raise Invalid("INCOMPLETE")
    return VerifiedReadiness(identity(statement), expected_build, expected_state, challenge)
