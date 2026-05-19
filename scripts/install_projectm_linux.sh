#!/usr/bin/env bash
set -euo pipefail

echo "[projectm-01] Linux runtime helper"

auto_detect() {
  if command -v dnf >/dev/null 2>&1; then
    echo "Detected dnf-based distro"
    echo "Try: sudo dnf install projectm projectm-pulseaudio"
  elif command -v apt >/dev/null 2>&1; then
    echo "Detected apt-based distro"
    echo "Try: sudo apt install libprojectm4v5 libprojectm-dev"
  elif command -v pacman >/dev/null 2>&1; then
    echo "Detected pacman-based distro"
    echo "Try: sudo pacman -S projectm"
  else
    echo "Unknown package manager. Install libprojectM 4 runtime manually."
  fi
}

auto_detect

echo
echo "Checking runtime via helper script..."
python3 "$(dirname "$0")/check_projectm_runtime.py" || true

echo
echo "Preset pack location: drop-ins/projectm-01/presets/"
echo "You can add .milk/.prjm packs there (recursively scanned)."
