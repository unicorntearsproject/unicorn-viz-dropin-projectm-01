"""projectM preset host drop-in for Unicorn Viz (Phase 1)."""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import random
import re
from collections import deque
from pathlib import Path

import moderngl
import numpy as np
import sdl2

from unicornviz.effects.base import AudioData, BaseEffect

log = logging.getLogger(__name__)

_GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
_PROJECTM_MONO = 1

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
uniform float iZoom;
in vec2 v_uv;
out vec4 fragColor;

vec3 palette(float t) {
    vec3 a = vec3(0.52, 0.50, 0.58);
    vec3 b = vec3(0.45, 0.42, 0.40);
    vec3 c = vec3(1.0, 1.0, 1.0);
    vec3 d = vec3(0.20, 0.11, 0.63);
    return clamp(a + b * cos(6.28318 * (c * t + d)), 0.0, 1.0);
}

void main() {
    vec2 uv = v_uv * 2.0 - 1.0;
    uv /= max(iZoom, 0.01);
    float r = length(uv);
    float a = atan(uv.y, uv.x);
    float wave = sin(a * 6.0 - iTime * (0.9 + iMid * 1.1) + r * 10.0);
    float ring = smoothstep(0.72 + iBass * 0.08, 0.07, abs(r - 0.45 - wave * 0.07));
    float haze = exp(-3.1 * r) * (0.35 + iTreble * 0.9);
    vec3 col = palette(r * 0.9 + iTime * 0.08 + wave * 0.08);
    col *= ring + haze + 0.18;
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""


class _ProjectMBridge:
    """Minimal ctypes bridge for projectM 4.x C API."""

    def __init__(self, library_hint: str = '') -> None:
        self._lib = self._load_library(library_hint)
        self._bind_symbols()
        self._load_proc_cb = self._make_load_proc()
        self._handle = self._lib.projectm_create_with_opengl_load_proc(self._load_proc_cb, None)
        if not self._handle:
            raise RuntimeError('projectM instance creation failed')
        self._gl_get_integerv = self._make_gl_get_integerv()

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
        if texture_dirs and hasattr(self._lib, 'projectm_set_texture_search_paths'):
            arr = (ctypes.c_char_p * len(texture_dirs))(
                *[path.encode('utf-8') for path in texture_dirs]
            )
            self._lib.projectm_set_texture_search_paths(self._handle, arr, len(texture_dirs))

    def set_window_size(self, width: int, height: int) -> None:
        self._lib.projectm_set_window_size(self._handle, width, height)

    def set_timing(self, elapsed_time: float, fps_hint: int) -> None:
        self._lib.projectm_set_frame_time(self._handle, float(elapsed_time))
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

        seen: set[str] = set()
        ordered = [c for c in candidates if c and not (c in seen or seen.add(c))]
        required_symbols = (
            'projectm_create_with_opengl_load_proc',
            'projectm_destroy',
            'projectm_load_preset_file',
            'projectm_pcm_add_float',
            'projectm_opengl_render_frame',
        )
        for candidate in ordered:
            try:
                lib = ctypes.CDLL(candidate)
                if all(hasattr(lib, sym) for sym in required_symbols):
                    return lib
            except OSError:
                continue
        raise RuntimeError('libprojectM 4.x not found')

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
            raise RuntimeError('glGetIntegerv not available from SDL_GL_GetProcAddress')
        fn_type = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
        return fn_type(ctypes.cast(ptr, ctypes.c_void_p).value)


def _make_projectm_bridge(
    library_hint: str,
) -> '_ProjectMBridge':
    """Create a projectM bridge for the supported v4 C API runtime."""
    return _ProjectMBridge(library_hint)


class ProjectMEffect(BaseEffect):
    """Embedded projectM preset renderer."""

    _LAST_PRESET_PATH: str | None = None
    _LAST_PACK_NAME: str | None = None

    NAME = 'ProjectM Presets'
    AUTHOR = 'unicorn-viz drop-in'
    TAGS = ['projectm', 'milkdrop', 'audio', 'drop-in']
    HELP_ENTRIES = [
        ('ProjectM Presets', 'Ctrl+N', 'Next preset'),
        ('ProjectM Presets', 'Ctrl+P', 'Previous preset'),
        ('ProjectM Presets', 'Ctrl+R', 'Random preset'),
        ('ProjectM Presets', 'Ctrl+Shift+N', 'Next preset pack'),
        ('ProjectM Presets', 'Ctrl+Shift+P', 'Prev preset pack'),
        ('ProjectM Presets', 'Ctrl+Shift+R', 'Random preset pack'),
    ]

    def _init(self) -> None:
        self.parameters = {
            'speed': 1.0,
            'preset_duration': float(self.config.get('preset_duration', 20.0)),
            'beat_sensitivity': float(self.config.get('beat_sensitivity', 1.0)),
            'zoom': 1.0,
        }
        self._fallback_prog = self._make_program(_VERT, _FRAG_FALLBACK)
        self._fallback_vao, self._fallback_vbo = self._fullscreen_quad(self._fallback_prog)
        self._bridge: '_ProjectMBridge | None' = None
        self._preset_packs = self._scan_packs()
        self._pack_names = sorted(self._preset_packs.keys())
        self._pack_index = 0
        self._preset_paths = self._preset_packs[self._pack_names[0]] if self._pack_names else []
        self._preset_index = 0
        self._preset_timer = 0.0
        self._preset_cycle: list[int] = []
        self._preset_cycle_pos: int = 0
        self._preset_recent: deque[int] = deque(maxlen=6)
        self._active_label = 'projectM fallback'
        self._short_label = 'projectM fallback'
        self._bass = 0.0
        self._mid = 0.0
        self._treble = 0.0

        # Choose startup pack/preset regardless of whether native init succeeds.
        self._select_start_pack()
        self._select_start_preset()

        try:
            self._bridge = _make_projectm_bridge(
                str(self.config.get('projectm_library', '')).strip(),
            )
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
            self._load_start_preset()
            log.info('ProjectMEffect initialized with %d preset(s)', len(self._preset_paths))
        except Exception as exc:
            self._bridge = None
            self._load_current_preset(False)
            self._active_label = f'{self._active_label} [projectM unavailable: {exc}]'
            log.warning('ProjectMEffect fallback mode: %s', exc)

    @property
    def current_label(self) -> str:
        label = self._short_label.strip()
        # Strip pack prefix when present ("pack: preset").
        if ':' in label:
            label = label.split(':', 1)[1].strip()

        # Remove common boilerplate words and collapse whitespace.
        label = re.sub(r'(?i)\b(project\s*m|projectm|presets?|preset)\b', '', label)
        label = re.sub(r'\s+', ' ', label).strip(' -:_')
        if not label:
            label = 'Unknown'
        return f'pM :: {label}'

    @property
    def current_debug_label(self) -> str:
        return self._active_label

    @property
    def current_position_label(self) -> str:
        return 'PRESET IDX'

    @property
    def current_position_text(self) -> str:
        if not self._preset_paths:
            return '-/-'
        return f'{(self._preset_index % len(self._preset_paths)) + 1}/{len(self._preset_paths)}'

    @property
    def current_variant_label(self) -> str:
        return 'PACK'

    @property
    def current_variant_text(self) -> str:
        if not self._pack_names:
            return '-/-'
        return f'{self._pack_names[self._pack_index]} ({self._pack_index + 1}/{len(self._pack_names)})'

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
        raw = str(self.config.get('texture_dirs', '')).strip()
        out = [token.strip() for token in raw.split(',') if token.strip()]
        # Auto-add common public texture pack directory when present.
        auto_tex = Path(__file__).parent / 'presets' / 'presets-milkdrop-texture-pack'
        if auto_tex.is_dir():
            out.append(str(auto_tex))
        # De-duplicate while preserving order.
        seen: set[str] = set()
        uniq: list[str] = []
        for item in out:
            if item not in seen:
                seen.add(item)
                uniq.append(item)
        return uniq

    def _scan_packs(self) -> dict[str, list[Path]]:
        packs: dict[str, list[Path]] = {}
        for base in self._preset_dirs():
            # Treat each top-level directory under base as its own pack.
            top_dirs = [d for d in base.iterdir() if d.is_dir()]
            if top_dirs:
                for pack_dir in sorted(top_dirs):
                    key = pack_dir.name
                    for p in pack_dir.rglob('*'):
                        if p.suffix.lower() in {'.milk', '.prjm'}:
                            packs.setdefault(key, []).append(p)

        for key, values in list(packs.items()):
            packs[key] = sorted(set(values))
        # Drop any accidentally empty buckets.
        packs = {k: v for k, v in packs.items() if v}
        return packs

    def _rebuild_preset_cycle(self, avoid_index: int | None = None) -> None:
        self._preset_cycle = list(range(len(self._preset_paths)))
        random.shuffle(self._preset_cycle)
        blocked = set(self._preset_recent)
        if avoid_index is not None:
            blocked.add(avoid_index)
        if len(self._preset_cycle) > 1 and self._preset_cycle[0] in blocked:
            for i, idx in enumerate(self._preset_cycle):
                if idx not in blocked:
                    self._preset_cycle[0], self._preset_cycle[i] = self._preset_cycle[i], self._preset_cycle[0]
                    break
        self._preset_cycle_pos = 0

    def _select_pack(self, new_index: int) -> None:
        if not self._pack_names:
            self._preset_paths = []
            self._preset_index = 0
            return
        self._pack_index = new_index % len(self._pack_names)
        self._preset_paths = self._preset_packs[self._pack_names[self._pack_index]]
        self._preset_index = 0
        self._preset_recent.clear()
        self._rebuild_preset_cycle(avoid_index=None)

    def _select_start_pack(self) -> None:
        """Pick a startup pack, preferring a different one than last activation."""
        if not self._pack_names:
            return
        if len(self._pack_names) == 1:
            self._select_pack(0)
            return

        last = self.__class__._LAST_PACK_NAME
        candidates = [i for i, name in enumerate(self._pack_names) if name != last]
        if not candidates:
            candidates = list(range(len(self._pack_names)))
        self._select_pack(random.choice(candidates))

    def _select_start_preset(self) -> None:
        """Pick a startup preset with anti-repeat against last activation."""
        if not self._preset_paths:
            self._preset_index = 0
            return

        last_path = self.__class__._LAST_PRESET_PATH
        if len(self._preset_paths) == 1:
            self._preset_index = 0
            return

        candidates = [
            i for i, p in enumerate(self._preset_paths)
            if str(p) != last_path
        ]
        if not candidates:
            candidates = list(range(len(self._preset_paths)))
        self._preset_index = random.choice(candidates)
        self._rebuild_preset_cycle(avoid_index=self._preset_index)

    def _load_start_preset(self) -> None:
        start_preset = str(self.config.get('start_preset', '')).strip()
        if start_preset:
            matched_pack_index: int | None = None
            matched_preset_index: int | None = None
            for pack_index, pack_name in enumerate(self._pack_names):
                for preset_index, p in enumerate(self._preset_packs.get(pack_name, [])):
                    if p.name == start_preset or str(p) == start_preset:
                        matched_pack_index = pack_index
                        matched_preset_index = preset_index
                        break
                if matched_pack_index is not None:
                    break
            if matched_pack_index is not None and matched_preset_index is not None:
                self._select_pack(matched_pack_index)
                self._preset_index = matched_preset_index
                self._load_current_preset(False)
                return
        # No explicit start preset: shuffle startup pack/preset for variety.
        if self._preset_paths:
            self._load_current_preset(False)
        else:
            self._active_label = 'idle (no presets)'
            self._short_label = self._active_label

    def _load_current_preset(self, smooth: bool) -> None:
        if not self._preset_paths:
            return
        path = self._preset_paths[self._preset_index % len(self._preset_paths)]
        pack_name = self._pack_names[self._pack_index] if self._pack_names else 'pack'
        if self._bridge is not None:
            self._bridge.load_preset(str(path), smooth)
            self._active_label = f'{pack_name}: {path.stem}'
        else:
            # Keep UI feedback meaningful even if native runtime is unavailable.
            self._active_label = f'{pack_name}: {path.stem} (native unavailable)'
        self._short_label = f'{pack_name}: {path.stem}'
        self._preset_timer = 0.0
        self._preset_recent.append(self._preset_index)
        self.__class__._LAST_PRESET_PATH = str(path)
        self.__class__._LAST_PACK_NAME = pack_name

    def next_preset(self) -> str | None:
        if not self._preset_paths:
            return None
        self._preset_index = (self._preset_index + 1) % len(self._preset_paths)
        self._rebuild_preset_cycle(avoid_index=self._preset_index)
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    def prev_preset(self) -> str | None:
        if not self._preset_paths:
            return None
        self._preset_index = (self._preset_index - 1) % len(self._preset_paths)
        self._rebuild_preset_cycle(avoid_index=self._preset_index)
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    def random_preset(self) -> str | None:
        if not self._preset_paths:
            return None
        if not self._preset_cycle or self._preset_cycle_pos >= len(self._preset_cycle):
            self._rebuild_preset_cycle(avoid_index=self._preset_index)
        self._preset_index = self._preset_cycle[self._preset_cycle_pos]
        self._preset_cycle_pos += 1
        self._load_current_preset(bool(self.config.get('smooth_transition', True)))
        return self._active_label

    def next_variant(self) -> str | None:
        if not self._pack_names:
            return None
        self._select_pack(self._pack_index + 1)
        if self._preset_paths:
            self._load_current_preset(False)
        return self.current_variant_text

    def prev_variant(self) -> str | None:
        if not self._pack_names:
            return None
        self._select_pack(self._pack_index - 1)
        if self._preset_paths:
            self._load_current_preset(False)
        return self.current_variant_text

    def random_variant(self) -> str | None:
        if not self._pack_names:
            return None
        if len(self._pack_names) > 1:
            new_idx = self._pack_index
            while new_idx == self._pack_index:
                new_idx = random.randrange(len(self._pack_names))
            self._select_pack(new_idx)
        if self._preset_paths:
            self._load_current_preset(False)
        return self.current_variant_text

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
                    self.random_preset()

    def resize(self, width: int, height: int) -> None:
        super().resize(width, height)
        if self._bridge is not None:
            self._bridge.set_window_size(width, height)

    def render(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.render()
                return
            except Exception as exc:
                log.warning('ProjectM render failed; switching fallback: %s', exc)
                self._bridge.destroy()
                self._bridge = None
                self._active_label = 'projectM render error'

        self._fallback_prog['iTime'].value = self.time
        self._fallback_prog['iBass'].value = self._bass
        self._fallback_prog['iMid'].value = self._mid
        self._fallback_prog['iTreble'].value = self._treble
        self._fallback_prog['iZoom'].value = float(self.parameters['zoom'])
        self._fallback_vao.render(moderngl.TRIANGLE_STRIP)

    def destroy(self) -> None:
        if self._bridge is not None:
            self._bridge.destroy()
            self._bridge = None
        self._fallback_vao.release()
        self._fallback_vbo.release()
        self._fallback_prog.release()
