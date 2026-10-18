#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
UV_VERSION=0.13.1
UV_SHA256_X64=eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220
UV_SHA256_ARM64=9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14
UV_HOME=""
PYTHON_ENV="$ROOT/.venv"
PYTHON_ENV_OWNED=0

cleanup() {
  [ "$PYTHON_ENV_OWNED" -eq 1 ] && rm -rf -- "$PYTHON_ENV"
  [ -n "$UV_HOME" ] && rm -rf -- "$UV_HOME"
  return 0
}
trap cleanup EXIT INT TERM

uv_binary=$(command -v uv 2>/dev/null || true)
if [ -z "$uv_binary" ] || [ "$("$uv_binary" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]; then
  [ "$(uname -s)" = Linux ] || {
    printf 'uv %s is required on this platform\n' "$UV_VERSION" >&2
    exit 1
  }
  case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64; digest=$UV_SHA256_X64 ;;
    aarch64 | arm64) arch=aarch64; digest=$UV_SHA256_ARM64 ;;
    *) printf 'no pinned uv artifact for %s\n' "$(uname -m)" >&2; exit 1 ;;
  esac
  UV_HOME=$(mktemp -d "${TMPDIR:-/tmp}/ci-workflows-uv.XXXXXX")
  curl -fsSL \
    "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${arch}-unknown-linux-gnu.tar.gz" \
    -o "$UV_HOME/uv.tar.gz"
  printf '%s  %s\n' "$digest" "$UV_HOME/uv.tar.gz" | sha256sum --check --status
  tar -xzf "$UV_HOME/uv.tar.gz" --strip-components=1 -C "$UV_HOME"
  uv_binary=$UV_HOME/uv
fi

cd "$ROOT"
if [ ! -e "$PYTHON_ENV" ]; then
  python_binary=$("$uv_binary" python find 3.13.14)
  # A symlinked interpreter records the link's directory as the venv `home`,
  # so a --copies binary resolves prefix/../lib there and loses its stdlib.
  python_binary=$("$python_binary" -c 'import os,sys; print(os.path.realpath(sys.executable))')
  "$python_binary" -I -B -m venv --copies "$PYTHON_ENV"
  PYTHON_ENV_OWNED=1
fi
[ -d "$PYTHON_ENV" ] && [ ! -L "$PYTHON_ENV" ] || {
  printf '%s must be a real directory\n' "$PYTHON_ENV" >&2
  exit 1
}
"$uv_binary" pip install --python "$PYTHON_ENV/bin/python" \
  --require-hashes -r requirements-ci.txt
"$PYTHON_ENV/bin/python" -I -B scripts/check_python_execution_contract.py \
  --launch validate_all.py -- --tier core
