"""projectM preset host drop-in for Unicorn Viz.

Phase 1 embeds libprojectM as a single Unicorn Viz effect using the C API via
`ctypes`. When the native library or presets are unavailable, the effect falls
back to an internal shader so the playlist remains stable.

Diagnostics
-----------
Three libprojectM-4 callbacks are wired to Python's logging system:

* **Global log callback** (``projectm_set_log_callback``): routes libprojectM's
  own internal messages (shader compilation errors, I/O errors, etc.) into this
  module's logger.  WARN and above are always forwarded; DEBUG/TRACE are
  forwarded only when the Python log level is DEBUG.

* **Preset-switch-failed callback**: fires with the failing preset filename and
  the error message every time libprojectM rejects a preset (bad GLSL, corrupt
  file).  These are logged at WARNING and also accumulated in
  ``ProjectMEffect.failed_presets`` for inspection.

* **Preset-switch-requested callback**: fires when libprojectM decides to
  auto-advance.  Logged at DEBUG so normal operation stays quiet.

Dark-preset detection
---------------------
After ``dark_skip_after`` seconds on a preset (default 3 s), a 100×100 pixel
sample is taken from the centre of the framebuffer via ``glReadPixels``.  If
the average brightness is below ``dark_brightness_threshold`` (default 0.02,
i.e. 2 %), the preset is skipped automatically.  Set ``dark_skip_enabled =
false`` in ``[effects.ProjectMEffect]`` to disable.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import random
import atexit
import json
import re
import shutil
import threading
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import moderngl
import numpy as np
import sdl2

from unicornviz.effects.base import AudioData, BaseEffect

log = logging.getLogger(__name__)

# libprojectM log-level constants (projectm_log_level enum from logging.h)
_PM_LOG_TRACE = 1
_PM_LOG_DEBUG = 2
_PM_LOG_INFO  = 3
_PM_LOG_WARN  = 4
_PM_LOG_ERROR = 5
_PM_LOG_FATAL = 6

# Map libprojectM levels to Python logging levels
_PM_TO_PY_LEVEL: dict[int, int] = {
    _PM_LOG_TRACE: logging.DEBUG,
    _PM_LOG_DEBUG: logging.DEBUG,
    _PM_LOG_INFO:  logging.INFO,
    _PM_LOG_WARN:  logging.WARNING,
    _PM_LOG_ERROR: logging.ERROR,
    _PM_LOG_FATAL: logging.CRITICAL,
}

_GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
_GL_RGB = 0x1907
_GL_UNSIGNED_BYTE = 0x1401
_PROJECTM_MONO = 1

# Lazy-loaded glReadPixels symbol used by the dark-preset detector.
_gl_read_pixels: ctypes._CFuncPtr | None = None


@dataclass(slots=True)
class _PresetCatalogItem:
    """Catalog entry describing one discovered projectM preset file."""

    path: Path
    display_name: str
    pack_name: str
    category_path: tuple[str, ...]
    tags: tuple[str, ...]
    enabled: bool


def _get_glReadPixels() -> ctypes._CFuncPtr | None:
    """Return a ctypes wrapper for glReadPixels, loading libGL on first call."""
    global _gl_read_pixels
    if _gl_read_pixels is not None:
        return _gl_read_pixels
    for lib_name in ('libGL.so.1', 'libGL.so', 'opengl32.dll', 'libGL.dylib'):
        try:
            lib = ctypes.CDLL(lib_name)
            fn = lib.glReadPixels
            fn.restype = None
            fn.argtypes = [
                ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
                ctypes.c_uint, ctypes.c_uint,
                ctypes.c_void_p,
            ]
            _gl_read_pixels = fn
            return fn
        except (OSError, AttributeError):
            continue
    return None

# ctypes callback types (kept alive as module-level to prevent GC)
_LogCbType = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_SwitchFailedCbType = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
_SwitchRequestedCbType = ctypes.CFUNCTYPE(None, ctypes.c_bool, ctypes.c_void_p)

# Module-level log callback (global, not per-instance); stored here so the
# ctypes wrapper is never garbage-collected while the library is loaded.
_global_log_cb: _LogCbType | None = None

# Keep one bridge alive for the app lifetime so returning to ProjectM presets
# does not need to recreate the native projectM engine each time.
_shared_bridge: _ProjectMBridge | None = None

_VERT = """
#version 330
in vec2 in_vert;
out vec2 v_uv;
void main() {
    v_uv = in_vert * 0.5 + 0.5;
    gl_Position = vec4(in_vert, 0.0, 1.0);
}
"""

_FRAG_FALLBACK = """
#version 330
uniform float iTime;
uniform float iBass;
uniform float iMid;
uniform float iTreble;
in vec2 v_uv;
out vec4 fragColor;

vec3 palette(float t) {
    vec3 a = vec3(0.55, 0.45, 0.60);
    vec3 b = vec3(0.45, 0.50, 0.40);
    vec3 c = vec3(1.00, 1.00, 1.00);
    vec3 d = vec3(0.18, 0.12, 0.62);
    return a + b * cos(6.28318 * (c * t + d));
}

void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    float r = length(uv);
    float ang = atan(uv.y, uv.x);
    float swirl = sin(ang * 5.0 - iTime * (0.8 + iMid * 1.2) + r * 9.0);
    float pulse = 0.35 + iBass * 0.8;
    float ring = smoothstep(0.78 + pulse * 0.10, 0.10, abs(r - 0.45 - 0.08 * swirl));
    float haze = exp(-3.2 * r) * (0.4 + iTreble * 0.8);
    vec3 col = palette(r * 0.8 + iTime * 0.08 + swirl * 0.1);
    col *= 0.35 + ring + haze;
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""


