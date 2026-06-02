#!/usr/bin/env python3
"""Scan projectM preset packs for bad/broken presets and missing textures.

Pass 1 — Corrupt files
    A .milk file that cannot be read or has no [preset00] section is corrupt
    or in the wrong format and will produce a black screen immediately.

Pass 2 — Incompatible HLSL shaders
    Some old Milkdrop 2.x presets embed shader-model declarations like
    ``ps_2_0`` / ``ps_3_0`` / ``vs_1_1`` etc.  projectM 4's GLSL transpiler
    rejects these outright; the preset renders black and logs an error.

Pass 3 — Missing textures
    Scans ``sampler_<name>`` references in every preset and cross-checks them
    against the texture files found in the configured texture directories.
    Built-in projectM textures (noise, blur layers, etc.) are excluded from
    the check.

Usage:
    # From the unicorn-viz repo root:
    python3 drop-ins/projectm-01/scan_presets.py [options]

    # Or from inside the drop-in directory:
    python3 scan_presets.py [options]

Options:
    --presets-dir DIR   Root to scan for .milk/.prjm files
                        (default: presets/ next to this script)
    --texture-dir DIR   Comma-separated list of texture directories to check
                        against (default: presets/presets-milkdrop-texture-pack/textures)
    --remove-bad        Delete corrupt + HLSL-incompatible presets (keeps a
                        .bak next to each removed file by default)
    --no-backup         When --remove-bad is set, delete without backup
    --report FILE       Write a JSON report to FILE (default: preset_scan_report.json)
    --dry-run           Print what would be removed without touching anything
    -v, --verbose       Print each preset as it is scanned
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Texture names that are always provided internally by projectM and do not
# need to be present as files on disk.
BUILTIN_TEXTURES: frozenset[str] = frozenset({
    'main', 'blur1', 'blur2', 'blur3',
    'pw', 'fc_main', 'fc', 'video', 'reflection',
    'noise_lq', 'noise_lq_lite', 'noise_mq', 'noise_hq',
    'noise_lq_v2', 'noise_mq_v2', 'noise_hq_v2',
    'noisevol_lq', 'noisevol_hq',
    'rand_preset', 'rand_frame',
})

# Regex: ``sampler_<name>`` — captures the texture name
_RE_SAMPLER = re.compile(r'\bsampler_([A-Za-z0-9_]+)')

# Regex: old HLSL shader-model declarations that projectM 4 cannot transpile
_RE_HLSL_COMPAT = re.compile(
    r'(?i)\b(ps_[123]_[x0-9]+|vs_[123]_[x0-9]+|gs_[45]_[x0-9]+|'
    r'VertexShader\s*=|PixelShader\s*=|technique\s+\w+\s*\{)',
)

# Supported preset extensions
_PRESET_EXTS: frozenset[str] = frozenset({'.milk', '.prjm'})

# Texture file extensions projectM recognises
_TEX_EXTS: frozenset[str] = frozenset({'.jpg', '.jpeg', '.png', '.tga', '.bmp', '.dds', '.gif'})

# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

def collect_preset_paths(root: Path) -> list[Path]:
    """Return all preset files under root, sorted."""
    return sorted(p for p in root.rglob('*') if p.suffix.lower() in _PRESET_EXTS)


def collect_texture_names(tex_dirs: list[Path]) -> set[str]:
    """Return the lower-cased stems of every texture file in tex_dirs."""
    names: set[str] = set()
    for d in tex_dirs:
        if d.is_dir():
            for f in d.iterdir():
                if f.suffix.lower() in _TEX_EXTS:
                    names.add(f.stem.lower())
    return names


def scan_preset(path: Path) -> dict:
    """Return a scan result dict for a single preset file."""
    result: dict = {
        'path': str(path),
        'name': path.stem,
        'corrupt': False,
        'corrupt_reason': '',
        'hlsl_incompatible': False,
        'hlsl_markers': [],
        'external_textures': [],   # all non-builtin sampler names found
    }

    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        result['corrupt'] = True
        result['corrupt_reason'] = f'unreadable: {exc}'
        return result

    # Pass 1: basic format check
    if not text.strip():
        result['corrupt'] = True
        result['corrupt_reason'] = 'empty file'
        return result
    if '[preset00]' not in text and '[Preset00]' not in text:
        result['corrupt'] = True
        result['corrupt_reason'] = 'missing [preset00] section'
        return result

    # Pass 2: HLSL incompatibility markers
    markers = list({m.group(0) for m in _RE_HLSL_COMPAT.finditer(text)})
    if markers:
        result['hlsl_incompatible'] = True
        result['hlsl_markers'] = sorted(markers)

    # Pass 3: external texture references
    found: set[str] = set()
    for m in _RE_SAMPLER.finditer(text):
        name = m.group(1).lower()
        if name not in BUILTIN_TEXTURES:
            found.add(m.group(1))   # preserve original casing
    result['external_textures'] = sorted(found)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Scan projectM preset packs for bad presets and missing textures.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    script_dir = Path(__file__).parent
    parser.add_argument(
        '--presets-dir',
        type=Path,
        default=script_dir / 'presets',
        help='Root directory to scan (default: presets/ next to this script)',
    )
    parser.add_argument(
        '--texture-dir',
        default='',
        help='Comma-separated texture directories (default: presets-milkdrop-texture-pack/textures)',
    )
    parser.add_argument(
        '--remove-bad',
        action='store_true',
        help='Delete corrupt + HLSL-incompatible presets',
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='With --remove-bad: delete without keeping a .bak copy',
    )
    parser.add_argument(
        '--report',
        type=Path,
        default=Path('preset_scan_report.json'),
        help='JSON report output path (default: preset_scan_report.json)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Report what would be removed without deleting anything',
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Print each preset as scanned',
    )
    args = parser.parse_args(argv)

    presets_dir = args.presets_dir
    if not presets_dir.is_dir():
        print(f'ERROR: presets directory not found: {presets_dir}', file=sys.stderr)
        return 1

    # Resolve texture directories
    if args.texture_dir:
        tex_dirs = [Path(t.strip()) for t in args.texture_dir.split(',') if t.strip()]
    else:
        tex_dirs = [presets_dir / 'presets-milkdrop-texture-pack' / 'textures']

    available_textures = collect_texture_names(tex_dirs)
    print(f'Texture dirs: {[str(d) for d in tex_dirs]}')
    print(f'Available textures: {len(available_textures)}')

    # Scan all presets
    all_presets = collect_preset_paths(presets_dir)
    print(f'Scanning {len(all_presets)} preset files under {presets_dir} ...')

    corrupt: list[dict] = []
    hlsl_bad: list[dict] = []
    missing_tex: dict[str, list[str]] = {}   # texture name → list of preset paths
    total_external_refs: dict[str, int] = {}

    for i, path in enumerate(all_presets, 1):
        if args.verbose:
            print(f'  [{i}/{len(all_presets)}] {path.name}')

        result = scan_preset(path)

        if result['corrupt']:
            corrupt.append(result)
        elif result['hlsl_incompatible']:
            hlsl_bad.append(result)

        for tex in result['external_textures']:
            total_external_refs[tex] = total_external_refs.get(tex, 0) + 1
            if tex.lower() not in available_textures:
                missing_tex.setdefault(tex, []).append(str(path))

    # Build sorted lists
    missing_tex_sorted = [
        {'texture': k, 'preset_count': len(v), 'sample_presets': v[:5]}
        for k, v in sorted(missing_tex.items(), key=lambda x: -len(x[1]))
    ]

    # Print summary
    print()
    print('=' * 60)
    print('SCAN RESULTS')
    print('=' * 60)
    print(f'  Total presets:          {len(all_presets):>6}')
    print(f'  Corrupt / unreadable:   {len(corrupt):>6}')
    print(f'  HLSL incompatible:      {len(hlsl_bad):>6}')
    print(f'  Bad total (removable):  {len(corrupt) + len(hlsl_bad):>6}')
    print(f'  Missing texture names:  {len(missing_tex_sorted):>6}')
    print()

    if corrupt:
        print('--- CORRUPT (first 10) ---')
        for r in corrupt[:10]:
            print(f'  {r["corrupt_reason"]}: {r["name"]}')
        if len(corrupt) > 10:
            print(f'  ... and {len(corrupt) - 10} more')
        print()

    if hlsl_bad:
        print('--- HLSL INCOMPATIBLE (first 10) ---')
        for r in hlsl_bad[:10]:
            print(f'  markers={r["hlsl_markers"]!r}: {r["name"]}')
        if len(hlsl_bad) > 10:
            print(f'  ... and {len(hlsl_bad) - 10} more')
        print()

    if missing_tex_sorted:
        print('--- MISSING TEXTURES (top 20 by affected preset count) ---')
        for entry in missing_tex_sorted[:20]:
            print(f'  {entry["preset_count"]:>5} presets  →  {entry["texture"]}')
        if len(missing_tex_sorted) > 20:
            print(f'  ... and {len(missing_tex_sorted) - 20} more texture names missing')
        print()

    # Write JSON report
    report = {
        'presets_dir': str(presets_dir),
        'texture_dirs': [str(d) for d in tex_dirs],
        'total_presets': len(all_presets),
        'corrupt': corrupt,
        'hlsl_incompatible': hlsl_bad,
        'missing_textures': missing_tex_sorted,
    }
    args.report.write_text(json.dumps(report, indent=2))
    print(f'Full report written to: {args.report}')
    print()

    # Remove bad presets if requested
    to_remove = corrupt + hlsl_bad
    if to_remove and (args.remove_bad or args.dry_run):
        action = 'Would remove' if args.dry_run else 'Removing'
        print(f'{action} {len(to_remove)} bad preset(s) ...')
        removed = 0
        for r in to_remove:
            p = Path(r['path'])
            if not p.exists():
                continue
            if args.dry_run:
                print(f'  [dry-run] {p}')
            else:
                if not args.no_backup:
                    p.rename(p.with_suffix(p.suffix + '.bak'))
                else:
                    p.unlink()
                removed += 1
                if args.verbose:
                    print(f'  removed: {p.name}')
        if not args.dry_run:
            print(f'Done. Removed {removed} preset(s).')
    elif to_remove:
        print(f'Run with --remove-bad to delete {len(to_remove)} bad preset(s).')

    return 0


if __name__ == '__main__':
    sys.exit(main())
