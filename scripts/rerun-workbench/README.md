# Standalone Rerun workbench pilot

Independent Rust launcher over `sotf-rerun-viewer`, using the existing
`roomeq-model::RoomConfig` and AutoEQ measurement CSV reader. No GPUI shell,
playback, hardware access or chat; studies reuse the canonical optimizer.

```sh
rtk cargo run --locked --manifest-path scripts/rerun-workbench/Cargo.toml \
  --target-dir target/lean-rerun -j2 -- \
  --config target/qa/native-rerun-ui-20261022/recordings-qa.json \
  --output target/qa/rerun-workbench-example --seconds 300
```

Open the printed loopback viewer URL. Optional `--room /path/to/room-model.json`
uses the existing digest-bound metre/+Z-up room loader. `--seconds 0` records
without keeping the server running. Output must be a new directory; source
project and measurement files remain unchanged. The web renderer uses WebGL
because this host's embedded WebGPU path failed qualification previously.

Repeat `--candidate-oca /path/to/retained.oca` to compare up to eight retained
native candidate files. `candidate-comparison.json` exports 512 logarithmically
spaced electrical FIR response samples per channel and source digests, suitable
for OpenUI's read-only chart panel. The existing local `math-iir-fir::Fir`
evaluator supplies responses; no TypeScript DSP or second FIR implementation.
Candidate curves remain in the separate OpenUI electrical-response chart.
Rerun logs candidate metadata alongside the project tree, but its measurement
chart contains measured responses only: electrical FIR gain and measured
pressure must not share an axis or imply the same reference.
The native Swift contract defines 128 taps at 48 kHz for satellites and 512 taps
at 6 kHz for subwoofers. Responses span 20 Hz to just below each channel's Nyquist limit.
These are serialized filter magnitudes relative to unity, not measured room
responses or acoustic predictions. Trims, crossover transfer, source processing,
polarity phase and receiver state are not applied. Sampled extrema are display
metrics, not full-band filter qualification; exact zero amplitude follows the
existing evaluator's -200 dB display floor. Candidate identity labels are taken
from their files; the separately computed source digest binds the loaded bytes.

`catalog.json` and the recording's `project/**` entity hierarchy contain typed
speaker, measurement, constraint, algorithm, configuration and candidate projections.
Project entities bind the original config digest; candidate entities bind their
OCA source digest instead. Measurement entries additionally
bind the parsed numeric curve digest. Algorithm entries identify configuration,
not proof that an algorithm ran. Speaker positions and qualification are never
inferred. Metadata uses the Rerun entity tree, not an executable workflow graph.
Select these entities for inspection; the shared viewer's existing chart/room/
solver blueprint adds a separate canonical final-output view.

This pilot supports ordinary single-driver channels with one or multiple
microphone measurements, from inline arrays or existing CSV files. Grouped
driver/subwoofer topologies fail explicitly. Loading does not average, smooth,
resample or qualify measurements. Runtime-only in-memory inputs cannot be
represented by saved RoomConfig files. Measurement eligibility remains owned
by the calibration engine.

The standalone process owns its offline study state. It does not subscribe to the
running SOTF application's state or replace the embedded viewer.

Optional `--designer-port 0` starts an owned loopback command endpoint. Set
`SOTF_WORKBENCH_RUN_ID` to the run identifier shared by the local presentation
proxy. The printed `designer_endpoint` accepts the existing `WorkbenchRequest`
inspect/update/run-study/cancel-study contract. Only frequency, maximum boost and maximum cut limits
are editable through the canonical `RoomEqScreenModel::update_design_limits`
validator's shared `roomeq_model::DesignerLimits` implementation. The standalone
host does not depend on the player/playback crate. Optimistic revisions reject
stale edits; accepted changes persist
an independent `designer-draft.json` before replacing in-memory state, then
publish the current snapshot into `project/designer` in Rerun. The original
project, measurements and OCA files are never overwritten.

This draft initializes those four limits from the supplied config. `run_study`
freezes a complete copy of that configuration, applies the four accepted limits,
copies local path inputs and hashes their exact bytes. It calls
`roomeq_workflow::optimize_room` at 48 kHz on a worker thread, without the player
crate. The pilot accepts local DE studies with at most eight filters, 2,000
iterations, population 64, 32 inputs and 128 MiB total copied inputs; remote
CEA-2034 resources are rejected. Inputs and retained candidate files stay unchanged.

Every actual callback is synchronously appended to `study-NNNN/callbacks.jsonl`
and sent directly to Rerun before the browser coalesces display updates. Inspect
returns only the latest callback and final response curves. Optimizer proposals
carry loss only when an actual optimizer response is supplied; unrelated stages
do not contribute placeholder zeroes to the objective chart. Optimizer proposals
are distinct from canonical final output after the workflow's safety gates;
finalization may legitimately restore the uncorrected baseline. Rerun has a
separate final-output view. Neither plot establishes acoustic improvement.
The viewer clears previous study plots at the next start; distinct historical
studies are retained in their per-run JSONL/results, rather than a multi-run
Rerun replay layout. Callback recording stops the study explicitly at 512 MiB
instead of dropping callbacks.

`run_study` and `cancel_study` require the current optimistic revision; concurrent
runs and edits during execution are rejected. Cancellation stops the solver at
its next callback. Each run has a distinct ID and fresh output directory with
source-bound identity, config, callbacks and terminal receipt. Successful runs
also retain the canonical DSP/curves in `result.json`; only a completed result
whose revision matches the draft is current. Failures and cancellations never
become current results. Chat and hardware operations remain unavailable.

Input JSON/CSV is bounded to 16 MiB per file; room mesh and per-entity metadata
retain the shared loader's limits. This is a trusted local development tool;
explicit CSV references may point outside the config directory. No remote
fetches are performed by this launcher.

```sh
rtk cargo test --locked --manifest-path scripts/rerun-workbench/Cargo.toml \
  --target-dir target/lean-rerun -j2
```
