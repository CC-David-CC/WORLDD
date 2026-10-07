"""Local fake-effect budget simulator. Not a production tool broker or confinement boundary.

Effects, consumption and receipts commit in one SQLite transaction. Physical/external
side effects are deliberately absent. This tests ancestor accounting and idempotency.
"""
from __future__ import annotations
from .canonical import Invalid
from .objects import identity
from .authority import decide, validate_intent, verify_chain
from .log import LocalLog

class FakeBroker:
    def __init__(self, log: LocalLog, trusted_roots: set[str], policy: dict):
        self.log, self.roots, self.policy = log, set(trusted_roots), policy

    def state(self) -> tuple[dict, set[str], dict]:
        usage, revoked, requests = {}, set(), {}
        for event in self.log.replay():
            effect = event["body"]["effect"]
            if effect["kind"] == "revoke-fixture":
                revoked.add(effect["capability"])
            elif effect["kind"] == "consume-fixture":
                key = (effect["actor"], effect["request"])
                if key in requests:
                    raise Invalid("DUPLICATE_COMMIT")
                requests[key] = (effect["intent"], event)
                for cid in effect["chain"]:
                    spent = usage.setdefault(cid, {"calls": 0, "units": 0})
                    spent["calls"] += 1
                    spent["units"] += effect["units"]
            else:
                raise Invalid("UNKNOWN_FIXTURE_EFFECT")
        return usage, revoked, requests

    def execute(self, chain: list[dict], intent: dict, now: int) -> dict:
        actor = validate_intent(intent)
        with self.log.store.transaction():
            usage, revoked, requests = self.state()
            # Authenticate and re-check the chain even for cached receipts.
            verify_chain(chain, self.roots, revoked, now)
            request_key = (actor, intent["body"]["request"])
            if request_key in requests:
                prior, event = requests[request_key]
                if prior != identity(intent):
                    raise Invalid("IDEMPOTENCY_CONFLICT")
                return event
            decision = decide(chain, intent, self.roots, revoked, usage, now, self.policy)
            if decision["body"]["outcome"] != "ALLOW":
                raise Invalid(decision["body"]["reason"])
            return self.log.append_in_transaction({
                "kind": "consume-fixture", "actor": actor,
                "request": intent["body"]["request"], "intent": identity(intent),
                "chain": [identity(c) for c in chain], "units": intent["body"]["units"],
                "decision": identity(decision)
            })

    def revoke_fixture(self, cid: str) -> None:
        """Trusted test setup only. Production revocation authorization remains a gate."""
        self.log.append({"kind": "revoke-fixture", "capability": cid})
