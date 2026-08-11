"""Regression tests for the ProjectM preset manager's Excluded / Quarantined
/ Disabled special categories and the quarantine move/restore/delete cycle.

Filesystem-integration style: _dropin_root() is monkeypatched to a tmp_path
tree with real presets/ and quarantine/ subdirectories, so the tests exercise
the actual file moves, not a mock of them. Monkeypatched at the *class*
level (not the instance) because _exclusion_key()/_exclusion_key_for_abs()
are classmethods that call cls._dropin_root() -- an instance-level override
wouldn't be visible to them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import projectm_effect as pm  # noqa: E402


def _root(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(pm.ProjectMEffect, '_dropin_root', staticmethod(lambda: tmp_path))
    return tmp_path


def _effect(tmp_path: Path) -> 'pm.ProjectMEffect':
    e = pm.ProjectMEffect.__new__(pm.ProjectMEffect)
    e._deleted_presets = set()
    e._disabled_presets = set()
    e._preset_catalog = []
    e._excluded_presets_cache = None
    # _save_preset_state()/refresh_preset_catalog() touch a lot of runtime
    # machinery unrelated to what's under test here; no-op them.
    e._save_preset_state = lambda: None
    e.refresh_preset_catalog = lambda *a, **k: None
    e.rescan_preset_files = lambda: None
    return e


def _write(path: Path, text: str = 'preset') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


# --------------------------------------------------------- quarantine_preset

def test_quarantine_preset_moves_file_preserving_relative_structure(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    source = _write(root / 'presets' / 'pack' / 'Cat' / 'x.milk')
    e = _effect(tmp_path)

    assert e.quarantine_preset(str(source)) is True
    assert not source.exists()
    dest = root / 'quarantine' / 'pack' / 'Cat' / 'x.milk'
    assert dest.is_file()


def test_quarantine_preset_removes_excluded_entry_and_marks_deleted(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    source = _write(root / 'presets' / 'pack' / 'x.milk')
    (root / 'excluded_presets.txt').write_text('presets/pack/x.milk\npresets/other.milk\n', encoding='utf-8')
    e = _effect(tmp_path)

    assert e.quarantine_preset(str(source)) is True
    remaining = (root / 'excluded_presets.txt').read_text(encoding='utf-8').splitlines()
    assert remaining == ['presets/other.milk']
    assert str(source.resolve()) in e._deleted_presets


def test_quarantine_preset_refuses_file_outside_presets_root(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    outside = _write(tmp_path / 'elsewhere' / 'x.milk')
    e = _effect(tmp_path)

    assert e.quarantine_preset(str(outside)) is False
    assert outside.exists()  # untouched
    assert not (root / 'quarantine').exists()


def test_quarantine_preset_refuses_missing_file(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    (root / 'presets').mkdir(parents=True)
    e = _effect(tmp_path)
    assert e.quarantine_preset(str(root / 'presets' / 'ghost.milk')) is False


# ---------------------------------------------------- restore_from_quarantine

def test_restore_from_quarantine_moves_file_back(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    quarantined = _write(root / 'quarantine' / 'pack' / 'Cat' / 'x.milk')
    e = _effect(tmp_path)
    e._deleted_presets = {str((root / 'presets' / 'pack' / 'Cat' / 'x.milk').resolve())}

    assert e.restore_from_quarantine(str(quarantined)) is True
    assert not quarantined.exists()
    restored = root / 'presets' / 'pack' / 'Cat' / 'x.milk'
    assert restored.is_file()
    assert str(restored.resolve()) not in e._deleted_presets


def test_restore_from_quarantine_refuses_file_outside_quarantine_dir(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    not_quarantined = _write(root / 'presets' / 'x.milk')
    e = _effect(tmp_path)
    assert e.restore_from_quarantine(str(not_quarantined)) is False
    assert not_quarantined.exists()  # untouched


# ------------------------------------------------------ delete_quarantined_preset

def test_delete_quarantined_preset_removes_file(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    quarantined = _write(root / 'quarantine' / 'x.milk')
    e = _effect(tmp_path)
    assert e.delete_quarantined_preset(str(quarantined)) is True
    assert not quarantined.exists()


def test_delete_quarantined_preset_refuses_file_outside_quarantine_dir(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    normal = _write(root / 'presets' / 'x.milk')
    e = _effect(tmp_path)
    assert e.delete_quarantined_preset(str(normal)) is False
    assert normal.exists()  # refused, not deleted


# --------------------------------------------------------- _remove_excluded_preset_entry

def test_remove_excluded_preset_entry_removes_only_the_matching_key(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    (root / 'excluded_presets.txt').write_text('a.milk\nb.milk\nc.milk\n', encoding='utf-8')
    e = _effect(tmp_path)
    e._remove_excluded_preset_entry('b.milk')
    assert (root / 'excluded_presets.txt').read_text(encoding='utf-8').splitlines() == ['a.milk', 'c.milk']


def test_remove_excluded_preset_entry_handles_last_entry(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    (root / 'excluded_presets.txt').write_text('only.milk\n', encoding='utf-8')
    e = _effect(tmp_path)
    e._remove_excluded_preset_entry('only.milk')
    assert (root / 'excluded_presets.txt').read_text(encoding='utf-8') == ''


def test_remove_excluded_preset_entry_is_a_no_op_for_unknown_key(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    (root / 'excluded_presets.txt').write_text('a.milk\n', encoding='utf-8')
    e = _effect(tmp_path)
    e._remove_excluded_preset_entry('nonexistent.milk')
    assert (root / 'excluded_presets.txt').read_text(encoding='utf-8').splitlines() == ['a.milk']


# ------------------------------------------------------------ excluded-presets cache

def test_load_excluded_presets_is_cached_across_calls(monkeypatch, tmp_path) -> None:
    """preset_catalog() calls this on every manager keypress -- a real
    session log profiled re-reading + re-parsing a ~7,700-line file that
    often as ~150ms of the cost. The second call must not touch disk."""
    root = _root(monkeypatch, tmp_path)
    (root / 'excluded_presets.txt').write_text('a.milk\n', encoding='utf-8')
    e = _effect(tmp_path)

    first = e._load_excluded_presets()
    # Rewrite the file directly (bypassing the two methods that invalidate
    # the cache) -- if the second call re-read from disk, it would see this.
    (root / 'excluded_presets.txt').write_text('a.milk\nb.milk\n', encoding='utf-8')
    second = e._load_excluded_presets()

    assert first == second == {'a.milk'}


def test_load_excluded_presets_returns_independent_copies(monkeypatch, tmp_path) -> None:
    """Callers (e.g. _remove_excluded_preset_entry) mutate the returned set
    directly -- that must never corrupt the cache for the next caller."""
    root = _root(monkeypatch, tmp_path)
    (root / 'excluded_presets.txt').write_text('a.milk\nb.milk\n', encoding='utf-8')
    e = _effect(tmp_path)

    first = e._load_excluded_presets()
    first.clear()
    second = e._load_excluded_presets()

    assert second == {'a.milk', 'b.milk'}


def test_save_excluded_preset_entry_invalidates_the_cache(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    _write(root / 'presets' / 'new.milk')
    e = _effect(tmp_path)
    assert e._load_excluded_presets() == set()   # warms the cache empty

    e._save_excluded_preset_entry(root / 'presets' / 'new.milk')

    assert e._load_excluded_presets() == {'presets/new.milk'}


def test_remove_excluded_preset_entry_invalidates_the_cache(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    (root / 'excluded_presets.txt').write_text('a.milk\nb.milk\n', encoding='utf-8')
    e = _effect(tmp_path)
    assert e._load_excluded_presets() == {'a.milk', 'b.milk'}   # warms the cache

    e._remove_excluded_preset_entry('a.milk')

    assert e._load_excluded_presets() == {'b.milk'}


def test_quarantine_preset_stops_a_stale_scan_cache_from_resurrecting_it(monkeypatch, tmp_path) -> None:
    """quarantine_preset() deliberately does NOT force a full rescan (that's
    the exact ~10,000-file / ~11s-freeze cost delete_presets() also avoids)
    -- it relies on _deleted_presets, the same mechanism delete already
    uses, to stop _build_preset_catalog() from re-adding a preset via a
    filesystem-scan cache entry that still points at the old (now empty)
    location."""
    root = _root(monkeypatch, tmp_path)
    source = _write(root / 'presets' / 'pack' / 'x.milk')
    (root / 'excluded_presets.txt').write_text('presets/pack/x.milk\n', encoding='utf-8')
    e = _effect(tmp_path)
    e.config = {}

    # Seed the module-level scan cache exactly as _scan_preset_files()
    # would have, before the file ever moves.
    prev_cache = pm._file_scan_cache
    pm._file_scan_cache = [(source, root / 'presets', str(source.resolve()))]
    try:
        assert e.quarantine_preset(str(source)) is True
        # The stale cache entry is still there -- quarantine_preset() must
        # not need (or trigger) a rescan to stay correct.
        assert pm._file_scan_cache == [(source, root / 'presets', str(source.resolve()))]

        catalog = e._build_preset_catalog()
        assert catalog == []   # not resurrected via the stale scan entry
    finally:
        pm._file_scan_cache = prev_cache


def test_quarantine_preset_reflects_immediately_in_a_later_catalog_call(monkeypatch, tmp_path) -> None:
    """End-to-end: quarantining must not leave a stale cached exclusion
    list showing the preset in both Excluded and Quarantined at once."""
    root = _root(monkeypatch, tmp_path)
    source = _write(root / 'presets' / 'pack' / 'x.milk')
    (root / 'excluded_presets.txt').write_text('presets/pack/x.milk\n', encoding='utf-8')
    e = _effect(tmp_path)
    assert any(x['category_key'] == pm.ProjectMEffect.EXCLUDED_CATEGORY for x in e.preset_catalog())

    assert e.quarantine_preset(str(source)) is True

    entries = e.preset_catalog()
    assert not any(x['category_key'] == pm.ProjectMEffect.EXCLUDED_CATEGORY for x in entries)
    assert any(x['category_key'] == pm.ProjectMEffect.QUARANTINE_CATEGORY for x in entries)


# --------------------------------------------------------------- preset_catalog

def _catalog_item(pm_mod, path: Path, enabled: bool) -> '_PresetCatalogItem':  # noqa: F821
    return pm_mod._PresetCatalogItem(
        path=path, display_name=path.stem, pack_name='pack',
        category_path=('Cat',), tags=(), enabled=enabled,
    )


def test_preset_catalog_omits_special_categories_when_empty(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    (root / 'presets').mkdir(parents=True)
    e = _effect(tmp_path)
    real = _write(root / 'presets' / 'x.milk')
    e._preset_catalog = [_catalog_item(pm, real, enabled=True)]

    keys = {entry['category_key'] for entry in e.preset_catalog()}
    assert pm.ProjectMEffect.DISABLED_CATEGORY not in keys
    assert pm.ProjectMEffect.EXCLUDED_CATEGORY not in keys
    assert pm.ProjectMEffect.QUARANTINE_CATEGORY not in keys


def test_preset_catalog_includes_disabled_section_only_for_disabled_items(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    on_path = _write(root / 'presets' / 'on.milk')
    off_path = _write(root / 'presets' / 'off.milk')
    e = _effect(tmp_path)
    e._preset_catalog = [
        _catalog_item(pm, on_path, enabled=True),
        _catalog_item(pm, off_path, enabled=False),
    ]

    entries = e.preset_catalog()
    disabled_rows = [x for x in entries if x['category_key'] == pm.ProjectMEffect.DISABLED_CATEGORY]
    assert len(disabled_rows) == 1
    assert disabled_rows[0]['display_name'] == 'off'
    # The real (non-virtual) row for the disabled preset must still be
    # present too -- Disabled is an additional view, not a move.
    real_rows = [x for x in entries if x['category_key'] == 'Cat']
    assert {r['display_name'] for r in real_rows} == {'on', 'off'}


def test_preset_catalog_includes_excluded_section_from_excluded_presets_file(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    _write(root / 'presets' / 'pack' / 'gone.milk')
    (root / 'excluded_presets.txt').write_text('presets/pack/gone.milk\n', encoding='utf-8')
    e = _effect(tmp_path)

    entries = e.preset_catalog()
    excluded_rows = [x for x in entries if x['category_key'] == pm.ProjectMEffect.EXCLUDED_CATEGORY]
    assert len(excluded_rows) == 1
    assert excluded_rows[0]['display_name'] == 'gone'
    assert excluded_rows[0]['kind'] == 'excluded'
    assert excluded_rows[0]['path'] == str(root / 'presets' / 'pack' / 'gone.milk')


def test_preset_catalog_includes_quarantined_section_from_quarantine_dir(monkeypatch, tmp_path) -> None:
    root = _root(monkeypatch, tmp_path)
    _write(root / 'quarantine' / 'pack' / 'stashed.milk')
    e = _effect(tmp_path)

    entries = e.preset_catalog()
    quarantined_rows = [x for x in entries if x['category_key'] == pm.ProjectMEffect.QUARANTINE_CATEGORY]
    assert len(quarantined_rows) == 1
    assert quarantined_rows[0]['display_name'] == 'stashed'
    assert quarantined_rows[0]['kind'] == 'quarantined'


def test_preset_catalog_does_not_infinite_loop_when_many_presets_disabled(monkeypatch, tmp_path) -> None:
    """Regression for a real bug caught before shipping: appending to the
    same list being iterated re-visited (and re-duplicated) each newly
    appended Disabled copy, since those are disabled too."""
    root = _root(monkeypatch, tmp_path)
    e = _effect(tmp_path)
    paths = [_write(root / 'presets' / f'{i}.milk') for i in range(5)]
    e._preset_catalog = [_catalog_item(pm, p, enabled=False) for p in paths]

    entries = e.preset_catalog()
    disabled_rows = [x for x in entries if x['category_key'] == pm.ProjectMEffect.DISABLED_CATEGORY]
    assert len(disabled_rows) == 5   # not 10, not infinite


# --------------------------------------------------------------------- _init

def test_init_does_not_crash_on_a_cold_catalog(monkeypatch, tmp_path) -> None:
    """Regression: _excluded_presets_cache was originally initialised near
    the *end* of _init(), but the cold-catalog branch (_catalog_cache is
    None) calls refresh_preset_catalog() -> _build_preset_catalog() ->
    _load_excluded_presets() earlier than that, which read the attribute
    before it existed. AttributeError, every time, on the very first
    ProjectM load of a session (real repro: the first Ctrl+L of a run).
    The _effect() stub above bypasses _init() entirely and pre-sets the
    attribute itself, so it can't catch an ordering bug inside _init() --
    this test goes through the real constructor instead."""
    moderngl = pytest.importorskip('moderngl')
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'no headless GL context: {exc}')
    _root(monkeypatch, tmp_path)
    monkeypatch.setattr(pm, '_catalog_cache', None)
    _write(tmp_path / 'presets' / 'x.milk')
    try:
        effect = pm.ProjectMEffect(ctx, 16, 16, {})
        effect.destroy()
    finally:
        ctx.release()
