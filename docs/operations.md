# projectm-01 Operations

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-05-24

## Purpose

Runbook for operating ProjectM preset playback in Unicorn Viz.

## Startup Checklist

1. Ensure libprojectM shared library is installed or `projectm_library` is set.
2. Ensure preset paths contain `.milk` or `.prjm` files.
3. Start Unicorn Viz and switch to ProjectM effect.
4. Verify preset navigation works with hotkeys.

## Live Controls

- `Ctrl+N`: next preset.
- `Ctrl+P`: previous preset.
- `Ctrl+R`: random preset.

## Runtime Notes

- Effect falls back to internal shader if native projectM load fails.
- Preset switching cadence depends on `preset_duration` and manual control.

## Related Docs

- ../README.md
- configuration.md
- integration.md
- troubleshooting.md
