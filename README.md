# projectm-01 - ProjectM + MilkDrop Presets Drop-In

A Unicorn Viz drop-in effect that hosts both **projectM presets** and
**classic MilkDrop presets** through libprojectM.

## Preset Compatibility

This drop-in is intentionally a unified preset host for both ecosystems:

- `projectM` presets (`.prjm`)
- `MilkDrop` presets (`.milk`)

You do **not** need a separate MilkDrop drop-in. MilkDrop packs and projectM
packs are handled by the same `ProjectM Presets` effect as pack variants.

## Runtime Requirement

This drop-in needs a native **libprojectM** runtime installed on the machine.
Preset packs alone are not enough.

If the runtime is missing, Unicorn Viz will still:

- discover the drop-in
- scan preset packs
- show preset/pack names in the HUD

but it will fall back to the internal shader instead of rendering real
projectM / MilkDrop visuals.

## Quick Install Notes

### Fedora

Install the distro runtime package:

```bash
sudo dnf install libprojectM
```

Fedora currently ships **libprojectM 3.x**, not 4.x. This drop-in supports
both the older Fedora-style runtime and the newer projectM 4 API.

### Arch Linux

Install the system package:

```bash
sudo pacman -S projectm
```

### Debian / Ubuntu

Install the runtime package if available in your release:

```bash
sudo apt install libprojectm4v5 libprojectm-dev
```

Package names can vary by distro release. If auto-detection fails, set
`projectm_library` in config to the full shared-library path.

### Windows

Install or build libprojectM, then point Unicorn Viz at the DLL explicitly:

```toml
[effects.ProjectMEffect]
projectm_library = "C:/path/to/projectM.dll"
```

Windows support is not yet as polished as Linux-first setups. Expect to supply
the DLL path manually for now.

### macOS

Install libprojectM through your package manager or local build, then set the
full dylib path if auto-detection does not find it:

```toml
[effects.ProjectMEffect]
projectm_library = "/opt/homebrew/lib/libprojectM-4.dylib"
```

## Verification

After installing the runtime:

1. Launch Unicorn Viz.
2. Switch to `ProjectM Presets`.
3. Confirm the HUD no longer shows an unavailable/fallback state.
4. Use `Ctrl+N`, `Ctrl+P`, and `Ctrl+R` to confirm preset changes are visible.
5. Use `Ctrl+Shift+N`, `Ctrl+Shift+P`, and `Ctrl+Shift+R` to switch packs.

## Hotkeys

These shortcuts are intentionally mnemonic: `Ctrl` scopes the action to the current preset set, while `Ctrl+Shift` scopes it to the pack list.

- `Ctrl+N` / `Ctrl+P` / `Ctrl+R` — next / previous / random preset
- `Ctrl+Shift+N` / `Ctrl+Shift+P` / `Ctrl+Shift+R` — next / previous / random pack

## Phase 1 + 2 Features

- Native libprojectM bridge through `ctypes`
- Preset scanning (`.milk`, `.prjm`)
- Audio PCM feed from Unicorn Viz waveform
- Render into Unicorn Viz framebuffer/FBO path
- Preset controls while active:
  - `Ctrl+N`: next preset
  - `Ctrl+P`: previous preset
  - `Ctrl+R`: random preset
- Pack controls while active:
  - `Ctrl+Shift+N`: next pack
  - `Ctrl+Shift+P`: previous pack
  - `Ctrl+Shift+R`: random pack
- Fallback shader when projectM runtime/presets are unavailable

Phase 2 adds pack-aware browsing and improved randomization quality:

- Presets are grouped by pack folders.
- Random preset picks use a shuffled cycle with recency guard (fewer repeats).
- `presets-milkdrop-texture-pack` is auto-added to texture search paths when present.

## Quick Start

1. Install libprojectM runtime on Linux.
2. Put projectM and/or MilkDrop preset packs under `drop-ins/projectm-01/presets/`.
3. Select `ProjectM Presets` in playlist/effect shortcuts.

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
projectm_library = ""   # explicit .so path if auto-detect fails
preset_dir = ""         # single override directory
preset_dirs = ""        # comma-separated extra preset directories
texture_dirs = ""       # comma-separated texture paths for presets
start_preset = ""       # optional startup preset file name/path
```

## Helpers

- `scripts/check_projectm_runtime.py`: runtime/library detection check.
- `scripts/install_projectm_linux.sh`: distro-oriented install hints and checks.
