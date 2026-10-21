//! Safe scalar facade around the two Swift exports used by this build fixture.
unsafe extern "C" {
    fn adeze_probe_swift_abi_version() -> u32;
    fn adeze_probe_swift_round_trip(value: i32) -> i32;
}

fn swift_round_trip(value: i32) -> i32 {
    // SAFETY: The version-1 scalar ABI accepts every i32 and has no pointer ownership.
    unsafe { adeze_probe_swift_round_trip(value) }
}

fn main() {
    // SAFETY: The linked fixture exports this argument-free version query.
    assert_eq!(unsafe { adeze_probe_swift_abi_version() }, 1);
    for value in [0, -1, i32::MIN, i32::MAX] {
        assert_eq!(swift_round_trip(value), value.wrapping_add(2));
    }
    println!("Rust → Swift → Rust: ABI version and scalar edge cases passed");
}
