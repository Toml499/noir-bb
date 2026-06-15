"""A msgpack-API backend that mirrors ``@aztec/bb.js`` exactly.

``bb.js`` is not a separate proving engine -- it is a thin client that drives
the very same Barretenberg API exposed by the ``bb`` binary under
``bb msgpack run``. ``UltraHonkBackend.generateProof`` sends a ``CircuitProve``
command; ``getVerificationKey`` sends ``CircuitComputeVk``; ``verifyProof``
sends ``CircuitVerify``. The proof, the public inputs and the verification key
all come back as arrays of field elements directly -- the same data
``deflattenFields`` and ``acirVkAsFieldsUltraHonk`` produce in JavaScript.

This module is the Python counterpart of that client. It talks the identical
wire protocol to ``bb msgpack run`` so the recursion flow works on *any* bb
build that ships the msgpack API -- including the 3.0.x/4.0.x nightlies whose
trimmed-down CLI cannot emit vk-as-fields via ``--output_format`` (see
:func:`noir_bb.doctor`). Where :class:`noir_bb.Barretenberg` shells the ``bb``
CLI, :class:`MsgpackBackend` speaks the API, exactly like bb.js.

Wire protocol (reverse-engineered from ``bb msgpack schema``):

* a request is ``<uint32 little-endian length><msgpack payload>``;
* the payload is a 1-element array (the "tuple of arguments") whose single
  element is the command, encoded as the 2-element array
  ``[command_name, body]`` (bb's ``named_union`` convention);
* the response is framed the same way and decodes to
  ``[response_name, body]``; ``"ErrorResponse"`` carries ``{"message": ...}``;
* circuit bytecode and witnesses are passed **decompressed** (nargo writes them
  gzip-compressed; bb's msgpack API wants the raw bytes);
* field elements (proof, public inputs, vk fields, hashes) cross the wire as
  32-byte big-endian ``bin`` objects.
"""

from __future__ import annotations

import base64
import gzip
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

from . import _msgpack
from .artifacts import Proof, VerificationKey
from .bb import _LEGACY_ORACLE, VERIFIER_TARGETS
from .errors import ArtifactError, CommandError
from .nargo import ExecutionResult, NoirProject
from .runner import PathLike, find_tool, run_binary

CircuitLike = Union[PathLike, NoirProject, "ExecutionResult"]
WitnessLike = Union[PathLike, "ExecutionResult", bytes]


# ---------------------------------------------------------------------------
# verifier_target -> bb ProofSystemSettings
# ---------------------------------------------------------------------------

def settings_for(verifier_target: str, *, optimized_solidity_verifier: bool = False) -> dict:
    """Translate a noir-bb ``verifier_target`` into a bb ``ProofSystemSettings``.

    This is the msgpack equivalent of the CLI ``--verifier_target`` flag and the
    legacy ``--oracle_hash``/``--zk``/``--ipa_accumulation`` mapping: the target
    fully determines the oracle hash, whether ZK is on, and IPA accumulation.
    """
    if verifier_target not in VERIFIER_TARGETS:
        raise ValueError(
            f"unknown verifier_target {verifier_target!r}; expected one of {VERIFIER_TARGETS}"
        )
    return {
        "ipa_accumulation": "rollup" in verifier_target,
        "oracle_hash_type": _LEGACY_ORACLE[verifier_target],
        "disable_zk": verifier_target.endswith("-no-zk"),
        "optimized_solidity_verifier": optimized_solidity_verifier,
    }


def _field_hex(value) -> str:
    """A 32-byte ``bin`` field from bb -> ``0x`` hex string (bb.js ``.toString()``)."""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, int):
        return "0x" + value.to_bytes(32, "big").hex()
    return str(value)


def _load_bytecode(circuit: CircuitLike) -> tuple[str, bytes]:
    """Return ``(name, raw_acir_bytecode)`` from a compiled circuit.

    Accepts a path to the ``nargo compile`` JSON, a :class:`NoirProject`
    (uses its ``circuit_json``), or an :class:`ExecutionResult`. The ``bytecode``
    field is base64 of gzip'd ACIR; bb's msgpack API wants the decompressed bytes.
    """
    if isinstance(circuit, NoirProject):
        path = circuit.circuit_json
    elif isinstance(circuit, ExecutionResult):
        path = circuit.circuit_path
    else:
        path = Path(circuit)
    data = json.loads(Path(path).read_text())
    raw = base64.b64decode(data["bytecode"])
    if raw[:2] == b"\x1f\x8b":  # gzip magic
        raw = gzip.decompress(raw)
    name = data.get("name") or Path(path).stem
    return name, raw


