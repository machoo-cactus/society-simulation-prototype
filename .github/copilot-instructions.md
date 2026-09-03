# Copilot instructions for Stage 0

Use Python 3.12 or newer:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Use `.\update.ps1` on Windows or `bash ./update.sh` on Linux to refresh an
existing environment. Source updates require explicit `-Pull` or `--pull`.

## Development workflow

- Classify the change before exploring or testing. Follow
  [Development workflow](../docs/DEVELOPMENT_WORKFLOW.md).
- Start with `git status`, the current diff, the owning current document, the
  authoritative call chain, and its closest tests. Stop when those boundaries
  are known; do not inventory the repository for a localized task.
- Keep ordinary plans below roughly 150 lines with three to seven ordered
  tasks. Do not copy full schemas, exhaustive test matrices, or validation logs
  into plans.
- Run focused local tests and Ruff paths that can falsify the changed behavior.
  Broad pytest, Playwright, mypy, build, package, and platform gates are CI
  responsibilities unless the task is explicitly a release or diagnostic run.
- Use `python -m pytest -m quick` for stable fast feedback and
  `python -m pytest -m startup_contract` when application composition,
  settings, persistence defaults, or startup changes. `python -m pytest` is
  source regression, not complete project validation; only CI
  `full-validation` covers browser, installed package, and platform modules.
- For UI work, run the closest HTTP tests and one focused Playwright workflow.
- For model protocol work, run `python -m pytest -m model_contract`; a live
  provider is optional and uses the bounded smoke in
  [LLM operations](../docs/LLM_OPERATIONS.md).
- Update only the document that owns the changed contract. Volatile identifiers
  live in [Current contracts](../docs/CURRENT_CONTRACTS.md).
- Runtime contracts are current-only and may break rapidly. Do not add
  compatibility branches without an explicit current requirement. Character,
  element, and scenario schemas still require tested adjacent migrators and
  legacy fixtures.

Use **character** for a simulated person and **character controller** for the
software selecting decisions.

## Non-negotiable invariants

- Dependency direction is `domain <- application <- adapters/API/UI`.
- Domain systems alone decide physical outcomes; providers return proposals.
- Physical ECS components plus `SpatialIndex` are live truth. `CityWorld` and
  element hierarchy are immutable construction/display metadata.
- Semantic mass/dimensions are distinct from rendered collision footprints.
  Equipment uses live slotted `ATTACHED_TO` relations and deterministic typed
  effects; per-sense structural blockers and footprint sweeps remain domain
  authority.
- Local physical execution is fixed at 9 microcells per legacy cell with a
  5×5-microcell character body; physical anchors are microcells and coarse
  compatibility positions may be legacy cells.
- Never call a model or embedding provider from an ordered domain system.
- System 1 is deterministic, non-bypassable, and clears incompatible work.
- System order, stable entity iteration, tie-breaking, and commit order are
  simulation semantics.
- Tool calls become immutable intents and are revalidated before deterministic
  commit. A commit is not proof of success.
- Physical interaction verbs and
  `interaction.requested|started|completed|failed|cancelled` are closed
  contracts. Door links use live open/locked state and domain traversal.
- Custody/hands/live relations, descriptive ownership, and abstract
  possessions are independent representations.
- Telemetry and research traces are never character perception or controller
  context.
- Perception exposes observer-specific execution evidence, not private plans,
  destinations, reasons, vitals, drives, prompts, profiles, or memories.
- Strict scenario/action/tool schemas reject unknown values. Character dossier
  extensions use preserved JSON fields or ordered `custom_sections`; they
  remain descriptive only.
- Provider-specific HTTP/SDK objects stay inside `adapters\llm`.
- Real providers are opt-in; scripted and replay clients remain first-class.
- Failures are explicit; do not create success-shaped fallbacks.
- Browser forms/links and Python state remain authoritative. JavaScript is only
  progressive transport and browser interaction, with a no-JavaScript fallback.
- Operator SVGs project footprints, bodies, posture/held/door/path/anchor
  state; keep markup content-bounded and expose state without relying on color.
- Scenario staging must not start or advance a run.
- Datasets are research records, not checkpoints. Private exports require
  explicit handling.
- Use `pathlib` and preserve Windows and Linux behavior.

## Authoritative documentation

- [Development workflow](../docs/DEVELOPMENT_WORKFLOW.md)
- [Current contracts](../docs/CURRENT_CONTRACTS.md)
- [Documentation map](../docs/README.md)
- [Architecture](../docs/ARCHITECTURE.md)
- [Runtime semantics](../docs/RUNTIME.md)
- [Configuration](../docs/CONFIGURATION.md)
- [Scenario and element authoring](../docs/SCENARIO_EDITOR_GUIDE.md)
- [Content migration](../docs/CONTENT_MIGRATION.md)
- [Character authoring](../docs/CHARACTER_PROFILE_GUIDE.md)
- [Actions and events](../docs/ACTIONS_AND_EVENTS.md)
- [Research data](../docs/DATA_COLLECTION.md)
- [API and UI workflows](../docs/API_AND_UI.md)
- [UI architecture and testing](../docs/UI_TESTING.md)
- [LLM operations](../docs/LLM_OPERATIONS.md)
