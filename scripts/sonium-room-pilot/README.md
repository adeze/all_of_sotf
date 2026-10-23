# Sonium / Swift room trial

Offline comparison using saved VisionPlan nominal room dimensions, FL and MLP coordinates. No receiver connection, calibration changes or filter generation. Registered room mesh remains solver-unapproved and is not used numerically.

## Numerical boundary

The harness imports unchanged Sonium ray-tracing and time-domain source modules and uses its sparse matrix crate. It supplies a cell-centered, seven-point finite-difference operator to `LeapfrogStepper`; this is not a full Sonium FEM assembly.

Wave scenario: sealed rectangular room, rigid lossless boundaries, 343 m/s, 80 Hz Ricker source centered at 30 ms, 0.6 s duration, grids 0.20, 0.15, 0.12 and 0.10 m. The actual left opening is deliberately closed in this approximation. The Swift reference compiles the canonical `SpatialWaveStudy.swift` directly, without building the app. Source, listener, dimensions, frequency grid and numerical configuration are checked before comparison. Earlier two-grid reference files remain accepted. Each simulation is bounded to 60,000 cells and 250 million cell updates; display glyphs are subsampled to at most 5,000 points without changing numerical calculations.

Ray scenario: left boundary open, rigid other surfaces, 20,000 rays, eight bounces, 0.12 s, 0.15 m receiver radius. Five first-order paths are checked against independently calculated image-source geometry. This does not execute or qualify the Swift raycaster. Energy histograms are not coherent impulse responses. Material absorption and scattering are not fitted.

## Run

From the umbrella root, supply saved paths; outputs must not already exist:

```sh
swiftc -O -parse-as-library ../open-room-calibration/Sources/OpenRoomCalibrationDSP/SpatialWaveStudy.swift scripts/sonium-room-pilot/swift-reference/main.swift -o /tmp/room-wave-reference
/tmp/room-wave-reference "$ROOM_MODEL" "$SWIFT_REFERENCE"
bun scripts/sonium-room-pilot/swift-reference/analytic-rays.ts "$ROOM_MODEL" "$ANALYTICAL_RAYS"
cargo run --offline --manifest-path scripts/sonium-room-pilot/Cargo.toml --target-dir target/lean-rerun -j2 -- "$SWIFT_REFERENCE" "$ROOM_MODEL" "$ANALYTICAL_RAYS" "$NEW_OUTPUT_DIR"
cargo test --offline --manifest-path scripts/sonium-room-pilot/Cargo.toml --target-dir target/lean-rerun -j2 pilot_tests
```

Add `--features viewer` before `--` for the standard Cargo viewer build when storage permits. This logs pressure glyphs on separate coarse/finest grid tabs, geometric reflection paths, Swift/Sonium receiver traces and ray energy arrivals. Select the `frequency_hz` timeline for the spectral comparison tab; select `simulation_time` for room animation and receiver traces. Frequency plots use a common peak-magnitude reference across all grids, in relative dB, without independent curve normalization. Pressure is arbitrary amplitude, not calibrated SPL. Each grid has a fixed pressure display scale across time; colors cannot establish cross-grid amplitude agreement. The loopback viewer serves for 30 minutes; the `.rrd` recording remains replayable afterward.

For this development run, `build-cached-viewer.ts` reused complete Rerun SDK artifacts rather than rebuilding the renderer. It requires compatible compiler artifacts in `target/lean-rerun/debug` and the current trial's `core-build.jsonl`; this is not a portable release build path.

## Recorded qualification, 2026-10-23

Ignored artifacts: `target/qa/visionplan-sonium-trial-20261023/`.

| Check | Result |
| --- | --- |
| Receiver pressure relative L2 error, 0.20 m | 9.59e-13 |
| Receiver pressure relative L2 error, 0.15 m | 2.83e-12 |
| Complex spectrum relative error, respective grids | 4.99e-13 / 1.53e-12 |
| Five reflection paths, maximum length error | 8.88e-16 m |
| Coarse/fine complex spectrum difference | 1.162 relative |
| Coarse/fine magnitude spectrum difference | 0.596 relative |

Matched-operator implementation parity passes. Grid convergence is **not established**. Grid-dependent represented room dimensions and lossless long-duration phase accumulation limit this approximation. Do not interpret agreement between implementations as physical accuracy, measured-room validation or improvement in calibration. Artifacts bind input and implementation hashes and explicitly prohibit transfer.

### Finer-grid extension

Artifacts: `finer-results/` and `swift-finer.json` beneath the same ignored trial directory. Three pilot tests and pilot Clippy pass. Viewer code was linked against the existing cached SDK; no application or renderer dependency rebuild was performed.

| Grid spacing | Cells | Swift/Sonium pressure relative L2 error |
| --- | ---: | ---: |
| 0.20 m | 5,016 | 9.59e-13 |
| 0.15 m | 12,000 | 2.83e-12 |
| 0.12 m | 22,940 | 2.90e-12 |
| 0.10 m | 41,040 | 3.54e-12 |

| Refinement | Magnitude-spectrum relative difference | Complex-spectrum relative difference |
| --- | ---: | ---: |
| 0.20 → 0.15 m | 0.596 | 1.162 |
| 0.15 → 0.12 m | 0.469 | 1.038 |
| 0.12 → 0.10 m | 0.593 | 1.224 |

The differences do not decrease monotonically. The 0.10 m run is a comparison reference, not ground truth. Reports include represented domain dimensions: cell-center inclusion moves the effective walls when spacing changes. This study combines boundary approximation, spatial/time discretization and finite-record spectral effects. A subsequent convergence qualification must hold physical boundaries fixed and compare against an independent known solution before attributing these differences to propagation discretization alone. No claim of acoustic convergence or perceptual equivalence is supported by this result.

## Rerun upgrades

The viewer was installed as a prebuilt binary:

```sh
cargo binstall rerun-cli --version 0.40.0 --no-confirm --disable-strategies compile
```

SDK manifests use compatible `0.40.0` requirements, not exact `=0.40.0` requirements. Lockfiles record the tested resolution; they can be updated. Coordinate SDK and viewer upgrades, regenerate recordings where necessary, then verify file replay, live connection and numeric payloads. Cargo's 0.x compatibility range permits patch updates; a new minor release requires an intentional manifest update. The CLI binary is user-wide, not installed separately inside each repository.

## Speaker / crossover direction

Reuse Sonium's typed `ProductDesign`, cabinet parameters and crossover evaluation, together with the existing `speaker-geometry-pilot` Solarxy/Manifold adapter. Bind driver data, materials, geometry revision, mesh resolution and crossover configuration to one study revision; send results to Rerun.

Keep electrical crossover calculations separate from acoustic summation. Existing cabinet lowering uses equivalent cylinders; it does not establish geometry-exact behavior for organic chambers. The geometry pilot provides mesh construction and display, not validated acoustic optimization, passive RLC synthesis, BREP/STEP or CAM. Exact CAD and manufacturing toolpaths remain separate qualification tasks.
