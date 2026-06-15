// Outer ("recursive") circuit from the Barretenberg recursive aggregation
// tutorial. It verifies the inner circuit's ZK UltraHonk proof in-circuit.
//
// The tutorial shows this circuit with the verification-key hash hardcoded to
// 0x0 and therefore only three ABI inputs:
//
//     use bb_proof_verification::{verify_honk_proof, UltraHonkVerificationKey, UltraHonkZKProof};
//     fn main(
//         verification_key: UltraHonkVerificationKey,
//         proof: UltraHonkZKProof,
//         public_inputs: pub [Field; 1],
//     ) {
//         verify_honk_proof(verification_key, proof, public_inputs, 0x0); // key_hash
//     }
//
// `0x0` works only on bb builds that treat a zero key_hash as "skip the vk-hash
// check". bb 4.0.0-nightly.20260120 (the toolchain this example targets) instead
// *enforces* hash(vk) == key_hash, so the 0x0 form fails when proving the outer
// circuit with:
//
//     Recursive Ultra Verifier: VK Hash Mismatch
//
// (and the final proof does not verify). bb.js hits the exact same error on this
// bb -- it drives the identical engine. So, like noir-examples/recursion, we take
// key_hash as an input and pass bb's real vk hash. Everything else -- the ZK
// `verify_honk_proof`, the `UltraHonkZKProof`, the single public input -- is the
// tutorial circuit unchanged.
use bb_proof_verification::{UltraHonkVerificationKey, UltraHonkZKProof, verify_honk_proof};

fn main(
    verification_key: UltraHonkVerificationKey,
    proof: UltraHonkZKProof,
    public_inputs: pub [Field; 1],
    key_hash: Field,
) {
    verify_honk_proof(verification_key, proof, public_inputs, key_hash);
}
