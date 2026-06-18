from noir_bb import Barretenberg, NoirProject

def main() -> None:
    bb = Barretenberg()

    # 1. compile and execute the circuit to get a witness
    project = NoirProject("hello_world")
    w = project.execute({"x": 3, "y": 4})
    print(f"witness : {w.witness_path}")

    # 2. prove the circuit and verify the proof
    proof = bb.prove(w.circuit_path, w.witness_path)
    print(f"proof   : {proof.summary()}")
    print(f"verification: {bb.verify(proof, proof.vk)}")


if __name__ == "__main__":
    main()