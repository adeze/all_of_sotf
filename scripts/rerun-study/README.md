# Rerun checkpoint pilot

For new studies, prefer AutoEQ's optional `rerun-telemetry` feature with
`--rerun-recording PATH.rrd` and optional `--rerun-port PORT`. That path records
the existing solver callbacks directly, including frequency-response snapshots;
it does not poll or skip generations. See the
[AutoEQ instructions](../../autoeq/README.md). This tool remains useful for
existing checkpoint files and older executables.

Standalone, read-only Rust exporter/observer. No app-shell integration, bundled viewer, network
server, chat, GPU renderer or optimizer execution. SDK uses the compatible `rerun 0.40.0` requirement
(Rust 1.96+) with default features disabled and only `sdk` enabled. Decoder
dependencies are test-only. Cargo.lock pins the transitive dependency graph.

## Accepted producer

AutoEQ's existing `--checkpoint-exact PATH.json` writes
[`ExactOptimizerState` v1](../../autoeq/crates/autoeq-workflow/src/workflow/exact_resume.rs),
containing [`DECheckpoint` v1](../../math-audio/crates/math-optimisation/src/de_checkpoint.rs).
Warm-start candidates, generic reports, raw WAV and arbitrary progress JSON are
not supported. The producer replaces its checkpoint file at generation barriers;
one file contains one snapshot, not the trajectory of earlier generations.

```sh
# From umbrella root; target directory is isolated and disposable.
rtk cargo run --locked --manifest-path scripts/rerun-study/Cargo.toml \
  --target-dir scripts/rerun-study/target -- \
  /path/to/study.rrd /path/to/exact-checkpoint.json

# Sparse history: supply independently retained checkpoints in generation order.
# Output must not exist. No interpolation or absent iterations are generated.
rtk cargo run --locked --manifest-path scripts/rerun-study/Cargo.toml \
  --target-dir scripts/rerun-study/target -- \
  /path/to/history.rrd /path/to/generation-10.json /path/to/generation-20.json

rtk cargo test --locked --manifest-path scripts/rerun-study/Cargo.toml \
  --target-dir scripts/rerun-study/target -j 4
```

Open the result separately in a compatible Rerun viewer; the exporter does not
download or start one. Test fixtures are handcrafted schema fixtures, not actual
solver runs or acoustic evidence.

## Meaning and limits

- Timeline is completed **generation**, not physical acoustic time or elapsed
  optimizer time. Source timestamps are retained unparsed as provenance text.
- Logs best pre-polish objective, indexed best parameters, finite population
  minimum/maximum, non-finite status and exact evaluation counts in text metadata.
  Positive-infinity sentinels remain explicit; unavailable scalar series are
  cleared rather than retaining stale values. Final post-polish results, if
  present, remain distinctly labeled within the original terminal metadata.
- Objective and parameter units are unspecified in this source schema. Parameter
  indices are not spatial coordinates; no dB, Hz, room mesh or pressure field is
  inferred. No claim of acoustic validity follows from this visualization.
- Each snapshot retains input-file SHA-256 plus original source path, timestamp,
  run identity, source/build/configuration identities and seed. Multi-file input
  requires matching identities, seed and parameter dimension, increasing
  generations and nondecreasing evaluation counts. Same-generation terminal
  finalization is rejected by offline multi-file export as ambiguous: export
  either state separately. Live observation distinguishes terminal-only updates.
- This adapter validates its viewing projection, dimensions and finite numeric
  arrays; it does not validate RNG/adaptation/archive restore state or whether
  producer identity hashes are truthful. It cannot resume an optimizer. Preserve
  original checkpoint files for authoritative continuation/provenance.
- At most 128 files and 32 MiB combined serialized input; each read is bounded.
  JSON NaN/Infinity/overflow are rejected. Existing output is never replaced,
  including a file created concurrently during export. Successful recordings
  publish from a temporary file in the destination directory.

## Live observer

For a local web viewer (separate terminal; all listeners stay on loopback):

