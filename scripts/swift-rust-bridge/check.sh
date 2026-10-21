#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
    echo "This build probe currently supports native macOS only." >&2
    exit 2
fi

fixture_dir="$(cd "$(dirname "$0")" && pwd)"
repo_dir="$(cd "$fixture_dir/../.." && pwd)"
output_dir="$repo_dir/target/swift-rust-bridge"
rust_compiler="$(rustup which --toolchain stable rustc)"
rust_host="$("$rust_compiler" -vV | awk '/^host:/ { print $2 }')"
rust_library_dir="$output_dir/rust/$rust_host/release"
mkdir -p "$output_dir"

xcrun swiftc --version
"$rust_compiler" --version
RUSTC="$rust_compiler" CARGO_TARGET_DIR="$output_dir/rust" \
    rustup run stable cargo build --manifest-path "$fixture_dir/Cargo.toml" \
    --target "$rust_host" --release --locked --offline

xcrun swiftc -swift-version 6 -parse-as-library -emit-library -emit-module \
    -module-name AdezeSwiftBridge -emit-module-path "$output_dir/AdezeSwiftBridge.swiftmodule" \
    -emit-objc-header-path "$output_dir/AdezeSwiftBridge-Swift.h" \
    -I "$fixture_dir/include" -L "$rust_library_dir" -ladeze_rust_bridge \
    -Xlinker -rpath -Xlinker "@loader_path/rust/$rust_host/release" \
    "$fixture_dir/SwiftBridge.swift" -o "$output_dir/libAdezeSwiftBridge.dylib"

# Compile the compiler-generated Swift declarations as C, independently of Rust's declarations.
xcrun clang -x c -fsyntax-only "$output_dir/AdezeSwiftBridge-Swift.h"
xcrun swiftc -swift-version 6 -I "$output_dir" -I "$fixture_dir/include" \
    -L "$output_dir" -lAdezeSwiftBridge -L "$rust_library_dir" -ladeze_rust_bridge \
    -Xlinker -rpath -Xlinker @executable_path \
    -Xlinker -rpath -Xlinker "@executable_path/rust/$rust_host/release" \
    "$fixture_dir/SwiftSmoke.swift" -o "$output_dir/swift-smoke"
"$rust_compiler" --edition=2024 "$fixture_dir/rust_smoke.rs" \
    -L "native=$output_dir" -l dylib=AdezeSwiftBridge \
    -C link-arg=-Wl,-rpath,@executable_path -o "$output_dir/rust-smoke"

"$output_dir/swift-smoke"
"$output_dir/rust-smoke"
