# OpenUI filter comparison pilot

An isolated Bun/React/OpenUI panel, not a replacement app shell. It consumes
Rust-exported responses from actual retained OCA files and connects supported
controls to the existing `WorkbenchRequest` protocol. No chat, model calls,
receiver operations or JavaScript FIR implementation. Offline studies run in Rust.

## Start

First run `scripts/rerun-workbench` with an explicit RoomConfig, two
`--candidate-oca` inputs and `--designer-port 0`; see its README. Give that host
an explicit `SOTF_WORKBENCH_RUN_ID`. Its receipt prints the Designer endpoint.
All source inputs and generated comparisons stay outside tracked source.

From this directory:

```sh
rtk bun install --frozen-lockfile
rtk bun run check
rtk bun test
# Set these in the local shell, not in browser code or a tracked file:
export OPENUI_COMPARISON=/absolute/path/to/candidate-comparison.json
export WORKBENCH_URL=http://127.0.0.1:PORT/workbench
export SOTF_WORKBENCH_RUN_ID=YOUR_LOCAL_HOST_RUN_ID
rtk bun run dev
```

Open `http://127.0.0.1:8094/`. The proxy keeps the run identifier server-side,
requires a loopback endpoint, rejects cross-origin requests and allows only
`inspect` and revision-bound `update_designer`, `run_study` and `cancel_study`. Rust remains responsible for
atomic validation, persistence and stale-revision rejection. Without a configured
host the real charts remain available and controls report disconnection.

The optional standalone host owns an independent offline draft, not the running
SOTF application's state. Both its OpenUI controls and its Rerun project metadata
use that same host state. The native UI and this host use the same extracted
`roomeq_model::DesignerLimits` validator. Only four correction limits are editable;
the pilot does not claim to own a complete calibration workflow or algorithm config.
Changes persist to the host output's `designer-draft.json`; they do not rewrite
retained Swift candidate bytes. Run starts a new canonical offline RoomEQ study
with frozen inputs and the applied limits; cancellation stops at a solver callback.
Edits are locked while running. The draft's
48 kHz rate is explicit; each plotted filter retains its own native response rate.

## Rendering and interpretation

Pinned `@openuidev/react-lang` 1.0.1 renders a deterministic OpenUI Lang program
with five registered components: comparison container, saved response chart,
live study response, tradeoff summary and Designer controls. Components read real data from application context;
the UI language cannot invent measurements or execute arbitrary commands. This
qualifies renderer/action integration, not conversational generation quality.
OpenUI's optional installation telemetry script remains blocked by Bun.

The browser draws supplied curves on a logarithmic frequency axis and reports
sampled electrical extrema. These are serialized `filter` slot FIR magnitudes
relative to unity, not target curves, predicted room responses, trim-inclusive
transfer functions or proof of audible preference. Full-precision inputs remain
in the source OCA; Rust's existing FIR evaluator owns response computation.
Candidate hashes are available in details rather than taking over the legend.

The live chart polls the current study every 250 ms, showing supplied measurement,
target and candidate prediction arrays. Direct Rust callbacks are durably recorded
before Rerun publication; display polling can skip frames without losing history.
Completed studies show canonical final curves separately from optimizer proposals:
the pipeline can reject the last proposal and retain the original response.
Changing the draft marks earlier results as historical. No acoustic improvement is
inferred from optimizer progress or electrical response extrema.

Bun watches server modules and supports client updates. The tiny browser-only
`process.env.NODE_ENV` shim handles OpenUI 1.0.1's unguarded inspector lookup;
it exposes no server environment. Rust source changes require rebuilding and
restarting the standalone host. Restarting the presentation server does not
discard the live host's draft. Restart/resume of that draft is not implemented.

## Qualification

Four boundary tests and TypeScript checks pass. Local browser review used two
distinct retained candidates, eight channels each, with 512 native-rate response
samples per channel. A browser cut-limit edit reached and persisted in the Rust
host. Live checks confirmed unauthenticated native requests return 401, stale
proxy edits return 409 without changing the draft, and the browser response does
not contain the server-held run identifier. No AVR state changed.

The live-study browser check applied a 10 dB cut limit, then ran the saved
measured L-channel CSV through the canonical Rust workflow. All 103 callbacks
were recorded, with 76 response frames and 26 distinct predictions. The final
pipeline retained the original response despite a lower optimizer objective;
the completed chart correctly shows that canonical result. A separate browser
cancellation retained 38 callbacks without creating a successful result.