def _load_witness(witness: WitnessLike) -> bytes:
    """Return decompressed witness bytes (nargo writes them gzip'd)."""
    if isinstance(witness, ExecutionResult):
        witness = witness.witness_path
    if isinstance(witness, (bytes, bytearray)):
        raw = bytes(witness)
    else:
        raw = Path(witness).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


# ---------------------------------------------------------------------------
# Low-level transport: one round-trip against `bb msgpack run`
# ---------------------------------------------------------------------------

class MsgpackClient:
    """Frames a single command to ``bb msgpack run`` and decodes the reply.

    This is the transport bb.js implements over WASM; here it is a subprocess
    round-trip. Each call is one ``bb msgpack run`` invocation (bb reads the
    framed command from stdin and writes the framed response to stdout).
    """

    def __init__(self, bb_path: Optional[PathLike] = None, *, timeout: Optional[float] = None):
        self.bin = find_tool("bb", bb_path)
        self.timeout = timeout

    def call(self, command: str, body: dict) -> tuple[str, object]:
        payload = _msgpack.packb([[command, body]])
        framed = struct.pack("<I", len(payload)) + payload
        proc = run_binary(
            [self.bin, "msgpack", "run"],
            input_bytes=framed,
            timeout=self.timeout,
        )
        out = proc.stdout_bytes
        if len(out) < 4:
            raise CommandError(
                f"bb msgpack run returned no response for {command}",
                cmd=proc.cmd, returncode=proc.returncode,
                stdout=out.decode("utf-8", "replace"), stderr=proc.stderr,
            )
        n = struct.unpack_from("<I", out, 0)[0]
        name, resp = _msgpack.unpackb(out[4:4 + n])
        if name == "ErrorResponse":
            msg = resp.get("message") if isinstance(resp, dict) else resp
            raise CommandError(
                f"bb msgpack {command} failed: {msg}",
                cmd=proc.cmd, returncode=proc.returncode,
                stdout="", stderr=proc.stderr,
            )
        return name, resp


# ---------------------------------------------------------------------------
# High-level backend: the bb.js UltraHonkBackend analogue
# ---------------------------------------------------------------------------

