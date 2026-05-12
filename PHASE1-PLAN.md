# projectm-01 — Phase 1 Plan

## Goal

Integrate projectM as a first-class Unicorn Viz drop-in effect with a narrow,
Linux-first Phase 1 scope:

1. One embedded `ProjectMEffect` hosted inside the existing SDL2/moderngl GL context.
2. Preset directory scanning for `.milk` and `.prjm` files.
3. Manual preset switching from Unicorn Viz hotkeys.
4. PCM audio feed from Unicorn Viz `AudioData.waveform` into libprojectM.
5. Graceful fallback when the native library or presets are unavailable.

This phase explicitly does **not** include:

- cross-platform packaged dependency installation
- preset browser UI
- favorites / ratings / search
- playlist C-wrapper integration
- full preset metadata management

## Architecture Notes

### Why a drop-in

`unicornviz.dropins` already supports effect discovery from `drop-ins/*/*.py`, so
projectM fits the architecture as an optional external renderer without adding
special cases to the main app.

### Native API anchor

Phase 1 targets the projectM 4 C API via `ctypes`, specifically:

- `projectm_create_with_opengl_load_proc`
- `projectm_destroy`
- `projectm_set_window_size`
- `projectm_set_fps`
- `projectm_set_frame_time`
- `projectm_set_beat_sensitivity`
- `projectm_set_preset_duration`
- `projectm_set_preset_locked`
- `projectm_set_preset_start_clean`
- `projectm_set_texture_search_paths`
- `projectm_load_preset_file`
- `projectm_pcm_get_max_samples`
- `projectm_pcm_add_float`
- `projectm_opengl_render_frame_fbo`

The upstream SDL sample creates the instance with `SDL_GL_GetProcAddress`, which
maps cleanly to Unicorn Viz because SDL2 already owns the GL context.

### Rendering path

Unicorn Viz effects must render into the **currently bound** framebuffer so they
participate in transitions. That means the drop-in should prefer
`projectm_opengl_render_frame_fbo(current_fbo)` over implicit default-framebuffer
rendering.

### Audio path

Unicorn Viz already computes FFT and beat, but projectM expects PCM samples.
Phase 1 feeds the mono `AudioData.waveform` buffer into `projectm_pcm_add_float`
every frame. If projectM wants stereo, mono duplication is handled by the
library for `PROJECTM_MONO` input.

### Preset management

Phase 1 uses Unicorn Viz-side preset scanning and switching instead of the
projectM playlist wrapper. That keeps the bridge smaller and gives us explicit
control over next/prev/random preset hotkeys.

### Fallback behavior

If libprojectM cannot be loaded, or GL initialization fails, or no preset pack
is present, the effect falls back to a lightweight internal shader so the app
never goes blank or crashes because projectM is missing.

## Config Surface (Phase 1)

```toml
[effects.ProjectMEffect]
speed = 1.0
preset_duration = 20.0
smooth_transition = true
lock_preset = false
start_clean = false
beat_sensitivity = 1.0
fps_hint = 60
projectm_library = ""       # optional explicit .so/.dll path
preset_dir = ""             # single override dir
preset_dirs = ""            # comma-separated extra preset dirs
texture_dirs = ""           # comma-separated texture search dirs
start_preset = ""           # optional exact preset filename/path
```

## Hotkeys (Phase 1)

When `ProjectMEffect` is active:

- `Ctrl+N` — next preset
- `Ctrl+P` — previous preset
- `Ctrl+R` — random preset

These are implemented as effect-local hotkeys in Unicorn Viz and reused by
other effects that expose compatible methods.

## Known Risks

1. projectM shared library naming differs across distros/platforms.
2. Some presets require texture packs; missing textures may cause load failures.
3. projectM GL state may still need cleanup/guarding if it dirties state in a
   way that affects subsequent overlay rendering.
4. Windows packaging is intentionally deferred beyond Phase 1.

## Notes About Repo Policy

The long-term repo policy says each drop-in should live in its own private repo
and be mounted as a submodule. In this environment, no GitHub repo-creation API
is available, so this work is scaffolded locally inside `drop-ins/projectm-01`
first.

## Phase 1 Deliverables

1. `projectm_effect.py` native bridge scaffold with fallback renderer
2. `README.md` usage/install notes
3. `__init__.py` export
4. Config/docs entries
5. Effect-local preset hotkeys wired in `HotkeyHandler`
