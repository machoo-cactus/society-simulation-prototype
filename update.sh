#!/usr/bin/env bash
set -euo pipefail

pull=false
if [[ "${1:-}" == "--pull" ]]; then
  pull=true
elif [[ $# -gt 0 ]]; then
  printf 'Usage: %s [--pull]\n' "$0" >&2
  exit 2
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

if [[ "$pull" == true ]]; then
  git pull --ff-only
fi

if [[ -d .venv && ! -x .venv/bin/python ]]; then
  printf '%s\n' \
    '.venv exists but is not a POSIX virtual environment.' \
    'Remove or rename it, then run this script again.' >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  python_command="${PYTHON:-}"
  if [[ -z "$python_command" ]]; then
    for candidate in python3.14 python3.13 python3.12 python3 python; do
      if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c \
          'import sys; raise SystemExit(sys.version_info < (3, 12))'
      then
        python_command="$candidate"
        break
      fi
    done
  fi
  if [[ -z "$python_command" ]]; then
    printf '%s\n' \
      'Python 3.12 or newer is required. Set PYTHON to an interpreter path.' \
      >&2
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

mkdir -p data/characters data/scenarios data/elements data/runs

if [[ ! -e .env ]]; then
  cp .env.example .env
fi

printf '%s\n' 'Environment refresh complete.'
if [[ "$pull" == false ]]; then
  printf '%s\n' \
    'Source was not pulled; rerun with --pull for git pull --ff-only.'
fi
printf '%s\n' \
  'Start the app with:' \
  '  .venv/bin/python -m uvicorn stage0_sim.api.app:app --reload'
