# projectm-01 Configuration

Owner: Drop-in Maintainers
Status: active
Last updated: 2026-05-24

## Purpose

Reference for ProjectM effect runtime and preset-loading keys.

## Config Block

```toml
[effects.ProjectMEffect]
speed = 1.0
preset_duration = 20.0
smooth_transition = true
lock_preset = false
start_clean = false
beat_sensitivity = 1.0
fps_hint = 60
projectm_library = ""
preset_dir = ""
preset_dirs = "drop-ins/projectm-01/presets"
texture_dirs = ""
start_preset = ""
```

## Notes

- Use `projectm_library` for explicit shared-library path when autodiscovery fails.
- Use `preset_dir`/`preset_dirs` to point at external preset packs.
- Keep `fps_hint` aligned with show target for smoother motion.

## Related Docs

- ../README.md
- operations.md
- integration.md
- troubleshooting.md
