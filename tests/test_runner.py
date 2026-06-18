"""Tests for the streaming execution path in noir_bb.runner.run()."""
from __future__ import annotations

import sys

import pytest

from noir_bb.errors import CommandError
from noir_bb.runner import run


def test_stream_returns_empty_capture_and_streams_stdout(capfd):
    res = run([sys.executable, "-c", "print('hello-stream')"], stream=True)
    assert res.returncode == 0
    # Nothing is buffered in the parent CommandResult when streaming.
    assert res.stdout == ""
    assert res.stderr == ""
    # ...but the child's output reached the inherited stdout.
    out, _ = capfd.readouterr()
    assert "hello-stream" in out


def test_stream_failure_raises_command_error_with_stderr_tail():
    script = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"
    with pytest.raises(CommandError) as exc:
        run([sys.executable, "-c", script], stream=True)
    assert exc.value.returncode == 3
    assert "boom" in exc.value.stderr


def test_stream_stderr_tail_is_bounded_to_last_lines():
    # Emit more stderr lines than the tail cap; only the last ones are retained.
    from noir_bb.runner import _STREAM_STDERR_TAIL

    n = _STREAM_STDERR_TAIL + 50
    script = (
        "import sys\n"
        f"for i in range({n}):\n"
        "    sys.stderr.write(f'line{i}\\n')\n"
        "sys.exit(1)\n"
    )
    with pytest.raises(CommandError) as exc:
        run([sys.executable, "-c", script], stream=True)
    lines = exc.value.stderr.splitlines()
    assert len(lines) == _STREAM_STDERR_TAIL
    assert lines[-1] == f"line{n - 1}"          # last emitted line kept
    assert "line0" not in exc.value.stderr      # earliest lines dropped


def test_non_stream_still_captures_stdout():
    res = run([sys.executable, "-c", "print('captured')"])
    assert res.returncode == 0
    assert "captured" in res.stdout
