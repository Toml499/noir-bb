"""Unit tests for the msgpack backend's building blocks (no real bb needed):

* the dependency-free MessagePack codec (round-trips + exact wire bytes),
* verifier_target -> bb ProofSystemSettings mapping,
* the bb.js-shape helpers and the ABI-based recursive-input checks.

The full cryptographic round-trip against `bb msgpack run` lives in
examples/recursive_aggregation/run_recursive_aggregation.py.
"""
from __future__ import annotations

import json

import pytest

from noir_bb import (
    Proof,
    VerificationKey,
    acir_vk_as_fields_ultra_honk,
    check_against_circuit,
    deflatten_fields,
    recursive_inputs,
    settings_for,
)
from noir_bb import _msgpack as mp
from noir_bb.errors import VersionError
from noir_bb.msgpack_api import _field_hex, _field_to_bin


# --------------------------------------------------------------------------
# codec
# --------------------------------------------------------------------------

ROUND_TRIP_SAMPLES = [
    None, True, False,
    0, 1, 127, 128, 255, 256, 65535, 65536, 2 ** 32 - 1, 2 ** 32, 2 ** 64 - 1,
    -1, -32, -33, -128, -129, -32768, -40000, -(2 ** 31), -(2 ** 63),
    "", "x", "CircuitProveResponse", "a" * 40, "a" * 300,
    b"", b"\x00" * 32, bytes(range(40)),
    [1, 2, 3], [], [b"\x01" * 32, b"\x02" * 32],
    {"name": "main", "bytecode": b"\x01\x02", "settings": {"disable_zk": False}},
    [["CircuitComputeVk", {"circuit": {"name": "m", "bytecode": b"abc"}}]],
]


@pytest.mark.parametrize("value", ROUND_TRIP_SAMPLES)
def test_codec_round_trip(value):
    assert mp.unpackb(mp.packb(value)) == value


def test_codec_exact_wire_bytes():
    # Lock down the framing the bb API depends on.
    assert mp.packb(None) == b"\xc0"
    assert mp.packb(True) == b"\xc3"
    assert mp.packb(False) == b"\xc2"
    assert mp.packb(0) == b"\x00"
    assert mp.packb(127) == b"\x7f"
    assert mp.packb(-1) == b"\xff"
    assert mp.packb(256) == b"\xcd\x01\x00"          # uint16
    assert mp.packb("x") == b"\xa1x"                  # fixstr len 1
    assert mp.packb(b"\x01\x02") == b"\xc4\x02\x01\x02"  # bin8 len 2
    assert mp.packb([1, 2]) == b"\x92\x01\x02"         # fixarray
    assert mp.packb({"a": 1}) == b"\x81\xa1a\x01"      # fixmap


def test_codec_str_and_bin_are_distinct():
    # bb distinguishes command names (str) from field elements (bin); the codec
    # must not conflate them.
    assert isinstance(mp.unpackb(mp.packb("vk")), str)
    assert isinstance(mp.unpackb(mp.packb(b"vk")), bytes)


@pytest.mark.skipif(
    pytest.importorskip("msgpack", reason="reference msgpack not installed") is None,
    reason="reference msgpack not installed",
)
def test_codec_matches_reference_msgpack():
    import msgpack
    for value in ROUND_TRIP_SAMPLES:
        assert mp.packb(value) == msgpack.packb(value, use_bin_type=True)


# --------------------------------------------------------------------------
# settings mapping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("target,oracle,disable_zk,ipa", [
    ("evm", "keccak", False, False),
    ("evm-no-zk", "keccak", True, False),
    ("noir-recursive", "poseidon2", False, False),
    ("noir-recursive-no-zk", "poseidon2", True, False),
    ("noir-rollup", "poseidon2", False, True),
    ("noir-rollup-no-zk", "poseidon2", True, True),
    ("starknet", "starknet", False, False),
])
def test_settings_for(target, oracle, disable_zk, ipa):
    s = settings_for(target)
    assert s["oracle_hash_type"] == oracle
    assert s["disable_zk"] is disable_zk
    assert s["ipa_accumulation"] is ipa
    assert s["optimized_solidity_verifier"] is False


def test_settings_for_rejects_unknown_target():
    with pytest.raises(ValueError):
        settings_for("nope")


# --------------------------------------------------------------------------
# field helpers
# --------------------------------------------------------------------------

def test_field_hex_and_bin_round_trip():
    raw = bytes(range(32))
    hexstr = _field_hex(raw)
    assert hexstr == "0x" + raw.hex()
    assert _field_to_bin(hexstr) == raw
    assert _field_to_bin(2) == (2).to_bytes(32, "big")
    assert _field_hex(2) == "0x" + (2).to_bytes(32, "big").hex()


def test_deflatten_and_vk_as_fields_helpers(tmp_path):
    proof = Proof(directory=tmp_path, fields=["0x01", "0x02"], public_inputs=["0x09"])
    vk = VerificationKey(directory=tmp_path, fields=["0x0a", "0x0b", "0x0c"])
    assert deflatten_fields(proof) == ["0x01", "0x02"]
    assert deflatten_fields(["0x05"]) == ["0x05"]
    assert acir_vk_as_fields_ultra_honk(vk) == ["0x0a", "0x0b", "0x0c"]


# --------------------------------------------------------------------------
# recursive_inputs: key_hash toggle + ABI check
# --------------------------------------------------------------------------

def test_recursive_inputs_can_omit_key_hash(tmp_path):
    proof = Proof(directory=tmp_path, fields=["0x01"] * 508, public_inputs=["0x02"],
                  vk_hash="0x07", verifier_target="noir-recursive")
    vk = VerificationKey(directory=tmp_path, fields=["0x02"] * 115, key_hash="0x07")
    three = recursive_inputs(proof, vk, include_key_hash=False, check=False)
    assert set(three) == {"proof", "public_inputs", "verification_key"}  # bb.js shape
    four = recursive_inputs(proof, vk, check=False)
    assert set(four) == {"proof", "public_inputs", "verification_key", "key_hash"}


def _fake_compiled_circuit(path, *, proof_len, vk_len, n_pub):
    path.write_text(json.dumps({"abi": {"parameters": [
        {"name": "verification_key", "type": {"kind": "array", "length": vk_len}},
        {"name": "proof", "type": {"kind": "array", "length": proof_len}},
        {"name": "public_inputs", "type": {"kind": "array", "length": n_pub}},
        {"name": "key_hash", "type": {"kind": "field"}},
    ]}}))


def test_recursive_inputs_checks_against_circuit_abi(tmp_path):
    circuit = tmp_path / "recursive.json"
    _fake_compiled_circuit(circuit, proof_len=508, vk_len=115, n_pub=1)
    proof = Proof(directory=tmp_path, fields=["0x01"] * 508, public_inputs=["0x02"],
                  vk_hash="0x07", verifier_target="noir-recursive")
    vk = VerificationKey(directory=tmp_path, fields=["0x02"] * 115, key_hash="0x07")

    # matching sizes: passes even though they differ from the hardcoded constants
    inputs = recursive_inputs(proof, vk, circuit=circuit)
    assert len(inputs["proof"]) == 508

    # mismatched ABI: descriptive VersionError
    wrong = tmp_path / "wrong.json"
    _fake_compiled_circuit(wrong, proof_len=500, vk_len=115, n_pub=1)
    with pytest.raises(VersionError) as exc:
        check_against_circuit(proof, vk, wrong)
    assert "508" in str(exc.value) and "500" in str(exc.value)
