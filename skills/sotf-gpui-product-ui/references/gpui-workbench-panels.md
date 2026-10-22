# GPUI workbench panels in the Adeze fork

Verified against local sources on 2026-10-22. Recheck signatures and feature paths when dependencies change. This is the project's GPUI specialist reference, not a substitute for crate `AGENTS.md` files.

## Ownership and dependency direction

- `sotf-3rdparties/gpui/`: forked GPUI core, platform backends and scheduler. SOTF's `gpui` dependency is package `sotf-gpui-toolkit-gpui`, not an interchangeable crates.io GPUI release. Umbrella `.cargo/config.toml` selects local sources.
- `gpui-toolkit/crates/gpui-ui-kit/`: reusable controls, workflow canvas, focus and accessibility. `gpui-design` owns platform-adaptive design rules; `gpui-builder` owns constraint-based layout; `gpui-d3rs`/`gpui-px` own plotting primitives.
- `gpui-toolkit/crates/gpui-audio-kit/`: reusable potentiometers, faders, volume controls, meters, spectra and audio-axis helpers. These APIs are intentionally not re-exported by `gpui-ui-kit`. Put general audio-control improvements here; keep calibration-specific meaning in the workbench.
- `sotf/crates/app-gpui/`: native shell, selected application state, event routing and platform glue. Preserve its navigation, menus and startup behavior when modifying a workbench panel.
- `sotf-player` and AutoEQ own Room EQ configuration, validation and execution. A workbench plugin is a build-linked UI component, not a new numerical authority or a realtime DSP plugin.
- `sotf-calibration-workbench` owns the isolated Designer presentation; `sotf-speaker-workbench` embeds Sonium's passive editor and explicit active-crossover preview. Sonium retains its project/physics ownership.

Use the local toolkit catalog/showcase or source signatures. Upstream GPUI examples may describe different package names, callback types or platform APIs.

Reusable graph interaction belongs in `gpui-ui-kit::workflow`; reusable chart primitives belong in `gpui-d3rs`/`gpui-px`. A workbench supplies typed scientific values, units and provenance; these rendering crates do not become another acoustic solver. Existing DSP-plugin UI adapters live in `sotf-daw/crates/sotf-plugins/crates/plugins-gpui`: `PluginViewHost` abstracts parameter access and UI state across the player and Audio Unit hosts. Reuse that abstraction for DSP-plugin controls rather than coupling them to the application shell; the calibration workbench uses its separate typed command/snapshot boundary.

## Entities, render props and events

Keep long-lived editor state in an `Entity<T>` retained by the host. Create it once and mount the same entity across tab changes. Rebuilding an editor in `render()` loses selection, viewport and draft state. See the host adapter in `sotf/crates/app-gpui/components/room_eq/pipeline_graph.rs` and speaker `Workbench::designer`.

`Render::render` produces the current element tree. Many toolkit controls use `RenderOnce` props backed by keyed internal entities or thread-local state. Stable element IDs matter: `TabsEntity` is cached by ID, retaining its focus handle and hover state (`gpui-ui-kit/src/tabs.rs`). Do not create another focus-management layer to compensate for ephemeral builder props.

Use `EventEmitter<DesignerCommand>` for typed UI intentions. The host subscribes through `cx.subscribe`, maps events into existing Room EQ commands and publishes state updates with `cx.notify()`. A small `DesignerSnapshot` is a derived view of that state; it must not retain cloned measurement arrays or become an independent configuration store.

Observers and subscriptions need a retained subscription or the established `.detach()` lifetime pattern. Observe only changes whose meaning matches the action: a view-selection notification is not proof that the scientific project changed. Background calculations capture a generation/revision and reject stale completion before publishing results.

Current toolkit callback signatures differ:

| Control | Callback |
| --- | --- |
| `Button::on_click` | `Fn(&mut Window, &mut App)` |
| `Button::on_click_event` | Event-aware form; inspect its local signature |
| `Tabs::on_change` | `Fn(usize, &mut Window, &mut App)` |
| `NumberInput::on_change` | `Fn(f64, &mut Window, &mut App)` |

Copy an active local call site, rather than adding or removing callback arguments by guesswork.

## Theme and design rules

`PlayerView::render` bridges the selected app theme into toolkit `ThemeState` through `Theme::to_ui_kit_theme` (`sotf/crates/app-gpui/ui/render.rs`). Embedded components can use `ThemeExt::theme()`; its result is a cheap `Arc<Theme>`. Do not initialize a separate global theme inside a plugin.

