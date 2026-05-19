# projectm-01 Phase 1 Plan

## Scope

Phase 1 targets a Linux-first, drop-in implementation for embedding libprojectM presets in Unicorn Viz.

Included:

- `ProjectMEffect` drop-in discovered by existing drop-in loader.
- Preset scanning for `.milk` and `.prjm` files.
- Runtime preset switching (`Ctrl+N`, `Ctrl+P`, `Ctrl+R`).
- PCM waveform feed from Unicorn Viz audio into projectM.
- Graceful fallback shader when native library/presets are unavailable.
- Linux helper scripts for dependency checks and install notes.

Excluded from Phase 1:

- Windows packaging and bundled native runtime.
- Preset browser UI/search/favorites.
- Full projectM playlist C-wrapper integration.

## Technical Notes

- projectM is integrated through C API calls with `ctypes`.
- Rendering prefers `projectm_opengl_render_frame_fbo` to respect Unicorn Viz transition FBO pipeline.
- OpenGL function resolving uses `SDL_GL_GetProcAddress` callback.
- Preset pack is external content and loaded from configured directories.

## Config Surface

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

## Risks

- Shared library naming/path differs by distro.
- Some presets depend on texture packs.
- projectM OpenGL state could require additional restoration in edge cases.

## Status Sync (May 12, 2026)

Phase 1 is complete and operational.

Phase 2 is now implemented; see `PHASE2-PLAN.md` for details. Highlights:

- Pack-aware preset catalog + pack switching hotkeys (`Ctrl+Shift+N/P/R`)
- Improved preset randomization (shuffle-cycle + recency guard)
- Auto texture-pack search path support for MilkDrop texture assets
- HUD/help now display both preset index and active variant/pack line

Installed preset packs under `drop-ins/projectm-01/presets/`:

- `presets-cream-of-the-crop`
- `presets-milkdrop-original`
- `presets-milkdrop-texture-pack`

Also present from prior setup:

- `presets-projectm-classic`
