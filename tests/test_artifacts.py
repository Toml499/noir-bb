"""Tests for noir_bb.artifacts: loading proofs/VKs in all three layouts."""
from __future__ import annotations

import json

import pytest

from noir_bb import Proof, VerificationKey
from noir_bb.errors import ArtifactError


def _field(i: int) -> str:
    return "0x" + i.to_bytes(32, "big").hex()


def test_load_binary_layout_chunks_fields(tmp_path):
    proof_fields = [_field(i) for i in range(4)]
    pubs = [_field(99)]
    (tmp_path / "proof").write_bytes(b"".join(bytes.fromhex(f[2:]) for f in proof_fields))
    (tmp_path / "public_inputs").write_bytes(bytes.fromhex(pubs[0][2:]))
    (tmp_path / "vk").write_bytes(b"\x01" * 64)
    (tmp_path / "vk_hash").write_bytes(b"\xab" * 32)

    proof = Proof.load(tmp_path, verifier_target="noir-recursive-no-zk")
    assert proof.fields == proof_fields
    assert proof.public_inputs == pubs
    assert proof.proof_path == tmp_path / "proof"
    assert proof.proof_bytes == (tmp_path / "proof").read_bytes()

    vk = VerificationKey.load(tmp_path)
    assert vk.fields is None
    assert vk.key_hash == "0x" + "ab" * 32
    assert vk.path == tmp_path / "vk"
    with pytest.raises(ArtifactError):
        vk.require_fields()


def test_load_modern_json_layout(tmp_path):
    (tmp_path / "proof.json").write_text(json.dumps(
        {"proof": [_field(1), _field(2)], "vk_hash": _field(7), "scheme": "ultra_honk"}))
    (tmp_path / "public_inputs.json").write_text(json.dumps({"public_inputs": [_field(3)]}))
    (tmp_path / "vk.json").write_text(json.dumps({"vk": [_field(4)], "hash": _field(7)}))

    proof = Proof.load(tmp_path)
    assert proof.fields == [_field(1), _field(2)]
    assert proof.public_inputs == [_field(3)]
    assert proof.vk_hash == _field(7)
    assert proof.scheme == "ultra_honk"
    # no binary file: bytes are rebuilt from the fields
    assert proof.proof_bytes == bytes.fromhex(_field(1)[2:]) + bytes.fromhex(_field(2)[2:])

    vk = VerificationKey.load(tmp_path)
    assert vk.fields == [_field(4)]
    assert vk.key_hash == _field(7)
    assert vk.require_fields() == [_field(4)]


def test_load_legacy_fields_layout(tmp_path):
    (tmp_path / "proof").write_bytes(bytes.fromhex(_field(1)[2:]))
    (tmp_path / "proof_fields.json").write_text(json.dumps([_field(1)]))
    (tmp_path / "public_inputs_fields.json").write_text(json.dumps([_field(2)]))
    (tmp_path / "vk_fields.json").write_text(json.dumps([_field(5), _field(6)]))
    (tmp_path / "vk_hash").write_bytes(bytes.fromhex(_field(9)[2:]))

    proof = Proof.load(tmp_path)
    assert proof.fields == [_field(1)]
    assert proof.public_inputs == [_field(2)]
    assert proof.proof_path == tmp_path / "proof"  # binary preferred for bb verify

    vk = VerificationKey.load(tmp_path)
    assert vk.fields == [_field(5), _field(6)]
    assert vk.key_hash == _field(9)


def test_load_missing_artifacts_raise(tmp_path):
    with pytest.raises(ArtifactError):
        Proof.load(tmp_path)
    with pytest.raises(ArtifactError):
        VerificationKey.load(tmp_path)
