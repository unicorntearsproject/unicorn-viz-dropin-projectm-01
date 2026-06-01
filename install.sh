#!/usr/bin/env bash
# install.sh — projectm-01 drop-in installer
#
# Builds and installs libprojectM 4.x from source, then optionally fetches
# the classic MilkDrop preset collection into this drop-in's presets/ dir.
#
# Usage:
#   ./install.sh [--no-presets] [--prefix /usr/local]
#
# Options:
#   --no-presets   Skip fetching preset packs
#   --prefix PATH  Installation prefix (default: /usr/local)
#
# Requirements (handled automatically for dnf/apt/pacman):
#   cmake >= 3.20, gcc-c++, glm-devel, mesa-libGL-devel, mesa-libEGL-devel
#
# The built library is installed system-wide (requires sudo).
# Preset packs are installed locally into the presets/ subdirectory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX="/usr/local"
FETCH_PRESETS=1
BUILD_DIR="/tmp/projectm-build-$$"
SRC_DIR="/tmp/projectm-src-$$"
PROJECTM_REPO="https://github.com/projectM-visualizer/projectm.git"
PRESETS_CLASSIC_REPO="https://github.com/projectM-visualizer/presets-projectm-classic.git"
LIB_NAME="projectM-4"

# ── argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --no-presets) FETCH_PRESETS=0 ;;
        --prefix=*) PREFIX="${arg#--prefix=}" ;;
        --prefix)   shift; PREFIX="$1" ;;
    esac
done

# ── helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[projectm-01] INFO  $*"; }
warn()  { echo "[projectm-01] WARN  $*" >&2; }
die()   { echo "[projectm-01] ERROR $*" >&2; exit 1; }

have() { command -v "$1" &>/dev/null; }

need_sudo() {
    if [[ $EUID -eq 0 ]]; then return; fi
    have sudo || die "sudo is required but not available"
}

# ── OS detection & dependency install ────────────────────────────────────────
install_build_deps() {
    if have dnf; then
        info "Installing build deps via dnf …"
        sudo dnf install -y \
            cmake \
            gcc-c++ \
            glm-devel \
            mesa-libGL-devel \
            mesa-libEGL-devel \
            libGL-devel \
            git
    elif have apt-get; then
        info "Installing build deps via apt …"
        sudo apt-get install -y \
            cmake \
            g++ \
            libglm-dev \
            libgl-dev \
            libegl-dev \
            git
    elif have pacman; then
        info "Installing build deps via pacman …"
        sudo pacman -S --noconfirm \
            cmake \
            gcc \
            glm \
            mesa \
            git
    else
        warn "Unknown package manager — please install cmake, g++, glm, libGL, libEGL manually"
    fi
}

# ── library detection ─────────────────────────────────────────────────────────
library_installed() {
    # Returns 0 (true) if a usable libprojectM-4.so is found
    if python3 -c "
import ctypes, ctypes.util, sys
lib = ctypes.util.find_library('${LIB_NAME}')
if not lib:
    sys.exit(1)
h = ctypes.CDLL(lib)
if not hasattr(h, 'projectm_create_with_opengl_load_proc'):
    sys.exit(1)
" 2>/dev/null; then
        return 0
    fi
    return 1
}

# ── build + install libprojectM ───────────────────────────────────────────────
build_and_install() {
    need_sudo
    install_build_deps

    local tag
    tag="$(git ls-remote --tags "${PROJECTM_REPO}" | grep -oP 'refs/tags/\K[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
    [[ -n "$tag" ]] || tag="4.1.0"
    info "Building libprojectM ${tag} from source …"

    git clone --depth=1 --branch "${tag}" \
        "${PROJECTM_REPO}" "${SRC_DIR}" 2>&1 | grep -v '^remote:'
    git -C "${SRC_DIR}" submodule update --init --recursive --depth=1

    mkdir -p "${BUILD_DIR}"
    cmake -S "${SRC_DIR}" -B "${BUILD_DIR}" \
        -DCMAKE_BUILD_TYPE=Release \
        -DENABLE_SDL=OFF \
        -DENABLE_TESTING=OFF \
        -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
        -DBUILD_SHARED_LIBS=ON
    cmake --build "${BUILD_DIR}" --parallel "$(nproc)"
    sudo cmake --install "${BUILD_DIR}"

    # Ensure the lib prefix is in ldconfig on Linux
    local lib_dir="${PREFIX}/lib64"
    [[ -d "${lib_dir}" ]] || lib_dir="${PREFIX}/lib"
    local conf_file="/etc/ld.so.conf.d/local-lib64.conf"
    if [[ -d "/etc/ld.so.conf.d" ]]; then
        if ! grep -qF "${lib_dir}" /etc/ld.so.conf.d/*.conf 2>/dev/null && \
           ! grep -qF "${lib_dir}" /etc/ld.so.conf 2>/dev/null; then
            info "Adding ${lib_dir} to ldconfig …"
            echo "${lib_dir}" | sudo tee "${conf_file}" > /dev/null
        fi
        sudo ldconfig
    fi

    rm -rf "${SRC_DIR}" "${BUILD_DIR}"
}

# ── preset fetch ──────────────────────────────────────────────────────────────
fetch_presets() {
    local preset_root="${SCRIPT_DIR}/presets"
    mkdir -p "${preset_root}"

    if [[ -d "${preset_root}/classic/.git" ]]; then
        info "Classic preset pack already present — skipping clone"
        return
    fi

    info "Cloning classic projectM preset pack (~4200 presets) …"
    if git clone --depth=1 "${PRESETS_CLASSIC_REPO}" "${preset_root}/classic"; then
        local count
        count="$(find "${preset_root}/classic" -name '*.milk' | wc -l)"
        info "Preset pack installed: ${count} presets at ${preset_root}/classic/"
    else
        warn "Could not clone preset pack — you can fetch it manually:"
        warn "  git clone --depth=1 ${PRESETS_CLASSIC_REPO} ${preset_root}/classic"
    fi
}

# ── verify ────────────────────────────────────────────────────────────────────
verify() {
    info "Verifying ctypes load …"
    python3 -c "
import ctypes, ctypes.util, sys
lib_name = ctypes.util.find_library('${LIB_NAME}')
if not lib_name:
    print('[projectm-01] ERROR  libprojectM-4 not found by ctypes.util.find_library')
    sys.exit(1)
h = ctypes.CDLL(lib_name)
needed = [
    'projectm_create_with_opengl_load_proc',
    'projectm_destroy',
    'projectm_pcm_add_float',
    'projectm_opengl_render_frame_fbo',
    'projectm_load_preset_file',
]
missing = [s for s in needed if not hasattr(h, s)]
if missing:
    print(f'[projectm-01] ERROR  missing symbols: {missing}')
    sys.exit(1)
print(f'[projectm-01] INFO   libprojectM-4 OK  ({lib_name}) — all {len(needed)} symbols present')
"
}

# ── main ──────────────────────────────────────────────────────────────────────
main() {
    if library_installed; then
        info "libprojectM-4 already installed — skipping build"
    else
        info "libprojectM-4 not found — building from source …"
        build_and_install
    fi

    verify

    if [[ $FETCH_PRESETS -eq 1 ]]; then
        fetch_presets
    fi

    info "Done.  You can now enable projectM in config.toml."
}

main
