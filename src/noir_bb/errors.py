"""Exception hierarchy for noir-bb."""

from __future__ import annotations


class NoirBBError(Exception):
    """Base class for all noir-bb errors."""


class ToolNotFoundError(NoirBBError):
    """A required CLI tool (nargo / bb) could not be located."""


class CommandError(NoirBBError):
    """A subprocess exited with a non-zero status."""

    def __init__(self, message: str, *, cmd=None, returncode=None, stdout="", stderr=""):
        super().__init__(message)
        self.cmd = list(cmd) if cmd else []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        base = super().__str__()
        parts = [base]
        if self.cmd:
            parts.append(f"  command: {' '.join(map(str, self.cmd))}")
        if self.returncode is not None:
            parts.append(f"  exit code: {self.returncode}")
        tail = (self.stderr or self.stdout or "").strip()
        if tail:
            # Keep the last ~25 lines; bb/nargo put the useful bits at the end.
            lines = tail.splitlines()[-25:]
            parts.append("  output:\n    " + "\n    ".join(lines))
        return "\n".join(parts)


class VersionError(NoirBBError):
    """The installed tool does not support a required feature/flag."""


class ArtifactError(NoirBBError):
    """Proof/VK artifacts are missing or in an unexpected format."""


class InputError(NoirBBError):
    """Circuit inputs could not be encoded into Prover.toml."""
