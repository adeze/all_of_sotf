# Acoustic workbench workflow and ownership

This document maps the desired integrated workflow onto observed components. Planned contracts are requirements, not claims of finished integration. Visual rules live in `DESIGN.md`; contribution inventory lives in `ADEZE_WORKBENCH_SPEC.md`.

## Restart checkpoint — 2026-10-22

The original application shell, navigation, menus and startup behavior are restored. Do not hide or repurpose music destinations. The next integration should be an isolated embedded workbench extension, following the speaker-workbench ownership pattern, with its own view and the existing shared commands/state. This is a workbench feature, not a realtime DSP plugin. Native Denon acquisition can become a supported measurement-source adapter through the authoritative Swift implementation; it is not connected to Rust by this checkpoint. Keep chat disabled.

The selectable pipeline currently remains a read-only projection in Room EQ Configure, with the existing form providing parameter edits. Arbitrary stage execution/reordering and a standalone calibration workbench extension remain unimplemented. Before resuming feature work, run the toolkit read-only canvas interaction test and review the graph/speaker tabs visually. The restored-shell application suites passed 146 tests; no hardware acquisition or transfer was performed.

## Shared context

Views should share a selected acoustic project, measurement/capture, channel, seat and baseline/candidate. Switching between Measurements, Designer, Response and Room must preserve this context. A selection change can select a different trace without regenerating filters. Configuration changes make affected results stale; calculation creates a new source-bound result. Do not silently reuse an old result with new controls.

Existing state includes SOTF `RoomEqState` and `sotf/crates/app-gpui/app/workbench_api.rs`. Reuse those owners rather than constructing an independent calibration state in each view. Cross-view selection persistence and acoustic project persistence require explicit contracts wherever the current state does not cover them.

## Task flow

| Task | Existing foundation | Integration requirement |
| --- | --- | --- |
| Acquire or import | Existing recording/Room EQ paths; Swift Denon acquisition and format adapters identified in contribution inventory | Offer supported source types only. Preserve original input and processing state. Native receiver acquisition, direct app measurement and saved-file import are different capabilities. |
| Organize measurements | Room EQ input state and existing analysis/import records | Select channel/seat/repeat; add, exclude, replace or delete deliberately. Interrupted capture must retain completed items; resume only through a supported acquisition state machine. |
| Analyze | Existing response/graph components and Room EQ review | Shared selected evidence, overlays, implemented smoothing and compatible units. Missing phase, decay or coordinates remain unavailable. |
| Configure | Existing Room EQ configuration and target editors; contribution's typed Designer contracts | Vertical stages with parameters, constraints and locks tied to selected project. Save effective settings and distinguish supported controls from future proposals. |
| Calculate | Existing Room EQ/AutoEQ execution and progress paths | Delegate to their numerical owners. Record engine revision, effective parameters and algorithms that ran. Cancel and retry without losing selected inputs. |
| Compare and listen | Room EQ review plus existing local listening-test workspace | Compare source-bound baseline/candidate, realized response and tradeoffs. Local DSP A/B/ABX is separate from hardware installation. |
| Export or deliver | Existing supported exports and Swift receiver adapter contribution | Export and verified receiver installation are distinct outcomes. Receiver transfer stays device-specific, candidate-bound and readback-aware. |
| Confirm | Post-calibration measurements | Attach installed candidate/preset identity and acquisition processing state. Treat confirmation as confirmation; do not silently mix EQ-active traces into uncorrected filter-design evidence. |

Navigation is non-linear. Return to a specific channel measurement, edit a target, compare a previous result or continue a valid interrupted operation without repeating unrelated stages. Readiness belongs to each operation, not an invented global progress percentage.

## Vertical Designer and graph editor

The intended Designer stages are evidence/selection, analysis, goals/constraints, calculation, comparison and delivery/confirmation. These are presentation groupings; retain the actual engine's stage identifiers and parameter semantics. Do not present an unimplemented stage as runnable.

