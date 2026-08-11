# projectm-01 — ProjectM Effect

**Version 0.12.0**


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

### Excluded / Quarantined / Disabled (manager categories)

Three additional categories appear in the preset manager (`Ctrl+M`) — each
only when it actually has something in it — visually set apart from real,
path-derived categories (a leading `!` and a distinct accent colour), but
reachable the same way: select them like any other category.

- **`! Excluded`** — every preset currently listed in
  `excluded_presets.txt` (auto-excluded by the solid-color detector, see
  above). These files are still sitting in `presets/`, just skipped by the
  scanner.
- **`! Quarantined`** — presets moved out of `presets/` into `quarantine/`
  (a sibling directory, git-ignored, mirroring the same relative path
  structure — `presets/x/y.milk` becomes `quarantine/x/y.milk`).
- **`! Disabled`** — every currently-disabled preset that's still on disk,
  gathered into one place instead of having to hunt through each real
  category. This is an additional view, not a move — a disabled preset
  still appears in its normal category too.

The full excluded → quarantined → gone workflow is entirely manual, one
preset at a time, from inside the manager: select a preset in `! Excluded`
and press `Q` to move it to `! Quarantined`; from there, `R` restores it to
its exact original path, or `Delete` removes it permanently — no trash, no
confirmation, since anything reaching quarantine was already reviewed once
by definition. See `HELP_ENTRIES` in `projectm_effect.py` (surfaced in the
app's own `H` help overlay) for the exact key bindings — this file doesn't
duplicate them so they can't drift out of sync.

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

- **0.12.0** — Preset manager gains three special categories (only shown
  when non-empty): `! Excluded` (everything in `excluded_presets.txt`),
  `! Quarantined` (presets moved out of `presets/` into a new `quarantine/`
  sibling directory, preserving relative path structure), and `! Disabled`
  (every disabled-but-present preset, gathered into one view). New actions:
  `Q` moves a selected Excluded preset to quarantine and drops its stale
  exclusion-list entry; `R` restores a Quarantined preset to its exact
  original path; `Delete` on a Quarantined preset permanently deletes it —
  no trash, no confirmation, since anything there was already reviewed
  once by definition. `E`/`D` (enable/disable) are suppressed on Excluded/
  Quarantined entries, which aren't part of the normal rotation.

  New methods: `quarantine_preset()`, `restore_from_quarantine()`,
  `delete_quarantined_preset()`, `_remove_excluded_preset_entry()`.
  Quarantine reuses `_deleted_presets` (the same set `delete_presets()`
  uses) to stop a stale cached filesystem-scan entry from resurrecting a
  moved-away preset, the same mechanism the soft-delete trash already
  relied on — no new bookkeeping concept, no expensive full rescan except
  on restore (where one's actually needed, since the cache has no record
  of a file at that path at all).

  `preset_catalog()` now appends synthetic entries for the three sections;
  `unicornviz/catalog_browser.py` (the shared model behind both this
  manager and the effects browser) gained a small, generically useful
  `HIDDEN_FROM_ALL_PREFIX` convention (categories starting with `!` are
  reachable by selecting them directly but excluded from the `(all)`
  catch-all view and its counts) so the new categories don't inflate or
  duplicate into the "(all)" tally — reused, not projectM-specific.

  32 new regression tests across
  `drop-ins/projectm-01/tests/test_quarantine.py` (real filesystem moves
  against a tmp_path tree — 16 tests),
  `tests/test_catalog_browser.py` (5 new tests for the hidden-from-all
  convention), and `tests/test_hotkeys_projectm_manager.py` (6 new tests
  for the Q/R/Delete key routing). Caught and fixed a real bug before
  shipping: an early version of the Disabled-section builder appended to
  the same list it was iterating, re-visiting (and infinitely
  re-duplicating) each newly appended copy since those are disabled too.

- **0.11.0** — Replaces the dark-only, once-per-interval check with a
  continuous **solid-color** detector: any frame that reads as
  functionally one flat color — any color, not just black — counts
  toward a running streak, and the preset is permanently excluded once
  that streak crosses `solid_color_duration` (default 0.2s). The streak
  resets to zero the instant a frame isn't flat, so a preset that goes
  solid black, recovers, then goes solid blue two seconds later still
  gets caught on either span independently — they don't need to match or
  connect. No warm-up grace period: a preset that opens on a flat color
  and takes its time waking up reads exactly as bad on a live stream as
  one that never recovers, so both are caught identically.

  What actually distinguishes "solid" from "lively" is variance, not
  brightness — a solid magenta or solid white frame has *high* average
  brightness, so the old mean-brightness check could never have caught
  it. The five 48×48 sample blocks (unchanged) are now pooled into one
  combined set and their standard deviation compared against
  `solid_color_std_threshold` (default 4.0, out of 255) — pooling across
  all five, not per-block, means a frame that's genuinely two-tone (half
  solid red, half solid blue) still reads as high-variance and is
  correctly not flagged, even though each individual block might look
  locally flat on its own.

  Runs every render() frame while active (previously gated to roughly
  once per `dark_skip_after`) — this genuinely needs continuous
  monitoring; a once-per-interval snapshot can't distinguish
  "continuously flat for 0.2s" from "flat for one sampled instant,
  twice." The glReadPixels buffer is now preallocated once and reused
  every frame rather than allocated fresh per check.

  Persistent exclusion list renamed `dark_excluded.txt` →
  `excluded_presets.txt` to match (the old file's ~7,400 hard-earned
  entries are now orphaned — a full-library re-sweep at a fast preset
  interval is the planned way to rebuild it against the new detector).
  Config renamed to match: `dark_skip_enabled` →
  `solid_color_skip_enabled`, `dark_skip_after` → `solid_color_duration`,
  `dark_brightness_threshold` → `solid_color_std_threshold`.

  Also adds a same-tick reentrancy guard in `_load_current_preset()`:
  refuses a second preset load within one frame, logged at WARNING. This
  is a defensive, contained fix for part of a confirmed-but-separate bug
  (a preset sometimes gets swapped out again in well under a second,
  with zero frames rendered in between — meaning no solid-color sample
  ever gets a chance to run for it regardless of tuning); the
  investigation traced the likely root cause to auto-vj-01's async
  `goto_effect()` leaving `projectm_active()` reading a stale reference
  during a crossfade, which is out of scope for this drop-in to fix on
  its own.

  9 regression tests for the new detector/streak-tracking/reentrancy
  guard (`tests/test_solid_color_check.py`, superseding
  `test_dark_check.py`); the main-repo's own
  `tests/test_projectm_dark_exclusion.py` renamed to
  `test_projectm_solid_color_exclusion.py` and updated to match.

