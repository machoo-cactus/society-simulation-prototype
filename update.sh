#!/usr/bin/env bash
set -euo pipefail

skip_pull=false
if [[ "${1:-}" == "--skip-pull" ]]; then
  skip_pull=true
elif [[ $# -gt 0 ]]; then
  printf 'Usage: %s [--skip-pull]\n' "$0" >&2
  exit 2
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

if [[ "$skip_pull" == false ]]; then
  git pull --ff-only
fi

if [[ -d .venv && ! -x .venv/bin/python ]]; then
  printf '%s\n' \
    '.venv exists but is not a Linux virtual environment.' \
    'Remove or rename it, then run this script again.' >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  python_command="${PYTHON:-}"
  if [[ -z "$python_command" ]]; then
    for candidate in python3.12 python3 python; do
      if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c \
          'import sys; raise SystemExit(sys.version_info < (3, 12))'
        then
          python_command="$candidate"
          break
        fi
      fi
    done
  fi
  if [[ -z "$python_command" ]]; then
    printf '%s\n' 'Python 3.12 or newer is required.' >&2
    exit 1
  fi
  "$python_command" -m venv .venv
fi

if ! .venv/bin/python -c \
  'import sys; raise SystemExit(sys.version_info < (3, 12))'
then
  printf '%s\n' '.venv must use Python 3.12 or newer.' >&2
  exit 1
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'

if [[ ! -e .env ]]; then
  cp .env.example .env
fi

printf '%s\n' \
  'Project update complete.' \
  'Start the app with:' \
  '  .venv/bin/python -m uvicorn stage0_sim.api.app:app --reload'
