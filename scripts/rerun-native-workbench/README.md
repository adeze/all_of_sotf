# Native acoustic workbench

Standalone Rust application embedding Rerun 0.40 and a native egui control panel.
There is no browser, chat provider, receiver connection or calibration mutation.
The optional offline study runs the existing Rust solver through a bounded,
direct callback pipe; it does not generate or install an AVR calibration.
The SOTF and Swift application shells are unchanged.

```sh
cargo build --manifest-path scripts/rerun-native-workbench/Cargo.toml \
  --target-dir target/lean-rerun -j2
target/lean-rerun/debug/rerun-native-workbench "$NATIVE_CAPTURE_JSON" \
  --room-study "$SAVED_ROOM_COMPARISON_JSON" \
  --solver-study "$SOLVER_CALLBACK_DIRECTORY" \
  --recording "$ROOM_PRESSURE_RECORDING_RRD" \
  --recording "$SOLVER_WORKFLOW_RECORDING_RRD"
```

Only the capture is required. Inputs are explicit paths; no private room defaults
are embedded. The first native-viewer build includes its renderer and query
dependencies; it does not build SOTF, Swift or the calibration application. Reuse
the target directory for subsequent builds and monitor free disk space.

To enable **Run offline study**, also build `scripts/solver-envelope-pilot/` in
the same target directory and pass:

```sh
--solver-engine target/lean-rerun/debug/solver-envelope-pilot \
--solver-config /absolute/path/to/recordings.json \
--solver-channels L,R \
--solver-output /absolute/path/to/new-study-directory
```

The output directory must not exist; failed/stopped studies retain partial
artifacts. One output path permits one run. This deliberately bounded study uses
two cut-only PEQs per selected speaker, 20–300 Hz, 24 population members and 48
generations with seed 91827. The config supplies CSV measurement paths; its other
optimizer settings are not applied by this pilot. The common flat target is a
demonstration, not a selected room-correction goal.

## Views and controls

- **Spectrogram + waterfall:** saved native capture JSON (`sampleRateHz`,
  `positionID`, `records[].channelID/repeatNumber/impulseResponse`). Select one
  speaker/repeat; one Apply action updates both views from the same STFT grid.
  Frequency, time, floor and ceiling affect the shared crop/color/height scale.
  Changing these ranges never renormalizes the underlying grid. Analysis-window
  changes explicitly recompute the grid.
- **Solver response:** native fixed-axis egui plot with speaker selection,
  seven smoothing modes, min/max and p10/p90 bands, generation replay and reset.
  Reads Rust-exported `chart.json` and `callbacks.jsonl`, or consumes every
  synchronous callback from a new offline study. Follow live tracks incoming
  generations without resetting axes. The UI validates contiguous callbacks,
  terminal receipts and process exit; it never relabels this separate CSV study
  as the selected native room capture. Stop or app exit terminates its child.
- **Impulse + energy decay:** linked native time plots from the selected saved
  response: signed peak-normalized impulse and reverse-integrated broadband
  energy. Finite-record decay is not noise-corrected and does not infer RT60.
- **Room layout / reflection paths:** saved comparison JSON plus its digest-bound
  adjacent Swift reference. Geometry and first-order paths are simulated;
  reference JSON does not contain spatial pressure snapshots.
- **Saved recordings:** buttons load existing Rerun views, including actual saved
  pressure animations and algorithm graphs/stage inspectors. Recordings load on
  demand; their native Sources/Blueprint panels retain navigation and timelines.

Unavailable views remain disabled. Spatial Studio's shader oscillations and
synthetic-volume fixtures are not measured wave propagation and are not ported
as measurement evidence. Existing native room recordings supply actual simulated
pressure snapshots instead. This is a first integration, not a complete port of
every web visualization or a custom WGSL renderer.

## Scientific conventions

Native capture samples already include the receiver response coefficient; this
loader does not apply it a second time. Time zero is the absolute IR peak for
display only, not physical time of flight. Frame centres stop at available saved
samples; shorter captures never become 500 ms measurements by padding their tail.

The shared analysis defaults to a 128 ms symmetric Hann window and 4 ms hop.
Its nominal window resolution is 7.8125 Hz; FFT zero-padding does not improve
physical frequency resolution. Longer windows improve spectral discrimination
while smearing changes in time. Edge windows are zero-padded explicitly.

Levels are relative to the full analysis-grid peak, not calibrated SPL. Every
display crop retains that reference. Spectrogram colors and waterfall heights
share one floor/ceiling; display geometry/raster sampling is bounded while full
grid tensors remain available. Log-frequency raster columns use nearest FFT bins
and do not add spectral detail.

Room pressure remains a rigid/lossless model where declared; ray energy is not a
coherent room impulse response. Numerical visualization does not establish
audible improvement, measured directivity or filter eligibility.

## Validation

```sh
cargo test --manifest-path scripts/rerun-native-workbench/Cargo.toml \
  --target-dir target/lean-rerun -j2 -- --skip rir_waterfall::tests
```

Focused tests cover shared analysis, crop/reference consistency, native renderer
logging, capture validation/scaling, native control ranges, recording paths,
room-study validation and solver-history validation. The source-included shared
math module avoids linking unrelated DSP features; legacy waterfall functions
remain unchanged. UI acceptance additionally requires opening the native app and
exercising Apply, channel selection, solver playback and recording navigation.

Local validation (2026-10-23): native debug build and all 13 focused tests passed;
the solver's focused stream-protocol test passed. Format and diff checks passed.
Two UI-launched offline studies each delivered 100 contiguous callbacks (50 per
speaker), finalized both channels and matched the prior study's parameters and
losses exactly. The second saved RRD passed `rerun rrd verify`. Native review
covered initial framing/axes, impulse and integrated decay, early/final population
bands, plus earlier FL/FR selection, shared range edits, smoothing, room pressure
and workflow navigation. Screenshots are disposable outputs under
`target/qa/native-workbench-20261023/`.

Spectrogram labels include logarithmic frequency, elapsed milliseconds and the
shared relative-dB legend. Waterfall camera framing is explicit when opening the
view; Reset decay framing restores it after orbiting. Range edits retain camera
state. Unified recording navigation and custom web shader ports remain future
work. No full SOTF or Swift build is required for this isolated application.
Native Stop/app-exit cleanup was reviewed but not manually interrupted mid-run.
Convex band fills narrower than 0.01 dB are omitted to avoid degenerate
anti-aliasing artifacts; retained numerical arrays and solver results are unchanged.

## Precomputed walkthrough and arrival replay

`Study walkthrough` presents Source → Goal → Bounds → Search → Prediction from
the loaded bass-EQ study. `Play steps` advances these panels and replays actual
generation callbacks during Search. Recorded filter parameters and the 20–300 Hz
correction boundaries remain visible. Stage timing is presentation timing, not
recorded algorithm execution timing; this is not the full Swift calibration pipeline.

`Arrival timing replay` uses the saved room study's FL source, MLP receiver,
direct travel time and first-reflection paths. Moving markers follow path length;
arrival colors change at the saved travel times. `arrival_frame` slows playback
while labels retain physical milliseconds. It is a single-source geometric model,
not a phase-coherent pressure field or a multi-speaker alignment result.

Both views work from precomputed inputs without `--solver-engine`, new solver
execution or output persistence. Native review verified panel selection, automatic
step progression and arrival-frame scrubbing. Calibration remains unchanged and
chat disabled.
