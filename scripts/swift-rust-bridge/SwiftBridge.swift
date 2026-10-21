import AdezeRustProbe

/// Typed Swift facade over the Rust C ABI; fixture methods have no side effects.
public enum RustProbe {
    public static var abiVersion: UInt32 { adeze_probe_rust_abi_version() }

    public static func increment(_ value: Int32) -> Int32 {
        adeze_probe_rust_increment(value)
    }
}

@c(adeze_probe_swift_abi_version)
public func swiftProbeABIVersion() -> UInt32 { 1 }

/// Rust calls this Swift export, which calls back into the Rust library.
@c(adeze_probe_swift_round_trip)
public func swiftProbeRoundTrip(_ value: Int32) -> Int32 {
    RustProbe.increment(value) &+ 1
}
