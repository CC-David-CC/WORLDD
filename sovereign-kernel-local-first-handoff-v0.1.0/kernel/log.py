"""Signed linear fixture log with externally supplied trusted head for rollback detection."""
from __future__ import annotations
from .canonical import Invalid
from .objects import make, identity
from .crypto import sign, verify, principal, public_bytes
from .store import Store

class LocalLog:
    """Not a broker: callers are trusted test harness code, never untrusted agents."""
    def __init__(self, store: Store, key):
        self.store, self.key = store, key
        self.signer = principal(public_bytes(key))

    def replay(self, *, expected_head: str | None = None) -> list[dict]:
        result = []
        previous = None
        for seq, cid in self.store.db.execute("SELECT seq,cid FROM events ORDER BY seq"):
            event = self.store.get(cid)
            verify(event, kind="receipt", expected_principal=self.signer)
            if type(event["body"].get("seq")) is not int or seq != len(result) or event["body"].get("seq") != seq:
                raise Invalid("LOG_SEQUENCE")
            if set(event["body"]) != {"seq", "effect"}:
                raise Invalid("LOG_FIELDS")
            expected_refs = {} if previous is None else {"previous": previous}
            if event["refs"] != expected_refs:
                raise Invalid("LOG_LINK")
            result.append(event)
            previous = cid
        if expected_head is not None and previous != expected_head:
            raise Invalid("TRUNCATION_OR_FORK")
        return result

    def append_in_transaction(self, effect: dict) -> dict:
        if not self.store.db.in_transaction:
            raise Invalid("TRANSACTION_REQUIRED")
        history = self.replay()
        refs = {} if not history else {"previous": identity(history[-1])}
        event = sign(make("receipt", {"seq": len(history), "effect": effect}, refs), self.key)
        cid = self.store.put(event)
        self.store.db.execute("INSERT INTO events VALUES (?,?)", (len(history), cid))
        return event

    def append(self, effect: dict) -> dict:
        with self.store.transaction():
            return self.append_in_transaction(effect)
