# Adeze workbench contribution and capability specification

Source inventory and proposed architecture, 2026-10-22. This document does not
establish numerical parity, acoustic improvement or upstream acceptance.

## Scope

Current direction (2026-10-23): trial an independent calibration/speaker workbench
using Rerun for scientific views and a bounded OpenUI comparison surface. Reuse
SOTF's Room EQ models, optimization libraries and DSP plugins without treating its
player shell as the permanent application host. Preserve the existing SOTF shell
and embedded pilots while the independent host is qualified. Current Swift calibration remains authoritative until a Rust replacement
passes explicit qualification. Python owns offline analysis/experimental DSP and
presentation; presence of a pipeline stage does not establish Swift parity.

Architectural parity means common capability contracts, identities and behavior,
not identical implementation languages or dynamic plugin loaders. SOTF real-time
DSP plugins are distinct from offline workbench capabilities. Prefer existing
Rust crates, Swift modules and Python packages; no new loader is required now.

Paths: `Swift/` means `../open-room-calibration/`; `Python/` means
`../py-open-room-calibration/`; other paths refer to this matched umbrella tree.

## Capability boundaries

The product target is a complete pipeline catalog, with a project-specific enabled
subgraph. Resolve availability from the receiver/DSP capability profile, speaker
topology, source measurements, room evidence and user goals. A stage declares typed
input/output ports, algorithms/versions, units, parameter bounds and locks,
dependencies and output invalidation. Parameter roles distinguish solver-free,
user-fixed, derived and read-only values; locks bind the run recipe and must survive
UI/agent edits. Enabling a stage validates prerequisites; disabling or editing one
invalidates dependent results. Explain unavailability and distinguish implemented/off,
unsupported by this project and not implemented. A node overview and guided panels
must share this configuration, not invent separate execution paths. This catalog-wide
integration remains a target; the current envelope pilot maps only its actual study.

### Target: Dirac Live ART and Trinnov WaveForming capability benchmarks

User-selected target, 2026-10-23: pursue independently implemented functional capabilities comparable to Dirac Live Room Correction, Bass Control and ART, and Trinnov Optimizer/WaveForming, with agent operation through the same commands as the UI. This is a development target, not a current parity or acoustic-performance claim. The cited vendors describe proprietary systems; there is no evidence that they use these Rust libraries.

| Required capability | Current boundary | Acceptance needed |
| --- | --- | --- |
| Multi-position magnitude, impulse and phase correction; editable correction bands and targets | Existing source inventory below contains several components; complete Rust workflow parity is not established | Source-bound reference cases, realized causal filter checks, repeatable multi-seat post-correction measurements |
| Multi-subwoofer integration and crossover optimization | Reuse Room EQ and existing Swift alignment/bass-management work; distinguish experimental models from installed transfer functions | Joint measured transfer functions, crossover summation and seat-consistency checks, headroom limits |
| ART-like supporting-speaker MIMO bass/decay control | Supporting-source and matrix-processing components are starting points, not proof of ART equivalence | Causal regularized matrix filter realization, support-band/level constraints, latency and stability checks, real routing, independent held-out-seat and decay validation |
| WaveForming-like coordinated subwoofer sound-field control | Ray/FD pilots visualize simplified models; they do not implement or validate this control | Explicit array/room feasibility, independent output channels, measured field-control study, decay and spatial-uniformity validation |
| AI operation across project, measurement, Designer, solver and visual panels | Extend existing capability/MCP command services; keep in-app chat disabled until separately activated | Typed inspection/edit/study commands sharing UI state, revision/conflict checks, cancellable jobs, reproducible parameter/run receipts and supported renderer controls |

AI may explain tradeoffs and propose supported settings; deterministic commands validate and execute them. Preserve goals, constraints and locks across the guided panels. Offline study authorization does not authorize receiver writes. A receiver's per-channel FIR export does not by itself provide the cross-channel routing required for general MIMO playback.

Delivery order: measured workflow and realized filters → joint multi-sub/crossover qualification → causal supporting-speaker matrix pilot → array field-control qualification. Extend AI commands alongside each working capability rather than advertise unsupported actions.

