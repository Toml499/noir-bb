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
    check: bool = True,
) -> Dict[str, Any]:
    """Build the input dict for an outer verifier circuit.

    Returns ``{"verification_key", "proof", "public_inputs", "key_hash"}`` —
    exactly the parameter names used by the noir-examples recursive circuit —
    ready to pass to ``NoirProject.execute()``.
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
    kh = key_hash if key_hash is not None else (vk.key_hash or proof.vk_hash)
    if kh is None:
        raise ArtifactError(
            "no verification-key hash available; pass key_hash=... explicitly "
            "(bb writes it into vk.json / the vk_hash file)"
        )
    if check and proof.verifier_target:
        check_recursive_artifacts(proof, vk, proof.verifier_target)
    return {
        "verification_key": list(vk_fields),
        "proof": list(proof.fields),
        "public_inputs": list(proof.public_inputs),
        "key_hash": kh if isinstance(kh, str) else str(kh),
    }


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
