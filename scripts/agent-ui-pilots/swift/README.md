# A2UI Swift Designer pilot

An isolated macOS executable using the **official A2UI Swift parser, strict runtime,
Basic Catalog and SwiftUI renderer**. It reads the shared synthetic Workbench snapshot,
renders four bound sliders, and previews a revision-bound `update_designer` request.
It does not execute that request or connect to any model, receiver or application server.
Chat remains disabled.

## Run

From this directory, with Swift 6.4 and macOS 15 or newer:

```sh
sh bootstrap.sh
swift test --filter DesignerPilotTests --jobs 4
swift run --skip-build a2ui-designer-pilot --fixture ../fixtures/workbench.json --verify
swift run --skip-build a2ui-designer-pilot --fixture ../fixtures/workbench.json
```

For a native SwiftUI snapshot without starting the window event loop:

```sh
swift run --skip-build a2ui-designer-pilot --fixture ../fixtures/workbench.json --snapshot artifacts/designer.png
```

Snapshot creation refuses to overwrite an existing file. Use another destination on
subsequent runs. `--verify` prints the proposed request to stdout; it does not write
the fixture. In the window, changing a slider updates the SDK-owned draft. The
preview button displays its JSON. The host must still validate `expected_revision`
and every parameter before accepting a proposal.

## Dependency and boundaries

- Official source: [a2ui-project/a2ui](https://github.com/a2ui-project/a2ui).
- SDK revision: `c4e0e069730eb265d7f6e81443a8394fa80ac3ad`.
- Archive SHA-256: `73c01fc978f75dd220bc5b2eeaa3071b09213b609171f4785f91e73b62b2c4d0`.
- Protocol/catalog: `v0.9.1`; no claim of v1.0 interoperability.
- Bootstrap downloads and verifies the archive, extracts only the SDK, schemas,
  catalogs and license, and removes the archive. `.sdk/` and `.build/` are ignored
  disposable caches. Transitive package versions are recorded in `Package.resolved`.
- Only fixed `Text`, `Column` and four `Slider` components are constructed. This
  executable accepts a snapshot, not arbitrary generated component documents.
- Four finite numeric limits are validated, including minimum frequency below maximum.
  Unknown components are rejected by strict SDK validation. Local validation does not
  replace Rust Workbench validation or stale-revision rejection.
- No production Swift app, Rust host, Swift–Rust ABI or shell navigation is changed.

## Qualification

Targeted qualification uses Apple Swift 6.4 on arm64 macOS 27. Tests exercise actual
SDK parsing, component schemas, JSON-pointer bindings, proposal serialization and
invalid draft/component rejection. Native rendering is checked separately through
an AppKit-hosted SwiftUI view capture; this is not an interactive accessibility or
full application review. SwiftUI `ImageRenderer` cannot capture the SDK's native
AppKit-backed sliders, so the snapshot path uses `NSHostingView.cacheDisplay`.
The executable is macOS-only; the SDK advertises additional Apple platforms, but
this pilot has not been built or tested on visionOS, iOS or a simulator.

The pinned SDK emits existing icon-cast and deprecated `onChange` warnings. No vendor
patch is applied. This pilot evaluates integration feasibility, not production readiness.
