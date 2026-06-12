"""End-to-end recursion flow (inner proof -> outer circuit), mirroring
noir-examples/recursion, against the fake toolchain. Plus size-check guards."""
from __future__ import annotations

import tomllib

import pytest

from noir_bb import (
    Barretenberg,
    NoirProject,
    Proof,
    RECURSIVE_PROOF_LENGTH,
    RECURSIVE_ZK_PROOF_LENGTH,
    ULTRA_VK_LENGTH_IN_FIELDS,
    VerificationKey,
    check_recursive_artifacts,
    expected_proof_length,
    noir_verifier_snippet,
    recursive_inputs,
)
from noir_bb.errors import ArtifactError, VersionError


def test_constants_match_bb_proof_verification():
    assert RECURSIVE_PROOF_LENGTH == 449
    assert RECURSIVE_ZK_PROOF_LENGTH == 500
    assert ULTRA_VK_LENGTH_IN_FIELDS == 115
    assert expected_proof_length("noir-recursive-no-zk") == 449
    assert expected_proof_length("evm") is None


def test_full_recursion_pipeline(inner_project, outer_project, tmp_path):
    bb = Barretenberg()

    # 1. execute + prove the inner circuit for in-circuit verification
    inner = NoirProject(inner_project)
    w1 = inner.execute({"x": 3, "y": 3})
    inner_proof = bb.prove(
        w1.circuit_path, w1.witness_path, tmp_path / "inner_proof",
        verifier_target="noir-recursive-no-zk", write_vk=True,
    )

    # 2. turn the proof into outer-circuit inputs (noir-examples parameter names)
    inputs = recursive_inputs(inner_proof)
    assert set(inputs) == {"verification_key", "proof", "public_inputs", "key_hash"}
    assert len(inputs["proof"]) == RECURSIVE_PROOF_LENGTH
    assert len(inputs["verification_key"]) == ULTRA_VK_LENGTH_IN_FIELDS
    assert inputs["key_hash"] == inner_proof.vk_hash

    # 3. execute the outer circuit on those inputs; everything lands in TOML
    outer = NoirProject(outer_project)
    w2 = outer.execute(inputs)
    written = tomllib.loads((outer_project / "Prover_noirbb.toml").read_text())
    assert len(written["proof"]) == RECURSIVE_PROOF_LENGTH
    assert written["key_hash"] == inner_proof.vk_hash

    # 4. prove the outer circuit for an EVM verifier and check it
    outer_proof = bb.prove(
        w2.circuit_path, w2.witness_path, tmp_path / "outer_proof",
        verifier_target="evm", write_vk=True,
    )
    assert bb.verify(outer_proof, outer_proof.vk) is True


def test_recursive_inputs_requires_vk_and_fields(tmp_path):
    proof = Proof(directory=tmp_path, fields=["0x01"], verifier_target=None)
    with pytest.raises(ArtifactError):
        recursive_inputs(proof)  # no vk anywhere

    vk = VerificationKey(directory=tmp_path, fields=None)
    with pytest.raises(ArtifactError):
        recursive_inputs(proof, vk)  # vk without field representation


def test_recursive_inputs_requires_some_key_hash(tmp_path):
    proof = Proof(directory=tmp_path, fields=["0x01"] * 449,
                  verifier_target="noir-recursive-no-zk")
    vk = VerificationKey(directory=tmp_path, fields=["0x02"] * 115, key_hash=None)
    with pytest.raises(ArtifactError):
        recursive_inputs(proof, vk)
    ok = recursive_inputs(proof, vk, key_hash="0x09")
    assert ok["key_hash"] == "0x09"


def test_size_mismatch_raises_version_error(tmp_path):
    short = Proof(directory=tmp_path, fields=["0x01"] * 440,   # wrong length
                  public_inputs=["0x03"], vk_hash="0x07",
                  verifier_target="noir-recursive-no-zk")
    vk = VerificationKey(directory=tmp_path, fields=["0x02"] * 115, key_hash="0x07")
    with pytest.raises(VersionError) as exc:
        recursive_inputs(short, vk)
    assert "440" in str(exc.value) and "449" in str(exc.value)

    bad_vk = VerificationKey(directory=tmp_path, fields=["0x02"] * 100, key_hash="0x07")
    good = Proof(directory=tmp_path, fields=["0x01"] * 449,
                 public_inputs=["0x03"], vk_hash="0x07",
                 verifier_target="noir-recursive-no-zk")
    with pytest.raises(VersionError):
        check_recursive_artifacts(good, bad_vk, "noir-recursive-no-zk")
    # opt-out for non-standard setups
    assert recursive_inputs(short, vk, check=False)["proof"]


def test_noir_verifier_snippet_matches_target(tmp_path):
    proof = Proof(directory=tmp_path, fields=["0x01"] * 449,
                  public_inputs=["0x03"], vk_hash="0x07",
                  verifier_target="noir-recursive-no-zk")
    vk = VerificationKey(directory=tmp_path, fields=["0x02"] * 115, key_hash="0x07")
    snippet = noir_verifier_snippet(proof, vk)
    assert "verify_honk_proof_non_zk" in snippet
    assert "UltraHonkProof" in snippet and "UltraHonkZKProof" not in snippet
    assert "[Field; 1]" in snippet

    zk_proof = Proof(directory=tmp_path, fields=["0x01"] * 500,
                     public_inputs=["0x03"], vk_hash="0x07",
                     verifier_target="noir-recursive")
    assert "UltraHonkZKProof" in noir_verifier_snippet(zk_proof, vk)
