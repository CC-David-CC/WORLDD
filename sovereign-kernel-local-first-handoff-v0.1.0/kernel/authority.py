"""Finite attenuating capabilities. No wildcard, implicit union, or role backdoor."""
from __future__ import annotations
import re
from dataclasses import dataclass
from .canonical import Invalid, MAX_INTEGER
from .objects import identity, validate, valid_cid, make
from .crypto import verify, PRINCIPAL_RE

ATOM = re.compile(r"[A-Za-z0-9_:-]{1,128}\Z")
FIELDS = {"subject", "scope", "actions", "destinations", "unit", "not_before",
          "expires", "depth", "max_calls", "max_units"}
MAX_CHAIN = 16

def natural(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_INTEGER

def atoms(value: object, *, ordered_set: bool) -> bool:
    if type(value) is not list or not all(type(x) is str and ATOM.fullmatch(x) for x in value):
        return False
    return not ordered_set or value == sorted(set(value))

def validate_cap(cap: dict) -> None:
    validate(cap)
    if cap["type"] != "capability" or cap["proof"] is None or set(cap["body"]) != FIELDS:
        raise Invalid("CAP_FIELDS")
    if set(cap["refs"]) not in ({"resource"}, {"resource", "parent"}):
        raise Invalid("CAP_REFS")
    b = cap["body"]
    if type(b["subject"]) is not str or not PRINCIPAL_RE.fullmatch(b["subject"]):
        raise Invalid("SUBJECT")
    if not atoms(b["scope"], ordered_set=False):
        raise Invalid("SCOPE")
    if not atoms(b["actions"], ordered_set=True) or not atoms(b["destinations"], ordered_set=True):
        raise Invalid("SET_ENCODING")
    if type(b["unit"]) is not str or not ATOM.fullmatch(b["unit"]):
        raise Invalid("UNIT")
    if not all(natural(b[x]) for x in ("not_before", "expires", "depth", "max_calls", "max_units")):
        raise Invalid("LIMIT_TYPE")
    if b["not_before"] >= b["expires"] or b["depth"] >= MAX_CHAIN:
        raise Invalid("CAP_BOUNDS")

def attenuates(parent: dict, child: dict) -> bool:
    p, c = parent["body"], child["body"]
    return (
        child["refs"]["resource"] == parent["refs"]["resource"]
        and c["scope"][:len(p["scope"])] == p["scope"]
        and set(c["actions"]) <= set(p["actions"])
        and set(c["destinations"]) <= set(p["destinations"])
        and c["unit"] == p["unit"]
        and p["not_before"] <= c["not_before"] < c["expires"] <= p["expires"]
        and p["depth"] > 0 and c["depth"] < p["depth"]
        and c["max_calls"] <= p["max_calls"]
        and c["max_units"] <= p["max_units"]
    )

@dataclass(frozen=True)
class VerifiedChain:
    ids: tuple[str, ...]
    leaf: dict

def verify_chain(chain: list[dict], trusted_roots: set[str] | frozenset[str],
                 revoked: set[str] | frozenset[str], now: int) -> VerifiedChain:
    if not natural(now) or not chain or len(chain) > MAX_CHAIN:
        raise Invalid("CHAIN_LENGTH_OR_TIME")
    ids: list[str] = []
    for index, cap in enumerate(chain):
        validate_cap(cap)
        actor = verify(cap, kind="capability")
        cid = identity(cap)
        if cid in ids:
            raise Invalid("CHAIN_CYCLE_OR_DUPLICATE")
        if cid in revoked:
            raise Invalid("REVOKED")
        if not cap["body"]["not_before"] <= now < cap["body"]["expires"]:
            raise Invalid("TIME_WINDOW")
        if index == 0:
            if set(cap["refs"]) != {"resource"} or cid not in trusted_roots:
                raise Invalid("UNTRUSTED_ROOT")
        else:
            parent = chain[index - 1]
            if cap["refs"].get("parent") != ids[-1]:
                raise Invalid("PARENT_SUBSTITUTION")
            if actor != parent["body"]["subject"]:
                raise Invalid("ISSUER_NOT_PARENT_SUBJECT")
            if not attenuates(parent, cap):
                raise Invalid("AUTHORITY_EXPANSION")
        ids.append(cid)
    return VerifiedChain(tuple(ids), chain[-1])

def validate_intent(intent: dict) -> str:
    actor = verify(intent, kind="intent")
    if set(intent["refs"]) != {"capability", "resource"}:
        raise Invalid("INTENT_REFS")
    b = intent["body"]
    if set(b) != {"request", "scope", "action", "destination", "units", "unit"}:
        raise Invalid("INTENT_FIELDS")
    if not atoms(b["scope"], ordered_set=False) or not natural(b["units"]):
        raise Invalid("INTENT_VALUE")
    if not all(type(b[x]) is str and ATOM.fullmatch(b[x]) for x in ("request", "action", "destination", "unit")):
        raise Invalid("INTENT_ATOM")
    return actor

def decide(chain: list[dict], intent: dict, roots: set[str], revoked: set[str],
           usage: dict[str, dict[str, int]], now: int, policy: dict) -> dict:
    """Reference evaluator: total on canonically valid objects; deterministic denial reasons."""
    validate(policy)
    reason = "ALLOW"
    try:
        if policy["type"] != "policy" or set(policy["body"]) != {"engine", "actions"}:
            raise Invalid("POLICY_SHAPE")
        if policy["body"]["engine"] != "finite-capability-v1":
            raise Invalid("UNKNOWN_POLICY")
        if not atoms(policy["body"]["actions"], ordered_set=True):
            raise Invalid("POLICY_ACTIONS")
        actor = validate_intent(intent)
        verified = verify_chain(chain, roots, revoked, now)
        leaf = verified.leaf
        b, grant = intent["body"], leaf["body"]
        if actor != grant["subject"]:
            raise Invalid("INTENT_SUBJECT")
        if intent["refs"] != {"resource": leaf["refs"]["resource"], "capability": verified.ids[-1]}:
            raise Invalid("INTENT_TARGET")
        if b["action"] not in policy["body"]["actions"] or b["action"] not in grant["actions"]:
            raise Invalid("ACTION")
        if b["scope"][:len(grant["scope"])] != grant["scope"]:
            raise Invalid("INTENT_SCOPE")
        if b["destination"] not in grant["destinations"] or b["unit"] != grant["unit"]:
            raise Invalid("DESTINATION_OR_UNIT")
        for cap, cid in zip(chain, verified.ids):
            spent = usage.get(cid, {"calls": 0, "units": 0})
            if set(spent) != {"calls", "units"} or not all(natural(x) for x in spent.values()):
                raise Invalid("USAGE_STATE")
            if spent["calls"] + 1 > cap["body"]["max_calls"]:
                raise Invalid("CALL_BUDGET")
            if spent["units"] + b["units"] > cap["body"]["max_units"]:
                raise Invalid("UNIT_BUDGET")
    except Invalid as exc:
        reason = str(exc)
    facts = make("decision-facts", {
        "now": now, "roots": sorted(roots), "revoked": sorted(revoked),
        "usage": [{"capability": k, **usage[k]} for k in sorted(usage)],
        "chain": [identity(c) for c in chain]
    })
    return make("decision", {"outcome": "ALLOW" if reason == "ALLOW" else "DENY",
                             "reason": reason},
                {"intent": identity(intent), "policy": identity(policy), "facts": identity(facts)})
