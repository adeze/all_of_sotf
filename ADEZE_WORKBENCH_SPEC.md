# Adeze workbench contribution and capability specification

Source inventory and proposed architecture, 2026-10-22. This document does not
establish numerical parity, acoustic improvement or upstream acceptance.

## Scope

Integrate both speaker design and our calibration Designer into SOTF's main
workbench. Reuse its existing Room EQ screen, optimization libraries and DSP
plugins. Current Swift calibration remains authoritative until a Rust replacement
passes explicit qualification. Python owns offline analysis/experimental DSP and
presentation; presence of a pipeline stage does not establish Swift parity.

Architectural parity means common capability contracts, identities and behavior,
not identical implementation languages or dynamic plugin loaders. SOTF real-time
DSP plugins are distinct from offline workbench capabilities. Prefer existing
Rust crates, Swift modules and Python packages; no new loader is required now.

Paths: `Swift/` means `../open-room-calibration/`; `Python/` means
`../py-open-room-calibration/`; other paths refer to this matched umbrella tree.

## Capability boundaries

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
| Smoothing | Python `dsp/response_smoothing.py`; Swift `FrequencyDisplaySmoother`; Rust `autoeq-core/src/curve_transforms.rs` | Different algorithms despite shared labels: Python Gaussian log windows, Swift log-cell overlap, Rust integral means and different psychoacoustic bandwidth defaults. Explicit method/version IDs; never silently replace defaults. Display smoothing does not change correction arrays. |
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
| SwiftUI/AppKit controls inside GPUI | No generic existing control slot established by this audit. Requires a macOS platform adapter for view geometry, clipping/occlusion, input/focus, accessibility, main-thread lifecycle and teardown. Do not assume the scalar ABI solves this. |
| Embedded web controls | `sotf/crates/app-gpui/ui/report_window.rs:SharedReportWindow::new` already uses a Wry child webview in a dedicated report window. An inline workbench panel still needs sizing/focus/lifecycle and typed event integration; it is not implemented by this fixture. |

Keep the speaker workbench inside SOTF's main content view. Add a native or web
panel adapter only for a concrete control that cannot reasonably use the host's
existing toolkit. Do not wrap/rewrite the entire Rust player merely to obtain
one richer control. Apple tracking/RealityKit remain platform adapters consuming
shared geometry and scientific data, not cross-language UI objects.

## Delivery sequence and acceptance

### Live scientific workbench, CAD and knowledge

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

### Agent protocols

Use MCP for an external agent operating the application: typed tool calls and
resources over the same commands/state used by ordinary UI. Existing Swift
`CalibrationMCP*` adapters already cover saved operations, measurement workflows
and receiver-service adapters; do not build a competing calibration authority.
Proposed workbench operations include inspect current selection/design, propose
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
