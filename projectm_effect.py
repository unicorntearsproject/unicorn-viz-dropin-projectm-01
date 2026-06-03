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
import threading
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
        ('ProjectM Presets', 'Ctrl+N', 'Next preset'),
        ('ProjectM Presets', 'Ctrl+P', 'Previous preset'),
        ('ProjectM Presets', 'Ctrl+R', 'Random preset'),
    ]

    def _init(self) -> None:
        self.parameters = {
            'speed': 1.0,
            'preset_duration': float(self.config.get('preset_duration', 20.0)),
            'beat_sensitivity': float(self.config.get('beat_sensitivity', 1.0)),
        }
        self._fallback_prog = self._make_program(_VERT, _FRAG_FALLBACK)
        self._fallback_vao, self._fallback_vbo = self._fullscreen_quad(self._fallback_prog)
        self._bridge: _ProjectMBridge | None = None
        self._preset_paths = self._scan_presets()
        self._preset_index = self.rng.integers(0, max(1, len(self._preset_paths))).item()
        self._preset_timer = 0.0
        self._dark_skip_timer: float = 0.0
        self._dark_check_needed: bool = False
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

    def _scan_presets(self) -> list[Path]:
        exclude = self._exclude_dirs()
        paths: set[Path] = set()
        for root in self._preset_dirs():
            for path in root.rglob('*'):
                if path.suffix.lower() not in {'.milk', '.prjm'}:
                    continue
                if any(part.lower() in exclude for part in path.parts):
                    continue
                paths.add(path)
        if exclude:
            log.info('ProjectM preset scan: excluded dirs matching %s', sorted(exclude))
        return sorted(paths)

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

    def goto_preset(self, index: int) -> str | None:
        """Go to a specific preset by index (wraps with modulo).

        Returns the preset label, or ``None`` when no presets are loaded.
        """
        if not self._preset_paths:
            return None
        self._preset_index = int(index) % len(self._preset_paths)
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

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

            self._preset_timer += dt * max(0.1, float(self.parameters['speed']))
            if not bool(self.config.get('lock_preset', False)) and self._preset_paths:
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
                if self._dark_check_needed:
                    self._dark_check_needed = False
                    if self._is_output_dark():
                        log.info('Dark preset detected after %.1fs, skipping: %s',
                                 self._dark_skip_timer, self._active_label)
                        self.next_preset()
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
