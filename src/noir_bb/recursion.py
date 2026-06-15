"""Helpers for recursive proof composition.

Mirrors the flow in noir-examples/recursion: an *inner* proof generated with a
``noir-recursive*`` verifier target is fed, as ordinary circuit inputs, into an
*outer* circuit that calls ``verify_honk_proof`` /
``verify_honk_proof_non_zk`` from Aztec's ``bb_proof_verification`` Noir
library.

The expected field counts below come from bb_proof_verification (aztec-packages
v4.x) and shift between major bb releases — ``check_recursive_artifacts``
exists precisely to catch that mismatch early with a readable error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .artifacts import Proof, VerificationKey
from .errors import ArtifactError, VersionError

#: Constants from bb_proof_verification (aztec-packages v4.1.1, bb 3.x).
ULTRA_VK_LENGTH_IN_FIELDS = 115
RECURSIVE_PROOF_LENGTH = 449            # noir-recursive-no-zk
RECURSIVE_ZK_PROOF_LENGTH = 492 + 8     # noir-recursive (ZK)
IPA_CLAIM_SIZE = 6
IPA_PROOF_LENGTH = 64
RECURSIVE_ROLLUP_PROOF_LENGTH = RECURSIVE_PROOF_LENGTH + IPA_CLAIM_SIZE + IPA_PROOF_LENGTH

_EXPECTED_PROOF_LEN = {
    "noir-recursive-no-zk": RECURSIVE_PROOF_LENGTH,
    "noir-recursive": RECURSIVE_ZK_PROOF_LENGTH,
    "noir-rollup-no-zk": RECURSIVE_ROLLUP_PROOF_LENGTH,
    "noir-rollup": RECURSIVE_ZK_PROOF_LENGTH + IPA_CLAIM_SIZE + IPA_PROOF_LENGTH,
}


def expected_proof_length(verifier_target: str) -> Optional[int]:
    return _EXPECTED_PROOF_LEN.get(verifier_target)


def recursive_inputs(
    proof: Proof,
    vk: Optional[VerificationKey] = None,
    *,
    key_hash: Optional[Union[str, int]] = None,
    include_key_hash: bool = True,
    check: bool = True,
    circuit: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build the input dict for an outer verifier circuit.

    Returns ``{"verification_key", "proof", "public_inputs", "key_hash"}`` —
    the parameter names used by the noir-examples recursive circuit — ready to
    pass to ``NoirProject.execute()``.

    ``include_key_hash=False`` drops the ``key_hash`` entry, matching outer
    circuits whose ABI has only three inputs because the key hash is hardcoded
    in the circuit (e.g. ``verify_honk_proof(vk, proof, public_inputs, 0x0)`` in
    the Barretenberg recursive-aggregation tutorial). The resulting dict is then
    byte-for-byte the ``{ proof, public_inputs, verification_key }`` object that
    bb.js's ``recursive_inputs`` snippet builds.

    ``circuit`` (a :class:`~noir_bb.NoirProject`, an ``ExecutionResult``, or a
    path to the compiled ``*.json``) checks the proof/vk/public-input lengths
    against that circuit's actual ABI — version-independent, and preferable to
    the hardcoded ``check`` against bb_proof_verification's constants, which
    drift between bb releases.
    """
    vk = vk or proof.vk
    if vk is None:
        raise ArtifactError(
            "no verification key: pass vk=... or generate the proof with write_vk=True"
        )
    vk_fields = vk.require_fields()
    if not proof.fields:
        raise ArtifactError(
            "proof has no field representation; generate it with "
            "output_format='json' (or 'bytes_and_fields' on legacy bb)"
        )
    if circuit is not None:
        check_against_circuit(proof, vk, circuit)
    elif check and proof.verifier_target:
        check_recursive_artifacts(proof, vk, proof.verifier_target)

    inputs: Dict[str, Any] = {
        "verification_key": list(vk_fields),
        "proof": list(proof.fields),
        "public_inputs": list(proof.public_inputs),
    }
    if include_key_hash:
        kh = key_hash if key_hash is not None else (vk.key_hash or proof.vk_hash)
        if kh is None:
            raise ArtifactError(
                "no verification-key hash available; pass key_hash=... explicitly "
                "(bb writes it into vk.json / the vk_hash file), or set "
                "include_key_hash=False if the outer circuit hardcodes it"
            )
        inputs["key_hash"] = kh if isinstance(kh, str) else str(kh)
    return inputs


