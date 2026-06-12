# noir-bb

Run, prove and verify [Noir](https://noir-lang.org) circuits from Python by driving the `nargo` and Barretenberg (`bb`) CLIs — including the recursive-proof flow from [noir-examples/recursion](https://github.com/noir-lang/noir-examples/tree/master/recursion), but without Node.

There is no official Python counterpart to `@noir-lang/noir_js` + `@aztec/bb.js`; the proving stack ships as TypeScript/WASM packages and native CLIs only. This library is the missing glue: a thin, dependency-free wrapper that turns Python dicts into `Prover.toml`, shells out to the real tools, and parses the artifacts back into Python objects with the same ergonomics as the JS API.

```python
from noir_bb import NoirProject, Barretenberg, recursive_inputs

inner = NoirProject("circuits/inner")
witness = inner.execute({"x": 3, "y": 3})          # dict -> Prover.toml -> witness.gz

bb = Barretenberg()
proof = bb.prove(witness.circuit_path, witness.witness_path, "proofs/inner",
                 verifier_target="noir-recursive-no-zk")
assert bb.verify(proof, proof.vk)                  # vk written alongside the proof

outer = NoirProject("circuits/recursive")          # calls verify_honk_proof_non_zk(...)
w2 = outer.execute(recursive_inputs(proof))        # proof becomes circuit inputs
proof2 = bb.prove(w2.circuit_path, w2.witness_path, "proofs/recursive",
                  verifier_target="evm")
assert bb.verify(proof2, proof2.vk)
```

## Installation

```bash
pip install .                  # from this directory; Python >= 3.11, no dependencies
```

The actual proving tools are installed separately, exactly as for the JS workflow:

```bash
curl -L https://raw.githubusercontent.com/noir-lang/noirup/main/install | bash && noirup
curl -L https://raw.githubusercontent.com/AztecProtocol/aztec-packages/refs/heads/next/barretenberg/bbup/install | bash && bbup
```

This library targets Noir >= 1.0.0-beta.18 with bb >= 3.x (the pairing used by current noir-examples). `bb` downloads its CRS to `~/.bb-crs` automatically on first use. Check what noir-bb sees with:

```python
import noir_bb; noir_bb.doctor()
```

which prints both versions, whether your `bb` speaks the modern `--verifier_target` dialect, and which fields output format it will use.

## How it maps to the JS API

`NoirProject` plays the role of `Noir` from noir_js: `compile()` runs `nargo compile`, and `execute(inputs)` serialises a Python dict (ints, bools, hex strings, bytes, lists, nested dicts/structs — negative ints are reduced mod the BN254 scalar field) into a `Prover_noirbb.toml`, runs `nargo execute`, and returns the witness path plus the circuit's return value. Your own `Prover.toml` is never touched.

`Barretenberg` (aliased `UltraHonkBackend` for familiarity) wraps `bb prove`, `bb write_vk`, `bb verify`, `bb gates` and `bb write_solidity_verifier`. `prove(...)` returns a `Proof` whose `.fields`, `.public_inputs`, `.vk_hash` and `.vk.fields` are already parsed — the equivalents of bb.js's `proofToFields`/`vkAsFields`, except bb computes them natively via `--output_format json` so no manual byte chunking is needed. When only binary artifacts exist, noir-bb falls back to the same 32-byte chunking bb.js uses.

bb >= 4.x requires `prove` to be given a verification key. `prove()` handles this transparently: with no arguments it adds `--write_vk` so bb emits the key alongside the proof (and `.vk` is populated), and for the faster path you can precompute the key once and reuse it — `vk = bb.write_vk(circuit, "vk/"); bb.prove(circuit, witness, "proof/", vk=vk)` — which becomes `bb prove -k`.

`recursive_inputs(proof)` then produces exactly the dict the outer circuit from noir-examples expects: `{"verification_key", "proof", "public_inputs", "key_hash"}`.

## Recursion end-to-end

A complete, runnable port of the noir-examples recursion flow lives in [`examples/recursion`](examples/recursion): the same inner circuit (`x * 2 + y == 9`), an outer circuit using Aztec's `bb_proof_verification` library, and `run_recursion.py` reproducing `generate-proof.ts` step for step.

```bash
cd examples/recursion && python run_recursion.py
```

Recursion needs a bb that can emit the verification key as field elements (`--output_format json` or `bytes_and_fields`). Nightly builds (3.0.x/4.0.x) drop that flag entirely — `noir_bb.doctor()` tells you which category your bb is in; native prove/verify (the hello_world example) work everywhere.

The single sharpest edge in this flow is version coupling: the outer circuit's `bb_proof_verification` dependency hard-codes artifact sizes (449 proof fields for `noir-recursive-no-zk`, 500 for the ZK variant, 115 vk fields in the v4.x line), and those constants move between bb major releases. The dependency tag in the outer `Nargo.toml` must therefore match your installed `bb` release. noir-bb checks the artifact sizes before they ever reach the circuit and raises a `VersionError` that says what mismatched and how to fix it — instead of the cryptic ABI/assertion error you would otherwise chase. `noir_verifier_snippet(proof, vk)` generates an outer `main.nr` sized to whatever artifacts you actually have.

## Older bb releases

If `bb prove --help` does not advertise `--verifier_target` (bb 0.8x–2.x), noir-bb maps each target onto the legacy flags it finds in the help text (`--oracle_hash keccak|starknet|poseidon2`, `--honk_recursion 1|2`, `--zk`, `--output_format bytes_and_fields`) and reads the `*_fields.json` artifacts those versions produce. Anything genuinely unsupported (for example `--write_vk` during prove) raises `VersionError` with the upgrade hint rather than failing inside bb. This path is best-effort; for recursion, use the modern pairing.

## Error handling

Everything derives from `NoirBBError`: `ToolNotFoundError` (with noirup/bbup install hints), `CommandError` (carrying the exact command, exit code and the tail of the tool's output), `VersionError` (capability or artifact-size mismatches), `ArtifactError`, `InputError`. `verify()` returns `False` on a cleanly rejected proof and only raises for tool-level failures (pass `strict=True` to raise on rejection too).

## Testing

`pytest` runs a 40-test suite against high-fidelity fake `nargo`/`bb` executables (in `tests/fakebin`) that reproduce the real tools' help texts, flag dialects, file layouts and exit codes for both modern and legacy bb — so the flag selection, artifact parsing and recursion wiring are exercised hermetically. The fakes do not do real cryptography; run `examples/recursion/run_recursion.py` with the real toolchain installed for an end-to-end cryptographic check.

## License

MIT
