"""Subprocess plumbing shared by the nargo and bb wrappers."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
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
    stdout_bytes: bytes = b""

    @property
    def text(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def run_binary(
    cmd: Sequence[PathLike],
    *,
    input_bytes: bytes,
    timeout: Optional[float] = None,
    check: bool = True,
) -> CommandResult:
    """Run a command with binary stdin, capturing stdout as raw bytes.

    Used for the ``bb msgpack run`` transport, whose stdin and stdout are framed
    binary MessagePack rather than text. stderr is still decoded as text (bb logs
    human-readable diagnostics there).
    """
    argv = [str(c) for c in cmd]
    log.debug("running (binary): %s", " ".join(argv))
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, input=input_bytes, capture_output=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ToolNotFoundError(f"Executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(
            f"Command timed out after {timeout}s", cmd=argv, returncode=None,
            stdout="", stderr=(exc.stderr or b"").decode("utf-8", "replace"),
        ) from exc
    duration = time.monotonic() - start
    stderr = proc.stderr.decode("utf-8", "replace")
    result = CommandResult(argv, proc.returncode, "", stderr, duration, stdout_bytes=proc.stdout)
    log.debug("finished in %.2fs (rc=%d)", duration, proc.returncode)
    if check and proc.returncode != 0:
        raise CommandError(
            f"`{Path(argv[0]).name} {argv[1] if len(argv) > 1 else ''}` failed",
            cmd=argv, returncode=proc.returncode, stdout="", stderr=stderr,
        )
    return result


#: How many trailing stderr lines a streamed run keeps for error reporting.
_STREAM_STDERR_TAIL = 200


def run(
    cmd: Sequence[PathLike],
    *,
    cwd: Optional[PathLike] = None,
    timeout: Optional[float] = None,
    check: bool = True,
    env: Optional[Mapping[str, str]] = None,
    verbose: bool = False,
    stream: bool = False,
) -> CommandResult:
    """Run a command, capturing output. Raises CommandError on failure when check=True.

    With ``stream=True`` the child inherits the parent's stdout and its stderr is
    teed live to the parent's stderr, so long-running commands (notably ``bb prove``
    / ``write_vk``) show progress immediately and the parent never buffers the whole
    log stream in memory. Nothing is captured, so the returned
    :class:`CommandResult` has empty ``stdout``/``stderr``; on failure a bounded tail
    of stderr (last ``_STREAM_STDERR_TAIL`` lines) is attached to the raised
    :class:`CommandError`. ``verbose`` is ignored when streaming (output is already
    shown live).
    """
    argv = [str(c) for c in cmd]
    log.debug("running: %s (cwd=%s, stream=%s)", " ".join(argv), cwd, stream)
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    if stream:
        return _run_streaming(argv, cwd=cwd, timeout=timeout, check=check, env=full_env)
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


def _run_streaming(
    argv: list[str],
    *,
    cwd: Optional[PathLike],
    timeout: Optional[float],
    check: bool,
    env: Mapping[str, str],
) -> CommandResult:
    """Stream a child's output to the terminal, keeping only a bounded stderr tail.

    stdout is inherited (written straight to our stdout, never buffered here);
    stderr is read line-by-line on a reader thread, echoed live to ``sys.stderr``
    and retained as a short rolling tail for error reporting.
    """
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=dict(env),
            stdout=None,  # inherit: stream live, no parent-side buffering
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )
    except FileNotFoundError as exc:
        raise ToolNotFoundError(f"Executable not found: {argv[0]}") from exc

    tail: deque[str] = deque(maxlen=_STREAM_STDERR_TAIL)

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
            tail.append(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        reader.join()
        raise CommandError(
            f"Command timed out after {timeout}s", cmd=argv, returncode=None,
            stdout="", stderr="".join(tail),
        ) from exc
    reader.join()

    duration = time.monotonic() - start
    result = CommandResult(argv, proc.returncode, "", "", duration)
    log.debug("finished in %.2fs (rc=%d, streamed)", duration, proc.returncode)
    if check and proc.returncode != 0:
        raise CommandError(
            f"`{Path(argv[0]).name} {argv[1] if len(argv) > 1 else ''}` failed",
            cmd=argv, returncode=proc.returncode,
            stdout="", stderr="".join(tail),
        )
    return result
