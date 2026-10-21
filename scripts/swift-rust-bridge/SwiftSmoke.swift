import AdezeSwiftBridge

precondition(RustProbe.abiVersion == 1)
for value: Int32 in [0, -1, .min, .max] {
    precondition(RustProbe.increment(value) == value &+ 1)
    precondition(swiftProbeRoundTrip(value) == value &+ 2)
}
print("Swift → Rust: ABI version and scalar edge cases passed")
