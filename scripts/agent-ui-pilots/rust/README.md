# Rust AG-UI client pilot

This standalone, unpublished package uses the actual crates.io `ag-ui-client = 0.1.0`
and `ag-ui-core = 0.1.0`. It is intentionally outside the generated umbrella
workspace. Its `sotf-dev-api` path dependency imports the existing
`WorkbenchRequest` contract; it does not depend on the player, DSP or GPUI shell.

Run from the umbrella root, using the stable toolchain:

```sh
CARGO_TARGET_DIR=target CARGO_BUILD_JOBS=4 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0 rustup run stable cargo test --manifest-path scripts/agent-ui-pilots/rust/Cargo.toml --locked
CARGO_TARGET_DIR=target CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=0 rustup run stable cargo run --manifest-path scripts/agent-ui-pilots/rust/Cargo.toml --locked
```

The default executable creates a one-request ephemeral loopback fixture server.
The real SDK posts a standard `RunAgentInput` containing
`../fixtures/workbench.json`, processes lifecycle/state/custom-panel SSE events,
and prints a read-only presentation report. No chat messages, model calls,
filter generation, receiver operation or study execution occur.

To exercise the separately enabled Python adapter instead, pass its loopback URL:

```sh
CARGO_TARGET_DIR=target rustup run stable cargo run --manifest-path scripts/agent-ui-pilots/rust/Cargo.toml --locked -- http://127.0.0.1:8000/__roomcal/agent-ui/offline
```

Only that exact offline route on a literal loopback IP is accepted, without query
parameters or credentials. The Python route requires `ROOMCAL_AGENT_UI_PILOT=1`; this executable supplies the
fixture through the SDK's real `RunAgentParams.state` field. It sends no path or
arbitrary command. The `roomcal.designer-panel` event carries the same revision-bound
fixture in a versioned, read-only envelope. Only replacement of `/study/message`
is accepted as a state delta. The display projection and SDK state are not another
application configuration owner.

`command_proposal` decodes canonical supported commands and rejects stale revisions
and unknown operations/parameters. It **returns** a proposal, never posts or executes
one. Numerical constraints, current run state and measurement eligibility remain
the existing host command handler's responsibility. Proposed edits require explicit
host dispatch; agent JSON patches cannot mutate the Designer.

## Qualification boundary

- Two grouped tests cover real loopback SDK SSE processing, lifecycle completion,
  read-only delta rejection, loopback-only URLs, supported command decoding and
  stale/unsupported proposal rejection.
- Published 0.1.0 differs from the upstream `main` README/API. Import
  `RunAgentParams` from `ag_ui_client::agent`; the published crate does not re-export
  `ag_ui_core`, so it is pinned explicitly. SDK state/messages are held by a run's
  result rather than a persistent application store. The published run method
  unwraps `forwarded_props`; supply `Some(json!({}))` explicitly even though that
  field is optional in `RunAgentParams`.
- This is not a production transport qualification: the published SDK does not
  check unsuccessful HTTP status before parsing SSE, does not expose a pre-parse
  stream-buffer limit, and decodes each network chunk as complete UTF-8. Our event
  checks run after decoding; a five-second client timeout, outer run timeout,
  loopback-only URL and disabled redirects/proxies constrain this trusted fixture
  exercise. Resolve those SDK limitations before accepting untrusted agent streams
  or promising arbitrary Unicode chunk handling.
- GPUI binding, normal native startup, remote model integration, reconnection,
  cancellation/resume and hardware behavior are not established by this pilot.

Primary sources inspected: [published client crate](https://crates.io/crates/ag-ui-client/0.1.0),
[upstream Rust SDK](https://github.com/ag-ui-protocol/ag-ui/tree/main/sdks/community/rust),
and [AG-UI introduction](https://docs.ag-ui.com/introduction).
