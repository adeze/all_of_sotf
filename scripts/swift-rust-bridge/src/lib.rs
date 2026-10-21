//! Build probes only; no calibration or production plugin interface.

/// Version of this fixture's scalar C contract.
#[unsafe(no_mangle)]
pub extern "C" fn adeze_probe_rust_abi_version() -> u32 {
    1
}

/// Fixed-width wrapping arithmetic avoids overflow panics across the ABI.
#[unsafe(no_mangle)]
pub extern "C" fn adeze_probe_rust_increment(value: i32) -> i32 {
    value.wrapping_add(1)
}
