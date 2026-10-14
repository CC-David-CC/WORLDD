"""Small encrypted SQLite content store. Metadata privacy and hostile-OS safety are out of scope."""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from .canonical import Invalid, dumps, loads
from .objects import identity, encode, make, verify_bytes, valid_cid

class Store:
    def __init__(self, path: str | Path, key: bytes):
        self.cipher = ChaCha20Poly1305(key)
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS objects (cid TEXT PRIMARY KEY, nonce BLOB UNIQUE NOT NULL, ciphertext BLOB NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, cid TEXT UNIQUE NOT NULL REFERENCES objects(cid))")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def put(self, obj: dict) -> str:
        cid, raw = identity(obj), encode(obj)
        found = self.db.execute("SELECT 1 FROM objects WHERE cid=?", (cid,)).fetchone()
        if found:
            if encode(self.get(cid)) != raw:
                raise Invalid("EXISTING_OBJECT_CORRUPT")
            return cid
        for _ in range(8):
            nonce = os.urandom(12)
            encrypted = self.cipher.encrypt(nonce, raw, cid.encode("ascii"))
            try:
                self.db.execute("INSERT INTO objects VALUES (?,?,?)", (cid, nonce, encrypted))
                return cid
            except sqlite3.IntegrityError:
                # Nonce collision is rejected by storage; never overwrite an object.
                if self.db.execute("SELECT 1 FROM objects WHERE cid=?", (cid,)).fetchone():
                    return identity(self.get(cid))
        raise Invalid("NONCE_ALLOCATION")

    def get(self, cid: str) -> dict:
        if not valid_cid(cid):
            raise Invalid("CID")
        row = self.db.execute("SELECT nonce,ciphertext FROM objects WHERE cid=?", (cid,)).fetchone()
        if row is None:
            raise Invalid("MISSING_OBJECT")
        try:
            raw = self.cipher.decrypt(row[0], row[1], cid.encode("ascii"))
        except InvalidTag as exc:
            raise Invalid("AEAD_TAMPER_OR_WRONG_KEY") from exc
        return verify_bytes(cid, raw)

    def ids(self) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT cid FROM objects ORDER BY cid")]

    def verify_all(self) -> str:
        ids = self.ids()
        for cid in ids:
            self.get(cid)
        return identity(make("store-inventory", {"count": len(ids)},
                             {f"object-{i:08d}": cid for i, cid in enumerate(ids)}))

    def reachable(self, roots: list[str]) -> set[str]:
        seen: set[str] = set()
        pending = list(roots)
        while pending:
            cid = pending.pop()
            if cid not in seen:
                obj = self.get(cid)
                seen.add(cid)
                pending.extend(obj["refs"].values())
        return seen

    def export(self) -> bytes:
        # Deliberately plaintext export for TEST FIXTURES, never a user-secret export command.
        return dumps({"objects": [self.get(cid) for cid in self.ids()], "v": 1})

    def import_fixture(self, raw: bytes) -> None:
        bundle = loads(raw)
        if type(bundle) is not dict or set(bundle) != {"objects", "v"} or type(bundle["v"]) is not int or bundle["v"] != 1 or type(bundle["objects"]) is not list:
            raise Invalid("EXPORT_FORMAT")
        with self.transaction():
            for obj in bundle["objects"]:
                self.put(obj)
