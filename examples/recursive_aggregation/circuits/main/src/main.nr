// Inner ("main") circuit from the Barretenberg recursive aggregation tutorial:
// a basic constraint of the form `assert(x != y)`. `y` is public, so it shows
// up as the single public input that the outer circuit checks.
fn main(x: Field, y: pub Field) {
    assert(x != y);
}
