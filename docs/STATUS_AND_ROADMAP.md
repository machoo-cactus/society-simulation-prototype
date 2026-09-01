# Status and Roadmap

**Status date:** 2026-09-01  
**Current release:** 0.2.0

Stage 0 is an implemented deterministic simulation prototype with a Python
runtime, canonical API, server-rendered operator UI, reusable source libraries,
and versioned SQLite/JSONL research datasets.

## Current capabilities

- Fixed-step ECS execution with stable system/entity ordering.
- Grid worlds and sparse hierarchical cities with room grids, portals,
  transport graphs, vehicles, and deterministic multimodal navigation.
- Continuous satiety, energy, and stress with non-bypassable System 1
  preemption and recovery.
- Typed character-controller tools, one global cognition barrier, deterministic
  intent commit, scripted/replay clients, and opt-in OpenAI-compatible clients.
- Plans, structured goals, affordances, possessions, atomic transactions,
  run-scoped service NPCs, speech, observer-specific perception, episodic
  memory, information retrieval, and learned route knowledge.
- Scenario source version 4, character version 2, element version 1, dataset
  v3, and fresh-only SQLite schema 8.
- Hash-protected character, scenario, and element libraries.
- Accessible Python-rendered HTML/SVG operator workflows with Playwright
  coverage and no-JavaScript fallbacks.
- Per-run research exploration, filtered/complete exports, cross-run
  aggregation, ownership reconciliation, and guarded deletion.
- Linux CI plus Windows CI and installed-wheel smoke tests on both platforms.

## Current limitations

- Prepared scenarios and live runners are process-local and are not restored
  after server restart.
- Research datasets are not simulation checkpoints.
- Real model providers are opt-in and can make wall-clock execution wait at the
  cognition barrier.
- The sparse city is an execution model, not a geographically complete city or
  traffic simulator.
- Dataset projection rebuild does not reconstruct every normalized lifecycle
  table.
- Authentication, deployment authorization, retention policy, encryption,
  columnar export, distributed storage, statistical priors, approximate
  populations, and unrestricted online activity are not implemented.

## Platform policy

Python 3.12 or newer is required. Linux is the primary runtime and CI platform.
Windows is first-class for development, tests, update tooling, packaging, and
installed-wheel smoke coverage. Runtime code and documentation must preserve
both; use `pathlib` and platform-neutral APIs.

## Active roadmap

The remaining information/navigation work is tracked in
[Information and Navigation Roadmap](roadmaps/INFORMATION_AND_NAVIGATION.md).
Additional current priorities are:

1. complete rebuild coverage for normalized dataset projections;
2. measure and control dataset size, retrieval quality, and large-scenario cost;
3. harden deployment guidance around authentication and restricted research
   artifacts;
4. keep installed-package, Windows/Linux, catalog, and documentation integrity
   checks aligned as public contracts evolve.

Any proposal for live checkpoint/resume, online/digital-space execution,
multi-fidelity populations, or distributed execution needs a separate design
and schema review.
