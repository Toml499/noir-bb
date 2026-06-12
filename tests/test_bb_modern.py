"""Tests for noir_bb.bb against the fake modern bb (--verifier_target dialect)."""
from __future__ import annotations

import json

import pytest

from noir_bb import (
    Barretenberg,
    NoirProject,
    Proof,
    UltraHonkBackend,
    VerificationKey,
)
from noir_bb.errors import CommandError


def _witness(project_dir, x=3, y=3):
    proj = NoirProject(project_dir)
    return proj.execute({"x": x, "y": y})


def _invocation(out_dir):
    return json.loads((out_dir / "bb_invocation.json").read_text())["argv"]


def test_version_and_capabilities():
    bb = Barretenberg()
    assert bb.version() == "4.0.0-fake.1"
    caps = bb.capabilities
    assert caps.modern and caps.verifier_target
    assert caps.output_json
    assert caps.write_vk_flag
    assert not caps.honk_recursion  # modern help drops the legacy flag
    assert UltraHonkBackend is Barretenberg


def test_prove_json_non_zk_sizes_and_flags(inner_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    out = tmp_path / "proof_dir"
    proof = bb.prove(w.circuit_path, w.witness_path, out,
                     verifier_target="noir-recursive-no-zk", write_vk=True)

    assert proof.n_fields == 449          # non-ZK recursive proof length
    assert len(proof.public_inputs) == 1
    assert proof.vk_hash and proof.vk_hash.startswith("0x")
    assert proof.scheme == "ultra_honk"
    assert proof.vk is not None
    assert len(proof.vk.fields) == 115    # ULTRA_VK_LENGTH_IN_FIELDS
    assert proof.vk.key_hash == proof.vk_hash

    argv = _invocation(out)
    assert ["--verifier_target", "noir-recursive-no-zk"] == \
        argv[argv.index("--verifier_target"): argv.index("--verifier_target") + 2]
    assert "--write_vk" in argv
    assert ["--output_format", "json"] == \
        argv[argv.index("--output_format"): argv.index("--output_format") + 2]
    assert "--oracle_hash" not in argv    # modern path never uses legacy flags


def test_prove_zk_length(inner_project, tmp_path):
    w = _witness(inner_project)
    proof = Barretenberg().prove(w.circuit_path, w.witness_path, tmp_path / "p",
                                 verifier_target="noir-recursive")
    assert proof.n_fields == 500          # ZK recursive proof length


def test_prove_auto_writes_vk_when_none_given(inner_project, tmp_path):
    w = _witness(inner_project)
    out = tmp_path / "p"
    proof = Barretenberg().prove(w.circuit_path, w.witness_path, out,
                                 verifier_target="noir-recursive-no-zk")
    assert "--write_vk" in _invocation(out)   # bb 4.x needs a vk or --write_vk
    assert proof.vk is not None
    assert proof.vk.key_hash == proof.vk_hash


def test_prove_with_precomputed_vk(inner_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    vk = bb.write_vk(w.circuit_path, tmp_path / "vk",
                     verifier_target="noir-recursive-no-zk")
    out = tmp_path / "p"
    proof = bb.prove(w.circuit_path, w.witness_path, out, vk=vk,
                     verifier_target="noir-recursive-no-zk")
    argv = _invocation(out)
    assert ["-k", str(vk.path)] == argv[argv.index("-k"): argv.index("-k") + 2]
    assert "--write_vk" not in argv           # no redundant vk computation
    assert proof.vk is vk
    assert bb.verify(proof, proof.vk) is True


def test_prove_accepts_raw_vk_path(inner_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    vk = bb.write_vk(w.circuit_path, tmp_path / "vk",
                     verifier_target="noir-recursive-no-zk")
    proof = bb.prove(w.circuit_path, w.witness_path, tmp_path / "p", vk=vk.path,
                     verifier_target="noir-recursive-no-zk")
    assert proof.vk.path == vk.path
    assert bb.verify(proof, proof.vk) is True


def test_prove_write_vk_false_fails_without_vk(inner_project, tmp_path):
    w = _witness(inner_project)
    with pytest.raises(CommandError):         # bb 4.x: no -k and no --write_vk
        Barretenberg().prove(w.circuit_path, w.witness_path, tmp_path / "p",
                             verifier_target="noir-recursive", write_vk=False)


def test_prove_rejects_unknown_target(inner_project, tmp_path):
    w = _witness(inner_project)
    with pytest.raises(ValueError):
        Barretenberg().prove(w.circuit_path, w.witness_path, tmp_path / "p",
                             verifier_target="not-a-target")


def test_prove_binary_format_chunks_on_load(inner_project, tmp_path):
    w = _witness(inner_project)
    out = tmp_path / "bin"
    proof = Barretenberg().prove(w.circuit_path, w.witness_path, out,
                                 verifier_target="noir-recursive-no-zk",
                                 output_format="binary", write_vk=True)
    assert (out / "proof").is_file() and not (out / "proof.json").exists()
    assert proof.n_fields == 449          # recovered via 32-byte chunking
    assert proof.vk.fields is None        # binary vk has no field repr
    assert proof.vk.key_hash             # ...but the hash file is read


def test_write_vk_and_verify_roundtrip(inner_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    proof = bb.prove(w.circuit_path, w.witness_path, tmp_path / "p",
                     verifier_target="noir-recursive-no-zk")
    vk = bb.write_vk(w.circuit_path, tmp_path / "vk",
                     verifier_target="noir-recursive-no-zk")
    assert isinstance(vk, VerificationKey)
    assert vk.key_hash == proof.vk_hash
    assert bb.verify(proof, vk) is True


def test_verify_rejects_mismatched_circuit(inner_project, outer_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    proof = bb.prove(w.circuit_path, w.witness_path, tmp_path / "p",
                     verifier_target="noir-recursive-no-zk")
    other = NoirProject(outer_project)
    other.compile()
    wrong_vk = bb.write_vk(other.circuit_json, tmp_path / "wrong_vk",
                           verifier_target="noir-recursive-no-zk")
    assert bb.verify(proof, wrong_vk) is False
    with pytest.raises(CommandError):
        bb.verify(proof, wrong_vk, strict=True)


def test_verify_errors_on_missing_proof(tmp_path, inner_project):
    bb = Barretenberg()
    proj = NoirProject(inner_project)
    proj.compile()
    vk = bb.write_vk(proj.circuit_json, tmp_path / "vk",
                     verifier_target="noir-recursive-no-zk")
    with pytest.raises(CommandError):     # rc=2 tool error, not a clean False
        bb.verify(tmp_path / "nope" / "proof", vk)


def test_verify_accepts_raw_paths(inner_project, tmp_path):
    w = _witness(inner_project)
    bb = Barretenberg()
    out = tmp_path / "p"
    bb.prove(w.circuit_path, w.witness_path, out,
             verifier_target="noir-recursive-no-zk", write_vk=True)
    ok = bb.verify(out / "proof.json", out / "vk.json",
                   public_inputs=out / "public_inputs.json",
                   verifier_target="noir-recursive-no-zk")
    assert ok is True


def test_gates_returns_dict(inner_project):
    proj = NoirProject(inner_project)
    circuit = proj.compile()
    report = Barretenberg().gates(circuit)
    assert report["functions"][0]["circuit_size"] == 1024


def test_write_solidity_verifier(inner_project, tmp_path):
    proj = NoirProject(inner_project)
    circuit = proj.compile()
    bb = Barretenberg()
    vk = bb.write_vk(circuit, tmp_path / "vk", verifier_target="evm")
    sol = bb.write_solidity_verifier(vk, tmp_path / "contracts" / "Verifier.sol")
    assert sol.is_file()
    assert "contract" in sol.read_text()
