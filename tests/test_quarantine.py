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
