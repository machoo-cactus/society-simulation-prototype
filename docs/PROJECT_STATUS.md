# Project status

**Status date:** 2026-08-31

Stage 0 is an implemented deterministic simulation prototype with a Python
runtime, FastAPI API, server-rendered operator UI, reusable character library,
and reproducible SQLite/JSONL datasets.

## Implemented

- Fixed-step ECS simulation with stable system and entity ordering.
- Grid worlds and sparse hierarchical cities with container buildings,
  authoritative room grids and portals, transport graphs, vehicles, and
  deterministic travel.
- Continuous satiety, energy, and stress with absolute System 1 survival
  priority.
- Plans, physical affordances, speech, observer-specific perception, episodic
  memory, information retrieval, and learned route knowledge.
- Scripted, replay, fake OpenAI-compatible, and opt-in real model clients.
- Typed character-controller tools with deterministic validation and commit.
- Global cognition barriers by default, with explicit background compatibility.
- Reusable `human-v1` character files and browser/API/CLI integration.
- Server-rendered accessible HTML/SVG operator console with direct form
  controls, targeted fragment refresh, preserved interaction state, and
  progressively enhanced map panning and zoom.
- Python Playwright browser workflows using ARIA roles and isolated temporary
  data directories.
- Linux CI covering tests, Chromium UI behavior, Ruff, strict mypy, a CLI smoke
  run, and wheel packaging.

## Current limitations

- Active scenarios and runners are process-local and are not restored after a
  server restart.
- SQLite and JSONL outputs are research datasets, not simulation checkpoints.
- Real model providers are opt-in and require explicit environment settings.
- The sparse-city implementation is a Stage 0 execution model, not a
  geographically complete city simulator.
- Online activity and unrestricted external tools are out of scope.

## Platform support

Linux is the primary runtime and CI target. Python 3.12 or newer and SQLite are
required. macOS and Windows remain supported secondary development platforms.
Runtime Python code uses `pathlib` and platform-neutral APIs; shell-specific
commands are kept in `update.sh` and `update.ps1`.

Use `README.md` for installation and operation, `CONCEPT_GUIDE.md` for the
architecture, and `roadmaps/INFORMATION_AND_NAVIGATION.md` for remaining
information/navigation work.
