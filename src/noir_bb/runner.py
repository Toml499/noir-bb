"""Subprocess plumbing shared by the nargo and bb wrappers."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from .errors import CommandError, ToolNotFoundError

log = logging.getLogger("noir_bb")

PathLike = Union[str, Path]

_INSTALL_HINTS = {
    "nargo": (
        "Install via noirup:\n"
        "  curl -L https://raw.githubusercontent.com/noir-lang/noirup/main/install | bash\n"
        "  noirup --version <version>"
    ),
    "bb": (
        "Install via bbup:\n"
        "  curl -L https://raw.githubusercontent.com/AztecProtocol/aztec-packages/refs/heads/next/barretenberg/bbup/install | bash\n"
        "  bbup"
    ),
}


def find_tool(name: str, explicit: Optional[PathLike] = None) -> str:
    """Resolve the path to a CLI tool, raising a helpful error if absent."""
    if explicit is not None:
        p = Path(explicit).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        raise ToolNotFoundError(f"{name!r} not found at explicit path: {p}")
    found = shutil.which(name)
    if found:
        return found
    hint = _INSTALL_HINTS.get(name, "")
    raise ToolNotFoundError(
        f"Could not find {name!r} on PATH. {hint}\n"
        f"Alternatively pass the binary location explicitly, e.g. "
        f"{'Nargo' if name == 'nargo' else 'Barretenberg'}(path='/path/to/{name}')."
    )


@dataclass
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def run(
    cmd: Sequence[PathLike],
    *,
    cwd: Optional[PathLike] = None,
    timeout: Optional[float] = None,
    check: bool = True,
    env: Optional[Mapping[str, str]] = None,
    verbose: bool = False,
) -> CommandResult:
    """Run a command, capturing output. Raises CommandError on failure when check=True."""
    argv = [str(c) for c in cmd]
    log.debug("running: %s (cwd=%s)", " ".join(argv), cwd)
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ToolNotFoundError(f"Executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(
            f"Command timed out after {timeout}s", cmd=argv, returncode=None,
            stdout=exc.stdout or "", stderr=exc.stderr or "",
        ) from exc
    duration = time.monotonic() - start
    result = CommandResult(argv, proc.returncode, proc.stdout, proc.stderr, duration)
    if verbose and result.text:
        print(result.text)
    log.debug("finished in %.2fs (rc=%d)", duration, proc.returncode)
    if check and proc.returncode != 0:
        raise CommandError(
            f"`{Path(argv[0]).name} {argv[1] if len(argv) > 1 else ''}` failed",
            cmd=argv, returncode=proc.returncode,
            stdout=proc.stdout, stderr=proc.stderr,
        )
    return result
