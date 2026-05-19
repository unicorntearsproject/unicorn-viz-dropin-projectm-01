# projectm-01 Phase 2 Plan

## Goals

Phase 2 extends the working Phase 1 integration with better live performance controls and pack-scale browsing:

- Pack-aware preset catalogs and pack switching.
- Better randomization quality for presets (shuffle-cycle + recency guard).
- Variant-level controls via hotkeys.
- Texture-pack aware search path auto-detection.

## Delivered in Phase 2

1. Pack-aware browsing

- Presets are grouped into logical packs based on folder layout.
- Effect exposes variant navigation to move between packs.

1. Preset random quality improvements

- Random preset mode now uses a shuffled cycle over all presets in the current pack.
- Recency guard avoids immediate repeats across cycle boundaries.

1. Live controls

- `Ctrl+N / Ctrl+P / Ctrl+R` keep preset-level navigation.
- `Ctrl+Shift+N / Ctrl+Shift+P / Ctrl+Shift+R` switch packs (variants).

1. Texture support

- Auto-adds `presets-milkdrop-texture-pack` directory when present so texture-dependent presets resolve assets.

## Operational Notes

- Large preset packs can be heavy; pack switching keeps runtime manageable by browsing one pack at a time.
- If libprojectM render fails mid-show, effect still falls back safely as in Phase 1.
