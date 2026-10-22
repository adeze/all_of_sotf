# Agent UI SDK pilots

These unpublished pilots evaluate two real SDKs without changing the SOTF shell,
enabling chat or executing calibration commands.

| Pilot | SDK | Boundary |
| --- | --- | --- |
| `swift/` | Official A2UI Swift, pinned source revision; protocol 0.9.1 | Native four-slider surface; emits a revision-bound proposal only |
| `rust/` | Published `ag-ui-client`/`ag-ui-core` 0.1.0 | Bounded loopback offline SSE; reads state and panel events |
| Python/browser companion | `ag-ui-protocol` 1.0.0, `@ag-ui/client` 1.0.3, A2UI React 0.14.0 | Separate optional presentation trials in Spatial Studio |

`fixtures/workbench.json` is synthetic, contains no room measurements, and matches
the existing Rust workbench snapshot shape. Swift proposals decode to the existing
`sotf-dev-api::WorkbenchRequest` shape. Host command validation remains authoritative;
neither SDK owns application state or executes arbitrary JSON patches.

The browser's existing target/sub-boost/tilt/curtain draft is not equivalent to
these Rust frequency/cut/boost limits. There is no implicit cross-language parameter
mapping. Shared commands and evidence contracts must be explicit before production
integration. The original GPUI controls remain native toolkit components.

See each pilot README for reproducible commands, immutable dependency pins and
qualification limits. The companion server and web documentation live in
`py-open-room-calibration/docs/AGENT_UI_PILOTS.md`. MCP Apps presents existing reads
and draft saves; AG-UI streams events; A2UI/json-render describe UI. They are not
interchangeable protocols. No global MCP configuration is modified by these pilots.