```sh
uv tool run --from rerun-sdk==0.40.0 rerun --serve-web --bind 127.0.0.1 \
  --port 9876 --web-viewer-port 9093 --server-memory-limit 256MiB
```

Open `http://127.0.0.1:9093/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9876%2Fproxy`.

Start a compatible viewer separately, then start the observer **before** AutoEQ.
AutoEQ's existing save callback atomically replaces the checkpoint; this observer
polls that file. This is callback → checkpoint → Rerun, not direct solver
instrumentation. A missing checkpoint file is expected while waiting for producer.

```sh
rtk cargo run --locked --manifest-path scripts/rerun-study/Cargo.toml \
  --target-dir scripts/rerun-study/target -- \
  watch /path/to/live.rrd /path/to/exact-checkpoint.json \
  --timeout-secs 180 --poll-ms 100 --viewer-port 9876 \
  --cancel-file /path/to/stop-observer
```

`--viewer-port` is optional; when supplied, the SDK writes simultaneously to the
local RRD and `rerun+http://127.0.0.1:PORT/proxy`. No remote host is accepted.
TCP availability is only a preflight; delivery requires SDK flush and viewer
review. If preflight fails, observation continues to file with explicit warning
and a non-success exit. Runtime flush failure stops observation and retains its
partial recording. The separate observer never blocks the optimizer callback.

- `generation` is producer time; `observation` distinguishes accepted snapshots
  and same-generation terminal updates. `adapter_elapsed` measures observer wall
  time, **not solver elapsed time**. Missed intermediate generations are recorded;
  polling cannot recover overwritten checkpoint versions.
- Same-generation updates require identical non-terminal checkpoint contents and
  a forward terminal transition. Final post-polish fields remain in terminal
  metadata; the best-objective chart still means pre-polish solver best.
- Stop on a validated `terminal.finalized` checkpoint; solver `success` is retained
  separately and is not inferred from observer completion. Timeout, cancellation,
  observation budget exhaustion and adapter failure produce non-success exits
  with explicit partial-recording outcome. This is not successful solver completion.
- Default timeout 600 s, permitted 1–86400 s; polling 20–5000 ms (default 100).
  Creating the cancel file stops gracefully. SIGKILL cannot finalize recordings.
  SDK transport flush has a separate 2 s bound; SDK teardown and filesystem I/O
  may add shutdown latency. No wall-time deadline guarantees filesystem response.
- At most 4096 accepted observations and 128 MiB cumulative serialized checkpoint
  input, with at most previous/current parsed snapshots retained. The per-file
  32 MiB bound also applies. Existing outputs are never overwritten.

Validation decodes actual SDK-written RRD messages and checks generation/clear
records, rejected malformed arrays and incompatible histories, source hashing,
overwrite preservation, atomic file following, sparse/same-generation terminal
updates, malformed-input retention, timeout and cancellation. Unit fixtures are
not actual solver runs; genuine campaign and viewer qualification are separate.

### Verified offline study — 2026-10-22

Ran AutoEQ DE against the existing measured room-response fixture
`autoeq/data_tests/roomeq/measured/2.0_8361a/L.csv`: two PEQ filters, 20–300 Hz,
nonpositive gains, population 48, evaluation budget 6000, seed 91827, serial
evaluation and absolute/relative tolerances `1e-12`. Both runs completed at
generation 103 with 4993 evaluations. The live observer recorded 53 updates.
Watched and unwatched complete checkpoint contents were identical after
excluding the outer timestamp, including RNG and terminal state. Rerun's CLI
verified the recording. The local web viewer displayed the live stream;
generation-axis linking, observed point markers and replay at generation 10
were reviewed. This qualifies the transport and this numerical trial, not an
acoustic improvement. AutoEQ separately warned of an APO integer-Hz export
objective drift of `0.010430`; no exported preset was installed.

Local artifacts, verification hashes, screenshot and retained executables live
under ignored `target/qa/rerun-live-20261022/`. Temporary compilation caches
were removed after verification. These artifacts can be regenerated; source
measurements remain unchanged.