Each stage has a summary, implemented inputs/overrides, applicable locks and a direct link to its response or room context. Perceptual goals should be recorded as explicit tradeoffs and translated only through supported typed parameters. Predicted front-stage/center-stage changes are not verified perceptual improvement. Keep chat disabled until requested.

Existing `sotf/crates/app-gpui/components/plugins/ui_graph.rs` uses toolkit `WorkflowCanvas` for pan, zoom, connections and hit testing. Plugin graph node identities are preserved when building canvas state. Reuse that interaction surface for routing and stage relationships where useful. Calibration execution remains with Room EQ/workflow services; a graph edge alone does not implement acquisition, evidence qualification or candidate generation. The graph is an alternative view of shared state, not a parallel configuration store or optimizer.

## Speaker workbench boundary

`../sonium/crates/sonium-speaker-designer` currently owns measured two-way passive RLC modeling: four FRD/ZMA imports, six components, driver delay/polarity, complex circuit solution and response/impedance prediction. Its version-1 JSON includes imported samples. GUI currently uses optional sibling GPUI toolkit dependencies. Keep reusable view/state separate from standalone launch code when embedding it.

`sotf-daw/crates/sotf-plugins/crates/sotf-plugin-crossover` owns realtime active IIR/FIR crossover processing, parameter metadata and routing. Do not turn passive circuit preview into another realtime crossover implementation. A future audition adapter needs a specified, tested digital realization and electrical/acoustic assumptions; component values cannot simply become active crossover cutoff parameters.

## Persistence, schemas and caches

Observed SOTF SQLite storage: `sotf/crates/sotf-player/src/database/`; configuration resolves `music.db` in `src/config/get.rs`. This is music-library storage. An acoustic project catalog and transactional measurement/run/result relations remain a separate design task; do not label the existing library database as that implementation.

A future catalog should reference immutable source inputs and compact derived arrays, retain saved effective configuration and result identities, and separate regenerable cache from retained project data. Migrations need explicit ownership and recovery. No schema migration or measurement deletion is authorized by this document.

Reusable Pkl schemas are owned by `../py-open-room-calibration/PklSchemas/` relative to this umbrella. Evaluate schemas into JSON at the contract boundary; Rust validates through typed `serde` contracts. Do not add a second schema copy or require a Pkl runtime inside realtime processing. Do not use Python pickle as an interchange format. Mojo is currently an optional Python projection/benchmark backend, not an established Rust integration; adding it would require a separate measured benefit and interface decision. These documentation changes add no runtime dependency.

## Local listening versus receiver actions

Existing `sotf/crates/app-gpui/components/listening_test/` supports local listening workflows; app state contains ABX trial modes and answer validation. Label these local DSP comparisons. They do not imply Denon preset switching, device readback, acoustic level matching or an installed calibration.

Receiver acquisition/transfer belongs to the device adapter identified in `ADEZE_WORKBENCH_SPEC.md`, preserving device ownership, exact candidate binding, terminal acknowledgement and readback. A successful export is an export. A successful local render is a render. Only confirmed device completion establishes installation; listening or confirmation measurements assess the acoustic outcome separately.

## Review acceptance

- Selection persists across connected views and displayed curves identify their selected source/channel.
- An input or configuration change invalidates dependent results, without deleting unrelated completed work.
- Locked constraints survive calculation; unsupported controls are absent or explicitly unavailable.
- Graph navigation reaches the corresponding stage/parameter view without another execution path.
- Measured, simulated, proposed and installed states cannot be confused in charts or actions.
- Project/catalog claims describe implemented storage; caches are distinguishable from retained inputs.
- Light/dark contrast, keyboard focus, narrow layout, readable units and accessible alternatives are reviewed with real data.

This documentation pass does not establish those acceptance checks as passed; implementation agents must report their actual focused validation.
