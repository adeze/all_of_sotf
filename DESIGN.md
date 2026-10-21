# Design System: Adeze Acoustic Workbench

Stitch project: `14200360512738084340` (Open Room Calibration — Cross-platform UX).
Scope: the shared SOTF workbench design specification; existing application themes remain authoritative runtime values. This document specifies integration behavior, not a claim that every planned view is implemented.

## 1. Visual theme and atmosphere

A precise, calm audio workbench: neutral surfaces, one blue interaction accent, clearly labeled scientific plots and a persistent project context. Prefer light presentation for new concepts and retain the existing dark theme. Do not override a user's theme selection. Charts and room geometry are content; controls and navigation recede.

Use a restrained bento arrangement for related summaries and comparison panels. The main analysis plot remains larger than secondary cards. Use ordinary split panes for dense editing; avoid wrapping every field in another card. No decorative waveform, invented telemetry, engine-version badges, confidence percentages or device connection indicators.

## 2. Color palette and runtime ownership

Observed SOTF sources: `sotf/crates/app-gpui/app/theme/{light,black}.rs`. These concrete GPUI values document existing themes; they are not replacements for native Apple semantic colors.

| Role | SOTF light | SOTF dark |
| --- | --- | --- |
| Background | `#F5F5F5` | `#1E1E1E` |
| Secondary background | `#EEEEEE` | `#252525` |
| Surface | `#FFFFFF` | `#2D2D2D` |
| Hover surface | `#F0F0F0` | `#3E3E3E` |
| Selected surface | `#CCE5FF` | `#264F78` |
| Primary text | `#000000` | `#FFFFFF` |
| Muted text | `#444444` | `#BBBBBB` |
| Disabled text | `#767676` | `#888888` |
| Border | `#999999` | `#555555` |
| Interaction accent | `#0074E8` | `#0A84FF` |
| Focus border | `#0077EE` | `#1C8CD9` |

Resolve GPUI colors through the selected `Theme`; do not scatter copies into new screens. Native macOS/visionOS uses semantic foreground, background, accent, materials and text styles. A Stitch hex preview is illustrative, not a native color token. Respect increased contrast and reduced transparency.

Scientific colors have a separate meaning from interface state. Existing `plugin_palette.graph_colors` assigns input, target, filter response, corrected, error and deviation distinct roles. For example, light input is `#007BFF`, target `#28A745`, filter `#FFC107`, corrected `#0066CC`; dark input is `#6366F1`, target `#22C55E`, filter `#F59E0B`, corrected `#3B82F6`. Preserve those roles, adding trace labels and dash patterns where colors are similar. Do not recolor every trace to the UI accent.

Sequential quantities such as decay magnitude require a labeled sequential scale; signed pressure or residual requires a zero-centered diverging scale. Wrapped phase may use a cyclic scale; group delay is not cyclic. The toolkit includes surface/GPU colormap implementations, including Viridis; this does not establish that every chart uses one. Phosphor-inspired perceptual ordering is a design direction, not a claim of imported palette assets or validated uniformity. Keep scale limits identical for comparisons; show units and reference. Uncalibrated magnitude must not be called SPL. A color-map maximum does not imply clipping.

## 3. Typography

GPUI resolves font through `Theme::resolved_font_family`, falling back to `cx.design().typography.font_family`. Neutral `DesignSystem` uses `system-ui`, base 14, small 11 and large 18. Preserve chosen design-language and theme overrides. Native Apple applications use system text styles and Dynamic Type, not a web font replacement. Stitch may use Inter as a preview approximation only; it must not mandate Inter, Geist or IBM Plex Sans at runtime.

Use tabular numerals for changing readouts, explicit units for axes and values, short sentence-case labels and readable text at supported display sizes. Do not impose tiny uppercase labels or a fixed 24-pixel ceiling on native accessibility sizing.

## 4. Layout and spacing

