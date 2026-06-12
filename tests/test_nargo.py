"""Tests for noir_bb.nargo against the fake nargo binary."""
from __future__ import annotations

import tomllib

import pytest

from noir_bb import Nargo, NoirProject
from noir_bb.errors import CommandError, NoirBBError


def test_nargo_version():
    assert Nargo().version() == "1.0.0-beta.19"


def test_project_requires_manifest(tmp_path):
    with pytest.raises(NoirBBError):
        NoirProject(tmp_path)


def test_compile_produces_circuit_json(inner_project):
    proj = NoirProject(inner_project)
    assert proj.name == "inner"
    circuit = proj.compile()
    assert circuit == inner_project / "target" / "inner.json"
    assert circuit.is_file()


def test_execute_writes_inputs_and_witness(inner_project):
    proj = NoirProject(inner_project)
    result = proj.execute({"x": 3, "y": 3})

    prover = inner_project / "Prover_noirbb.toml"   # user's Prover.toml untouched
    assert prover.is_file()
    assert tomllib.loads(prover.read_text()) == {"x": "3", "y": "3"}

    assert result.witness_path == inner_project / "target" / "inner.gz"
    assert result.witness_path.is_file()
    assert result.circuit_path.is_file()
    assert result.return_value == "Field(0x09)"


def test_execute_custom_witness_and_prover_names(inner_project):
    proj = NoirProject(inner_project)
    result = proj.execute({"x": 1, "y": 7}, witness_name="w1", prover_name="Custom")
    assert (inner_project / "Custom.toml").is_file()
    assert result.witness_path.name == "w1.gz"


def test_execute_without_inputs_needs_existing_prover_toml(inner_project):
    proj = NoirProject(inner_project)
    with pytest.raises(CommandError):
        proj.execute()  # fake nargo: "could not find prover file Prover.toml"
    (inner_project / "Prover.toml").write_text('x = "3"\ny = "3"\n')
    result = proj.execute()
    assert result.witness_path.is_file()


def test_new_scaffolds_project(tmp_path):
    proj = Nargo().new(tmp_path / "fresh", name="fresh")
    assert proj.name == "fresh"
    assert (tmp_path / "fresh" / "src" / "main.nr").is_file()