Use semantic colors and toolkit text constructors (`section_header`, `body`, `label`, `caption`). Application-owned layouts use `Ds::from_cx(cx)` for scalable spacing/typography. A reusable plugin should consume the active toolkit design rules without depending back on app-gpui merely to import `Ds`.

Preserve the user's theme, design language and font zoom. Fixed numeric-control widths can be intentional for stable editing, but must be reviewed at narrow widths and larger font scales. Scientific plot colors have separate meanings from interaction accents.

## Layout, scrolling and keyboard input

For a bounded editor pane, use the existing flex pattern: a parent with a definite available size, `flex_col()`, a nonshrinking tab/header row, then `flex_1().min_h_0().min_w_0()` for the content. The substantial content pane owns scrolling. A full-height child cannot create a height constraint that its parent does not provide.

Room EQ already wraps steps through `render_workflow_shell` (`app-gpui/components/workflow.rs`). Check that scroll ownership before adding another scroller. Speaker tabs retain one full-height Sonium entity or a separately scrollable active pane. Do not force both editors into one compressed footer.

Use the toolkit's accessible Tabs and controls before hand-built clickable text. Tabs already handle focused Left/Right/Home/End keys. NumberInput editing relies on the application's `TextInput` key context and `is_number_input_editing()` bypass; player action bindings otherwise consume typed keys. Do not wrap inputs in parent `on_key_down` handlers that stop propagation: this fork dispatches those before the focused child's handler. See the detailed NumberInput section of `gpui-ui-kit/AGENTS.md`.

## Workflow canvas limits

`WorkflowCanvas` is a presentation/interaction component. Its graph does not execute calibration jobs. The Designer projects `PipelineStepId::ALL` and actual stage history; it must not imply that dragging a stage rewires AutoEQ execution.

The Designer opts into `set_compact_nodes(true)` to omit repeated default body titles. Custom node content remains visible; other canvases retain their existing defaults.

The current `set_read_only(true)` path permits node selection and wheel zoom while blocking drag/edit, context menus and keyboard mutations. It does **not** establish usable panning: `handle_mouse_down` returns early, and the renderer binds only left/right buttons. The presence of an `InteractionMode::Panning` match arm is not proof that an input binding reaches it.

The Designer's explicit **Fit diagram** action calls `WorkflowCanvas::fit_to_view` using the actual last-painted viewport bounds. It leaves user zoom unchanged during ordinary snapshot refreshes. The method rejects invalid dimensions and graphs that cannot fit at the supported minimum zoom; it is not a generic panning implementation. Check narrow-window coverage before promising pan/zoom navigation.

Programmatic graph mutation APIs remain available; read-only is an input policy, not an immutable graph type. Keep the canonical graph owner separate from UI snapshots.

## Focused verification

Run tests in the owning workspace. Cargo will not run dev-dependency tests for a dependency-only package selected from the umbrella.

```sh
cargo test --manifest-path gpui-toolkit/Cargo.toml -p gpui-ui-kit --lib read_only_canvas_selects_without_editing --locked
cargo test --manifest-path sotf/Cargo.toml -p sotf-calibration-workbench --features desktop --locked
cargo test --manifest-path sotf/Cargo.toml -p sotf-gpui --test room_eq_config_tests --features calibration-designer --locked
uv run --no-project python sotf/scripts/check-design-tokens.py
```

Verify the current checkout's exact manifest/features before using these recipes. A locked-resolution failure needs a deliberate local-source lock review, not a blind dependency upgrade.

For visual checks, use the current development app and isolated QA state. Confirm its executable/revision rather than inspecting an older installed app with the same window title. Check Parameters/Designer and Passive/Active tabs, selection retention, typed number editing, focus order, narrow layout, font zoom and light/dark contrast. Do not trigger receiver writes or live acquisition as part of panel review.

For reusable toolkit changes, use the component-lab stories and the capture inventories exposed by `gpui_audio_kit::audio_visual_regression_manifest()` and `gpui_px::chart_visual_regression_manifest()`. `audio_automation_pattern_report()` describes automation-control patterns. These are verification resources, not evidence that captures or accessibility checks have already passed.

Arbitrary graph execution/reordering, dynamic plugin hot-loading, native Denon acquisition inside Rust and cross-language UI embedding are not established by these panel components. Report implemented behavior and remaining limits separately.
