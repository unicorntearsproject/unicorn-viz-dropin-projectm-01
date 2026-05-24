# projectm-01 Troubleshooting

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-05-24

## Purpose

Troubleshooting guide for native projectM integration and preset playback.

## Common Issues

### ProjectM effect shows fallback visuals only

- Confirm libprojectM is installed.
- Set `projectm_library` explicitly to the shared-library path.

### No presets are available

- Verify `preset_dir`/`preset_dirs` path contents.
- Confirm files use supported extensions (`.milk`, `.prjm`).

### Preset switching appears stalled

- Check `lock_preset` setting.
- Use `Ctrl+N`/`Ctrl+P` to confirm manual preset change works.

### Performance instability

- Lower resolution scale or reduce overall post-FX load.
- Keep `fps_hint` realistic for the target hardware.

## Related Docs

- ../README.md
- operations.md
- configuration.md
- integration.md
