"""
Turning node inputs and outputs into values Postgres accepts as JSONB.

Never fails on an odd value: what can't be represented is replaced by a
marker naming its type, so recording a step can't break the node.
"""

import dataclasses
import hashlib
import math
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from uuid import UUID

from pydantic import BaseModel

# Postgres can't store U+0000 in a jsonb string (it rejects "\u0000").
_NUL_REPLACEMENT = "�"


def jsonable(value):
    """A copy of `value` made of dicts, lists, strings, numbers, bools and None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return jsonable(value.value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # JSON has no NaN or infinity.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return value.replace("\x00", _NUL_REPLACEMENT)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Page images and files: their size and hash identify them; the
        # bytes themselves are already on disk.
        data = bytes(value)
        return {"$bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {jsonable_key(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [jsonable(v) for v in value]
        return sorted(items, key=repr)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal, PurePath)):
        return str(value)
    return {"$unserializable": type(value).__name__}


def jsonable_key(key) -> str:
    key = jsonable(key)
    return key if isinstance(key, str) else str(key)
