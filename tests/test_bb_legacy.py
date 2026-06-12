"""Tests for the legacy-bb flag mapping (BB_FAKE_LEGACY=1, bb 0.8x dialect)."""
from __future__ import annotations

import json

import pytest

from noir_bb import Barretenberg, NoirProject
from noir_bb.errors import VersionError


def _witness(project_dir):
    return NoirProject(project_dir).execute({"x": 3, "y": 3})


def _invocation(out_dir):
    return json.loads((out_dir / "bb_invocation.json").read_text())["argv"]


def test_legacy_capabilities(legacy_bb):
    bb = Barretenberg()
    assert bb.version() == "0.87.0-fake"
    caps = bb.capabilities
    assert not caps.modern
    assert caps.oracle_hash and caps.honk_recursion and caps.zk_flag
    assert caps.output_bytes_and_fields and not caps.output_json
    assert not caps.write_vk_flag


def test_legacy_recursive_flag_mapping(legacy_bb, inner_project, tmp_path):
    w = _witness(inner_project)
    out = tmp_path / "p"
    proof = Barretenberg().prove(w.circuit_path, w.witness_path, out,
                                 verifier_target="noir-recursive-no-zk")
    argv = _invocation(out)
    assert ["--oracle_hash", "poseidon2"] == \
        argv[argv.index("--oracle_hash"): argv.index("--oracle_hash") + 2]
    assert ["--honk_recursion", "1"] == \
        argv[argv.index("--honk_recursion"): argv.index("--honk_recursion") + 2]
    assert "--zk" not in argv and "--verifier_target" not in argv
    assert ["--output_format", "bytes_and_fields"] == \
        argv[argv.index("--output_format"): argv.index("--output_format") + 2]

    # artifacts come back via the *_fields.json files
    assert (out / "proof_fields.json").is_file()
    assert proof.n_fields == 449
    assert len(proof.public_inputs) == 1
    assert proof.proof_path == out / "proof"


def test_legacy_evm_maps_keccak_and_zk(legacy_bb, inner_project, tmp_path):
    w = _witness(inner_project)
    out = tmp_path / "p"
    proof = Barretenberg().prove(w.circuit_path, w.witness_path, out,
                                 verifier_target="evm")
    argv = _invocation(out)
    assert ["--oracle_hash", "keccak"] == \
        argv[argv.index("--oracle_hash"): argv.index("--oracle_hash") + 2]
    assert "--zk" in argv
    assert "--honk_recursion" not in argv
    assert proof.n_fields == 500  # ZK-sized


def test_legacy_prove_write_vk_unsupported(legacy_bb, inner_project, tmp_path):
    w = _witness(inner_project)
    with pytest.raises(VersionError):
        Barretenberg().prove(w.circuit_path, w.witness_path, tmp_path / "p",
                             verifier_target="noir-recursive-no-zk", write_vk=True)


def test_legacy_prove_vk_input_unsupported(legacy_bb, inner_project, tmp_path):
    w = _witness(inner_project)
    with pytest.raises(VersionError):
        Barretenberg().prove(w.circuit_path, w.witness_path, tmp_path / "p",
                             verifier_target="noir-recursive-no-zk",
                             vk=tmp_path / "vk")


def test_legacy_write_vk_then_verify(legacy_bb, inner_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    proof = bb.prove(w.circuit_path, w.witness_path, tmp_path / "p",
                     verifier_target="noir-recursive-no-zk")
    vk = bb.write_vk(w.circuit_path, tmp_path / "vk",
                     verifier_target="noir-recursive-no-zk")
    assert vk.fields is not None and len(vk.fields) == 115
    assert vk.key_hash and vk.key_hash.startswith("0x")
    assert vk.path == tmp_path / "vk" / "vk"   # binary handed to bb verify
    assert bb.verify(proof, vk) is True
