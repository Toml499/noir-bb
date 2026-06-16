# noir-bb

Run, prove and verify [Noir](https://noir-lang.org) circuits from Python by driving the `nargo` and Barretenberg (`bb`) CLIs — including recursive proofs — without Node.

There is no official Python counterpart to `@noir-lang/noir_js` + `@aztec/bb.js`; the proving stack ships as TypeScript/WASM packages and native CLIs only. noir-bb is the missing glue: a thin, **dependency-free** wrapper that turns Python dicts into `Prover.toml`, shells out to the real tools, and parses the artifacts back into Python objects with the same ergonomics as the JS API.

## Installation

```bash
pip install .                  # Python >= 3.11, no dependencies
```

The proving tools are installed separately, exactly as for the JS workflow:

```bash
curl -L https://raw.githubusercontent.com/noir-lang/noirup/main/install | bash && noirup
curl -L https://raw.githubusercontent.com/AztecProtocol/aztec-packages/refs/heads/next/barretenberg/bbup/install | bash && bbup
```

noir-bb targets Noir >= 1.0.0-beta.18 with bb >= 3.x. `bb` downloads its CRS to `~/.bb-crs` on first use. Check what noir-bb sees with `python -c "import noir_bb; noir_bb.doctor()"` — it prints both tool versions, whether your `bb` speaks the modern `--verifier_target` dialect, and which fields-output format it will use.

## Quick start

```python
from noir_bb import NoirProject, Barretenberg

project = NoirProject("examples/hello_world/hello_world")
w = project.execute({"x": 3, "y": 4})        # dict -> Prover.toml -> witness.gz

bb = Barretenberg()
proof = bb.prove(w.circuit_path, w.witness_path, "out/")   # vk written alongside
assert bb.verify(proof, proof.vk)            # -> True
```

`execute(inputs)` serialises a Python dict (ints, bools, hex strings, bytes, lists, nested dicts/structs) into `Prover_noirbb.toml`, runs `nargo execute`, and returns the witness path and the circuit's return value. Values are written verbatim and interpreted by nargo against the circuit's ABI — so `{"x": -5}` works for a signed `iN` input and is reduced mod the field for a `Field`, and a type that doesn't fit surfaces as nargo's own error. **Your own `Prover.toml` is never touched.**

`prove(...)` returns a `Proof` whose `.fields`, `.public_inputs`, `.vk_hash` and `.vk.fields` are already parsed. On bb >= 4.x (which requires `prove` to be given a key) it adds `--write_vk` automatically so the key lands alongside the proof. For the faster path, precompute the key once and reuse it:

```python
vk = bb.write_vk(w.circuit_path, "vk/")
proof = bb.prove(w.circuit_path, w.witness_path, "out/", vk=vk)   # -> bb prove -k
```

## Recursion

For recursion, prove the inner circuit with a `noir-recursive*` target, turn the
proof into inputs with `recursive_inputs(...)`, and feed those to an outer circuit
that calls `verify_honk_proof` / `verify_honk_proof_non_zk` from Aztec's
`bb_proof_verification` Noir library.

This needs a `bb` that can emit the verification key as field elements. The
stripped-down CLI on the 3.0.x/4.0.x **nightlies drops `--output_format`**, so it
cannot — but the underlying msgpack API still can. `MsgpackBackend` drives that API
(`bb msgpack run`) directly, exactly as `@aztec/bb.js` does over WASM, so recursion
works on those builds too:

```python
from noir_bb import NoirProject, MsgpackBackend, recursive_inputs

inner = NoirProject("circuits/main")
outer = NoirProject("circuits/recursive")

inner_backend = MsgpackBackend(inner.circuit_json, recursive=True)   # noir-recursive
outer_backend = MsgpackBackend(outer.circuit_json, recursive=False)  # evm

w = inner.execute({"x": 1, "y": 2})
proof = inner_backend.generate_proof(w.witness_path)   # proof + vk come back as fields
assert inner_backend.verify_proof(proof)

w2 = outer.execute(recursive_inputs(proof, circuit=outer.circuit_json))
rec = outer_backend.generate_proof(w2.witness_path)
assert outer_backend.verify_proof(rec)
```

`recursive_inputs(proof)` builds the `{"verification_key", "proof", "public_inputs",
"key_hash"}` dict the outer circuit expects. Pass `include_key_hash=False` for
tutorial-style circuits that hardcode the hash (three ABI inputs), and
`circuit=...` to size-check the artifacts against the outer circuit's actual ABI.

> **Version coupling is the sharpest edge.** The outer circuit's
> `bb_proof_verification` dependency hard-codes artifact sizes (449 proof fields for
> `noir-recursive-no-zk`, 500 for ZK, 115 vk fields in the v4.x line) and these move
> between bb major releases. Pin the dependency tag in the outer `Nargo.toml` to your
> installed `bb`. noir-bb checks sizes before they reach the circuit and raises a
> readable `VersionError` instead of a cryptic ABI failure;
> `noir_verifier_snippet(proof, vk)` generates an outer `main.nr` sized to your
> actual artifacts.

### `bb.js` parity

`MsgpackBackend` mirrors `UltraHonkBackend` from `@aztec/bb.js` method for method:

| bb.js | noir-bb |
| --- | --- |
| `new UltraHonkBackend(bytecode, {threads}, {recursive})` | `MsgpackBackend(circuit, threads=…, recursive=…)` |
| `backend.generateProof(witness)` | `backend.generate_proof(witness)` |
| `backend.getVerificationKey()` | `backend.get_verification_key()` |
| `backend.verifyProof(proofData)` | `backend.verify_proof(proof)` |
| `deflattenFields(proofData.proof)` | `deflatten_fields(proof)` |
| `acirVkAsFieldsUltraHonk(vk)` | `acir_vk_as_fields_ultra_honk(vk)` |

The `recursive` flag picks the default target (`noir-recursive`: poseidon2, ZK) for
the inner backend and `evm` for the outer; pass `verifier_target=…` to override per
call. The MessagePack codec (`noir_bb._msgpack`) is a tiny dependency-free
implementation checked byte-for-byte against the reference `msgpack` package.

## Examples

Two runnable ports live in [`examples/`](examples):

```bash
python examples/hello_world/run_hello_world.py                          # prove + verify
python examples/recursive_aggregation/run_recursive_aggregation.py      # full recursion via MsgpackBackend
```

`recursive_aggregation` ports Barretenberg's
[recursive-aggregation tutorial](https://barretenberg.aztec.network/docs/how_to_guides/recursive_aggregation/);
its outer `Nargo.toml` pins `bb_proof_verification` to the `v4.0.0-nightly.20260120`
tag matching that bb (508-field ZK proof, 115-field vk).

## API reference

**`NoirProject(path, *, package=None)`** — `compile()`, `execute(inputs=None, …) -> ExecutionResult`, `check()`, `test(pattern=None)`; properties `name`, `circuit_json`, `target_dir`.

**`Barretenberg(path=None, *, crs_path=None, scheme="ultra_honk", timeout=None, verbose=False)`** (aliased `UltraHonkBackend`):
- `prove(circuit, witness, out_dir, *, vk=None, verifier_target="noir-recursive", write_vk=None, …) -> Proof`
- `write_vk(circuit, out_dir, *, verifier_target="noir-recursive", …) -> VerificationKey`
- `verify(proof, vk, *, verifier_target=None, strict=False) -> bool`
- `gates(circuit) -> dict`, `write_solidity_verifier(vk, out_path, *, verifier_target="evm")`

**`MsgpackBackend(circuit, *, recursive=False, threads=8, …)`** (aliased `UltraHonkBackendApi`):
- `generate_proof(witness, *, verifier_target=None, out_dir=None) -> Proof`
- `get_verification_key(*, verifier_target=None) -> VerificationKey` (alias `compute_vk`)
- `verify_proof(proof, vk=None, *, verifier_target=None) -> bool`

**`Proof`** — `.fields`, `.n_fields`, `.public_inputs`, `.vk_hash`, `.verifier_target`, `.vk`, `.summary()`.
**`VerificationKey`** — `.fields`, `.n_fields`, `.key_hash`, `.require_fields()`, `.path`.

**`noir_bb.recursion`** — `recursive_inputs(proof, vk=None, *, key_hash=None, include_key_hash=True, check=True, circuit=None) -> dict`, `check_against_circuit(proof, vk, circuit)`, `check_recursive_artifacts(proof, vk, verifier_target)`, `expected_proof_length(verifier_target)`, `noir_verifier_snippet(proof, vk, *, zk=None)`.

**Verifier targets** — `evm`, `noir-recursive`, `noir-rollup`, `starknet`, each with a `-no-zk` variant.

## Older bb releases

If `bb prove --help` does not advertise `--verifier_target` (bb 0.8x–2.x), noir-bb maps each target onto the legacy flags it finds (`--oracle_hash`, `--honk_recursion`, `--zk`, `--output_format bytes_and_fields`) and reads the `*_fields.json` artifacts those versions produce. Anything genuinely unsupported raises `VersionError` with an upgrade hint. This path is best-effort; for recursion, use the modern pairing.

## Error handling

Everything derives from `NoirBBError`: `ToolNotFoundError` (with noirup/bbup hints), `CommandError` (carries the exact command, exit code and output tail), `VersionError` (capability or artifact-size mismatch), `ArtifactError`, `InputError`. `verify()` returns `False` on a cleanly rejected proof and only raises for tool-level failures — pass `strict=True` to raise on rejection too.

## Testing

`pytest` runs a hermetic suite against high-fidelity fake `nargo`/`bb` executables (`tests/fakebin`) that reproduce the real tools' help texts, flag dialects, file layouts and exit codes for both modern and legacy bb. The fakes do no real cryptography; run the examples with the real toolchain installed for an end-to-end check.

## License

MIT
