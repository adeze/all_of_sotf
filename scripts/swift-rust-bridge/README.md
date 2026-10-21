# Swift–Rust build bridge

Dependency-free, native macOS build fixture. No production DSP interface,
calibration authority, receiver operation or UI embedding is added.

From the umbrella root, with Xcode Swift 6.4 and rustup stable available:

```sh
bash scripts/swift-rust-bridge/check.sh
```

Builds a Rust C-ABI dylib, a Swift dylib importing it through a Clang module,
and separate Swift/Rust smoke executables. Swift exports use supported `@c`
declarations; its compiler-generated C header is syntax-checked. Runtime probes
check ABI versions and scalar edge cases in Swift→Rust and
Rust→Swift→Rust calls. Wrapping Int32 arithmetic is deliberate, not DSP behavior.

All generated libraries, modules, headers, Cargo cache and executables live in
the ignored `target/swift-rust-bridge/` directory. Re-running builds incrementally;
that directory is disposable when no probe is running. No global toolchain,
application target, release workspace, or existing FFI header is modified.

`include/AdezeRustProbe.h` is the fixture's Rust contract. Rust's Swift declarations
are a deliberately tiny manual subset; introducing richer contracts requires
generated bindings or signature conformance checks. There are no pointers or
ownership-transfer APIs here. Buffers, jobs, errors, callbacks and UI objects
must receive explicit contracts and tests before being added.

This validates host development linkage only. visionOS/iOS slices, XCFrameworks,
distribution/signing, deployment targets, production services and UI lifecycle
integration remain unimplemented. Existing production DSP FFI is separately
owned by `sotf-daw/crates/sotf-plugins/crates/plugins-ffi/`.

Swift export syntax follows [SE-0495's supported `@c` interface](https://www.swift.org/blog/embedded-swift-improvements-coming-in-swift-6.3/#c-interoperability).
