# Live solver response envelopes

Small offline trial using the existing AutoEQ DE implementation, `FlatStrategy`
and canonical PEQ evaluator. It reads saved measurement CSVs; it does not contact
receivers, modify calibration or enable chat. Project and source files remain unchanged.

## Cached build and run

From the umbrella root:

```sh
bun scripts/solver-envelope-pilot/build-cached.ts target/qa/solver-envelope-viewer
target/qa/solver-envelope-viewer \
  autoeq/data_tests/roomeq/measured/2.0_8361a/recordings.json L,R \
  target/qa/solver-envelope-run
```

Binary and output paths must be new. The development build helper links existing
compiler-compatible SDK/optimizer libraries and compiles only this harness and
shared presentation source. It matches ndarray/serde_json dependency fingerprints
to the cached optimizer; missing artifacts fail rather than trigger a dependency
build. A normal Cargo build is separate and was not required for this trial.

The printed loopback URL serves for 30 minutes. `envelopes.rrd` retains replay;
`callbacks.jsonl` and `receipt.json` retain numeric summaries and source identities.
Canonical CLI telemetry is also source-compiled into this harness and exercised
on the same callbacks, producing `<speaker>-canonical.rrd` validation recordings.
The cached numerical crates remain the tested engine; this is not a rebuild of
all current repository changes.

For the native workbench, append `--native-stream` as the fourth argument. This
mode opens no web or gRPC listener and exits when the offline study completes.
Standard output contains only newline-delimited JSON: first
`{"type":"chart","chart":...}` with the same metadata as `chart.json`, then
`{"type":"callback","callback":...}` for every synchronous solver callback,
then `{"type":"complete","studies":[...]}` after receipts and recordings are
flushed. Every line is flushed immediately; a closed consumer stops the study
with a nonzero exit. Errors use standard error. The ordinary recording,
`callbacks.jsonl`, canonical telemetry and receipt files remain available for
replay. Three-argument invocation retains the existing web behavior.

SDK and viewer use compatible `0.40.0` requirements. The cached helpers reject
SDK artifacts from another version; updating the installed CLI alone does not
update an embedded web viewer. Old recordings remain untouched; regenerate this
study from its saved inputs when moving across Rerun releases.

## Fixed chart panel

The fixed-frame response plot reuses the existing OpenUI presentation component.
Its axes remain anchored; wheel/button zoom changes log-frequency and dB ranges,
clips curves/bands to the frame, and survives generation updates. Speaker selection,
all seven smoothing modes, and generation replay use the Rust callback arrays.
The separate Rerun inspector tab retains the workflow tree and recordings.

```sh
cd scripts/openui-filter-pilot
ENVELOPE_STUDY=/absolute/path/to/solver-envelope-run PORT=8096 bun --hot envelope-server.ts
```

The loopback server reads only `chart.json`, `viewer.json` and `callbacks.jsonl`
from the explicitly selected directory. It exposes no mutation or chat commands.
`display_best_db` and bands are computed in Rust; the web chart performs no DSP.
This response-only CSV study cannot supply a measured time-frequency spectrogram
or decay waterfall; those require the saved impulse response and analysis metadata.

## Viewer

- Choose Raw, 1/3, 1/6, 1/12, 1/24, 1/48 octave or Psychoacoustic comparison tab.
- Scrub/play the `generation` timeline to see the actual population change.
  `callback_index` additionally distinguishes the terminal callback at the same generation.
- Expand a Compare view in Blueprint, expand its smoothing group, then toggle
  speaker groups such as L/R with their eye controls. Any loaded subset is supported;
  pairs are not averaged. The CLI can load any 1–8 existing named speakers.
- Solid colored trace: best predicted response; faint trace: supplied baseline;
  yellow: demonstration target; outer translucent intervals: min/max; inner:
  interpolated p10/p90. Colors identify speakers; all use one frequency/level scale.
- The legacy Rerun 2D comparison tabs use scene pan/zoom; use the fixed chart panel
  above for anchored axes and range zoom. Their display bands are
  alpha-blended vertical intervals covering log-frequency sample cells. Full f64
  envelope arrays remain in the recording; geometry is display precision.
- Open **Workflow · algorithm connections** for the native directed graph:
  saved measurement/derived target/constraints → DE → population → PEQ response →
  display smoothing → envelope → comparison. L/R branches remain separate.
  Expand **Algorithm stages** in Blueprint: **Target and constraints**, then
  **Speaker L/R**. Clicking a stage opens its dedicated explanation/parameter
  panel. Input/output units, bounds and roles (solver-free, fixed trial, derived
  and read-only) are visible there. Each speaker also has a **Live solver parameters**
  panel with current physical Hz/Q/dB values, generation and loss. The underlying
  `workflow/stages` entities retain the same information for inspection.
  Each actual callback logs current generation, best coefficients and loss.
  This maps this trial only; it is not the full Designer catalog, an execution
  engine or an interactive parameter-lock editor.

Smoothing uses `log-cell-rectangular-cubic-v1`, shared with production Swift.
Each population response is smoothed **before** statistics; target, baseline and
best response use the same transform. Raw optimization and coefficients remain
unchanged. Existing optimizer smoothing retains its separate semantics.

The scope view describes what executed. This is a bounded visualization trial:
two cut-only PK filters per speaker, 20–300 Hz, Q 0.5–3, gain −12–0 dB, 24 candidates,
48 generations, seed 91827, no polish. The common flat target is the first selected
measurement's mean in that band. It is a demonstration hypothesis, not a selected
calibration target. Source dB references are retained without independent channel
normalization; absolute SPL and acoustic improvement are not established.

Speakers optimize independently. The view does not implement coupled MIMO control,
joint multi-seat optimization, ART or WaveForming. Finite-fitness candidates supply
the bands; this does not prove arbitrary additional constraints or acoustic eligibility.
Population spread is not uncertainty, confidence, FIR-tap limits or global optimality.

The observer records initialization, every completed generation and terminal output
directly; no checkpoint polling or interpolated history. Logging is synchronous and
can slow wall time; no generation is dropped. Bounds: 256 candidates/4,096 bins in
the shared envelope helper, 16 MiB per input, 64 MiB callback file, 128 MiB live
viewer buffer. Budget errors stop the trial. Shared CSV grids must match; no implicit
resampling. Different-grid comparison needs an explicit preparation stage.

## Qualification, 2026-10-23

Saved L/R CSV run: 50 callbacks per channel, generations 0–48 plus terminal generation
48; no gaps. Objective values changed L 5.9128→5.6110 and R 5.8276→5.3542 (loss units,
not dB). Canonical adapter and comparison recorder both consumed those real callbacks.
Numeric checks verified count, sequence, ordered envelope bounds and transfer prohibition.
The same source and canonical telemetry adapter run against Rerun 0.40.0 with
identical final coefficients and losses to the 0.38.1 run. The fixed chart's browser
review covered zoom/reset, generation replay with retained zoom, and psychoacoustic
bands. SDK/web-viewer dependencies were compiled separately without a full app build.

Seven lightweight Rust tests include shared Swift/Rust reference vectors, validation, envelope
quantiles/smoothing order, explicit speaker selection, graph consistency and chart coordinate validation.
Swift's actual display source and focused XCTest cases also pass in a source-only
harness; full app/SPM and full CLI dependency builds are outside this check.

```sh
bun scripts/solver-envelope-pilot/build-cached.ts target/qa/envelope-tests --test
target/qa/envelope-tests
```
