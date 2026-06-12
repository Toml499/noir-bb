"""Shared fixtures: put the fake nargo/bb on PATH and scaffold Noir projects.

The fake binaries in tests/fakebin emulate the file side-effects, help text
and exit codes of the real tools so the library's plumbing (flag selection,
artifact parsing, recursion wiring) can be tested hermetically.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

FAKEBIN = Path(__file__).parent / "fakebin"
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

INNER_MAIN = "fn main(x: Field, y: pub Field) {\n    assert(x * 2 + y == 9);\n}\n"

OUTER_MAIN = """use bb_proof_verification::{UltraHonkProof, UltraHonkVerificationKey, verify_honk_proof_non_zk};

fn main(
    verification_key: UltraHonkVerificationKey,
    proof: UltraHonkProof,
    public_inputs: pub [Field; 1],
    key_hash: Field,
) {
    verify_honk_proof_non_zk(verification_key, proof, public_inputs, key_hash);
}
"""


@pytest.fixture(autouse=True)
def fake_tools_on_path(monkeypatch):
    for f in FAKEBIN.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{FAKEBIN}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("BB_FAKE_LEGACY", raising=False)


@pytest.fixture
def legacy_bb(monkeypatch):
    monkeypatch.setenv("BB_FAKE_LEGACY", "1")


def _scaffold(root: Path, name: str, main_nr: str) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "Nargo.toml").write_text(
        f'[package]\nname = "{name}"\ntype = "bin"\n\n[dependencies]\n'
    )
    (root / "src" / "main.nr").write_text(main_nr)
    return root


@pytest.fixture
def inner_project(tmp_path):
    return _scaffold(tmp_path / "inner", "inner", INNER_MAIN)


@pytest.fixture
def outer_project(tmp_path):
    return _scaffold(tmp_path / "outer", "outer", OUTER_MAIN)