class _ProjectMBridge:
    """Thin ctypes bridge for the subset of libprojectM used in Phase 1."""

    def __init__(self, library_hint: str = '') -> None:
        self._lib = self._load_library(library_hint)
        self._bind_symbols()
        self._install_log_callback()
        self._load_proc_cb = self._make_load_proc()
        self._handle = self._lib.projectm_create_with_opengl_load_proc(self._load_proc_cb, None)
        if not self._handle:
            raise RuntimeError('projectM instance creation failed')
        self._gl_get_integerv = self._make_gl_get_integerv()
        # Per-instance callbacks (stored so ctypes wrappers survive GC)
        self._switch_failed_cb: _SwitchFailedCbType | None = None
        self._switch_requested_cb: _SwitchRequestedCbType | None = None

    @property
    def handle(self):
        return self._handle

    def destroy(self) -> None:
        if self._handle:
            self._lib.projectm_destroy(self._handle)
            self._handle = None

    def configure(
        self,
        width: int,
        height: int,
        fps_hint: int,
        beat_sensitivity: float,
        preset_duration: float,
        lock_preset: bool,
        start_clean: bool,
        texture_dirs: list[str],
    ) -> None:
        self._lib.projectm_set_window_size(self._handle, width, height)
        self._lib.projectm_set_fps(self._handle, int(fps_hint))
        self._lib.projectm_set_beat_sensitivity(self._handle, float(beat_sensitivity))
        self._lib.projectm_set_preset_duration(self._handle, float(preset_duration))
        self._lib.projectm_set_preset_locked(self._handle, bool(lock_preset))
        self._lib.projectm_set_preset_start_clean(self._handle, bool(start_clean))
        if texture_dirs:
            arr = (ctypes.c_char_p * len(texture_dirs))(
                *[path.encode('utf-8') for path in texture_dirs]
            )
            self._lib.projectm_set_texture_search_paths(self._handle, arr, len(texture_dirs))

    def set_window_size(self, width: int, height: int) -> None:
        self._lib.projectm_set_window_size(self._handle, width, height)

    def set_timing(self, frame_time_seconds: float, fps_hint: int) -> None:
        self._lib.projectm_set_frame_time(self._handle, float(frame_time_seconds))
        self._lib.projectm_set_fps(self._handle, int(fps_hint))

    def load_preset(self, preset_path: str, smooth_transition: bool) -> None:
        self._lib.projectm_load_preset_file(
            self._handle,
            preset_path.encode('utf-8'),
            bool(smooth_transition),
        )

    def add_pcm(self, waveform: np.ndarray) -> None:
        samples = np.asarray(waveform, dtype=np.float32).reshape(-1)
        max_samples = int(self._lib.projectm_pcm_get_max_samples())
        if max_samples > 0 and samples.size > max_samples:
            samples = samples[-max_samples:]
        ptr = samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self._lib.projectm_pcm_add_float(self._handle, ptr, int(samples.size), _PROJECTM_MONO)

    def render(self) -> None:
        fbo = self._current_draw_fbo()
        if fbo != 0 and hasattr(self._lib, 'projectm_opengl_render_frame_fbo'):
            self._lib.projectm_opengl_render_frame_fbo(self._handle, ctypes.c_uint32(fbo))
        else:
            self._lib.projectm_opengl_render_frame(self._handle)

    def _current_draw_fbo(self) -> int:
        value = ctypes.c_int(0)
        self._gl_get_integerv(_GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(value))
        return int(value.value)

    def install_event_callbacks(
        self,
        on_switch_failed: _SwitchFailedCbType,
        on_switch_requested: _SwitchRequestedCbType,
    ) -> None:
        """Register per-instance preset event callbacks."""
        self._switch_failed_cb = on_switch_failed
        self._switch_requested_cb = on_switch_requested
        if hasattr(self._lib, 'projectm_set_preset_switch_failed_event_callback'):
            self._lib.projectm_set_preset_switch_failed_event_callback(
                self._handle, self._switch_failed_cb, None
            )
        if hasattr(self._lib, 'projectm_set_preset_switch_requested_event_callback'):
            self._lib.projectm_set_preset_switch_requested_event_callback(
                self._handle, self._switch_requested_cb, None
            )

    @staticmethod
    def _load_library(library_hint: str) -> ctypes.CDLL:
        candidates: list[str] = []
        if library_hint:
            candidates.append(library_hint)
        for name in ('projectM-4', 'projectM'):
            found = ctypes.util.find_library(name)
            if found:
                candidates.append(found)
        candidates.extend([
            'libprojectM-4.so',
            'libprojectM.so',
            'libprojectM-4.dylib',
            'projectM-4.dll',
            'libprojectM-4.dll',
        ])
        errors: list[str] = []
        for candidate in candidates:
            try:
                return ctypes.CDLL(candidate)
            except OSError as exc:
                errors.append(f'{candidate}: {exc}')
        raise RuntimeError('libprojectM not found. Tried: ' + '; '.join(candidates or ['<none>']))

    def _bind_symbols(self) -> None:
        load_proc = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
        self._lib.projectm_create_with_opengl_load_proc.argtypes = [load_proc, ctypes.c_void_p]
        self._lib.projectm_create_with_opengl_load_proc.restype = ctypes.c_void_p
        self._lib.projectm_destroy.argtypes = [ctypes.c_void_p]
        self._lib.projectm_destroy.restype = None
        self._lib.projectm_set_window_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        self._lib.projectm_set_window_size.restype = None
        self._lib.projectm_set_fps.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        self._lib.projectm_set_fps.restype = None
        self._lib.projectm_set_frame_time.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self._lib.projectm_set_frame_time.restype = None
        self._lib.projectm_set_beat_sensitivity.argtypes = [ctypes.c_void_p, ctypes.c_float]
        self._lib.projectm_set_beat_sensitivity.restype = None
        self._lib.projectm_set_preset_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self._lib.projectm_set_preset_duration.restype = None
        self._lib.projectm_set_preset_locked.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self._lib.projectm_set_preset_locked.restype = None
        self._lib.projectm_set_preset_start_clean.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self._lib.projectm_set_preset_start_clean.restype = None
        self._lib.projectm_load_preset_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]
        self._lib.projectm_load_preset_file.restype = None
        self._lib.projectm_pcm_get_max_samples.argtypes = []
        self._lib.projectm_pcm_get_max_samples.restype = ctypes.c_uint
        self._lib.projectm_pcm_add_float.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_uint, ctypes.c_int]
        self._lib.projectm_pcm_add_float.restype = None
        self._lib.projectm_opengl_render_frame.argtypes = [ctypes.c_void_p]
        self._lib.projectm_opengl_render_frame.restype = None
        if hasattr(self._lib, 'projectm_opengl_render_frame_fbo'):
            self._lib.projectm_opengl_render_frame_fbo.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            self._lib.projectm_opengl_render_frame_fbo.restype = None
        if hasattr(self._lib, 'projectm_set_texture_search_paths'):
            self._lib.projectm_set_texture_search_paths.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t]
            self._lib.projectm_set_texture_search_paths.restype = None
        # Callback registration functions (4.0+)
        if hasattr(self._lib, 'projectm_set_preset_switch_failed_event_callback'):
            self._lib.projectm_set_preset_switch_failed_event_callback.argtypes = [
                ctypes.c_void_p, _SwitchFailedCbType, ctypes.c_void_p
            ]
            self._lib.projectm_set_preset_switch_failed_event_callback.restype = None
        if hasattr(self._lib, 'projectm_set_preset_switch_requested_event_callback'):
            self._lib.projectm_set_preset_switch_requested_event_callback.argtypes = [
                ctypes.c_void_p, _SwitchRequestedCbType, ctypes.c_void_p
            ]
            self._lib.projectm_set_preset_switch_requested_event_callback.restype = None
        # Global log callback (4.2+)
        if hasattr(self._lib, 'projectm_set_log_callback'):
            self._lib.projectm_set_log_callback.argtypes = [
                _LogCbType, ctypes.c_bool, ctypes.c_void_p
            ]
            self._lib.projectm_set_log_callback.restype = None
        if hasattr(self._lib, 'projectm_set_log_level'):
            self._lib.projectm_set_log_level.argtypes = [ctypes.c_int, ctypes.c_bool]
            self._lib.projectm_set_log_level.restype = None

    def _install_log_callback(self) -> None:
        """Wire libprojectM's internal log into Python's logging system (global, one-time)."""
        global _global_log_cb
        if _global_log_cb is not None:
            return  # Already installed for this process
        if not hasattr(self._lib, 'projectm_set_log_callback'):
            return

        def _on_pm_log(message: bytes, level: int, _user_data) -> None:
            text = message.decode('utf-8', errors='replace').rstrip()
            py_level = _PM_TO_PY_LEVEL.get(level, logging.DEBUG)
            log.log(py_level, '[libprojectM] %s', text)

        _global_log_cb = _LogCbType(_on_pm_log)

        # Forward WARN+ always; include INFO/DEBUG only when Python logger is verbose
        pm_level = _PM_LOG_DEBUG if log.isEnabledFor(logging.DEBUG) else _PM_LOG_WARN
        if hasattr(self._lib, 'projectm_set_log_level'):
            self._lib.projectm_set_log_level(pm_level, False)
        self._lib.projectm_set_log_callback(_global_log_cb, False, None)

    @staticmethod
    def _make_load_proc():
        cb_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)

        def _resolve(name: bytes, _user_data) -> int:
            ptr = sdl2.SDL_GL_GetProcAddress(name)
            return ctypes.cast(ptr, ctypes.c_void_p).value or 0

        return cb_type(_resolve)

    @staticmethod
    def _make_gl_get_integerv():
        ptr = sdl2.SDL_GL_GetProcAddress(b'glGetIntegerv')
        if not ptr:
            raise RuntimeError('glGetIntegerv not available in active GL context')
        fn_type = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
        return fn_type(ctypes.cast(ptr, ctypes.c_void_p).value)


