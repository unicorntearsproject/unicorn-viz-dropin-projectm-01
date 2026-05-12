# projectm-01 — ProjectM Effect

A Unicorn Viz drop-in that embeds libprojectM / MilkDrop-style presets inside
the Unicorn Viz playlist and transition pipeline.

## Phase 1 Scope

- Load libprojectM via `ctypes`
- Scan preset directories for `.milk` / `.prjm`
- Feed live PCM waveform data from Unicorn Viz into projectM
- Render projectM into the active Unicorn Viz framebuffer
- Support next / previous / random preset switching
- Fall back gracefully when libprojectM is unavailable

## Config

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

## Hotkeys

- `Ctrl+N` — next preset
- `Ctrl+P` — previous preset
- `Ctrl+R` — random preset

## Native Dependency

This drop-in expects a projectM 4 shared library to be installed separately.

Typical Linux library names searched:

- `libprojectM-4.so`
- `libprojectM.so`

You can also set an explicit path via `projectm_library`.

## Presets

Put preset files in `drop-ins/projectm-01/presets/` or point `preset_dir` /
`preset_dirs` at a preset pack checkout.

Supported extensions in Phase 1:

- `.milk`
- `.prjm`
