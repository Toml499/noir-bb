"""Wrapper around ``nargo`` and a project-level abstraction for Noir packages."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .abi import dumps_prover_toml
from .errors import NoirBBError
from .runner import PathLike, find_tool, run

_OUTPUT_RE = re.compile(r"Circuit output:\s*(.+)")


class Nargo:
    """Thin wrapper over the nargo CLI."""

    def __init__(self, path: Optional[PathLike] = None, *,
                 timeout: Optional[float] = None, verbose: bool = False) -> None:
        self.bin = find_tool("nargo", path)
        self.timeout = timeout
        self.verbose = verbose

    def version(self) -> str:
        res = run([self.bin, "--version"], timeout=self.timeout)
        m = re.search(r"nargo version\s*=\s*([^\s(]+)", res.text)
        return m.group(1) if m else res.text.strip().splitlines()[0]

    def run(self, args: Sequence[str], *, cwd: PathLike, check: bool = True):
        return run([self.bin, *args], cwd=cwd, timeout=self.timeout,
                   check=check, verbose=self.verbose)

    def new(self, path: PathLike, name: Optional[str] = None) -> "NoirProject":
        target = Path(path)
        args = ["new", str(target)] + (["--name", name] if name else [])
        run([self.bin, *args], timeout=self.timeout, verbose=self.verbose)
        return NoirProject(target, nargo=self)


@dataclass
class ExecutionResult:
    witness_path: Path
    circuit_path: Path
    return_value: Optional[str]
    stdout: str


class NoirProject:
    """A Noir package on disk: compile it and execute it with Python inputs."""

    def __init__(
        self,
        path: PathLike,
        *,
        nargo: Optional[Nargo] = None,
        package: Optional[str] = None,
    ) -> None:
        self.path = Path(path).resolve()
        if not (self.path / "Nargo.toml").is_file():
            raise NoirBBError(f"no Nargo.toml found in {self.path}")
        self.nargo = nargo or Nargo()
        self.package = package  # for workspaces: --package <name>
        self._manifest = tomllib.loads((self.path / "Nargo.toml").read_text())

    # -- metadata --------------------------------------------------------------
    @property
    def name(self) -> str:
        if self.package:
            return self.package
        pkg = self._manifest.get("package", {})
        name = pkg.get("name")
        if not name:
            raise NoirBBError(
                f"{self.path/'Nargo.toml'} has no [package].name; "
                "for workspaces pass NoirProject(..., package='<member>')"
            )
        return name

    @property
    def target_dir(self) -> Path:
        return self.path / "target"

    @property
    def circuit_json(self) -> Path:
        return self.target_dir / f"{self.name}.json"

    def _pkg_args(self) -> list[str]:
        return ["--package", self.package] if self.package else []

    # -- nargo operations --------------------------------------------------------
    def check(self, *, overwrite: bool = False) -> None:
        """Type-check and scaffold Prover.toml (`nargo check`)."""
        args = ["check", *self._pkg_args()]
        if overwrite:
            args.append("--overwrite")
        self.nargo.run(args, cwd=self.path)

    def compile(self, *, extra_args: Sequence[str] = ()) -> Path:
        """Compile to ACIR (`nargo compile`); returns the circuit JSON path."""
        self.nargo.run(["compile", *self._pkg_args(), *extra_args], cwd=self.path)
        if not self.circuit_json.is_file():
            raise NoirBBError(
                f"compile succeeded but {self.circuit_json} was not produced"
            )
        return self.circuit_json

    def execute(
        self,
        inputs: Optional[Mapping[str, Any]] = None,
        *,
        witness_name: Optional[str] = None,
        prover_name: Optional[str] = None,
        extra_args: Sequence[str] = (),
    ) -> ExecutionResult:
        """Write inputs to a Prover toml, run `nargo execute`, return the witness.

        If ``inputs`` is None, the project's existing ``Prover.toml`` is used.
        Otherwise inputs are serialised to ``<prover_name>.toml``
        (default ``Prover_noirbb.toml`` so your own Prover.toml is untouched).
        """
        if inputs is not None:
            prover_name = prover_name or "Prover_noirbb"
            toml_text = dumps_prover_toml(inputs)
            (self.path / f"{prover_name}.toml").write_text(toml_text)
        witness_name = witness_name or self.name
        args = ["execute", witness_name, *self._pkg_args(), *extra_args]
        if prover_name:
            args += ["--prover-name", prover_name]
        res = self.nargo.run(args, cwd=self.path)
        witness_path = self.target_dir / f"{witness_name}.gz"
        if not witness_path.is_file():
            raise NoirBBError(
                f"nargo execute succeeded but witness {witness_path} was not produced"
            )
        m = _OUTPUT_RE.search(res.text)
        return ExecutionResult(
            witness_path=witness_path,
            circuit_path=self.circuit_json,
            return_value=m.group(1).strip() if m else None,
            stdout=res.text,
        )

    def test(self, pattern: Optional[str] = None) -> str:
        args = ["test", *self._pkg_args()] + ([pattern] if pattern else [])
        return self.nargo.run(args, cwd=self.path).text
