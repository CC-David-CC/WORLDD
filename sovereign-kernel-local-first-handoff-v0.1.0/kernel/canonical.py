"""SK-JSON-1: intentionally restricted JSON, not a general RFC 8785 implementation."""
from __future__ import annotations
import json
import re
from typing import Any

MAX_INTEGER = (1 << 53) - 1
MAX_BYTES = 1_048_576
MAX_DEPTH = 32
MAX_ITEMS = 20_000
ASCII_KEY = re.compile(r"[\x20-\x7e]{1,128}\Z")

class Invalid(ValueError):
    """Untrusted input failed the protocol contract."""

def _validate(value: Any, depth: int, budget: list[int]) -> None:
    budget[0] -= 1
    if depth > MAX_DEPTH or budget[0] < 0:
        raise Invalid("LIMIT")
    t = type(value)
    if value is None or t is bool:
        return
    if t is int:
        if abs(value) > MAX_INTEGER:
            raise Invalid("INTEGER_RANGE")
        return
    if t is str:
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise Invalid("SURROGATE")
        if len(value.encode("utf-8")) > MAX_BYTES:
            raise Invalid("LIMIT")
        return
    if t is list:
        for item in value:
            _validate(item, depth + 1, budget)
        return
    if t is dict:
        for key, item in value.items():
            if type(key) is not str or ASCII_KEY.fullmatch(key) is None:
                raise Invalid("MAP_KEY")
            _validate(item, depth + 1, budget)
        return
    raise Invalid("UNSUPPORTED_TYPE")

def dumps(value: Any) -> bytes:
    _validate(value, 0, [MAX_ITEMS])
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise Invalid("LIMIT")
    return raw

def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Invalid("DUPLICATE_KEY")
        out[key] = value
    return out

def _no_float(_: str) -> None:
    raise Invalid("FLOAT_FORBIDDEN")

def _integer(text: str) -> int:
    if text == "-0":
        raise Invalid("NEGATIVE_ZERO")
    return int(text)

def loads(raw: bytes, *, canonical: bool = True) -> Any:
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise Invalid("INPUT_BYTES")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_no_float, parse_constant=_no_float,
                           parse_int=_integer)
        normalized = dumps(value)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise Invalid("PARSE") from exc
    if canonical and normalized != raw:
        raise Invalid("NONCANONICAL")
    return value
