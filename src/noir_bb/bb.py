"""Wrapper around the Barretenberg ``bb`` CLI.

Targets the modern CLI (bb >= 3.x, as used by current noir-examples):

    bb prove    -b circuit.json -w witness.gz -o out/ --verifier_target <t> [-k vk | --write_vk] [--output_format json]
    bb write_vk -b circuit.json -o out/ --verifier_target <t> [--output_format json]
    bb verify   -p proof -i public_inputs -k vk --verifier_target <t>

bb >= 4.x additionally requires `prove` to receive a verification key (``-k``)
or be told to compute one (``--write_vk``); :meth:`Barretenberg.prove` handles
this automatically when neither ``vk`` nor ``write_vk`` is given.

For older bb releases (0.8x - 2.x) the high-level ``verifier_target`` is mapped
onto the legacy flags (``--oracle_hash``, ``--honk_recursion``, ``--zk``,
``--output_format bytes_and_fields``) on a best-effort basis, gated on what the
installed binary's ``--help`` actually advertises.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence, Union

from .artifacts import Proof, VerificationKey
from .errors import ArtifactError, CommandError, VersionError
from .runner import PathLike, find_tool, run

#: Verifier targets understood by modern bb (and emulated for legacy bb).
VERIFIER_TARGETS = (
    "evm", "evm-no-zk",
    "noir-recursive", "noir-recursive-no-zk",
    "noir-rollup", "noir-rollup-no-zk",
    "starknet", "starknet-no-zk",
)

_LEGACY_ORACLE = {
    "evm": "keccak", "evm-no-zk": "keccak",
    "starknet": "starknet", "starknet-no-zk": "starknet",
    "noir-recursive": "poseidon2", "noir-recursive-no-zk": "poseidon2",
    "noir-rollup": "poseidon2", "noir-rollup-no-zk": "poseidon2",
}


@dataclass(frozen=True)
class Capabilities:
    """What the installed bb binary supports, probed from its help text."""

    verifier_target: bool
    output_json: bool
    output_bytes_and_fields: bool
    scheme: bool
    oracle_hash: bool
    honk_recursion: bool
    zk_flag: bool
    disable_zk: bool
    write_vk_flag: bool
    ipa_accumulation: bool

    @property
    def modern(self) -> bool:
        return self.verifier_target


class Barretenberg:
    """Pythonic interface to the ``bb`` proving backend."""

    def __init__(
        self,
        path: Optional[PathLike] = None,
        *,
        crs_path: Optional[PathLike] = None,
        scheme: str = "ultra_honk",
        timeout: Optional[float] = None,
        verbose: bool = False,
    ) -> None:
        self.bin = find_tool("bb", path)
        self.crs_path = Path(crs_path) if crs_path else None
        self.scheme = scheme
        self.timeout = timeout
        self.verbose = verbose

    # -- introspection -------------------------------------------------------
    @lru_cache(maxsize=None)
    def version(self) -> str:
        res = run([self.bin, "--version"], check=False, timeout=self.timeout)
        m = re.search(r"v?(\d[\w.\-]*)", res.text)
        return m.group(1) if m else res.text.strip()

    @lru_cache(maxsize=None)
    def _help(self, subcommand: str) -> str:
        res = run([self.bin, subcommand, "--help"], check=False, timeout=self.timeout)
        return res.text

    @property
    def capabilities(self) -> Capabilities:
        h = self._help("prove")
        return Capabilities(
            verifier_target="--verifier_target" in h,
            output_json="--output_format" in h and "'json'" in h,
            output_bytes_and_fields="bytes_and_fields" in h,
            scheme="--scheme" in h or "-s," in h,
            oracle_hash="--oracle_hash" in h,
            honk_recursion="--honk_recursion" in h,
            zk_flag="--zk" in h and "--disable_zk" not in h,
            disable_zk="--disable_zk" in h,
            write_vk_flag="--write_vk" in h,
            ipa_accumulation="--ipa_accumulation" in h,
        )

    # -- flag construction ---------------------------------------------------
    def _supports(self, subcommand: str, flag: str) -> bool:
        return flag in self._help(subcommand)

    def _target_args(self, subcommand: str, verifier_target: Optional[str]) -> list[str]:
        if verifier_target is None:
            return []
        if verifier_target not in VERIFIER_TARGETS:
            raise ValueError(
                f"unknown verifier_target {verifier_target!r}; expected one of {VERIFIER_TARGETS}"
            )
        if self._supports(subcommand, "--verifier_target"):
            return ["--verifier_target", verifier_target]
        # ---- legacy mapping ------------------------------------------------
        args: list[str] = []
        oracle = _LEGACY_ORACLE[verifier_target]
        if self._supports(subcommand, "--oracle_hash"):
            args += ["--oracle_hash", oracle]
        elif oracle != "poseidon2":
            raise VersionError(
                f"bb {self.version()} `{subcommand}` does not support --oracle_hash, "
                f"required for verifier_target={verifier_target!r}. Upgrade bb (bbup)."
            )
        recursive = verifier_target.startswith("noir-")
        if recursive and self._supports(subcommand, "--honk_recursion"):
            level = "2" if "rollup" in verifier_target else "1"
            args += ["--honk_recursion", level]
        if "rollup" in verifier_target and self._supports(subcommand, "--ipa_accumulation"):
            args += ["--ipa_accumulation"]
        wants_zk = not verifier_target.endswith("-no-zk")
        if wants_zk:
            if self._supports(subcommand, "--zk") and not self._supports(subcommand, "--disable_zk"):
                args += ["--zk"]
            elif not self._supports(subcommand, "--disable_zk"):
                raise VersionError(
                    f"bb {self.version()} `{subcommand}` exposes no ZK flag; cannot honour "
                    f"verifier_target={verifier_target!r}. Use the '-no-zk' variant or upgrade bb."
                )
        else:
            if self._supports(subcommand, "--disable_zk"):
                args += ["--disable_zk"]
        return args

    def _common(self, subcommand: str) -> list[str]:
        args: list[str] = []
        if self._supports(subcommand, "--scheme") and self.scheme:
            args += ["--scheme", self.scheme]
        if self.crs_path and self._supports(subcommand, "--crs_path"):
            args += ["--crs_path", str(self.crs_path)]
        return args

    def _format_args(self, subcommand: str, output_format: Optional[str]) -> list[str]:
        caps = self.capabilities
        if output_format is None:  # auto: prefer field-bearing formats
            if caps.output_json:
                output_format = "json"
            elif caps.output_bytes_and_fields:
                output_format = "bytes_and_fields"
            else:
                return []
        if output_format == "binary":
            return []
        if not self._supports(subcommand, "--output_format"):
            raise VersionError(
                f"bb {self.version()} `{subcommand}` has no --output_format flag"
            )
        return ["--output_format", output_format]

    # -- core operations -----------------------------------------------------
    def prove(
        self,
        circuit: PathLike,
        witness: PathLike,
        out_dir: Optional[PathLike] = None,
        *,
        vk: Union[VerificationKey, PathLike, None] = None,
        verifier_target: str = "noir-recursive",
        output_format: Optional[str] = None,
        write_vk: Optional[bool] = None,
        self_verify: bool = False,
        stream: Optional[bool] = None,
        extra_args: Sequence[str] = (),
    ) -> Proof:
        """Generate a proof; returns a :class:`Proof` with ``.vk`` populated.

        ``prove(..., write_vk=True)`` is the single-pass, lowest-peak-memory and
        fastest path: one ``bb`` process derives the proving key once and emits
        both the proof and the verification key. With ``write_vk=None`` (auto)
        and no ``vk``, ``--write_vk`` is added whenever the binary supports it,
        since bb >= 4.x refuses to prove without either a vk or ``--write_vk``,
        and the emitted key feeds ``verify()`` / recursion without a separate
        :meth:`write_vk` run.

        Passing ``vk`` (a precomputed verification key, via ``bb prove -k``) does
        **not** avoid proving-key derivation — ``-k`` supplies only the
        *verification* key, and ``prove`` still builds the full proving key.
        Prefer it only when you already have a vk on disk and explicitly want to
        skip re-deriving that **vk** artifact; splitting :meth:`write_vk` +
        ``prove(vk=...)`` otherwise builds the (potentially huge) proving key
        twice.

        ``out_dir`` defaults to the directory holding ``circuit`` — i.e.
        ``PROJECTNAME/target`` for a standard Noir project layout.

        ``stream`` controls output handling; it defaults to ``True`` here because
        proving is long-running. Streamed output goes live to the terminal and is
        never buffered in this process (see :func:`noir_bb.runner.run`).
        """
        out = Path(out_dir) if out_dir is not None else Path(circuit).parent
        out.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "prove", "-b", str(circuit), "-w", str(witness), "-o", str(out)]
        vk_path: Optional[Path] = None
        if vk is not None:
            vk_path = vk.path if isinstance(vk, VerificationKey) else Path(vk)
            if not self._supports("prove", "--vk_path"):
                raise VersionError(
                    f"bb {self.version()} `prove` does not accept a verification "
                    "key (-k/--vk_path); drop vk=... or upgrade bb (bbup)."
                )
            cmd += ["-k", str(vk_path)]
        if write_vk is None:
            write_vk = vk is None and self.capabilities.write_vk_flag
        elif write_vk and not self.capabilities.write_vk_flag:
            raise VersionError(
                f"bb {self.version()} `prove` has no --write_vk flag; "
                "call write_vk() separately"
            )
        cmd += self._common("prove")
        cmd += self._target_args("prove", verifier_target)
        cmd += self._format_args("prove", output_format)
        if write_vk:
            cmd += ["--write_vk"]
        if self_verify and self._supports("prove", "--verify"):
            cmd += ["--verify"]
        cmd += list(extra_args)
        run(cmd, timeout=self.timeout, verbose=self.verbose,
            stream=True if stream is None else stream)
        proof = Proof.load(out, verifier_target=verifier_target)
        if write_vk:
            proof.vk = VerificationKey.load(out)
        elif isinstance(vk, VerificationKey):
            proof.vk = vk
        elif vk_path is not None:
            proof.vk = VerificationKey(
                directory=vk_path.parent,
                bytes_path=vk_path if vk_path.suffix != ".json" else None,
                json_path=vk_path if vk_path.suffix == ".json" else None,
            )
        return proof

    def write_vk(
        self,
        circuit: PathLike,
        out_dir: PathLike,
        *,
        verifier_target: str = "noir-recursive",
        output_format: Optional[str] = None,
        stream: Optional[bool] = None,
        extra_args: Sequence[str] = (),
    ) -> VerificationKey:
        """Derive the verification key for ``circuit``.

        Like :meth:`prove`, ``stream`` defaults to ``True`` (this is a long,
        memory-heavy operation): output streams live to the terminal rather than
        being buffered here. The key is read back from disk, so nothing depends on
        captured stdout.
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "write_vk", "-b", str(circuit), "-o", str(out)]
        cmd += self._common("write_vk")
        cmd += self._target_args("write_vk", verifier_target)
        cmd += self._format_args("write_vk", output_format)
        cmd += list(extra_args)
        run(cmd, timeout=self.timeout, verbose=self.verbose,
            stream=True if stream is None else stream)
        return VerificationKey.load(out)

    def verify(
        self,
        proof: Union[Proof, PathLike],
        vk: Union[VerificationKey, PathLike],
        *,
        public_inputs: Optional[PathLike] = None,
        verifier_target: Optional[str] = None,
        strict: bool = False,
        extra_args: Sequence[str] = (),
    ) -> bool:
        """Verify a proof. Returns True/False; raises on tool errors (rc > 1)."""
        if isinstance(proof, Proof):
            if verifier_target is None:
                verifier_target = proof.verifier_target
            proof_path = proof.proof_path
            if public_inputs is None:
                public_inputs = proof.public_inputs_path
        else:
            proof_path = Path(proof)
        if proof_path is None:
            raise ArtifactError("proof has no on-disk path to verify")
        vk_path = vk.path if isinstance(vk, VerificationKey) else Path(vk)

        cmd = [self.bin, "verify", "-p", str(proof_path), "-k", str(vk_path)]
        if public_inputs is not None and self._supports("verify", "--public_inputs_path"):
            cmd += ["-i", str(public_inputs)]
        cmd += self._common("verify")
        cmd += self._target_args("verify", verifier_target)
        cmd += list(extra_args)
        res = run(cmd, check=False, timeout=self.timeout, verbose=self.verbose)
        if res.returncode == 0:
            return True
        if res.returncode == 1:
            if strict:
                raise CommandError("proof failed verification", cmd=res.cmd,
                                   returncode=1, stdout=res.stdout, stderr=res.stderr)
            return False
        raise CommandError("bb verify errored", cmd=res.cmd, returncode=res.returncode,
                           stdout=res.stdout, stderr=res.stderr)

    # -- extras ----------------------------------------------------------------
    def gates(self, circuit: PathLike, *, verifier_target: Optional[str] = None) -> dict:
        """Return bb's gate-count report as a dict."""
        cmd = [self.bin, "gates", "-b", str(circuit)]
        cmd += self._common("gates")
        if verifier_target:
            cmd += self._target_args("gates", verifier_target)
        res = run(cmd, timeout=self.timeout)
        text = res.stdout.strip() or res.stderr.strip()
        start = text.find("{")
        if start == -1:
            raise ArtifactError(f"could not find JSON in `bb gates` output:\n{text}")
        return json.loads(text[start:])

    def write_solidity_verifier(
        self, vk: Union[VerificationKey, PathLike], out_path: PathLike,
        *, verifier_target: str = "evm", extra_args: Sequence[str] = (),
    ) -> Path:
        vk_path = vk.path if isinstance(vk, VerificationKey) else Path(vk)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "write_solidity_verifier", "-k", str(vk_path), "-o", str(out)]
        cmd += self._common("write_solidity_verifier")
        cmd += self._target_args("write_solidity_verifier", verifier_target)
        cmd += list(extra_args)
        run(cmd, timeout=self.timeout, verbose=self.verbose)
        return out


#: bb.js parity alias — reads nicely next to the TypeScript examples.
UltraHonkBackend = Barretenberg