class ProjectMEffect(BaseEffect):
    """Embedded projectM / MilkDrop preset effect."""

    NAME = 'ProjectM Presets'
    AUTHOR = 'unicorn-viz drop-in'
    TAGS = ['projectm', 'milkdrop', 'audio', 'drop-in']
    PING_PONG_FRIENDS: list[str] = ['Psychedelic', 'Kaleidoscope', 'Tunnel']
    HELP_ENTRIES = [
        ('ProjectM Presets', 'Ctrl+M', 'Open preset manager'),
        ('ProjectM Presets', '/', 'Enter manager search mode'),
        ('ProjectM Presets', 'Ctrl+Z', 'Undo last bulk manager state change'),
        ('ProjectM Presets', 'Ctrl+Y / Ctrl+Shift+Z', 'Redo last bulk manager undo'),
        ('ProjectM Presets', 'Ctrl+N', 'Next preset'),
        ('ProjectM Presets', 'Ctrl+P', 'Previous preset'),
        ('ProjectM Presets', 'Ctrl+R', 'Random preset'),
        ('ProjectM Presets', 'Delete', 'Soft-delete selected preset to trash'),
        ('ProjectM Presets', 'Shift+Delete', 'Hard-delete selected preset'),
    ]
    _TOKEN_RE = re.compile(r'[a-z0-9]+')

    def _init(self) -> None:
        self.parameters = {
            'speed': 1.0,
            'preset_duration': float(self.config.get('preset_duration', 20.0)),
            'beat_sensitivity': float(self.config.get('beat_sensitivity', 1.0)),
        }
        self._fallback_prog = self._make_program(_VERT, _FRAG_FALLBACK)
        self._fallback_vao, self._fallback_vbo = self._fullscreen_quad(self._fallback_prog)
        self._bridge: _ProjectMBridge | None = None
        self._preset_catalog: list[_PresetCatalogItem] = []
        self._preset_paths: list[Path] = []
        self._disabled_presets: set[str] = set()
        self._deleted_presets: set[str] = set()
        self._preset_index = 0
        self._load_preset_state()
        self.refresh_preset_catalog(preserve_current=False)
        if self._preset_paths:
            self._preset_index = self.rng.integers(0, len(self._preset_paths)).item()
        self._preset_timer = 0.0
        self._dark_skip_timer: float = 0.0
        self._dark_check_needed: bool = False
        self._manager_preview_active = False
        self._active_label = 'projectM fallback'
        self._bass = 0.0
        self._mid = 0.0
        self._treble = 0.0
        self.failed_presets: dict[str, str] = {}

        try:
            library_hint = str(self.config.get('projectm_library', '')).strip()
            global _shared_bridge
            if _shared_bridge is None:
                _shared_bridge = _ProjectMBridge(library_hint)
                log.info('ProjectM bridge created')
            self._bridge = _shared_bridge
            self._bridge.configure(
                self.width,
                self.height,
                int(self.config.get('fps_hint', 60)),
                float(self.parameters['beat_sensitivity']),
                float(self.parameters['preset_duration']),
                bool(self.config.get('lock_preset', False)),
                bool(self.config.get('start_clean', False)),
                self._texture_dirs(),
            )
            self._bridge.install_event_callbacks(
                self._make_switch_failed_cb(),
                self._make_switch_requested_cb(),
            )
            self._load_start_preset()
            log.info('ProjectMEffect ready with %d preset(s)', len(self._preset_paths))
        except Exception as exc:
            self._bridge = None
            self._active_label = 'projectM unavailable'
            log.warning('ProjectMEffect falling back to shader mode: %s', exc)

    @property
    def current_label(self) -> str:
        return self._active_label

    def _preset_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        preset_dir = str(self.config.get('preset_dir', '')).strip()
        if preset_dir:
            dirs.append(Path(preset_dir).expanduser())
        preset_dirs = str(self.config.get('preset_dirs', '')).strip()
        if preset_dirs:
            dirs.extend(Path(token.strip()).expanduser() for token in preset_dirs.split(',') if token.strip())
        dirs.append(Path(__file__).parent / 'presets')

        out: list[Path] = []
        seen: set[str] = set()
        for d in dirs:
            key = str(d.resolve()) if d.exists() else str(d)
            if key in seen:
                continue
            seen.add(key)
            if d.is_dir():
                out.append(d)
        return out

    def _texture_dirs(self) -> list[str]:
        raw = self.config.get('texture_dirs', '')
        if isinstance(raw, list):
            return [t.strip() for t in raw if str(t).strip()]
        out = [token.strip() for token in str(raw).split(',') if token.strip()]
        return out

    def _exclude_dirs(self) -> frozenset[str]:
        """Return a frozenset of lowercased directory name fragments to exclude from scanning."""
        raw = self.config.get('exclude_dirs', ['! Transition'])
        if isinstance(raw, list):
            return frozenset(s.strip().lower() for s in raw if str(s).strip())
        return frozenset(s.strip().lower() for s in str(raw).split(',') if s.strip())

    def _preset_state_file(self) -> Path:
        """Return path to the persisted projectM manager state file."""
        return Path(__file__).parent / 'preset_manager_state.json'

    def _preset_trash_dir(self) -> Path:
        """Return path to the local preset quarantine directory."""
        return Path(__file__).parent / 'preset-trash'

    def _preset_states_dir(self) -> Path:
        """Return path to timestamped manager state snapshots for undo."""
        return Path(__file__).parent / 'states'

    def _write_state_snapshot(self, prefix: str, action: str) -> Path | None:
        """Persist current manager state under the given snapshot prefix."""
        states_dir = self._preset_states_dir()
        try:
            states_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning('ProjectM state snapshot dir create failed: %s', exc)
            return None

        action_token = re.sub(r'[^a-z0-9_-]+', '-', action.strip().lower()) or 'bulk'
        ts = datetime.now(timezone.utc)
        timestamp = ts.strftime('%Y%m%dT%H%M%S-%fZ')
        snapshot_path = states_dir / f'{prefix}-{timestamp}-{action_token}.json'
        payload = {
            'version': 1,
            'created_at_utc': ts.isoformat(),
            'action': action_token,
            'disabled_presets': sorted(self._disabled_presets),
            'deleted_presets': sorted(self._deleted_presets),
        }
        try:
            snapshot_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + '\n',
                encoding='utf-8',
            )
        except OSError as exc:
            log.warning('ProjectM state snapshot save failed: %s', exc)
            return None
        return snapshot_path

    def _prune_snapshot_family(self, prefix: str, keep: int = 100) -> None:
        """Retain at most keep snapshots for one snapshot family prefix."""
        states_dir = self._preset_states_dir()
        snapshots = sorted(states_dir.glob(f'{prefix}-*.json'))
        if len(snapshots) <= keep:
            return
        for old_path in snapshots[:-keep]:
            try:
                old_path.unlink()
            except OSError:
                pass

    def _clear_snapshot_family(self, prefix: str) -> None:
        """Remove all snapshots in one family prefix."""
        states_dir = self._preset_states_dir()
        for path in states_dir.glob(f'{prefix}-*.json'):
            try:
                path.unlink()
            except OSError:
                pass

    def _snapshot_bulk_state(self, action: str) -> None:
        """Persist current state before a bulk enable/disable operation."""
        if self._write_state_snapshot('state', action) is None:
            return
        # Any new bulk edit invalidates the redo chain.
        self._clear_snapshot_family('redo')
        self._prune_snapshot_family('state', keep=100)

    def _restore_state_from_snapshot(self, snapshot_path: Path) -> tuple[str, str] | None:
        """Load manager state from one snapshot file.

        Returns (action, filename) on success.
        """
        try:
            payload = json.loads(snapshot_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None

        disabled = payload.get('disabled_presets', [])
        deleted = payload.get('deleted_presets', [])
        if not isinstance(disabled, list) or not isinstance(deleted, list):
            return None

        self._disabled_presets = {str(p).strip() for p in disabled if str(p).strip()}
        self._deleted_presets = {str(p).strip() for p in deleted if str(p).strip()}
        self._save_preset_state()
        self.refresh_preset_catalog()

        if self._bridge is not None:
            if self._preset_paths:
                self._load_current_preset(False)
            else:
                self._bridge.load_preset('idle://', False)
                self._active_label = 'idle://'

        action = str(payload.get('action', 'bulk')).strip() or 'bulk'
        return action, snapshot_path.name

    def undo_last_bulk_state_change(self) -> str | None:
        """Restore the most recent bulk state snapshot and return a summary."""
        states_dir = self._preset_states_dir()
        snapshots = sorted(states_dir.glob('state-*.json'), reverse=True)
        if not snapshots:
            return None

        for snapshot_path in snapshots:
            # Snapshot current state so undo has a redo path.
            self._write_state_snapshot('redo', 'redo-step')
            restored = self._restore_state_from_snapshot(snapshot_path)
            if restored is None:
                try:
                    snapshot_path.unlink()
                except OSError:
                    pass
                continue
            try:
                snapshot_path.unlink()
            except OSError:
                pass
            self._prune_snapshot_family('redo', keep=100)
            restored_action, restored_from = restored
            return f'{restored_action} ({restored_from})'
        return None

    def redo_last_bulk_state_change(self) -> str | None:
        """Re-apply the most recently undone bulk state snapshot."""
        states_dir = self._preset_states_dir()
        snapshots = sorted(states_dir.glob('redo-*.json'), reverse=True)
        if not snapshots:
            return None

        for snapshot_path in snapshots:
            # Snapshot current state so redo can be undone again.
            self._write_state_snapshot('state', 'undo-redo-step')
            restored = self._restore_state_from_snapshot(snapshot_path)
            if restored is None:
                try:
                    snapshot_path.unlink()
                except OSError:
                    pass
                continue
            try:
                snapshot_path.unlink()
            except OSError:
                pass
            self._prune_snapshot_family('state', keep=100)
            restored_action, restored_from = restored
            return f'{restored_action} ({restored_from})'
        return None

    def _load_preset_state(self) -> None:
        """Load persisted disabled/deleted preset state from disk."""
        state_file = self._preset_state_file()
        if not state_file.exists():
            return
        try:
            payload = json.loads(state_file.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning('ProjectM preset state load failed: %s', exc)
            return

        disabled = payload.get('disabled_presets', [])
        deleted = payload.get('deleted_presets', [])
        if isinstance(disabled, list):
            self._disabled_presets = {str(p).strip() for p in disabled if str(p).strip()}
        if isinstance(deleted, list):
            self._deleted_presets = {str(p).strip() for p in deleted if str(p).strip()}

    def _save_preset_state(self) -> None:
        """Persist disabled/deleted preset state for future sessions."""
        state_file = self._preset_state_file()
        payload = {
            'version': 1,
            'disabled_presets': sorted(self._disabled_presets),
            'deleted_presets': sorted(self._deleted_presets),
        }
        try:
            state_file.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + '\n',
                encoding='utf-8',
            )
        except OSError as exc:
            log.warning('ProjectM preset state save failed: %s', exc)

    @classmethod
    def _infer_tags(
        cls,
        category_parts: tuple[str, ...],
        display_name: str,
    ) -> tuple[str, ...]:
        """Infer lightweight tags from category folders and filename tokens."""
        tags: set[str] = set()
        for part in category_parts:
            for token in cls._TOKEN_RE.findall(part.lower()):
                tags.add(token)
        for token in cls._TOKEN_RE.findall(display_name.lower()):
            if len(token) >= 4:
                tags.add(token)
        return tuple(sorted(tags))

    def _build_preset_catalog(self) -> list[_PresetCatalogItem]:
        """Scan configured preset roots and build catalog entries."""
        exclude = self._exclude_dirs()
        dark_excluded = self._load_dark_excluded()
        entries: list[_PresetCatalogItem] = []
        for root in self._preset_dirs():
            for path in root.rglob('*'):
                if path.suffix.lower() not in {'.milk', '.prjm'}:
                    continue
                if any(part.lower() in exclude for part in path.parts):
                    continue
                abs_path = str(path.resolve())
                if abs_path in dark_excluded:
                    continue
                if abs_path in self._deleted_presets:
                    continue

                rel = path.relative_to(root)
                rel_parts = rel.parts
                if len(rel_parts) >= 2:
                    pack_name = rel_parts[0]
                    category_parts = tuple(rel_parts[1:-1])
                else:
                    pack_name = root.name
                    category_parts = tuple(rel_parts[:-1])
                display_name = path.stem
                tags = self._infer_tags(category_parts, display_name)

                entries.append(
                    _PresetCatalogItem(
                        path=path,
                        display_name=display_name,
                        pack_name=pack_name,
                        category_path=category_parts,
                        tags=tags,
                        enabled=abs_path not in self._disabled_presets,
                    )
                )

        if exclude:
            log.info('ProjectM preset scan: excluded dirs matching %s', sorted(exclude))
        if dark_excluded:
            log.info('ProjectM preset scan: skipped %d dark-excluded preset(s)', len(dark_excluded))
        return sorted(
            entries,
            key=lambda item: (
                item.pack_name.lower(),
                item.category_path,
                item.display_name.lower(),
            ),
        )

    def refresh_preset_catalog(self, preserve_current: bool = True) -> None:
        """Rebuild manager catalog and enabled playback list from current state."""
        current_path: Path | None = None
        if preserve_current and self._preset_paths:
            current_path = self._preset_paths[self._preset_index % len(self._preset_paths)]

        self._preset_catalog = self._build_preset_catalog()
        self._preset_paths = [item.path for item in self._preset_catalog if item.enabled]

        if not self._preset_paths:
            self._preset_index = 0
            return

        if current_path is not None:
            for idx, path in enumerate(self._preset_paths):
                if path == current_path:
                    self._preset_index = idx
                    return

        self._preset_index = self._preset_index % len(self._preset_paths)

    @staticmethod
    def _category_key(parts: tuple[str, ...]) -> str:
        """Render a category path tuple as a slash-separated category key."""
        if not parts:
            return '(uncategorized)'
        return '/'.join(parts)

    @staticmethod
    def _normalize_category(category: str) -> tuple[str, ...]:
        """Normalize a user-provided category expression into tuple tokens."""
        normalized = category.strip().strip('/').lower()
        if normalized in {'', '(uncategorized)', 'uncategorized'}:
            return tuple()
        return tuple(token for token in normalized.split('/') if token)

    def _category_matches(
        self,
        item: _PresetCatalogItem,
        category_parts: tuple[str, ...],
    ) -> bool:
        """Return True when item is inside category_parts (subtree match)."""
        item_norm = tuple(part.lower() for part in item.category_path)
        if not category_parts:
            return not item_norm
        if len(item_norm) < len(category_parts):
            return False
        return item_norm[: len(category_parts)] == category_parts

    def list_preset_categories(self) -> dict[str, int]:
        """Return category key -> preset count for all discovered presets."""
        counts: dict[str, int] = {}
        for item in self._preset_catalog:
            key = self._category_key(item.category_path)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda pair: pair[0].lower()))

    def preset_catalog(self) -> list[dict[str, object]]:
        """Return a serializable snapshot of projectM preset catalog entries."""
        return [
            {
                'path': str(item.path),
                'display_name': item.display_name,
                'pack_name': item.pack_name,
                'category_path': list(item.category_path),
                'category_key': self._category_key(item.category_path),
                'tags': list(item.tags),
                'enabled': item.enabled,
            }
            for item in self._preset_catalog
        ]

    def enable_all_presets(self) -> int:
        """Enable all known presets and return the enabled count."""
        if self._disabled_presets:
            self._snapshot_bulk_state('enable-all')
        self._disabled_presets.clear()
        self._save_preset_state()
        self.refresh_preset_catalog()
        return len(self._preset_paths)

    def disable_all_presets(self) -> int:
        """Disable all known presets and return the remaining enabled count."""
        next_disabled = {str(item.path.resolve()) for item in self._preset_catalog}
        if next_disabled != self._disabled_presets:
            self._snapshot_bulk_state('disable-all')
            self._disabled_presets = next_disabled
            self._save_preset_state()
            self.refresh_preset_catalog()
        return len(self._preset_paths)

    def set_presets_enabled(self, preset_paths: list[str], enabled: bool) -> int:
        """Set enabled state for specific presets by absolute path strings."""
        normalized = {str(Path(p).resolve()) for p in preset_paths if str(p).strip()}
        if not normalized:
            return len(self._preset_paths)

        changed = False
        for item in self._preset_catalog:
            path_key = str(item.path.resolve())
            if path_key not in normalized:
                continue
            if enabled:
                if path_key in self._disabled_presets:
                    self._disabled_presets.discard(path_key)
                    changed = True
            else:
                if path_key not in self._disabled_presets:
                    self._disabled_presets.add(path_key)
                    changed = True

        if changed:
            self._save_preset_state()
            self.refresh_preset_catalog()
        return len(self._preset_paths)

    def enable_category(self, category: str) -> int:
        """Enable all presets inside one category subtree; return enabled count."""
        category_parts = self._normalize_category(category)
        changed = False
        for item in self._preset_catalog:
            if self._category_matches(item, category_parts):
                path_key = str(item.path.resolve())
                if path_key in self._disabled_presets:
                    if not changed:
                        self._snapshot_bulk_state('enable-category')
                    self._disabled_presets.discard(path_key)
                    changed = True
        if changed:
            self._save_preset_state()
            self.refresh_preset_catalog()
        return len(self._preset_paths)

    def disable_category(self, category: str) -> int:
        """Disable all presets inside one category subtree; return enabled count."""
        category_parts = self._normalize_category(category)
        changed = False
        for item in self._preset_catalog:
            if self._category_matches(item, category_parts):
                path_key = str(item.path.resolve())
                if path_key not in self._disabled_presets:
                    if not changed:
                        self._snapshot_bulk_state('disable-category')
                    self._disabled_presets.add(path_key)
                    changed = True
        if changed:
            self._save_preset_state()
            self.refresh_preset_catalog()
        return len(self._preset_paths)

    def isolate_category(self, category: str) -> int:
        """Enable only one category subtree and disable everything else."""
        category_parts = self._normalize_category(category)
        next_disabled: set[str] = set()
        for item in self._preset_catalog:
            path_key = str(item.path.resolve())
            if not self._category_matches(item, category_parts):
                next_disabled.add(path_key)
        if next_disabled != self._disabled_presets:
            self._snapshot_bulk_state('isolate-category')
            self._disabled_presets = next_disabled
            self._save_preset_state()
            self.refresh_preset_catalog()
        return len(self._preset_paths)

    def delete_preset(self, index: int | None = None, permanent: bool = False) -> str | None:
        """Delete one preset by enabled-list index (defaults to current preset)."""
        if not self._preset_paths:
            return None

        target_index = self._preset_index if index is None else int(index)
        target_path = self._preset_paths[target_index % len(self._preset_paths)]
        target_key = str(target_path.resolve())

        try:
            if permanent:
                target_path.unlink()
            else:
                trash_dir = self._preset_trash_dir()
                trash_dir.mkdir(parents=True, exist_ok=True)
                destination = trash_dir / target_path.name
                suffix = 1
                while destination.exists():
                    destination = trash_dir / f'{target_path.stem}-{suffix}{target_path.suffix}'
                    suffix += 1
                shutil.move(str(target_path), str(destination))
        except OSError as exc:
            log.warning('ProjectM preset delete failed: %s', exc)
            return None

        self._deleted_presets.add(target_key)
        self._disabled_presets.discard(target_key)
        self._save_preset_state()
        self.refresh_preset_catalog()

        if self._bridge is not None:
            if self._preset_paths:
                self._load_current_preset(False)
            else:
                self._bridge.load_preset('idle://', False)
                self._active_label = 'idle://'

        return target_path.stem

    def delete_presets(self, preset_paths: list[str], permanent: bool = False) -> int:
        """Delete multiple presets by absolute path strings; returns deleted count."""
        targets = {str(Path(p).resolve()) for p in preset_paths if str(p).strip()}
        if not targets:
            return 0

        deleted_count = 0
        for item in list(self._preset_catalog):
            source = item.path.resolve()
            source_key = str(source)
            if source_key not in targets:
                continue
            try:
                if permanent:
                    source.unlink()
                else:
                    trash_dir = self._preset_trash_dir()
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    destination = trash_dir / source.name
                    suffix = 1
                    while destination.exists():
                        destination = trash_dir / f'{source.stem}-{suffix}{source.suffix}'
                        suffix += 1
                    shutil.move(str(source), str(destination))
            except OSError as exc:
                log.warning('ProjectM preset delete failed: %s', exc)
                continue

            self._deleted_presets.add(source_key)
            self._disabled_presets.discard(source_key)
            deleted_count += 1

        if deleted_count <= 0:
            return 0

        self._save_preset_state()
        self.refresh_preset_catalog()

        if self._bridge is not None:
            if self._preset_paths:
                self._load_current_preset(False)
            else:
                self._bridge.load_preset('idle://', False)
                self._active_label = 'idle://'

        return deleted_count

    def _load_start_preset(self) -> None:
        if self._bridge is None:
            return
        start_preset = str(self.config.get('start_preset', '')).strip()
        if start_preset:
            for idx, path in enumerate(self._preset_paths):
                if path.name == start_preset or str(path) == start_preset:
                    self._preset_index = idx
                    self._load_current_preset(False)
                    return
        if self._preset_paths:
            self._load_current_preset(False)
        else:
            self._bridge.load_preset('idle://', False)
            self._active_label = 'idle://'

    def _load_current_preset(self, smooth: bool) -> None:
        if self._bridge is None or not self._preset_paths:
            return
        path = self._preset_paths[self._preset_index % len(self._preset_paths)]
        log.info('ProjectM loading preset [%d/%d]: %s', self._preset_index + 1, len(self._preset_paths), path.name)
        self._bridge.load_preset(str(path), smooth)
        self._active_label = path.stem
        self._preset_timer = 0.0
        self._dark_skip_timer = 0.0
        self._dark_check_needed = False

    def _make_switch_failed_cb(self) -> _SwitchFailedCbType:
        """Return a ctypes callback that logs preset load failures."""
        def _on_failed(preset_filename: bytes, message: bytes, _user_data) -> None:
            name = preset_filename.decode('utf-8', errors='replace') if preset_filename else '?'
            msg  = message.decode('utf-8', errors='replace') if message else '?'
            short = Path(name).name
            self.failed_presets[name] = msg
            log.warning('ProjectM preset failed: %s — %s', short, msg)
        return _SwitchFailedCbType(_on_failed)

    def _make_switch_requested_cb(self) -> _SwitchRequestedCbType:
        """Return a ctypes callback that logs libprojectM-initiated preset advances."""
        def _on_requested(is_hard_cut: bool, _user_data) -> None:
            log.debug('ProjectM auto-advancing preset (hard_cut=%s)', is_hard_cut)
        return _SwitchRequestedCbType(_on_requested)

    def next_preset(self) -> str | None:
        if not self._preset_paths:
            return None
        self._preset_index = (self._preset_index + 1) % len(self._preset_paths)
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    def prev_preset(self) -> str | None:
        if not self._preset_paths:
            return None
        self._preset_index = (self._preset_index - 1) % len(self._preset_paths)
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    def random_preset(self) -> str | None:
        if not self._preset_paths:
            return None
        if len(self._preset_paths) > 1:
            current = self._preset_index
            while self._preset_index == current:
                self._preset_index = random.randrange(len(self._preset_paths))
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    @property
    def preset_count(self) -> int:
        """Return the total number of loaded presets (0 when none are available)."""
        return len(self._preset_paths)

    @property
    def current_preset_index(self) -> int:
        """Return the index of the currently displayed preset."""
        return self._preset_index

    @property
    def current_preset_path(self) -> str:
        """Return the absolute path for the active preset, if one is loaded."""
        if not self._preset_paths:
            return ''
        return str(self._preset_paths[self._preset_index % len(self._preset_paths)].resolve())

    @property
    def current_position_label(self) -> str:
        """Return the HUD label used for projectM preset position display."""
        return 'PROJECTM'

    @property
    def current_position_text(self) -> str:
        """Return current preset index text for the shared HUD."""
        if not self._preset_paths:
            return '0/0'
        return f'{self._preset_index + 1}/{len(self._preset_paths)}'

    @property
    def current_index(self) -> int:
        """Return the current 1-based preset index for shared HUD consumers."""
        if not self._preset_paths:
            return 0
        return self._preset_index + 1

    @property
    def current_total(self) -> int:
        """Return the total enabled preset count for shared HUD consumers."""
        return len(self._preset_paths)

    def goto_preset(self, index: int) -> str | None:
        """Go to a specific preset by index (wraps with modulo).

        Returns the preset label, or ``None`` when no presets are loaded.
        """
        if not self._preset_paths:
            return None
        self._preset_index = int(index) % len(self._preset_paths)
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    def goto_preset_path(self, preset_path: str) -> str | None:
        """Go to a specific enabled preset by absolute path string."""
        target = str(Path(preset_path).resolve())
        for idx, path in enumerate(self._preset_paths):
            if str(path.resolve()) == target:
                self._preset_index = idx
                self._load_current_preset(bool(self.config.get('smooth_transition', True)))
                return self._active_label
        return None

    def set_manager_preview_active(self, active: bool) -> None:
        """Toggle manager preview mode to pause automatic preset mutation."""
        self._manager_preview_active = bool(active)
        if self._manager_preview_active:
            self._preset_timer = 0.0
            self._dark_skip_timer = 0.0
            self._dark_check_needed = False

    def update(self, dt: float, audio: AudioData) -> None:
        super().update(dt, audio)
        self._bass = float(audio.bass)
        self._mid = float(audio.mid)
        self._treble = float(audio.treble)
        if self._bridge is not None:
            fps_hint = int(self.config.get('fps_hint', 60)) if dt <= 0.0 else int(max(1.0, min(240.0, round(1.0 / dt))))
            self._bridge.set_timing(self.time, fps_hint)
            self._bridge.add_pcm(audio.waveform)
            self._bridge.configure(
                self.width,
                self.height,
                fps_hint,
                float(self.parameters['beat_sensitivity']),
                float(self.parameters['preset_duration']),
                bool(self.config.get('lock_preset', False)),
                bool(self.config.get('start_clean', False)),
                self._texture_dirs(),
            )

            if not self._manager_preview_active:
                self._preset_timer += dt * max(0.1, float(self.parameters['speed']))
            if (
                not self._manager_preview_active
                and not bool(self.config.get('lock_preset', False))
                and self._preset_paths
            ):
                if self._preset_timer >= max(2.0, float(self.parameters['preset_duration'])):
                    self.next_preset()
                elif bool(self.config.get('dark_skip_enabled', True)) and not self._dark_check_needed:
                    self._dark_skip_timer += dt
                    dark_after = float(self.config.get('dark_skip_after', 3.0))
                    if self._dark_skip_timer >= dark_after:
                        self._dark_check_needed = True

    def resize(self, width: int, height: int) -> None:
        super().resize(width, height)
        if self._bridge is not None:
            self._bridge.set_window_size(width, height)

    def render(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.render()
                if self._dark_check_needed and not self._manager_preview_active:
                    self._dark_check_needed = False
                    if self._is_output_dark():
                        dark_path = self._preset_paths[self._preset_index % len(self._preset_paths)]
                        log.warning('Dark preset auto-excluded: %s', dark_path.name)
                        self._save_dark_excluded_entry(dark_path)
                        self._preset_paths.pop(self._preset_index % len(self._preset_paths))
                        if self._preset_paths:
                            self._preset_index = self._preset_index % len(self._preset_paths)
                            self._load_current_preset(False)
                        else:
                            log.warning('All presets excluded — no presets remain')
                return
            except Exception as exc:
                log.warning('ProjectM render failed; switching to fallback: %s', exc)
                self._bridge.destroy()
                global _shared_bridge
                if _shared_bridge is self._bridge:
                    _shared_bridge = None
                self._bridge = None
                self._active_label = 'projectM render error'

        self._fallback_prog['iTime'].value = self.time
        self._fallback_prog['iBass'].value = self._bass
        self._fallback_prog['iMid'].value = self._mid
        self._fallback_prog['iTreble'].value = self._treble
        self._fallback_vao.render(moderngl.TRIANGLE_STRIP)

    def _is_output_dark(self) -> bool:
        """Return True when a pixel sample of the current framebuffer is near-black.

        Reads a 16×16 block from the centre of the screen using glReadPixels.
        Returns False (not dark) when the GL symbol cannot be loaded so the
        detector silently degrades rather than skipping valid presets.
        """
        glrp = _get_glReadPixels()
        if glrp is None:
            return False
        sample_w, sample_h = 100, 100
        cx = max(0, self.width  // 2 - sample_w // 2)
        cy = max(0, self.height // 2 - sample_h // 2)
        buf = (ctypes.c_uint8 * (sample_w * sample_h * 3))()
        try:
            glrp(cx, cy, sample_w, sample_h, _GL_RGB, _GL_UNSIGNED_BYTE, buf)
        except Exception as exc:
            log.debug('glReadPixels failed in dark detector: %s', exc)
            return False
        total = sum(buf)
        brightness = total / (sample_w * sample_h * 3 * 255.0)
        threshold = float(self.config.get('dark_brightness_threshold', 0.02))
        log.debug('Dark check: brightness=%.4f threshold=%.4f preset=%s',
                  brightness, threshold, self._active_label)
        return brightness < threshold

    def _dark_excluded_file(self) -> Path:
        """Return path to the persistent dark-preset exclusion list."""
        return Path(__file__).parent / 'dark_excluded.txt'

    def _load_dark_excluded(self) -> set[str]:
        """Load the set of preset absolute path strings from the exclusion file."""
        f = self._dark_excluded_file()
        if not f.exists():
            return set()
        return {line.strip() for line in f.read_text(encoding='utf-8').splitlines() if line.strip()}

    def _save_dark_excluded_entry(self, path: Path) -> None:
        """Append a single preset path to the persistent exclusion file."""
        f = self._dark_excluded_file()
        try:
            with f.open('a', encoding='utf-8') as fh:
                fh.write(str(path.resolve()) + '\n')
        except OSError as exc:
            log.warning('Could not write dark_excluded.txt: %s', exc)

    def destroy(self) -> None:
        # Intentionally keep the shared bridge alive across effect switches to
        # avoid costly projectM re-initialization churn.
        self._bridge = None
        self._fallback_vao.release()
        self._fallback_vbo.release()
        self._fallback_prog.release()


def _shutdown_shared_bridge() -> None:
    global _shared_bridge
    if _shared_bridge is not None:
        bridge = _shared_bridge
        _shared_bridge = None

        def _destroy_worker() -> None:
            try:
                bridge.destroy()
            except Exception:
                pass

        worker = threading.Thread(
            target=_destroy_worker,
            name='uv-projectm-destroy',
            daemon=True,
        )
        worker.start()
        worker.join(timeout=1.0)


atexit.register(_shutdown_shared_bridge)