- **0.10.4** — Fixes the dark-preset check degrading from "once per
  `dark_skip_after` interval" into "every single frame" for the rest of
  a preset's life. `update()` armed `_dark_check_needed` once
  `_dark_skip_timer` crossed `dark_skip_after`, but never reset the
  timer back to 0 — so the moment `render()` cleared the flag after
  consuming one check, the very next frame's `update()` saw the timer
  still pinned above threshold and re-armed it immediately. Confirmed
  live via a real session log at DEBUG level: one preset logged 307
  separate "Dark check" evaluations over its run instead of the
  intended handful. Also fixes `__init__.py`'s eager relative import
  (`from .projectm_effect import ProjectMEffect`), which broke pytest's
  module import of this drop-in's own test tree exactly the way
  webcam-01's `__init__.py` already documented and fixed for itself —
  this drop-in never got the same fix, so it had no test suite at all
  until now. First test suite for this drop-in: 6 regression tests for
  the timer-arm/reset/re-arm sequence.

  **Not yet fixed, still under investigation:** a second, separately
  confirmed mechanism where a preset gets swapped out again within well
  under a second of loading — sometimes with a second "ProjectM loading
  preset" line appearing in the very next log line, zero frames
  rendered in between — meaning the dark check literally never gets a
  chance to run for that preset at all, regardless of `dark_skip_after`.
  Five such zero-check loads found in one session log. Root cause looks
  like a race between this drop-in's own `preset_duration` auto-advance
  and an external caller (very likely auto-vj-01) forcing a preset
  change in the same frame; needs auto-vj-01-side investigation to
  confirm.

- **0.10.3** — trashing a preset no longer rebuilds the catalog on the render
  thread at all. The playback list is pruned in place so the advance to the
  next preset stays correct, and the full catalog rebuild runs on a
  `projectm-catalog` worker; the result is installed at the top of the next
  `update()`, never by the worker itself, so nothing swaps the catalog while
  update/render are walking it. Superseded rebuilds are dropped via a
  generation counter.

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