def _abi_array_lengths(circuit: Any) -> Dict[str, int]:
    """Map each array parameter name to its length in a compiled circuit's ABI."""
    path = getattr(circuit, "circuit_json", None) or getattr(circuit, "circuit_path", None) or circuit
    data = json.loads(Path(path).read_text())
    out: Dict[str, int] = {}
    for param in data.get("abi", {}).get("parameters", []):
        ty = param.get("type", {})
        if ty.get("kind") == "array" and "length" in ty:
            out[param["name"]] = ty["length"]
    return out


def check_against_circuit(proof: Proof, vk: VerificationKey, circuit: Any) -> None:
    """Raise if the proof/vk/public-input sizes don't fit the outer circuit's ABI.

    The outer circuit's ``proof``/``verification_key``/``public_inputs`` array
    parameters encode the exact field counts it expects; comparing against them
    catches a bb/bb_proof_verification version mismatch without hardcoding any
    constant. This is the version-independent counterpart to
    :func:`check_recursive_artifacts`.
    """
    lengths = _abi_array_lengths(circuit)
    problems: List[str] = []
    checks = [
        ("proof", proof.n_fields),
        ("verification_key", vk.n_fields),
        ("public_inputs", len(proof.public_inputs)),
    ]
    for name, actual in checks:
        expected = lengths.get(name)
        if expected is not None and actual is not None and actual != expected:
            problems.append(
                f"{name}: artifact has {actual} field(s) but the outer circuit's "
                f"ABI expects [{name}; {expected}]"
            )
    if problems:
        raise VersionError(
            "recursive artifact sizes do not match the outer circuit's ABI — your "
            "bb version and the outer circuit's bb_proof_verification dependency "
            "likely disagree:\n  - " + "\n  - ".join(problems) +
            "\nPin the outer Nargo.toml's bb_proof_verification tag to the "
            "aztec-packages release matching your bb (see noir_bb.doctor())."
        )


def check_recursive_artifacts(proof: Proof, vk: VerificationKey, verifier_target: str) -> None:
    """Raise a descriptive error when artifact sizes don't match expectations.

    Size drift is the classic nargo/bb/bb_proof_verification version-mismatch
    symptom; this turns a cryptic circuit ABI error into an actionable one.
    """
    problems: List[str] = []
    expected = expected_proof_length(verifier_target)
    if expected is not None and proof.n_fields != expected:
        problems.append(
            f"proof has {proof.n_fields} fields but bb_proof_verification v4.x "
            f"expects {expected} for target {verifier_target!r}"
        )
    if vk.n_fields is not None and vk.n_fields != ULTRA_VK_LENGTH_IN_FIELDS:
        problems.append(
            f"verification key has {vk.n_fields} fields, expected "
            f"{ULTRA_VK_LENGTH_IN_FIELDS}"
        )
    if problems:
        raise VersionError(
            "recursive artifact size mismatch — your bb version and the "
            "bb_proof_verification dependency of the outer circuit likely "
            "disagree:\n  - " + "\n  - ".join(problems) +
            "\nPin matching versions (Nargo.toml dependency tag <-> bb release) "
            "or pass check=False and adjust the outer circuit's array sizes to "
            "the measured lengths."
        )


def noir_verifier_snippet(proof: Proof, vk: VerificationKey, *, zk: Optional[bool] = None) -> str:
    """Return a ready-to-paste outer-circuit ``main.nr`` matching these artifacts."""
    if zk is None:
        zk = not (proof.verifier_target or "").endswith("-no-zk")
    fn = "verify_honk_proof" if zk else "verify_honk_proof_non_zk"
    proof_ty = "UltraHonkZKProof" if zk else "UltraHonkProof"
    n_pub = len(proof.public_inputs)
    return f"""use bb_proof_verification::{{{proof_ty}, UltraHonkVerificationKey, {fn}}};

// Generated for: {proof.n_fields} proof fields, {vk.n_fields} vk fields,
// {n_pub} public input(s), verifier_target={proof.verifier_target!r}
fn main(
    verification_key: UltraHonkVerificationKey,
    proof: {proof_ty},
    public_inputs: pub [Field; {n_pub}],
    key_hash: Field,
) {{
    {fn}(verification_key, proof, public_inputs, key_hash);
}}
"""
