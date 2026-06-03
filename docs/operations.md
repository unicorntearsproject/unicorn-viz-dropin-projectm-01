# projectm-01 Operations

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-06-03

## Purpose

Runbook for operating ProjectM preset playback in Unicorn Viz.

## Startup Checklist

1. Ensure libprojectM shared library is installed or `projectm_library` is set.
2. Ensure preset paths contain `.milk` or `.prjm` files.
3. Start Unicorn Viz and switch to ProjectM effect.
4. Verify preset navigation works with hotkeys.

## Live Controls

- `Ctrl+M`: open projectM preset manager.
- `Ctrl+N`: next preset.
- `Ctrl+P`: previous preset.
- `Ctrl+R`: random preset.

### Preset Manager Controls

- `Tab` or `Left/Right`: switch between category and preset panes.
- `Up/Down`: move selection in the active pane.
- `Enter`: load highlighted preset.
- `E` / `D`: enable or disable selected category/preset.
- `I`: isolate selected category.
- `Ctrl+A`: enable all presets.
- `Ctrl+Shift+A`: disable all presets.
- `Delete`: move selected preset to `preset-trash/`.
- `Shift+Delete`: permanently delete selected preset.
- `Esc`: close the manager.

## Runtime Notes

- Effect falls back to internal shader if native projectM load fails.
- Preset switching cadence depends on `preset_duration` and manual control.
- Disabled presets remain visible in the manager with an `OFF` status flag.
- Soft deletes quarantine files under `drop-ins/projectm-01/preset-trash/`.

## Related Docs

- ../README.md
- configuration.md
- integration.md
- troubleshooting.md
