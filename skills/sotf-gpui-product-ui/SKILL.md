---
name: sotf-gpui-product-ui
description: "Use for SOTF GPUI product UI work: app-gpui screens, gpui-toolkit components, RoomEQ wizard UI, plugin panels, accordions, steppers, remote server manager, layout solvers, theming, responsiveness, and UI compile checks."
---

# SOTF GPUI Product UI

## When To Use

Use this skill for `crates/app-gpui`, `crates/gpui-toolkit`, plugin panels rendered in GPUI, RoomEQ wizard screens, remote server UI, theme/layout fixes, and GPUI compile issues.

## Working Sequence

1. Use TokenSave to locate the screen/component/state surface.
2. Read GPUI-specific project guidance before editing GPUI code when present.
3. Put business logic in `sotf-player`, `autoeq`, or relevant math crates; keep app crates thin.
4. Follow existing component and design-system patterns before adding abstractions.
5. Verify compile and targeted UI tests for the edited crate.

## UI Expectations

- Prefer dense, utilitarian product UI over marketing-style layout.
- Use existing toolkit components, icons, spacing, theming, and layout solvers.
- Keep cards for repeated items or framed tools; avoid cards inside cards.
- Make fixed-format controls stable with explicit sizing.
- Check responsive behavior for long labels, plugin parameter names, and wizard panels.

## References

- Read `references/gpui-checklist.md` for common files, commands, and review focus.
- Read `references/gpui-workbench-panels.md` before changing embedded Designer/speaker panels, entity lifetimes, typed commands, theme inheritance or canvas interaction.
