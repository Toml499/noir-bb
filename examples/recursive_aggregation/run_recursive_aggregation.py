#!/usr/bin/env python3
"""Python port of the Barretenberg "Recursive Aggregation" tutorial.

    https://barretenberg.aztec.network/docs/how_to_guides/recursive_aggregation/

That guide's `recursive.test.ts` proves an inner circuit (`assert(x != y)`),
verifies its proof inside an outer circuit, and proves the outer circuit. It
drives `@aztec/bb.js`; this script does the identical thing with noir-bb's
msgpack backend, which is the *same* Barretenberg API bb.js talks to (bb.js is a
WASM client over `bb`'s msgpack interface; :class:`MsgpackBackend` is a
subprocess client over `bb msgpack run`). The step markers below line up with
the `// docs:start:*` blocks in the tutorial's TypeScript.

bb.js  ->  noir-bb mapping
--------------------------
  new UltraHonkBackend(bytecode,{threads},{recursive})  ->  MsgpackBackend(circuit, recursive=...)
  noir.execute(inputs)                                  ->  NoirProject.execute(inputs)
  backend.generateProof(witness)                        ->  backend.generate_proof(witness)   (CircuitProve)
  backend.getVerificationKey()                          ->  backend.get_verification_key()     (CircuitComputeVk)
  deflattenFields(proofData.proof)                      ->  deflatten_fields(proof)
  barretenberg.acirVkAsFieldsUltraHonk(vk)              ->  acir_vk_as_fields_ultra_honk(vk)
  backend.verifyProof(proofData)                        ->  backend.verify_proof(proof)        (CircuitVerify)

Requires real `nargo` and a `bb` that ships the msgpack API (`bb msgpack run`),
including the 3.0.x/4.0.x nightlies whose trimmed CLI cannot emit vk-as-fields
(run `python -c "import noir_bb; noir_bb.doctor()"`). bb downloads its CRS to
~/.bb-crs on first use.

One deviation from the tutorial, forced by the installed bb and explained in
`circuits/recursive/src/main.nr`: this bb enforces the verification-key hash
check, so we pass bb's real `key_hash` instead of the tutorial's hardcoded
`0x0`. bb.js would have to do the same on this bb.
"""
from __future__ import annotations

from pathlib import Path

from noir_bb import (
    MsgpackBackend,
    NoirProject,
    acir_vk_as_fields_ultra_honk,
    deflatten_fields,
    recursive_inputs,
)

HERE = Path(__file__).parent
CIRCUITS = HERE / "circuits"


def main() -> None:
    # docs:start:setup
    # Load the compiled circuits and make a Noir instance for each. (nargo has
    # already produced target/main.json and target/recursive.json; compile with
    # `nargo compile` in each circuit dir if they are missing.)
    main_noir = NoirProject(CIRCUITS / "main")
    recursive_noir = NoirProject(CIRCUITS / "recursive")
    # (the tutorial imports pre-compiled JSON; compile here if it is missing so
    #  the example runs from a fresh checkout)
    for project in (main_noir, recursive_noir):
        if not project.circuit_json.is_file():
            project.compile()
    # docs:end:setup

    # docs:start:backend_setup
    # Inner backend is recursion-friendly (recursive=True -> noir-recursive:
    # poseidon2 oracle, ZK), so its proof can be verified inside a Noir circuit.
    # Outer backend produces the final proof (recursive=False -> evm).
    main_backend = MsgpackBackend(main_noir.circuit_json, threads=8, recursive=True)
    recursive_backend = MsgpackBackend(recursive_noir.circuit_json, threads=8, recursive=False)
    # docs:end:backend_setup

    # docs:start:witness_generation
    # Execute the inner circuit to get its witness (assert(1 != 2); y is public).
    main_execution = main_noir.execute({"x": 1, "y": 2})
    # docs:end:witness_generation
    print(f"inner witness        : {main_execution.witness_path.name}")

    # docs:start:proof_generation
    # Generate the inner proof and verification key.
    main_proof = main_backend.generate_proof(main_execution.witness_path)
    main_vk = main_backend.get_verification_key()
    # docs:end:proof_generation
    print(f"inner proof          : {main_proof.n_fields} fields, "
          f"public_inputs={main_proof.public_inputs}")
    print(f"inner vk             : {main_vk.n_fields} fields, key_hash={main_vk.key_hash}")
    assert main_backend.verify_proof(main_proof), "inner proof must verify"

    # docs:start:recursive_inputs
    # Convert the proof and vk to fields and assemble the outer circuit's inputs.
    # In bb.js this is:
    #   recursiveInputs = {
    #     proof: deflattenFields(mainProofData.proof),
    #     public_inputs: [2],
    #     verification_key: vkAsFields,
    #   }
    # The tutorial stops there because its outer circuit hardcodes key_hash=0x0;
    # this bb enforces the vk-hash check, so we also pass the real key_hash.
    proof_fields = deflatten_fields(main_proof)
    vk_as_fields = acir_vk_as_fields_ultra_honk(main_vk)
    recursive_circuit_inputs = {
        "proof": proof_fields,
        "public_inputs": main_proof.public_inputs,  # == [2]
        "verification_key": vk_as_fields,
        "key_hash": main_vk.key_hash,
    }
    # noir-bb builds the identical dict (and checks every field count against the
    # outer circuit's ABI); use include_key_hash=False for the verbatim 0x0
    # circuit on a bb that skips the hash check.
    assert recursive_circuit_inputs == recursive_inputs(
        main_proof, circuit=recursive_noir.circuit_json
    )
    # docs:end:recursive_inputs

    # docs:start:recursive_proof
    # Execute the outer circuit (verifies the inner proof in-circuit) and prove it.
    recursive_execution = recursive_noir.execute(recursive_circuit_inputs)
    recursive_proof = recursive_backend.generate_proof(recursive_execution.witness_path)
    # docs:end:recursive_proof
    print(f"recursive witness    : {recursive_execution.witness_path.name}")
    print(f"recursive proof      : {recursive_proof.n_fields} fields")

    is_valid = recursive_backend.verify_proof(recursive_proof)
    print(f"recursive proof valid: {is_valid}")
    assert is_valid, "recursive proof failed to verify"
    print("\n=== recursive aggregation complete ===")


if __name__ == "__main__":
    main()
