#!/usr/bin/env python3
"""Python port of noir-examples/recursion/js/generate-proof.ts using noir-bb.

Flow (identical to the TypeScript original):

  1. execute the inner circuit (x=3, y=3)            -> witness
  2. bb prove, verifier_target=noir-recursive-no-zk  -> 449-field proof + 115-field vk
  3. feed {verification_key, proof, public_inputs, key_hash}
     into the outer circuit that calls verify_honk_proof_non_zk
  4. bb prove the outer circuit for an EVM verifier and verify it

Requires real `nargo` (>= 1.0.0-beta.18, via noirup) and a *stable* `bb`
release (>= 3.x, via bbup) on PATH. Nightlies (3.0.x/4.0.x) lack the
`--output_format` flag and cannot emit the vk as field elements, which this
recursive flow needs. Run `python -c "import noir_bb; noir_bb.doctor()"`
first if unsure. bb downloads its CRS to ~/.bb-crs on first use.
"""
from __future__ import annotations

from pathlib import Path

from noir_bb import Barretenberg, NoirProject, recursive_inputs

HERE = Path(__file__).parent
PROOFS = HERE / "proofs"


def main() -> None:
    bb = Barretenberg()

    # 1. inner circuit: witness for x*2 + y == 9
    inner = NoirProject(HERE / "circuits" / "inner")
    w_inner = inner.execute({"x": 3, "y": 3})
    print(f"inner witness  : {w_inner.witness_path}")

    # 2. recursion-friendly proof + vk (no-zk matches verify_honk_proof_non_zk)
    inner_proof = bb.prove(
        w_inner.circuit_path,
        w_inner.witness_path,
        PROOFS / "inner",
        verifier_target="noir-recursive-no-zk",
        write_vk=True,
    )
    print(f"inner proof    : {inner_proof.summary()}")
    assert bb.verify(inner_proof, inner_proof.vk), "inner proof failed to verify"

    # 3. outer circuit consumes the proof as ordinary inputs
    outer = NoirProject(HERE / "circuits" / "recursive")
    w_outer = outer.execute(recursive_inputs(inner_proof))
    print(f"outer witness  : {w_outer.witness_path}")

    # 4. EVM-targeted outer proof (what you would verify on-chain)
    outer_proof = bb.prove(
        w_outer.circuit_path,
        w_outer.witness_path,
        PROOFS / "recursive",
        verifier_target="evm",
        write_vk=True,
    )
    print(f"outer proof    : {outer_proof.summary()}")
    ok = bb.verify(outer_proof, outer_proof.vk)
    print(f"outer verified : {ok}")
    assert ok

    # bonus: Solidity verifier for the outer proof
    sol = bb.write_solidity_verifier(outer_proof.vk, PROOFS / "Verifier.sol")
    print(f"solidity       : {sol}")


if __name__ == "__main__":
    main()
