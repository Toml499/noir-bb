"""noir-bb: run, prove and verify Noir circuits from Python via nargo + Barretenberg.

Quick start::

    from noir_bb import NoirProject, Barretenberg, recursive_inputs

    inner = NoirProject("circuits/inner")
    witness = inner.execute({"x": 3, "y": 3})

    bb = Barretenberg()
    proof = bb.prove(witness.circuit_path, witness.witness_path, "proofs/inner",
                     verifier_target="noir-recursive-no-zk")
    assert bb.verify(proof, proof.vk)

    outer = NoirProject("circuits/recursive")
    w2 = outer.execute(recursive_inputs(proof))
    proof2 = bb.prove(w2.circuit_path, w2.witness_path, "proofs/recursive",
                      verifier_target="evm")
    assert bb.verify(proof2, proof2.vk)
"""

from __future__ import annotations

from .abi import (
    BN254_FR_MODULUS,
    dumps_prover_toml,
    fields_to_bytes,
    proof_bytes_to_fields,
    to_hex_field,
)
from .artifacts import Proof, VerificationKey
from .bb import Barretenberg, Capabilities, UltraHonkBackend, VERIFIER_TARGETS
from .errors import (
    ArtifactError,
    CommandError,
    InputError,
    NoirBBError,
    ToolNotFoundError,
    VersionError,
)
from .nargo import ExecutionResult, Nargo, NoirProject
from .recursion import (
    RECURSIVE_PROOF_LENGTH,
    RECURSIVE_ZK_PROOF_LENGTH,
    ULTRA_VK_LENGTH_IN_FIELDS,
    check_recursive_artifacts,
    expected_proof_length,
    noir_verifier_snippet,
    recursive_inputs,
)

__version__ = "0.1.0"

__all__ = [
    "Nargo", "NoirProject", "ExecutionResult",
    "Barretenberg", "UltraHonkBackend", "Capabilities", "VERIFIER_TARGETS",
    "Proof", "VerificationKey",
    "recursive_inputs", "check_recursive_artifacts", "expected_proof_length",
    "noir_verifier_snippet",
    "RECURSIVE_PROOF_LENGTH", "RECURSIVE_ZK_PROOF_LENGTH", "ULTRA_VK_LENGTH_IN_FIELDS",
    "dumps_prover_toml", "to_hex_field", "proof_bytes_to_fields", "fields_to_bytes",
    "BN254_FR_MODULUS",
    "NoirBBError", "ToolNotFoundError", "CommandError", "VersionError",
    "ArtifactError", "InputError",
    "doctor",
]


def doctor(*, nargo_path=None, bb_path=None) -> str:
    """Print and return a toolchain diagnostic (versions + bb capabilities).

    Useful when chasing nargo/bb version mismatches: shows whether the
    installed bb speaks the modern `--verifier_target` dialect or needs the
    legacy flag mapping, and reminds you of the artifact-size coupling with
    the bb_proof_verification Noir library.
    """
    lines = ["noir-bb doctor", "=" * 40]
    try:
        lines.append(f"nargo : {Nargo(nargo_path).version()}")
    except NoirBBError as exc:
        lines.append(f"nargo : NOT FOUND ({exc})")
    try:
        bb = Barretenberg(bb_path)
        caps = bb.capabilities
        lines.append(f"bb    : {bb.version()}")
        dialect = "modern (--verifier_target)" if caps.modern else "legacy (mapped flags)"
        lines.append(f"bb CLI dialect : {dialect}")
        fmt = ("json" if caps.output_json
               else "bytes_and_fields" if caps.output_bytes_and_fields
               else "binary only")
        lines.append(f"fields output  : {fmt}")
        if fmt == "binary only":
            lines.append(
                "WARNING: this bb has no --output_format flag, so it cannot emit "
                "the verification key as field elements from the CLI. The binary "
                "vk is a structured serialization (not flat fields), so it cannot "
                "be chunked either. Recursive composition (vk/proof as circuit "
                "inputs) is NOT possible with this bb; proof fields can still be "
                "recovered by 32-byte chunking, and native prove/verify work. "
                "Nightlies (3.0.x, 4.0.x) are typically in this category — switch "
                "to a stable release via bbup. Note that recursive proof lengths "
                "also differ across these versions, so the bb_proof_verification "
                "tag must match your bb."
            )
        if not caps.modern:
            lines.append(
                "note: recursion via legacy flags is best-effort; for the flow in "
                "noir-examples/recursion use Noir >= 1.0.0-beta.18 with bb >= 3.x "
                "(install via noirup/bbup) and pin the bb_proof_verification tag "
                "in the outer circuit's Nargo.toml to the matching aztec-packages release."
            )
    except NoirBBError as exc:
        lines.append(f"bb    : NOT FOUND ({exc})")
    report = "\n".join(lines)
    print(report)
    return report
