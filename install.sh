#!/usr/bin/env bash
set -euo pipefail

is_sourced() {
  if [ -n "${ZSH_VERSION-}" ]; then
    case "${ZSH_EVAL_CONTEXT-}" in
      *:file) return 0 ;;
    esac
  fi

  if [ -n "${BASH_VERSION-}" ]; then
    [[ "${BASH_SOURCE[0]}" != "$0" ]]
    return
  fi

  return 1
}

ROOT_DIR="$(
  CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd
)"
PIPX_BIN_DIR="${PIPX_BIN_DIR:-$HOME/.local/bin}"
SCRIPT_MODE="executed"

if is_sourced; then
  SCRIPT_MODE="sourced"
fi

ensure_pipx() {
  if command -v pipx >/dev/null 2>&1; then
    return 0
  fi

  if command -v brew >/dev/null 2>&1; then
    echo "Installing pipx with Homebrew..."
    brew install pipx
  elif command -v python3 >/dev/null 2>&1; then
    echo "Installing pipx with python3 -m pip --user..."
    python3 -m pip install --user pipx
  else
    echo "pipx is required, but neither brew nor python3 is available." >&2
    return 1
  fi
}

ensure_path_now() {
  case ":$PATH:" in
    *":$PIPX_BIN_DIR:"*) ;;
    *) export PATH="$PATH:$PIPX_BIN_DIR" ;;
  esac
}

refresh_shell_command_cache() {
  if command -v rehash >/dev/null 2>&1; then
    rehash >/dev/null 2>&1 || true
    return
  fi
  hash -r >/dev/null 2>&1 || true
}

main() {
  ensure_pipx
  pipx ensurepath >/dev/null || true
  ensure_path_now

  echo "Installing Local Codex Usage Viewer from this checkout..."
  pipx install "$ROOT_DIR" --force >/dev/null

  refresh_shell_command_cache

  if command -v cuv >/dev/null 2>&1; then
    echo
    echo "Ready."
    echo "Run: cuv"
    return 0
  fi

  echo
  echo "Install completed, but cuv is not visible in this shell yet."
  if [ "$SCRIPT_MODE" = "executed" ]; then
    echo "For current-shell auto-heal, source this script instead:"
    echo "  source \"$ROOT_DIR/install.sh\""
  fi
  echo "Otherwise open a new terminal and run: cuv"
}

main "$@"
