"""Tests for noir_bb.abi: Prover.toml encoding and field-element helpers."""
from __future__ import annotations

import tomllib

import pytest

from noir_bb import (
    BN254_FR_MODULUS,
    dumps_prover_toml,
    fields_to_bytes,
    proof_bytes_to_fields,
    to_hex_field,
)
from noir_bb.abi import to_abi_value
from noir_bb.errors import InputError


def test_to_hex_field_int_and_str():
    assert to_hex_field(9) == "0x" + "00" * 31 + "09"
    assert to_hex_field("9") == to_hex_field(9)
    assert to_hex_field("0x09") == to_hex_field(9)


def test_to_hex_field_negative_wraps_mod_p():
    assert to_hex_field(-1) == to_hex_field(BN254_FR_MODULUS - 1)


def test_proof_bytes_to_fields_round_trip():
    data = bytes(range(64))  # two field elements
    fields = proof_bytes_to_fields(data)
    assert len(fields) == 2
    assert all(f.startswith("0x") and len(f) == 66 for f in fields)
    assert fields_to_bytes(fields) == data


def test_proof_bytes_to_fields_rejects_misaligned():
    with pytest.raises(InputError):
        proof_bytes_to_fields(b"\x00" * 33)


def test_to_abi_value_basics():
    assert to_abi_value(7) == "7"
    assert to_abi_value(True) is True
    assert to_abi_value("0xff") == "0xff"
    assert to_abi_value(b"\x01\x02") == ["1", "2"]
    assert to_abi_value(-1) == str(BN254_FR_MODULUS - 1)
    with pytest.raises(InputError):
        to_abi_value(-1, wrap_negative=False)
    with pytest.raises(InputError):
        to_abi_value(object())


def test_dumps_prover_toml_parses_back():
    toml_text = dumps_prover_toml(
        {
            "x": 3,
            "flag": False,
            "arr": [1, 2, 3],
            "point": {"x": 1, "y": -1},
            "structs": [{"a": 1}, {"a": 2}],
        }
    )
    parsed = tomllib.loads(toml_text)
    assert parsed["x"] == "3"
    assert parsed["flag"] is False
    assert parsed["arr"] == ["1", "2", "3"]
    assert parsed["point"] == {"x": "1", "y": str(BN254_FR_MODULUS - 1)}
    assert parsed["structs"] == [{"a": "1"}, {"a": "2"}]


def test_dumps_prover_toml_nested_struct_tables():
    toml_text = dumps_prover_toml({"outer": {"inner": {"v": 5}, "w": 1}})
    parsed = tomllib.loads(toml_text)
    assert parsed["outer"]["w"] == "1"
    assert parsed["outer"]["inner"]["v"] == "5"
