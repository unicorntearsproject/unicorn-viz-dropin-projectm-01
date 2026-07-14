# projectm-01 — ProjectM Effect

**Version 0.9.0**


A Unicorn Viz drop-in that embeds libprojectM / MilkDrop-style presets inside
the Unicorn Viz playlist and transition pipeline.

## Phase 1 Scope

- Load libprojectM via `ctypes`
- Scan preset directories for `.milk` / `.prjm`
- Build a preset catalog with inferred category paths from folder layout
- Support manager-side preset state (`enabled` / `disabled` / deleted)
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

Preset packs are distributed as individual git repositories. The drop-in keeps
them under `presets/`, one cloned repo per subdirectory, and recursively scans
that tree for preset files. The `presets/` directory is git-ignored on purpose:
packs are large and fetched on demand rather than committed here.

```
drop-ins/projectm-01/presets/
  presets-cream-of-the-crop/      # ~9,800 .milk presets
  presets-milkdrop-original/      # ~550 .milk presets
  presets-milkdrop-texture-pack/  # textures referenced by presets
```

### Fetching / updating packs

Use the bundled helper (clones the curated default set, updates any already
present — safe to re-run):

```bash
bash drop-ins/projectm-01/fetch_presets.sh
```

Add extra packs by URL (also cloned into `presets/`):

```bash
bash drop-ins/projectm-01/fetch_presets.sh https://github.com/some/other-pack.git
```

To make a pack part of the default set, add its URL to `DEFAULT_PACKS` near the
top of `fetch_presets.sh`. List the current default set with `--list`.

You can also just `git clone` any pack repo into `presets/` by hand — the
scanner picks it up automatically on next launch.

Supported preset extensions in Phase 1:

- `.milk`
- `.prjm`

### Textures

Many presets reference shared texture images. Point `texture_dirs` at the
texture pack's inner `textures/` folder so they resolve:

```toml
texture_dirs = "drop-ins/projectm-01/presets/presets-milkdrop-texture-pack/textures"
```

---

## Structured Docs

- docs/planning.md
- docs/operations.md
- docs/configuration.md
- docs/integration.md
- docs/troubleshooting.md

## Manager Backend Status

The projectM manager backend is implemented in `projectm_effect.py` and
currently provides:

- category inference from preset paths
- all/none/category enable-disable operations
- selected preset enable-disable operations
- single and bulk delete operations with a local trash folder

The modal browser UI and hotkey wiring for interactive browsing are tracked in
`docs/planning.md` and are intentionally staged separately.

## Changelog

- **0.9.0** — initial versioned release.
