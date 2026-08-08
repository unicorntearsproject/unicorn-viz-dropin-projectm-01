# projectm-01 — ProjectM Effect

**Version 0.10.2**


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

- **0.10.2** — stop rebuilding the preset catalog from disk on every state
  change. Disabling or trashing a preset from a hotkey re-walked all ~10,000
  preset files and called `Path.resolve()` on each one, synchronously inside
  event handling — measured as an **11-second full-app freeze** twice in one
  session on the owner's rig, under concurrent OBS record+stream disk load.
  Nothing on disk changes when a preset is disabled, so the walk is cached and
  the catalog is rebuilt by re-applying filters in memory; the resolved path
  from the cached scan is reused for the dark-exclusion key instead of
  re-resolving per file. Locally 0.82 s -> 0.27 s, and the disk walk is gone
  from the hotkey path entirely. `rescan_preset_files()` forces a fresh walk
  when the presets on disk really did change.

- **0.10.1** — hold the timer-driven preset advance while a scene crossfade
  is running. `projectm_load_preset_file` compiles the preset's shaders
  synchronously on the render thread, and during a blend the app is already
  rendering two effects every frame, so a compile landing there is what turns
  a transition into a visible hitch. The preset timer keeps accumulating, so
  the switch happens as soon as the blend finishes — at most a second or two
  later on a 20 s preset duration. Uses the new `BaseEffect.transition_active`
  flag (core 1.0.0-beta.55).

- **0.10.0** — dark-exclusion overhaul: the list was mostly not working, so
  presets already known to be black were replayed, each costing a fresh
  `dark_skip_after` of dead screen.

  - **Exclusion keys are now relative to this drop-in.** They were absolute
    paths, which silently matched nothing in any checkout but the one that
    wrote them — the whole accumulated list went dead when the repo was used
    from a second location, and every known-dark preset had to be re-earned
    one black screen at a time. Legacy absolute entries are rebased onto the
    local drop-in (from any clone) and de-duplicated on first read, so nothing
    already learned is thrown away.
  - **Runtime exclusions now evict from the process-wide catalog cache.**
    `_init` rebuilds the playable list from `_catalog_cache` on every effect
    switch, but the dark detector only popped the instance-local list — so
    everything excluded during a session came straight back on the next
    switch to ProjectM, replayed its black screen, and appended another
    duplicate line. (That was the source of the duplicate entries in the
    list.)
  - **`dark_skip_after` default cut from 3.0s to 0.2s.** This is a VJ
    backdrop, not a MilkDrop viewer; a multi-second warm-up reads as a dead
    screen, so slow-building presets are now retired quickly rather than
    tolerated.
  - **Wider dark sampling at the same cost.** The detector sampled a single
    100×100 block dead centre — 0.48% of a 1080p frame, 0.12% at 4K, so it
    got *more* trigger-happy the higher the resolution and mistook
    edge-active presets for black ones. It now averages five 48×48 blocks
    spread across the frame for ~the same bytes read. Still at most one check
    per preset, never per frame, and only while ProjectM is the active
    effect.
  - **Added `preset_warmup_frames`** (default 0, off): renders that many
    throwaway frames right after a preset loads, before the first visible
    frame. A preset's warm-up is its feedback texture filling in over
    rendered frames, not a time ramp — the effect already reports a large
    randomized frame time, so only actually running frames arrives pre-warmed.
    Raise it if slow-building presets you want to keep are being excluded.

- **0.9.0** — initial versioned release.
