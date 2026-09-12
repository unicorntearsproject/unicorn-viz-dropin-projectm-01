"""Regression tests for ProjectM's solid-color-preset auto-exclusion.

Replaces the old dark-only, single-snapshot check (see git history for
test_dark_check.py, superseded by this file) with continuous per-frame
monitoring: any frame that reads as functionally one flat color (any
color, not just black) counts toward a running streak, and the preset
is excluded once that streak crosses solid_color_duration -- resetting
to zero the instant a frame isn't flat, so a preset that goes solid
black, recovers, then goes solid blue later still gets caught on either
span independently.

Also covers the reentrancy guard in _load_current_preset(): two
independent preset-advance triggers landing in the same tick (this
effect's own timer, an external caller) must not both succeed.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import projectm_effect as pm  # noqa: E402


# --------------------------------------------------------------------- helpers

class _FakeBridge:
    def __init__(self) -> None:
        self.load_calls: list[tuple[str, bool]] = []

    def load_preset(self, path: str, smooth: bool) -> None:
        self.load_calls.append((path, smooth))

    def render(self) -> None:
        pass


class _FakeGLReadPixels:
    """Mimics glReadPixels: fills the ctypes buffer with a chosen fill
    value, cycling through `fills` (one entry consumed per call -- i.e.
    per sample block)."""

    def __init__(self, fills: list[int]) -> None:
        self._fills = list(fills)
        self._call_idx = 0

    def __call__(self, x, y, w, h, fmt, typ, buf) -> None:  # noqa: ARG002
        fill = self._fills[self._call_idx % len(self._fills)]
        self._call_idx += 1
        for i in range(len(buf)):
            buf[i] = fill


def _effect(**config_overrides) -> 'pm.ProjectMEffect':
    e = pm.ProjectMEffect.__new__(pm.ProjectMEffect)
    e.time = 0.0
    e.width = 1920
    e.height = 1080
    e._bridge = _FakeBridge()
    e._bass = e._mid = e._treble = 0.0
    e._manager_preview_active = False
    e.transition_active = False
    e.parameters = {'speed': 1.0, 'preset_duration': 20.0, 'beat_sensitivity': 1.0}
    e.config = {
        'lock_preset': False,
        'solid_color_skip_enabled': True,
        'solid_color_duration': 0.2,
        'solid_color_std_threshold': 4.0,
        'start_clean': False,
        **config_overrides,
    }
    e._preset_paths = [Path('a.milk'), Path('b.milk')]
    e._preset_index = 0
    e._preset_timer = 0.0
    e._solid_color_duration = 0.0
    e._solid_color_check_time = None
    e._update_tick = 0
    e._preset_load_tick = -1
    e._active_label = 'a'
    e._color_sample_buf = (ctypes.c_uint8 * (pm.ProjectMEffect._COLOR_SAMPLE_SIZE ** 2 * 3))()
    return e


# ------------------------------------------------------------ _is_frame_solid_color

def test_is_frame_solid_color_true_for_uniform_frame(monkeypatch) -> None:
    e = _effect()
    monkeypatch.setattr(pm, '_get_glReadPixels', lambda: _FakeGLReadPixels([128]))
    assert e._is_frame_solid_color() is True


def test_is_frame_solid_color_false_for_two_tone_frame(monkeypatch) -> None:
    """Each individual 48x48 block is internally flat (uniform per call),
    but the blocks alternate between two very different tones -- pooling
    all five together must still read as high-variance, proving the
    detector catches genuinely two-tone frames, not just per-block noise."""
    e = _effect()
    monkeypatch.setattr(pm, '_get_glReadPixels', lambda: _FakeGLReadPixels([0, 255, 0, 255, 0]))
    assert e._is_frame_solid_color() is False


def test_is_frame_solid_color_returns_false_when_gl_symbol_missing(monkeypatch) -> None:
    e = _effect()
    monkeypatch.setattr(pm, '_get_glReadPixels', lambda: None)
    assert e._is_frame_solid_color() is False


def test_is_frame_solid_color_respects_custom_threshold(monkeypatch) -> None:
    """Same mild two-tone frame (pooled std ~4.9) reads as flat against a
    loose threshold and not-flat against a tight one -- proves the
    configured threshold is actually load-bearing, not a fixed cutoff."""
    monkeypatch.setattr(pm, '_get_glReadPixels', lambda: _FakeGLReadPixels([120, 130]))
    e_loose = _effect(solid_color_std_threshold=50.0)
    assert e_loose._is_frame_solid_color() is True

    monkeypatch.setattr(pm, '_get_glReadPixels', lambda: _FakeGLReadPixels([120, 130]))
    e_tight = _effect(solid_color_std_threshold=2.0)
    assert e_tight._is_frame_solid_color() is False


# ------------------------------------------------------------------ render() gate

def test_render_skips_solid_color_tracking_by_default() -> None:
    """2026-09-12: opt-in by default -- the library is already clean of flat
    presets, so the per-frame glReadPixels cost shouldn't be paid unless an
    operator explicitly sets solid_color_skip_enabled = true."""
    e = _effect()
    del e.config['solid_color_skip_enabled']  # simulate an unset config key
    e._prime_pending = 0
    calls: list[None] = []
    e._update_solid_color_tracking = lambda: calls.append(None)  # type: ignore[method-assign]
    e.render()
    assert calls == []


def test_render_runs_solid_color_tracking_when_explicitly_enabled() -> None:
    e = _effect(solid_color_skip_enabled=True)
    e._prime_pending = 0
    calls: list[None] = []
    e._update_solid_color_tracking = lambda: calls.append(None)  # type: ignore[method-assign]
    e.render()
    assert calls == [None]


# ------------------------------------------------------- _update_solid_color_tracking

def test_solid_color_tracking_first_call_establishes_baseline_no_accumulation(monkeypatch) -> None:
    e = _effect()
    clock = [100.0]
    monkeypatch.setattr(pm.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(pm.ProjectMEffect, '_is_frame_solid_color', lambda self: True)

    e._update_solid_color_tracking()
    assert e._solid_color_duration == 0.0
    assert e._solid_color_check_time == 100.0


def test_solid_color_tracking_accumulates_and_excludes_after_threshold(monkeypatch) -> None:
    """Clock steps are 0.12s each -- above _SOLID_COLOR_SAMPLE_INTERVAL_S
    (0.1s, the 2026-09-12 throttle) so every call actually samples; two
    samples' worth (0.24s) is what it takes to cross solid_color_duration
    here, so the middle checkpoint only gets one "still under" call instead
    of the pre-throttle test's two -- two samples each >=0.1s can't sum to
    under 0.2s, so a second "still under" step isn't expressible anymore."""
    e = _effect(solid_color_duration=0.2)
    clock = [100.0]
    monkeypatch.setattr(pm.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(pm.ProjectMEffect, '_is_frame_solid_color', lambda self: True)

    excluded: list[Path] = []
    monkeypatch.setattr(pm.ProjectMEffect, '_save_excluded_preset_entry',
                         lambda self, path: excluded.append(path))
    monkeypatch.setattr(pm.ProjectMEffect, '_drop_from_catalog_cache', staticmethod(lambda path: None))
    loaded: list[bool] = []
    monkeypatch.setattr(pm.ProjectMEffect, '_load_current_preset',
                         lambda self, smooth: loaded.append(smooth))

    e._update_solid_color_tracking()   # baseline @ t=100.0, no elapsed time yet
    clock[0] = 100.12
    e._update_solid_color_tracking()   # +0.12s -> duration=0.12, still under 0.2
    assert excluded == []
    clock[0] = 100.24
    e._update_solid_color_tracking()   # +0.12s -> duration=0.24 >= 0.2 -> excluded
    assert len(excluded) == 1
    assert excluded[0].name == 'a.milk'
    assert loaded == [False]


def test_solid_color_tracking_resets_streak_on_non_flat_frame(monkeypatch) -> None:
    """A non-flat frame in the middle of a streak resets the accumulator
    to zero -- and a fresh streak afterward still independently reaches
    the exclusion threshold, matching "doesn't need to be the same
    color, or one continuous span" from the spec.

    The baseline call (the very first one) returns before ever reaching
    _is_frame_solid_color() -- it only has a "now", no "last" to diff
    against yet -- so it consumes no item from the sequence below; the
    four items map onto calls 2-5.

    Clock steps are 0.15s/0.15s/0.15s/0.20s -- all safely above
    _SOLID_COLOR_SAMPLE_INTERVAL_S (0.1s, the 2026-09-12 throttle), so
    every call actually samples. (A 0.1s-exactly step is right on that
    boundary and float-rounds to *below* 0.1 half the time -- not a
    meaningful gap for what this test verifies, which is the reset
    logic, not the throttle's own precision.)
    """
    e = _effect(solid_color_duration=0.2)
    clock = [100.0]
    monkeypatch.setattr(pm.time, 'monotonic', lambda: clock[0])
    flat_sequence = iter([True, False, True, True])
    monkeypatch.setattr(pm.ProjectMEffect, '_is_frame_solid_color', lambda self: next(flat_sequence))

    excluded: list[Path] = []
    monkeypatch.setattr(pm.ProjectMEffect, '_save_excluded_preset_entry',
                         lambda self, path: excluded.append(path))
    monkeypatch.setattr(pm.ProjectMEffect, '_drop_from_catalog_cache', staticmethod(lambda path: None))
    monkeypatch.setattr(pm.ProjectMEffect, '_load_current_preset', lambda self, smooth: None)

    e._update_solid_color_tracking()   # call 1: baseline, no item consumed, no elapsed time
    clock[0] = 100.15
    e._update_solid_color_tracking()   # call 2, item[0]=True: +0.15s -> duration=0.15
    assert e._solid_color_duration == pytest.approx(0.15)
    assert excluded == []
    clock[0] = 100.30
    e._update_solid_color_tracking()   # call 3, item[1]=False: resets to 0
    assert e._solid_color_duration == 0.0
    assert excluded == []
    clock[0] = 100.45
    e._update_solid_color_tracking()   # call 4, item[2]=True: +0.15s from the new baseline -> 0.15
    assert e._solid_color_duration == pytest.approx(0.15)
    clock[0] = 100.65
    e._update_solid_color_tracking()   # call 5, item[3]=True: +0.20s -> 0.35 >= 0.2 -> excluded
    assert len(excluded) == 1


# ---------------------------------------------------------- _load_current_preset

def test_load_current_preset_refuses_second_load_same_tick() -> None:
    e = _effect()
    e._update_tick = 5
    e._load_current_preset(False)
    assert len(e._bridge.load_calls) == 1

    e._load_current_preset(False)  # same tick again
    assert len(e._bridge.load_calls) == 1  # refused

    e._update_tick = 6
    e._load_current_preset(False)
    assert len(e._bridge.load_calls) == 2  # a new tick is allowed through


def test_load_current_preset_resets_solid_color_tracking() -> None:
    e = _effect()
    e._solid_color_duration = 5.0
    e._solid_color_check_time = 123.0
    e._load_current_preset(False)
    assert e._solid_color_duration == 0.0
    assert e._solid_color_check_time is None
