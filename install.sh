#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DROPIN_NAME="projectm-01"
INSTALL_ROOT="${UV_DROPINS_ROOT:-${HOME}/.local/share/unicorn-viz}"
UNINSTALL=0
DRY_RUN=0

usage() {
  cat <<'EOF'
projectM drop-in bundle installer

Usage:
  bash install.sh [options]

Options:
  --root <dir>      Base installation root (default: ~/.local/share/unicorn-viz)
  --uninstall       Remove the installed drop-in bundle
  --dry-run         Print actions without executing them
  -h, --help        Show this help text
EOF
}

log() {
  echo "[projectm-01] $*"
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry-run: $*"
    return 0
  fi
  "$@"
}

stage_tree() {
  local source_dir="$1"
  local target_dir="$2"
  local entry

  shopt -s dotglob nullglob
  for entry in "$source_dir"/* "$source_dir"/.[!.]* "$source_dir"/..?*; do
    [[ -e "$entry" ]] || continue
    case "$(basename "$entry")" in
      install.sh|.git|.gitignore|__pycache__)
        continue
        ;;
    esac
    run cp -a "$entry" "$target_dir/"
  done
  shopt -u dotglob nullglob
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      INSTALL_ROOT="$2"
      shift 2
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[projectm-01] ERROR: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

TARGET_DIR="${INSTALL_ROOT%/}/drop-ins/${DROPIN_NAME}"

if [[ "$UNINSTALL" -eq 1 ]]; then
  log "Removing ${TARGET_DIR}"
  run rm -rf "$TARGET_DIR"
  log "Uninstall complete"
  exit 0
fi

log "Installing into ${TARGET_DIR}"
run rm -rf "$TARGET_DIR"
run mkdir -p "$TARGET_DIR"
stage_tree "$SCRIPT_DIR" "$TARGET_DIR"
log "Install complete"