Observed neutral `gpui-design::DesignSystem`: 4-point grid; 8-point control gap; 16-point section gap; 12-point card padding; 12 × 8 control padding; corner radii 4/8/12/16. The audio adapter inherits design-system geometry, uses boxed labels, dot knob indicators, no knob/meter glow and no meter gradient. These are the neutral baseline, not universal platform constants.

Persistent navigation leads to projects/measurements, analysis, calibration design, speaker design and listening/delivery capabilities. Reuse the existing SOTF shell rather than adding a second app shell. Maintain selected project, capture/channel and seat across views. A contextual inspector edits the selected object; it does not duplicate all controls everywhere.

Designer presents a vertical calibration pipeline beside the response or room canvas. Stage selection highlights corresponding controls and affected data. An optional graph representation reuses `WorkflowCanvas` for routing/navigation; it does not create another numerical executor. Collapse inspectors before squeezing plots; allow a single-column composition at narrow widths. Keep independent scrolling only for substantial panes, never nested scrolling inside small cards.

## 5. Components and interaction

Use existing GPUI controls, `PluginViewHost`, `ParamSpec`/`PluginLayout`, chart components and workflow canvas. One clearly named primary action per task. Read-only previews, proposals, computed results and installed device state must remain distinguishable through concise labels at the relevant action or plot; avoid a redundant provenance card occupying prime space.

Stage controls expose only implemented options. A lock retains a specified constraint through calculation; a bypass records an explicit stage choice. Changing inputs invalidates affected results. Show pending work, cancellation, validation errors and unavailable inputs where they occur. Preserve drafts across navigation. No successful status until the owning operation reports success.

Response controls include overlays, logarithmic frequency, units and implemented display smoothing; smoothing never changes authoritative evidence. Legends distinguish measured, simulated, target and corrected traces. Hover details complement accessible numerical summaries.

Native Apple chrome may use platform materials for navigation, toolbars and transient inspectors; chart surfaces stay quiet and readable. Vision Pro's primary room view is spatial geometry anchored to the room, with a compact companion panel. Retain a conventional accessible view when spatial data is absent. Utility motion should be brief and interruptible and respect reduced motion. Neutral GPUI currently uses 200 ms animation; Apple design-language settings own their own motion and target sizes.

## 6. Evidence and implementation boundaries

See `WORKBENCH_WORKFLOW.md` for workflow ownership and pending contracts. Local DSP A/B/ABX does not switch AVR presets. SQLite `music.db` is an existing music-library database, not an implemented acoustic project catalog. Speaker passive circuit prediction is not realtime crossover processing. Chat remains disabled during consolidation.

## 7. Stitch synchronization

Inspected 2026-10-22: project has 32 screen instances, including older dark concepts and reviewed light/native concepts. Project-level metadata still specifies dark teal/Geist; existing design-system asset `assets/7f275bce96cb407189fc3c180baf6366`, version 4 before this update, specifies light/IBM Plex Sans. These differ and must not be treated as one consistent runtime theme.

Update the existing asset's design specification only. Preserve existing screens/reference assets and avoid applying a theme to every screen. Keep light/dark and native/GPUI distinctions explicit. Remote synchronization status must be reported from the actual API response; a local document alone is not a remote update.

The first update changed preview tokens but retained earlier prose. One repair request, session `projects/14200360512738084340/sessions/16433057460368971732`, returned persisted asset version 6. Readback confirmed the submitted specification's native semantic typography/colors, `WorkflowCanvas` execution boundary, pending acoustic project catalog versus music-library `music.db`, and local DSP ABX versus AVR preset switching. The persisted specification explicitly prohibits fabricated telemetry; old MOTU/microphone/buffer examples were removed from the generated style guidelines. Stitch adds its own generated token front matter, so the remote Markdown is semantically synchronized rather than byte-identical. Light/Inter is only the Stitch preview, with `#0074E8` accent. No screen theme application was requested; existing screens remain historical concepts until separately reviewed.