Primary benchmark descriptions: [Dirac Room Correction](https://www.dirac.com/products/room-correction), [Dirac ART](https://www.dirac.com/resources/art-technology), [Trinnov WaveForming](https://www.trinnov.com/en/technologies/active-acoustics/waveforming/). ART describes supporting-speaker control below approximately 150 Hz; WaveForming describes room-dependent discrete subwoofer configurations, including front-only options and fuller front/rear arrays. These are separate approaches, not interchangeable modes of an already implemented solver.

| Capability | Owns | Boundary and integration |
| --- | --- | --- |
| Measurement acquisition/import | Native capture, receiver capture adapters, immutable supported file imports, partial campaign inventory | Produces source-bound observations, not automatically qualified correction evidence. Preserve per-take review and explicit new attempts after interruption. |
| Analysis and evidence | Response/IR/decay diagnostics, repeat/noise assessment, decision-specific qualification | Raw, active-preset confirmation and simulated data retain separate purposes. Missing support returns unavailable reasons. |
| Calibration Designer | Targets, stage configuration, locks, constraints, baseline comparisons, calculation orchestration | Shares selected project/channel/seat/measurement with Room and Response. Delegates numerical work; never embeds a second optimizer. |
| Speaker workbench | Driver imports, passive circuit editor, active crossover preview, enclosure study adapters | Embedded main-content feature. Existing Sonium physics and SOTF crossover DSP remain their owners. |
| Room model and simulation | Geometry, poses, boundary/material priors, measured directivity inputs, pressure/path studies | Explicit metric axes/handedness and provenance. Studies remain separate from canonical calibration and measured validation. |
| Visualization | Room layout, pressure glyphs/surfaces, waterfall, response overlays, shader adapters | Consumes typed arrays/geometry. Does not synthesize evidence or infer directivity from room appearance. |
| Receiver delivery | Denon protocol, candidate serialization, preflight, transfer/readback/receipts, supported Audyssey adapters | Device-specific capability, separate from DSP and UI. Offline export, sent command and verified installation are different outcomes. |
| Agent advice | Context inspection, typed proposals, deterministic validation and explicit application | Uses the same locked Designer state. Cannot invent measurements, bypass eligibility or silently mutate hardware. Keep chat disabled during current integration. |

The shared design document and measurement selection belong to the host, not to
individual plugins. A capability declares ID/version, supported operations,
parameter schema/units, evidence requirements, output schema and implementation
status. Reuse existing registries/contracts before adding a new abstraction.
Implement extraction incrementally at existing call boundaries; do not perform
a simultaneous three-language rewrite.

## Source and provenance inventory

Implementation ownership below is not a claim of mathematical invention. General
FIR, Linkwitz–Riley, Schroeder decay and Thiele–Small techniques predate this work.

| Area | Our inspected source / current integration | Existing Rust overlap and proposed contribution |
| --- | --- | --- |
| Passive/active crossover | `sonium/crates/sonium-speaker-designer/src/{lib,desktop}.rs`; new `sotf/crates/sotf-speaker-workbench/src/lib.rs:ActiveCrossoverSettings` | Adeze editor integration measures existing `sotf-plugin-crossover` output for explicit LR24 preview. Sonium cabinet/port/enclosure/Thiele–Small models already came from upstream. No passive-to-active conversion or playback routing added. |
| Vertical Designer | Swift `CalibrationDesignerView`, `CalibrationDesignerExecution`, `CalibrationDesignDocument`, `CalibrationDesignProposal`; Python `studio_app/{state,designer_store}.py` | SOTF Room EQ is upstream. Add our shared context, non-linear six-stage workflow, locks, saved constraints and source-bound comparison/application behavior around its existing engine. |
| Target/objective policies | Swift `CalibrationTargetPlanner`, `RoomCalibrationOptimizer.generateFullProfile`; Python `dsp/pipeline.py` target compilation, `bass_calibration_search.py` | Rust `roomeq-workflow/src/target_enforcement.rs`, `roomeq-model/src/{target_tilt,target_transition}.rs` already exist. Compare explicit listener/seat/channel targets and protected baselines; keep punch, tail, seat consistency and spatial preference separate. |
| FIR/temporal realization | Swift `RoomCalibrationOptimizer.swift`, `+Tonal.swift`, `+Realization.swift`, `DenonDualCurveFIR.swift`; Python `dsp/pipeline.py`, `temporal_evidence.py` | Rust `autoeq-fir/src/lib.rs` already has checked minimum/linear-phase and Kirkeby families; `roomeq-workflow/src/fir.rs` has correction. Port only missing evidence/realization policies after comparison. |
| Alignment | Swift `RoomCalibrationOptimizer+Alignment.swift`; Python `dsp/subwoofer_co_optimizer.py` | Reuse `roomeq-engine/src/phase_alignment/optimize.rs` and `crossover_summation.rs`. Preserve reference identity, processing compatibility, distance-coordinate semantics, multi-seat scoring and baseline retention. |
| Qualification/verification | Swift `QualifiedEvidencePool`, `MeasurementEvidence`, installed-feedback studies; Python `workflow_contracts.py`, `calculations/ensemble.py` | Rust `autoeq-measurements/src/provenance.rs`, `roomeq-workflow/src/{evidence_intake,verification}.rs` already provide substantial support. Adapt demonstrated policy gaps, not a duplicate generic quality score. |
| IR diagnostics | Python `room_analysis.py` uses pyfar/pyrato; Swift source-bound audit and `QualifiedDecayAnalysis` | `math-audio/crates/math-rir/src/metrics/{decay_curve,iso3382_metrics}.rs`, `lib/analyze.rs`, `sti.rs` already implement analysis. Compare noise compensation, fitting windows and unavailable results. |
| Smoothing | Swift `FrequencyDisplaySmoother`; Rust `autoeq-core/src/display_smoothing.rs`; legacy Python/Rust optimizer transforms | Shared Swift/Rust display algorithm `log-cell-rectangular-cubic-v1`, seven modes and mirrored parity fixtures. Legacy Python Gaussian/Rust optimizer modes retain different semantics. Display smoothing does not change correction arrays. |
| Run identity | Swift `CalibrationAlgorithmRecipe`, `CalibrationAlgorithmParameters`, `CalibrationAlgorithmExecution`, `CalibrationAlgorithmObservations`, build metadata plugin | Extend existing Rust provenance with engine revision, resolved parameters and observed executed/skipped/unavailable/fallback/reused stages. Python `semantic_contracts.py:AlgorithmDescriptor` alone is not a full execution receipt. |
| Denon acquisition and transfer | Swift `Sources/DenonNetworkTransport/`: `NativeMeasurementPlan`, `DenonCalibrationTransferPreflight`, governed transport/transfer and receipts; canonical OCA export | A substantial device adapter contribution, not part of general AutoEQ math. Preserve exact candidate/preset binding, shared device ownership, ACK/terminal completion and settled readback. Current experimental transfer still needs supervised live qualification; do not claim every path validated. |
| Audyssey interoperability | Swift `MultEQEditorDocument`, `MultEQEditorCommand`; `Documentation/MULTEQ_EDITOR_ADY.md`; Python offline ADY analysis/import helpers | Community-supported ADY subset inspection/round-trip/template-preserving settings/curtain export. This is not complete proprietary project ingestion, conversion to qualified native evidence, or proof of native FIR delivery through the Editor. Audit Rust support before specifying a new adapter. |
| Room geometry and placement | Swift room workspace contracts, `RealityKitRoomBuilder`, `RoomWorkspaceImporter`, saved VisionPlan/RoomPlan geometry and speaker poses | Reuse schema-bound room/source/pose exports. Separate saved-room coordinates, manual placement and relocalized anchors. Apple tracking stays an Apple-platform adapter; Rust need not reimplement ARKit. |
| Pressure/reflections/materials | Swift `RoomAcousticFieldSolver`, `RoomBoundaryPressureSolver`, `RoomPressureBoundaryValidation`, `RoomAcousticFieldRoomDraft`, `NativeRoomPressureStudyView`; Python `dsp/{spatial_forward_model,raycaster,fdtd_wave_solver}.py` | Read-only studies with explicit boundaries and material priors. Generic geometry/material/directivity equivalence with Rust was not established. First-order models are not full-wave room truth; variance heuristics are not measured directivity. |
| 3D and waterfall presentation | Swift `RoomPressureGeometry`, `RoomPressureSurfaceView`, `AcousticDynamicMeshView`, `StoredWaterfallSurface`, `RoomAcousticFieldRenderer`; Python `spatial-studio/src/` WGSL field/waterfall shaders | Significant reusable presentation/contracts work. Rust `math-dsp` already supplies waterfall/wavelet/early-late primitives. Adapt renderer inputs with units, axes, reference and measured/predicted labels; shaders do not become simulation authority. |
| Advice/automation | Python `studio_app/assistant.py`, `designer_store.py`, `agent_workflow.py`; Swift reviewed proposals and canonical calculation | Current assistant supports inspection/comparison and bounded draft/display proposals, not arbitrary hyperparameter optimization, qualification or hardware control. Port deterministic proposal→validate→explicit apply semantics over existing controls. |

Original `../autoeq` has three Adeze-only commits not integrated into this matched
checkout: `65de4deb` qualified temporal FIR/Zarr adapter, `a56a4ab3` dependencies,
`37dc0f8d` QA errors/Python vectors. Audit before selective migration; do not count
them as already delivered here. No PyO3/maturin binding package was found in the
inspected original Math Audio manifests. A future binding should expose a small
existing kernel only when dependency/performance benefit and parity justify it.

## Bidirectional Rust, Swift and Python interfaces

### Umbrella source and data organization

The shared inventory now lists Swift and Python companion checkouts separately
from Rust workspaces. `scripts/workspaces.py --applications` and `--all-sources`
expose those paths; existing default workspace/release behavior is unchanged.
No source is copied or moved. This is coordination, not a single-language build.

Proposed storage layout, not a completed migration: a user-selected data root
outside all source checkouts, containing `projects/<project-id>/` with immutable
measurement sources, room assets, design revisions, run recipes, retained
candidates and verification observations. Store large shared source objects once
and reference them by content identity; bind paths relative to the project/data
root rather than to an app repository. Keep machine-local device credentials and
preferences separate. Regenerable renders, derived previews and build products
belong in explicitly clearable caches, never in the durable source store.

Before moving existing files, enumerate Swift/Python readers, saved manifests,
candidate/rollback dependencies and raw-source hashes. Produce a dry-run mapping;
then migrate with conflict refusal, reference updates and reopen/reproduction
checks. Do not remove originals until references and retained inputs validate.
No existing measurement, preset or project storage path changed in this step.

### Native and application interfaces

Use a narrow versioned C ABI between Rust and Swift, with safe language wrappers.
Do not expose native Rust/Swift generic layouts or make a cyclic runtime dependency.
Each capability has one implementation owner; callers depend on its adapter.

The initial calibration implementation can remain Swift: a safe Rust wrapper
calls its versioned C interface. A later Rust implementation may replace that
adapter only after the same contract/conformance fixtures pass. Replacing an
implementation requires an explicit compatible version or migration; the ABI
cannot be broken arbitrarily while callers still depend on it.

React/TypeScript uses a local application API for typed requests, asynchronous
job progress, cancellation and artifact references. Python orchestration may
call native services/kernels directly or through that service. Do not create a
Swift→Python→React→Rust computation chain or cyclic cross-language ownership.
Native buffers cross FFI; browser presentation consumes bounded exported arrays
or artifacts. A cross-language connection is not an additional DSP authority.

Existing precedent: `sotf-daw/crates/sotf-plugins/crates/plugins-ffi/` already
builds `staticlib`/`cdylib`, generates headers with cbindgen, includes a Swift
package, and exposes ABI-version/capability queries. Inspect and reuse applicable
pieces rather than introduce an unrelated bridge framework. This does not yet
expose every AutoEQ, Math Audio or Swift calibration operation.

- **Rust to Swift:** selected Rust crate APIs → C ABI/header → SwiftPM C module
  and typed Swift wrapper. Package Apple device/simulator slices explicitly where
  needed; macOS success alone does not establish visionOS support.
- **Swift to Rust:** selected Swift services → toolchain-supported C entry points
  using Swift 6.4 `@c` → Rust declarations and safe wrapper. The scalar build
  fixture below proves host header generation, exports and runtime linkage;
  production thread and ownership behavior still require qualification. Do not depend
  on Swift ABI stability as though it were a Rust calling convention.
- **Rust to Python:** PyO3/maturin can wrap the same library kernels behind typed
  Python functions. The C ABI need not be the Python binding implementation.

Start with offline analysis or crossover-response kernels and reference fixtures.
Swift canonical calibration can later be called through an adapter without
rewriting its optimizer; receiver delivery remains a separately authorized service.
RealityKit/ARKit objects stay on the Apple side and export geometry/pose snapshots.

Contracts require ABI version negotiation, fixed-width fields, explicit array
shape/stride/units, pointer-length validation, ownership and matching destruction,
bounded allocation, cancellation, thread/actor requirements and structured errors.
Contain Rust panics and translate Swift errors; neither may unwind across the
boundary. Real-time audio entry points must remain allocation/lock-free; offline
job APIs use separate lifecycle rules. No JSON conversion of dense arrays merely
to cross a language boundary.

Acceptance: both-direction link/call smoke tests, null/length/ownership and
error-path tests, callback teardown/cancellation, repeated create/destroy,
concurrency isolation, and numerical fixtures through the wrapper itself.
No production service ABI is exported yet. The dependency-free
[`scripts/swift-rust-bridge/`](scripts/swift-rust-bridge/README.md) build fixture
exports only version queries and scalar arithmetic probes. Its single command
builds both libraries, syntax-checks the generated Swift C header and runs Swift
and Rust callers, including Rust→Swift→Rust. Generated products live in ignored
`target/swift-rust-bridge/`. This is native macOS development linkage, not an
SPM/XCFramework release package or a new calibration implementation.

Primary references: [Rust FFI](https://doc.rust-lang.org/nomicon/ffi.html),
[Swift C/C++ wrapping](https://www.swift.org/documentation/articles/wrapping-c-cpp-library-in-swift.html),
[Swift C++ interoperability](https://www.swift.org/documentation/cxx-interop/).

### Sophisticated UI controls

SOTF uses GPUI, not GTK. A C ABI enables function calls; it does not provide UI
composition, native object lifetime management or automatic widget translation.

| Arrangement | Current evidence and recommendation |
| --- | --- |
| Platform-specific controls over shared state/actions | Preferred baseline: GPUI in SOTF, React in Spatial Studio, SwiftUI in native Apple apps. Keep control parameter bounds, units and commands compatible; visual implementations may differ. |
| GPUI inside a Swift/AppKit host | Existing pattern: `gpui-toolkit/crates/gpui-au/src/ffi.rs:gpui_au_create_with`, `plugins-au/GenericAU/GPUIAUView.swift` and `GenericRustViewController.swift`. The AU host owns the NSView; FFI provides resizing/input/frame/teardown. Extending it to a full mixed workbench requires separate layout and lifecycle testing. |
| SwiftUI/AppKit controls inside GPUI | Out of scope by user decision. Use host-native controls over shared commands and typed interfaces; do not build a custom embedding adapter. |
| Embedded web controls | `sotf/crates/app-gpui/ui/report_window.rs:SharedReportWindow::new` already uses a Wry child webview in a dedicated report window. An inline workbench panel still needs sizing/focus/lifecycle and typed event integration; it is not implemented by this fixture. |

Keep the speaker workbench inside SOTF's main content view using existing GPUI
controls. Do not wrap/rewrite the entire Rust player merely to obtain
one richer control. Apple tracking/RealityKit remain platform adapters consuming
shared geometry and scientific data, not cross-language UI objects.

## Delivery sequence and acceptance

### Live scientific workbench, CAD and knowledge

#### Solver response envelopes

Requested visualization: a response plot indexed by frequency, animated by actual solver generation. Overlay the selected target and constraints, the best-so-far realized response, a translucent pointwise 10th–90th percentile population band, and faint pointwise min/max bounds. Keep the measured baseline visible or selectable. Bands represent candidate spread, not statistical confidence, measurement uncertainty or a proven global optimum. Pointwise envelope edges need not correspond to any single feasible candidate. FIR taps belong in a separate coefficient/time view.

This requires per-generation candidate response data on a shared frequency grid, with units/reference, generation identity, candidate count, feasibility rule and best-candidate identity. Objective-score extrema alone cannot construct a frequency-response envelope. Use validated feasible candidates for the main band; distinguish constraint failures rather than silently include them. If only a sampled population is available, label the sample size and sampling rule. Missing callbacks remain missing; do not interpolate invented solver history. Keep numerical optimization independent of visualization and bound telemetry volume without blocking the solver. Preserve full parameter/run identities for replay.

The current Sonium room-grid comparison is a discretization study, not a population optimization animation. Its overlapping response curves must not be relabeled as this envelope.

The envelope trial now includes a native Rerun directed graph of its executed
per-speaker chain and stage metadata with input/output units, tunable variable
bounds, fixed trial values and derived values. Actual callbacks populate live
generation/coefficient/loss metadata. This read-only overview is not the complete
project-capability catalog, interactive locks or a replacement execution engine.

Display smoothing must offer Off, fractional-octave widths (1/3, 1/6, 1/12, 1/24 and 1/48), and an explicitly named/versioned psychoacoustic mode. Persist the display choice separately from optimizer parameters. Smooth each candidate on the declared shared frequency grid before calculating pointwise percentiles and extrema; apply the same display transform to target, baseline and best response. Retain raw arrays and raw convergence/error calculations. Do not smooth wrapped phase, FIR taps or decay fields through a magnitude-only control.

Implemented display kernel: Rust `autoeq-core/src/display_smoothing.rs` and Swift
`FrequencyDisplaySmoother` share `log-cell-rectangular-cubic-v1`, with mirrored
reference fixtures across seven modes. Legacy Rust `curve_transforms` optimizer
smoothing remains unchanged and is not interchangeable with this display kernel.
The [solver-envelope pilot](scripts/solver-envelope-pilot/README.md) renders actual
DE population callbacks, translucent min/max and p10/p90, best/baseline/target traces,
and selectable speaker groups. The canonical CLI telemetry adapter uses the same
envelope calculation/rendering helper. The room-grid pilot remains a separate
discretization study; its curves are not optimizer populations.

The target interaction is shared selection across room/speaker geometry,
response/decay plots, objective tradeoffs and a solver progress timeline. Display
actual evaluated candidates, current best feasible result, constraint violations
and objective components where the backend reports them. Distinguish physical
propagation time from optimizer iterations. Do not invent smooth trajectories
or intermediate acoustic fields between unreported candidates.

Existing Rust hooks include `autoeq-optim` candidate/progress callbacks and
`roomeq-engine/src/channel_fir/progress.rs: FirProgress`. Callback support varies:
some algorithms report native generation boundaries; others have stage-boundary
cancellation only. Introduce bounded, revision-tagged snapshots outside solver
hot paths; UI sampling must not block optimization or change numerical results.
Start/pause/cancel/replay controls must reflect actual backend capabilities.
Preserve run/config/source identity when comparing or replaying candidates.

Rerun is the selected scientific recording/viewer SDK; Rust/SOTF remains the
application shell, workflow host and command/state authority. Keep the SDK opt-in
while packaging and embedding are qualified. The isolated
`scripts/rerun-study/` tool exports existing differential-evolution checkpoints
to `.rrd` without linking another graphics stack into GPUI. One checkpoint is
a snapshot; several compatible checkpoints form sparse recorded history, not
a reconstruction of unobserved iterations. Live observation reuses AutoEQ's
generation callback and atomic checkpoint file; a separate process forwards
observed snapshots to a loopback Rerun viewer and a recording. Polling can skip
generations and must report gaps rather than interpolate. Direct in-process
callback recording is an opt-in AutoEQ CLI feature, with generation and terminal
events captured directly and response snapshots evaluated by existing AutoEQ
filter functions. The polling tool remains a compatibility path for older
executables. Acoustic field rendering remains separate integration work.
Recordings are disposable derived artifacts; retain source inputs and run
configuration as authority.

Polyscope-rs is a candidate scientific-view adapter, not a replacement for
RealityKit's spatial tracking or the application shell. Before a GPUI fork is
adopted, qualify external device/texture ownership, event-loop independence,
resource lifetime, bounded snapshot uploads and the native macOS rendering
path. Existing GPUI mesh/surface controls remain the embedded implementation.
Fork only the needed adapter/component; avoid importing an entire viewer shell.

Python already has `cad/speaker.py: build` and checked STEP/GLB export in an
isolated locked build123d environment; `docs/SPEAKER_CAD_SOLVER_WORKFLOW.md`
describes its limits. Retain build123d as geometry authority, behind a job adapter
rather than a mandatory Rust rewrite. A parameter edit schedules geometry checks,
tessellation and explicitly supported simulation. Bind exact parameter/CAD/source
digests to meshes and studies. STEP is a derivative, not the editable parameter
history; visual meshes do not prove manufacturing validity. Current cavity air
reporting excludes real driver/gasket/brace displacement; forced structural/
acoustic response, absorber qualification and CAM are not completed by this CAD.

The knowledge capability should reuse Python `knowledge.py: search_knowledge`,
which already returns local Markdown passages with source paths, lines and
digests. It is bounded lexical retrieval, not an existing materials database or
semantic RAG system. Add typed, cited driver/material/manufacturing records only
as needed, retaining units, frequency dependence, uncertainty and licensing.
Knowledge suggestions become explicit reviewed design inputs; retrieval is not
measurement evidence or receiver state. No vector database is required initially.

SAR-DT offers `sar_dt/geometry/img2threejs.py` geometry import and
`sar_dt/apps/dash_evidence_viewer.py` evidence viewing. This bounded audit did not
establish an independently reusable voxel-editor component. Evaluate extraction
of geometry, selection, clipping and scientific-data adapters before importing
its application or dependencies. Spatial Studio and `gpui-scene3d` are nearer
existing renderer candidates. Keep exact CAD solids separate from voxel/mesh
inspection and acoustic discretization.

### Open3D and XDL qualification

The 2026-10-22 offline Open3D 0.20.0 trial used prebuilt wheels only: source-bound
registered VisionPlan mesh, topology checks, bounded mesh sampling/voxelization,
and ray intersections checked against an independent float64 calculation. Rerun
0.38.1 recorded and displayed the geometry. Private results and the reproducible
trial script remain in the Swift repository's ignored
`native-measurements/open3d-visionplan-trial-20261022/` directory. No runtime
dependency, Rust/Swift build, material inference or acoustic approval was added.
Treat Open3D as an optional offline geometry adapter candidate; the Rust bridge,
Apple GPU execution and independent scan alignment remain unqualified.

Do not adopt XDL's numerical wrappers based on API names. The inspected
[`xdl-amp` MPS operations](https://github.com/TuringWorks/xdl/blob/master/xdl-amp/src/mps.rs)
use CPU loops; the
[`DIGITAL_FILTER` and `HILBERT` implementations](https://github.com/TuringWorks/xdl/blob/master/xdl-stdlib/src/signal.rs)
contain placeholders. Metal dispatch/shader bounds and float32-only interfaces
also require qualification. Reuse existing direct numerical dependencies;
consider XDL's dispatch telemetry as a reference rather than a trusted backend.
These are source-inspection findings, not reproduced runtime failures.

### Rust-first migration boundary

`scripts/rerun-native-workbench/` now embeds the native Rerun 0.40 viewer with a
native control panel. Saved native IRs drive one checked STFT grid shared by
spectrogram and waterfall; Hz/time/dB crops preserve its relative level reference.
The native fixed-axis solver plot consumes saved arrays or a bounded direct
callback pipe from the existing offline Rust solver, including display smoothing
and population bands. Runs preserve recordings, validate callback continuity and
terminal receipts, and stop their child process on cancellation or app exit.
Native linked impulse/finite-record Schroeder plots share saved IR selection;
spectrogram axes/legend and explicit waterfall framing use shared display ranges.
Saved room geometry/reflections and
explicit RRD inputs supply other views; migrated pressure recordings retain their
simulated status. Unsupported data remains unavailable. Chat stays disabled,
including Rerun's optional agent panel; neither source loading nor display edits
invoke calibration generation or receiver operations. This is an isolated viewer
integration, not a complete shader port or engine migration.

Precomputed study playback now provides Source → Goal → Bounds → Search →
Prediction panels, actual recorded best parameters and correction-band markers.
Search replays saved generation callbacks; panel timing is illustrative. A separate
arrival replay animates the saved FL-to-MLP direct and first-reflection paths using
physical travel times on a slowed frame timeline. These are a bounded bass-EQ
study walkthrough and single-source geometric simulation, not full calibration
stage telemetry or multi-speaker pressure interference. Both operate without a
new solve, persistent output, receiver changes or chat.

Python application workflows are legacy migration references; do not add new
Python application-framework dependencies for the Rust product. Preserve existing
sources and fixtures until replacement behavior is qualified. The TypeScript/React
scientific viewers are migration inputs, not code that can be copied directly
into Rerun. First map their data contracts to Rerun: response curves and solver
timelines; IR-derived spectrograms and waterfall meshes; room geometry, speaker
poses and reflection paths; then pressure fields and volumetric inspection.
Reuse canonical solver arrays and coordinate transforms, with explicit units,
references, measured/predicted labels and unavailable states. Qualify slices,
color domains and geometry against existing fixtures before retiring each view.
Custom GPU shaders and interaction controls require separate renderer/embedding
qualification; do not claim they are automatically portable to Rerun or GPUI.

The application may become Rust-native without requiring Python frameworks at
runtime. Choose migration references by demonstrated behavior and tests, not
language preference: Swift's typed calibration contracts, Python's numerical
fixtures and existing SOTF implementations can each supply part of the reference.
Port only missing capabilities; do not duplicate an existing Rust implementation.
For each replacement, compare input interpretation, units, numerical output,
failure behavior and exported contracts before retiring its runtime dependency.
Keep display smoothing separate from correction and preserve unavailable data.
Python CAD/build123d, knowledge retrieval and research tools remain optional
companions until equivalent replacements are qualified. This policy does not
authorize deleting those sources or declaring full Swift/Rust engine parity.

### Agent protocols

Use MCP for an external agent operating the application: typed tool calls and
resources over the same commands/state used by ordinary UI. Existing Swift
`CalibrationMCP*` adapters already cover saved operations, measurement workflows
and receiver-service adapters; do not build a competing calibration authority.
The opt-in Rust [workbench MCP service](sotf/crates/sotf-dev-driver/WORKBENCH_MCP.md)
implements inspection, revision-bound edits to four Room EQ limits, and starting
or cancelling the existing offline optimization. UI and agent edits use the same
`RoomEqScreenModel::update_design_limits` validation. Chat remains disabled.
This does not establish Swift calibration parity or hardware qualification.

Further proposed operations include inspect current selection/design, propose
and validate a revision-bound patch, run/cancel an offline CAD or solver job,
read progress/results, retrieve cited knowledge and export a selected artifact.
These are proposed capabilities, not a claim that all tools already exist.

Long jobs return handles; provide bounded progress snapshots and artifact/resource
references rather than repeatedly serializing full meshes or measurement arrays.
Preserve locks, stale-state rejection, source qualification and existing receiver
authorization. Agent control must not bypass the host's normal validation.

ACP is an optional client interface if the workbench itself hosts an external
coding-agent conversation, including prompts, session updates and permission
handling. It is not required for agents to invoke our MCP tools. Keep chat
disabled until explicitly activated; MCP capability design does not enable a model.
See [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)
and [ACP overview](https://agentclientprotocol.com/protocol/v1/overview).

### Incremental delivery

1. Complete embedded speaker workbench and feature-on/off navigation checks.
2. Define shared Designer/context contracts over existing Room EQ controls.
   Changed inputs invalidate downstream results; returning to earlier stages
   must preserve drafts and partial measurement inventory.
3. Add source/algorithm capability map and run receipts before porting policies.
   Record source repository/revision/symbol, method version, parameters/units,
   input hashes, eligibility and actual call-site outcomes. Show concise method
   explanations in UI; keep hashes and developer details in inspectable reports.
4. Differentially qualify each missing policy using immutable shared fixtures:
   axes/units/sign/reference, irregular-grid smoothing, complex delay/polarity,
   multi-seat crossover summation, noise-limited decay and explicit unavailable
   cases. Do not treat matching function names as matching algorithms.
5. Validate realized serialized FIRs: 128/512 taps where required, full-band gain,
   finite truncation ripple, onset/tail energy, dual-bank headroom, protected
   settings and no double polarity. Define per-quantity tolerances in advance.
   Experimental phase/Kirkeby paths retain explicit blockers/fallbacks.
6. Adapt acquisition/Audyssey/Denon delivery separately, with format round-trips,
   partial/error cases, incompatible topology, ownership contention, failed
   completion/readback and immutable receipts. Never infer installation from
   export or a socket write. No receiver operations are part of this inventory.
7. Adapt room and renderer contracts: coordinate/handedness fixtures, correct
   pressure vs magnitude/SPL labels, material provenance, solver convergence,
   measured/predicted distinction and held-out observations. Verify native/web
   rendering separately from numerical and acoustic validation.
8. Add optional advisory tools after deterministic workflow works. Validate
   locks, stale digests, unsupported controls, cancellation and provider errors;
   model output cannot bypass evidence or delivery authority.

Package reviewable contributions separately: embedded crossover UI; Designer
contracts/context; evidence/run policies; individually qualified numerical
differences; receiver/interchange adapters; room/rendering adapters; agent tools.
This is broader than an optimizer port. Rust already supplies much numerical
foundation; no honest migration percentage follows from this inventory.

Current-source references: Swift `CODEBASE_MAP.md`, `Documentation/SWIFT_WORKFLOWS.md`
and `Documentation/CALIBRATION_STRATEGY.md`; Python `AGENTS.md`; source symbols
above. Older audit diagrams are architecture snapshots, not execution receipts.
Upstream acceptance, redistribution/licensing review and acoustic/device proof
remain separate gates. This specification itself ports no algorithm.
