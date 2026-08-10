"""Regression tests for ProjectM's dark-preset auto-exclusion timer.

Covers a confirmed bug in update()'s dark-check arm logic: without
resetting _dark_skip_timer at the moment _dark_check_needed is armed,
the timer stays pinned above dark_skip_after forever afterward -- so the
instant render() clears _dark_check_needed back to False (having
consumed one check), the very next update() call re-arms it immediately.
"Check once per dark_skip_after interval" degrades into "check every
single frame" for the rest of the preset's life.

Confirmed live in a real session log (DEBUG level, 2026-08-09): one
preset ("The NG + MindJourney - Lake of Dreams") logged 307 separate
"Dark check: brightness=..." lines over its run, instead of the small
handful the design intends.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import projectm_effect as pm  # noqa: E402


class _FakeBridge:
    def set_timing(self, *a, **k) -> None:
        pass

    def add_pcm(self, *a, **k) -> None:
        pass

    def configure(self, *a, **k) -> None:
        pass


def _effect(dark_skip_after: float = 0.4) -> 'pm.ProjectMEffect':
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
        'dark_skip_enabled': True,
        'dark_skip_after': dark_skip_after,
        'start_clean': False,
    }
    e._preset_paths = [Path('a.milk'), Path('b.milk')]
    e._preset_index = 0
    e._preset_timer = 0.0
    e._dark_skip_timer = 0.0
    e._dark_check_needed = False
    return e


def _audio(bass: float = 0.0, mid: float = 0.0, treble: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(bass=bass, mid=mid, treble=treble, waveform=None)


def test_dark_check_needed_arms_once_after_dark_skip_after() -> None:
    e = _effect(dark_skip_after=0.4)
    for _ in range(3):
        e.update(0.1, _audio())
        assert e._dark_check_needed is False
    e.update(0.1, _audio())  # total 0.4s
    assert e._dark_check_needed is True


def test_dark_skip_timer_resets_when_check_arms() -> None:
    """The core fix: _dark_skip_timer must reset to 0 the moment
    _dark_check_needed is armed, not stay pinned above dark_skip_after."""
    e = _effect(dark_skip_after=0.4)
    for _ in range(4):
        e.update(0.1, _audio())
    assert e._dark_check_needed is True
    assert e._dark_skip_timer == 0.0


def test_dark_check_does_not_rearm_every_frame_after_consumption() -> None:
    """The confirmed bug, directly: without the timer reset, clearing
    _dark_check_needed (as render() does immediately after each check)
    let the very next update() call re-arm it, because _dark_skip_timer
    was still sitting above dark_skip_after from before."""
    e = _effect(dark_skip_after=0.4)
    for _ in range(4):
        e.update(0.1, _audio())
    assert e._dark_check_needed is True

    e._dark_check_needed = False  # simulate render() consuming the check

    e.update(0.1, _audio())  # the very next frame
    assert e._dark_check_needed is False


def test_dark_check_rearms_after_a_full_new_interval() -> None:
    """After consumption, arming again should take a full dark_skip_after
    worth of newly-elapsed time -- not zero, not double-counted."""
    e = _effect(dark_skip_after=0.4)
    for _ in range(4):
        e.update(0.1, _audio())
    assert e._dark_check_needed is True
    e._dark_check_needed = False  # render() consumes it

    for _ in range(3):
        e.update(0.1, _audio())
        assert e._dark_check_needed is False
    e.update(0.1, _audio())  # 4th tick since consumption -> another 0.4s
    assert e._dark_check_needed is True


def test_dark_check_disabled_never_arms() -> None:
    e = _effect(dark_skip_after=0.1)
    e.config['dark_skip_enabled'] = False
    for _ in range(20):
        e.update(0.1, _audio())
    assert e._dark_check_needed is False


def test_dark_check_does_not_arm_while_locked() -> None:
    e = _effect(dark_skip_after=0.1)
    e.config['lock_preset'] = True
    for _ in range(20):
        e.update(0.1, _audio())
    assert e._dark_check_needed is False
