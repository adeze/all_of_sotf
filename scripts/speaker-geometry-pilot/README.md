# Speaker geometry pilot

Isolated, offline Rust experiment. No app-shell, calibration, receiver or audio-runtime changes.
Solarxy's headless `solarxy-kernel` generates the outer and inner parametric boxes.
The graph editor and renderer are deliberately not dependencies. Manifold performs
chamber subtraction, flared port construction, aperture subtraction and cutaway CSG.
Rerun displays the resulting solid meshes in three linked-purpose panes.

## Run

From the umbrella root, reusing the existing lean target:

```sh
cargo test --manifest-path scripts/speaker-geometry-pilot/Cargo.toml --target-dir target/lean-rerun -j2
cargo run --locked --manifest-path scripts/speaker-geometry-pilot/Cargo.toml --target-dir target/lean-rerun -j2 -- target/qa/speaker-geometry-pilot --serve
```

Output directory must not exist. The process prints a loopback viewer URL; `--serve`
keeps it alive until interrupted. Port 9878 must be free. The live buffer is 32 MiB.
`speaker.rrd`, four OBJ meshes and a compact `receipt.json` persist independently.
All generated outputs belong under ignored `target/`, not source control.
The previous Rerun 0.38.1 web viewer initially framed this small model too closely;
zoom out and orbit the cutaway toward +X. Camera initialization still needs refinement
before a product integration. Mesh positions and volumes are unaffected.

Optional JSON argument replaces defaults, for example:

```json
{"width_m":0.260,"height_m":0.450,"depth_m":0.350,"wall_m":0.018,"port_radius_m":0.025,"port_length_m":0.160,"port_wall_m":0.004}
```

Dimensions are metres, right-handed Y-up, +Z front. Invalid or out-of-envelope
parameters fail before output creation. Exact coincident Solarxy vertices are welded
because its per-face UV/normal layout duplicates corners; arbitrary mesh repair is
not attempted. Solarxy's f32 source coordinates are promoted to f64, not restored to
double precision. Manifold validation rejects failed, empty or self-intersecting solids.

The blue mesh is chamber air excluding the complete port envelope. Its volume excludes
port air and does not yet subtract driver baskets, bracing or absorbers. The orange
mesh is port material; the grey cutaway removes the right half for inspection.
Driver apertures and flare are illustrative, not manufacturer-derived or acoustically
optimized. There is no port tuning, acoustic simulation, tolerance analysis, BREP/STEP
export or production release qualification in this experiment.

Pinned Solarxy revision: `9aa3b49191f0904478fd3a19dc7a4c1fbbfb6e36` (GPL-3.0-or-later).
Manifold Rust: `0.16.0` (Apache-2.0). Rerun: `0.40.0`, matching the existing viewer pilot.
Keep this GPL-consuming experiment isolated when considering distribution.

## Local qualification

Three focused tests pass: Solarxy corner welding preserves box volume and rejects
an open mesh; thicker walls reduce chamber air and increase cabinet material;
invalid dimensions fail closed. Full, cutaway and air meshes were visually reviewed
in the local Rerun web viewer, including exposed port geometry and shaded surfaces.
Default chamber air excluding the port envelope is approximately 28.745 litres.
The generated pilot recording and meshes occupy approximately 204 KiB; the Rust
build cache is separate and regenerable. No desktop application build was required.