class MsgpackBackend:
    """Prove/verify a circuit through bb's msgpack API -- the bb.js way.

    Mirrors ``new UltraHonkBackend(bytecode, { threads }, { recursive })``:

        backend = MsgpackBackend(project.circuit_json, recursive=True)
        proof   = backend.generate_proof(witness)        # CircuitProve
        vk      = backend.get_verification_key()         # CircuitComputeVk
        ok      = backend.verify_proof(proof)            # CircuitVerify

    ``recursive=True`` selects the recursion-friendly default target
    (``noir-recursive``: poseidon2 oracle, ZK) so the resulting proof can be
    verified inside another Noir circuit by ``bb_proof_verification``'s
    ``verify_honk_proof``. ``recursive=False`` defaults to ``evm`` (keccak, ZK),
    the on-chain/final-proof target. Pass ``verifier_target=`` to either method
    to override per call -- the same knob as bb.js's ``{ verifierTarget }``.
    """

    def __init__(
        self,
        circuit: CircuitLike,
        *,
        recursive: bool = False,
        threads: int = 8,
        bb_path: Optional[PathLike] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.name, self.bytecode = _load_bytecode(circuit)
        self.recursive = recursive
        self.threads = threads  # accepted for bb.js parity; bb sizes its own pool
        self.client = MsgpackClient(bb_path, timeout=timeout)
        self._vk_cache: dict[str, VerificationKey] = {}

    # -- target resolution ---------------------------------------------------
    def _target(self, verifier_target: Optional[str]) -> str:
        if verifier_target is not None:
            return verifier_target
        return "noir-recursive" if self.recursive else "evm"

    # -- CircuitComputeVk  (bb.js getVerificationKey / acirVkAsFields) --------
    def get_verification_key(self, *, verifier_target: Optional[str] = None) -> VerificationKey:
        target = self._target(verifier_target)
        if target in self._vk_cache:
            return self._vk_cache[target]
        _, body = self.client.call("CircuitComputeVk", {
            "circuit": {"name": self.name, "bytecode": self.bytecode},
            "settings": settings_for(target),
        })
        vk = self._vk_from_response(body, target)
        self._vk_cache[target] = vk
        return vk

    # bb.js alias
    compute_vk = get_verification_key

    def _vk_from_response(self, body: dict, target: str) -> VerificationKey:
        vk = VerificationKey(
            directory=Path("."),
            fields=[_field_hex(f) for f in body.get("fields", [])] or None,
            key_hash=_field_hex(body["hash"]) if body.get("hash") else None,
        )
        vk._bytes = bytes(body["bytes"])  # raw vk, fed back into CircuitProve/Verify
        vk.verifier_target = target
        return vk

    # -- CircuitProve  (bb.js generateProof) ---------------------------------
    def generate_proof(
        self,
        witness: WitnessLike,
        *,
        verifier_target: Optional[str] = None,
        out_dir: Optional[PathLike] = None,
    ) -> Proof:
        target = self._target(verifier_target)
        vk = self.get_verification_key(verifier_target=target)
        _, body = self.client.call("CircuitProve", {
            "circuit": {
                "name": self.name,
                "bytecode": self.bytecode,
                "verification_key": vk._bytes,
            },
            "witness": _load_witness(witness),
            "settings": settings_for(target),
        })
        proof = Proof(
            directory=Path(out_dir) if out_dir else Path("."),
            fields=[_field_hex(f) for f in body.get("proof", [])],
            public_inputs=[_field_hex(f) for f in body.get("public_inputs", [])],
            vk_hash=vk.key_hash,
            verifier_target=target,
            vk=vk,
        )
        if out_dir is not None:
            self._write_artifacts(proof, vk, Path(out_dir))
        return proof

    # -- CircuitVerify  (bb.js verifyProof) ----------------------------------
    def verify_proof(
        self,
        proof: Proof,
        vk: Optional[VerificationKey] = None,
        *,
        verifier_target: Optional[str] = None,
    ) -> bool:
        vk = vk or proof.vk
        if vk is None or getattr(vk, "_bytes", None) is None:
            raise ArtifactError(
                "verify_proof needs the binary verification key; pass vk= or use "
                "a proof produced by this backend's generate_proof()"
            )
        target = verifier_target or proof.verifier_target or self._target(None)
        _, body = self.client.call("CircuitVerify", {
            "verification_key": vk._bytes,
            "public_inputs": [_field_to_bin(f) for f in proof.public_inputs],
            "proof": [_field_to_bin(f) for f in proof.fields],
            "settings": settings_for(target),
        })
        return bool(body.get("verified", False))

    # -- optional on-disk artifacts (for CLI interop / inspection) -----------
    @staticmethod
    def _write_artifacts(proof: Proof, vk: VerificationKey, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "proof_fields.json").write_text(json.dumps(proof.fields))
        (out / "public_inputs_fields.json").write_text(json.dumps(proof.public_inputs))
        if getattr(vk, "_bytes", None) is not None:
            (out / "vk").write_bytes(vk._bytes)
            vk.bytes_path = out / "vk"
        if vk.fields:
            (out / "vk_fields.json").write_text(json.dumps(vk.fields))


def _field_to_bin(field: Union[str, int]) -> bytes:
    if isinstance(field, int):
        return field.to_bytes(32, "big")
    s = field[2:] if field.lower().startswith("0x") else field
    return int(s, 16).to_bytes(32, "big")


# ---------------------------------------------------------------------------
# bb.js free-function parity
# ---------------------------------------------------------------------------

def deflatten_fields(proof: Union[Proof, Sequence[str]]) -> List[str]:
    """The fields of a proof -- bb.js ``deflattenFields(proofData.proof)``.

    bb's msgpack API already returns the proof as field elements, so this just
    surfaces them (and accepts a raw list for symmetry).
    """
    if isinstance(proof, Proof):
        return list(proof.fields)
    return list(proof)


def acir_vk_as_fields_ultra_honk(vk: Union[VerificationKey, Sequence[str]]) -> List[str]:
    """The vk as field elements -- bb.js ``acirVkAsFieldsUltraHonk(vk)``."""
    if isinstance(vk, VerificationKey):
        return vk.require_fields()
    return list(vk)


#: bb.js parity alias -- reads like the TypeScript ``UltraHonkBackend``.
UltraHonkBackendApi = MsgpackBackend
