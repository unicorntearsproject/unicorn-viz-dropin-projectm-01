#!/usr/bin/env bash
#
# fetch_presets.sh — clone/update projectM preset & texture packs.
#
# Each pack is an upstream git repo cloned as its own subdirectory under
# presets/. The ProjectMEffect drop-in recursively scans presets/ for
# .milk / .prjm files, so adding a pack is just cloning it here.
#
# The presets/ directory is git-ignored on purpose: preset packs are large
# and fetched on demand rather than committed to this drop-in's repo.
#
# Usage:
#   bash fetch_presets.sh                 # clone/update the default pack set
#   bash fetch_presets.sh <git-url> ...   # also fetch extra packs by URL
#   bash fetch_presets.sh --list          # print the default pack set and exit
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRESETS_DIR="${SCRIPT_DIR}/presets"

# Default curated pack set. Add a line here to make a pack part of the default
# fetch. Preset packs and texture packs are both just git repos.
DEFAULT_PACKS=(
  "https://github.com/projectM-visualizer/presets-cream-of-the-crop.git"
  "https://github.com/projectM-visualizer/presets-milkdrop-original.git"
  "https://github.com/projectM-visualizer/presets-milkdrop-texture-pack.git"
)

log() { echo "[projectm-01] $*"; }

if [[ "${1:-}" == "--list" ]]; then
  printf '%s\n' "${DEFAULT_PACKS[@]}"
  exit 0
fi

# Extra packs passed on the command line are appended to the default set.
PACKS=("${DEFAULT_PACKS[@]}")
if [[ $# -gt 0 ]]; then
  PACKS+=("$@")
fi

mkdir -p "$PRESETS_DIR"
cd "$PRESETS_DIR"

for url in "${PACKS[@]}"; do
  name="$(basename "${url%.git}")"
  if [[ -d "$name/.git" ]]; then
    log "Updating ${name}"
    git -C "$name" pull --ff-only --depth 1 || log "WARN: update failed for ${name}"
  else
    log "Cloning ${name}"
    git clone --depth 1 "$url" "$name"
  fi
done

milk_count="$(find "$PRESETS_DIR" -type f \( -iname '*.milk' -o -iname '*.prjm' \) | wc -l)"
log "Done. ${milk_count} preset file(s) available under presets/"
