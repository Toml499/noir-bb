"""Encoding Python values into Prover.toml and field-element helpers.

Noir's ABI accepts inputs in TOML where field/integer values are written as
decimal or 0x-hex strings, structs are tables, and fixed-size arrays are TOML
arrays. This module converts ordinary Python values (int, bool, str, bytes,
list, dict) into that representation.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, List, Mapping

from .errors import InputError

#: BN254 scalar field modulus (the Noir `Field` type over Barretenberg).
BN254_FR_MODULUS = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)

FIELD_BYTES = 32


# ---------------------------------------------------------------------------
# Field-element helpers
# ---------------------------------------------------------------------------

def to_hex_field(value: int | str) -> str:
    """Normalise an int or numeric string into a 0x-prefixed 32-byte hex field."""
    if isinstance(value, str):
        v = int(value, 16) if value.strip().lower().startswith("0x") else int(value)
    else:
        v = int(value)
    if v < 0:
        v %= BN254_FR_MODULUS
    if v >= 1 << 256:
        raise InputError(f"value does not fit in 32 bytes: {value!r}")
    return "0x" + v.to_bytes(FIELD_BYTES, "big").hex()


def proof_bytes_to_fields(data: bytes) -> List[str]:
    """Split raw proof/public-input bytes into 32-byte big-endian hex fields.

    UltraHonk proofs serialise as a flat sequence of 32-byte field elements,
    so this matches bb.js's manual ``proofToFields`` chunking.
    """
    if len(data) % FIELD_BYTES != 0:
        raise InputError(
            f"byte length {len(data)} is not a multiple of {FIELD_BYTES}; "
            "this does not look like a field-aligned proof"
        )
    return ["0x" + data[i:i + FIELD_BYTES].hex() for i in range(0, len(data), FIELD_BYTES)]


def fields_to_bytes(fields: Iterable[str | int]) -> bytes:
    """Concatenate hex/int field elements back into raw bytes."""
    out = bytearray()
    for f in fields:
        out += bytes.fromhex(to_hex_field(f)[2:])
    return bytes(out)


# ---------------------------------------------------------------------------
# Prover.toml serialisation
# ---------------------------------------------------------------------------

def to_abi_value(value: Any) -> Any:
    """Convert a Python value into its Prover.toml representation.

    int   -> decimal string, written verbatim (negatives keep their sign)
    bool  -> TOML boolean
    str   -> passed through (use for 0x-hex or pre-formatted values)
    bytes -> list of per-byte decimal strings (matches Noir ``[u8; N]``)
    list  -> array (recursively encoded)
    dict  -> table / struct (recursively encoded)

    Values are written as-is and left for nargo to interpret against the
    circuit's ABI: ``-5`` works for a signed integer and is reduced mod the
    field for a ``Field``, and nargo reports a clear error when a value does
    not fit its declared type. (For encoding actual field elements -- proofs,
    vk fields, recursion inputs -- use :func:`to_hex_field`, which does reduce
    mod the BN254 scalar field.)
    """
    if isinstance(value, bool):  # before int: bool is a subclass of int
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return [str(b) for b in value]
    if isinstance(value, Mapping):
        return {str(k): to_abi_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_abi_value(v) for v in value]
    raise InputError(
        f"cannot encode {type(value).__name__} into Prover.toml "
        f"(value: {value!r}); use int, bool, str, bytes, list or dict"
    )


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)  # valid TOML basic string
    raise InputError(f"unexpected scalar in encoded ABI value: {value!r}")


def _toml_value(value: Any) -> str:
    """Render an already-encoded ABI value as an inline TOML value."""
    if isinstance(value, dict):
        inner = ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return _toml_scalar(value)


def dumps_prover_toml(inputs: Mapping[str, Any]) -> str:
    """Serialise a dict of circuit inputs into Prover.toml text.

    Top-level dicts become ``[name]`` tables (Noir structs); everything else is
    rendered inline. Nested dicts inside arrays use inline tables.
    """
    encoded = {str(k): to_abi_value(v) for k, v in inputs.items()}
    lines: list[str] = []
    tables: list[tuple[str, dict]] = []
    for key, val in encoded.items():
        if isinstance(val, dict):
            tables.append((key, val))
        else:
            lines.append(f"{key} = {_toml_value(val)}")

    def emit_table(prefix: str, table: dict) -> None:
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
        lines.append("")
        lines.append(f"[{prefix}]")
        for k, v in scalars.items():
            lines.append(f"{k} = {_toml_value(v)}")
        for k, v in subtables.items():
            emit_table(f"{prefix}.{k}", v)

    for key, table in tables:
        emit_table(key, table)
    return "\n".join(lines).strip() + "\n"
