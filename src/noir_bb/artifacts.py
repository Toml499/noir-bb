"""Loading and representing proof / verification-key artifacts produced by bb.

Handles the three on-disk layouts bb has used:

* modern JSON (``--output_format json``, bb >= 3.x):
    proof.json          {"proof": [hex...], "vk_hash": "0x..", "bb_version", "scheme"}
    public_inputs.json  {"public_inputs": [hex...], ...}
    vk.json             {"vk": [hex...], "hash": "0x..", ...}
* legacy fields (``--output_format bytes_and_fields``, bb 0.8x-2.x):
    proof, public_inputs, proof_fields.json, public_inputs_fields.json,
    vk, vk_fields.json, vk_hash
* binary only (default):
    proof, public_inputs, vk, vk_hash
    (fields are recovered by 32-byte chunking, exactly like bb.js does)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .abi import proof_bytes_to_fields, fields_to_bytes
from .errors import ArtifactError


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"could not parse {path}: {exc}") from exc


def _hex(data: bytes) -> str:
    return "0x" + data.hex()


@dataclass
class VerificationKey:
    """A verification key plus its field representation and hash (when available)."""

    directory: Path
    fields: Optional[List[str]] = None
    key_hash: Optional[str] = None
    scheme: Optional[str] = None
    bytes_path: Optional[Path] = None
    json_path: Optional[Path] = None
    verifier_target: Optional[str] = None
    #: Raw vk bytes from the msgpack API, fed back into CircuitProve/CircuitVerify.
    _bytes: Optional[bytes] = field(default=None, repr=False)

    @classmethod
    def load(cls, directory: Path | str) -> "VerificationKey":
        d = Path(directory)
        vk_json = d / "vk.json"
        vk_fields = d / "vk_fields.json"
        vk_bin = d / "vk"
        vk_hash = d / "vk_hash"

        if vk_json.is_file():
            data = _read_json(vk_json)
            return cls(
                directory=d,
                fields=list(data.get("vk") or []) or None,
                key_hash=data.get("hash"),
                scheme=data.get("scheme"),
                bytes_path=vk_bin if vk_bin.is_file() else None,
                json_path=vk_json,
            )
        if vk_fields.is_file():
            raw = _read_json(vk_fields)
            fields_list = list(raw) if isinstance(raw, list) else list(raw.get("fields", []))
            khash = _hex(vk_hash.read_bytes()) if vk_hash.is_file() else None
            return cls(d, fields_list or None, khash,
                       bytes_path=vk_bin if vk_bin.is_file() else None, json_path=vk_fields)
        if vk_bin.is_file():
            khash = _hex(vk_hash.read_bytes()) if vk_hash.is_file() else None
            return cls(d, None, khash, bytes_path=vk_bin)
        raise ArtifactError(
            f"no verification key found in {d} "
            f"(looked for vk.json, vk_fields.json, vk)"
        )

    @property
    def n_fields(self) -> Optional[int]:
        return len(self.fields) if self.fields is not None else None

    @property
    def path(self) -> Path:
        """Best path to hand to `bb verify -k` (binary preferred, else JSON)."""
        p = self.bytes_path or self.json_path
        if p is None:
            raise ArtifactError(f"verification key in {self.directory} has no file on disk")
        return p

    def require_fields(self) -> List[str]:
        if not self.fields:
            raise ArtifactError(
                "verification key has no field representation; re-run write_vk "
                "with output_format='json' (modern bb) or 'bytes_and_fields' "
                "(legacy bb). If your bb exposes no --output_format flag at all "
                "(e.g. 3.0.x/4.0.x nightlies), it cannot produce vk fields from "
                "the CLI and recursion inputs are unavailable on that version — "
                "run noir_bb.doctor() and switch to a stable bb via bbup."
            )
        return self.fields


@dataclass
class Proof:
    """A proof, its public inputs, and field representations."""

    directory: Path
    fields: List[str] = field(default_factory=list)
    public_inputs: List[str] = field(default_factory=list)
    vk_hash: Optional[str] = None
    scheme: Optional[str] = None
    verifier_target: Optional[str] = None
    proof_path: Optional[Path] = None
    public_inputs_path: Optional[Path] = None
    vk: Optional[VerificationKey] = None  # the vk passed to / written by prove()

    # -- loading -----------------------------------------------------------
    @classmethod
    def load(cls, directory: Path | str, *, verifier_target: Optional[str] = None) -> "Proof":
        d = Path(directory)
        proof_json = d / "proof.json"
        pi_json = d / "public_inputs.json"
        proof_fields = d / "proof_fields.json"
        pi_fields = d / "public_inputs_fields.json"
        proof_bin = d / "proof"
        pi_bin = d / "public_inputs"

        if proof_json.is_file():
            pdata = _read_json(proof_json)
            pubs: List[str] = []
            if pi_json.is_file():
                pubs = list(_read_json(pi_json).get("public_inputs") or [])
            return cls(d, list(pdata.get("proof") or []), pubs,
                       vk_hash=pdata.get("vk_hash"), scheme=pdata.get("scheme"),
                       verifier_target=verifier_target,
                       proof_path=proof_json, public_inputs_path=pi_json if pi_json.is_file() else None)

        if proof_fields.is_file():
            raw = _read_json(proof_fields)
            flds = list(raw) if isinstance(raw, list) else list(raw.get("fields", []))
            pubs = []
            if pi_fields.is_file():
                praw = _read_json(pi_fields)
                pubs = list(praw) if isinstance(praw, list) else list(praw.get("fields", []))
            elif pi_bin.is_file():
                pubs = proof_bytes_to_fields(pi_bin.read_bytes())
            return cls(d, flds, pubs, verifier_target=verifier_target,
                       proof_path=proof_bin if proof_bin.is_file() else proof_fields,
                       public_inputs_path=pi_bin if pi_bin.is_file() else (pi_fields if pi_fields.is_file() else None))

        if proof_bin.is_file():
            flds = proof_bytes_to_fields(proof_bin.read_bytes())
            pubs = proof_bytes_to_fields(pi_bin.read_bytes()) if pi_bin.is_file() else []
            return cls(d, flds, pubs, verifier_target=verifier_target,
                       proof_path=proof_bin,
                       public_inputs_path=pi_bin if pi_bin.is_file() else None)

        raise ArtifactError(
            f"no proof found in {d} (looked for proof.json, proof_fields.json, proof)"
        )

    # -- conveniences --------------------------------------------------------
    @property
    def n_fields(self) -> int:
        return len(self.fields)

    @property
    def proof_bytes(self) -> bytes:
        """Raw proof bytes (read from disk if binary exists, else rebuilt from fields)."""
        if self.proof_path and self.proof_path.name == "proof" and self.proof_path.is_file():
            return self.proof_path.read_bytes()
        return fields_to_bytes(self.fields)

    @property
    def public_inputs_bytes(self) -> bytes:
        if (self.public_inputs_path and self.public_inputs_path.name == "public_inputs"
                and self.public_inputs_path.is_file()):
            return self.public_inputs_path.read_bytes()
        return fields_to_bytes(self.public_inputs)

    def summary(self) -> str:
        return (
            f"Proof(dir={self.directory}, fields={self.n_fields}, "
            f"public_inputs={len(self.public_inputs)}, vk_hash={self.vk_hash}, "
            f"target={self.verifier_target})"
        )
