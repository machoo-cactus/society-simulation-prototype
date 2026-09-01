# Stage 0 Simulation

Stage 0 is a deterministic, fully materialized simulation and research sandbox
for embodied characters. It combines fixed-step ECS execution, continuous
homeostasis, non-bypassable System 1 survival behavior, typed character
controllers, situated perception and memory, grid and sparse-city navigation,
an operator UI, and reproducible research datasets.

Version **0.2.0** supports Python 3.12 or newer. Linux is the primary CI/runtime
platform; Windows is a first-class development and CI platform.

## Quick start

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn stage0_sim.api.app:app --reload
```

### Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python -m uvicorn stage0_sim.api.app:app --reload
```

Open <http://127.0.0.1:8000/ui/>. OpenAPI is at
<http://127.0.0.1:8000/docs> and health status is at
<http://127.0.0.1:8000/health>.

Existing checkouts can run `.\update.ps1` on Windows or `bash ./update.sh` on
Linux. Add `-Pull` or `--pull` for an explicit fast-forward-only source update.

## Run a simulation

The installed package includes a self-contained deterministic demo:

```powershell
stage0-sim run demo --ticks 10
```

Tracked, read-only authoring examples live in:

- `examples\scenarios\`
- `examples\characters\`
- `examples\elements\`

For example:

```powershell
stage0-sim run examples\scenarios\minimal.json `
  --characters-dir examples\characters --ticks 10

stage0-sim run examples\scenarios\greyford-rivermarket-exchange.json `
  --characters-dir examples\characters `
  --elements-dir examples\elements --ticks 500
```

Linux uses the same arguments with `/` path separators and `\` line
continuations. See the [example catalog](examples/README.md) for each sample's
purpose.

Tool-controlled scenarios require an explicitly configured model provider.
The bundled fake OpenAI-compatible server supports local testing:

```powershell
stage0-fake-llm --host 127.0.0.1 --port 8081
$env:STAGE0_LLM_PROVIDER = "openai-compatible"
$env:STAGE0_LLM_BASE_URL = "http://127.0.0.1:8081/v1"
$env:STAGE0_LLM_MODEL = "stage0-fake"
stage0-sim run examples\scenarios\provider-character-controller.json `
  --characters-dir examples\characters --ticks 30
```

## Core workflows

- **CLI:** run schema-version-4 scenarios, assign character slots, select an
  element library, write canonical events, and persist/export datasets.
- **Simulation UI (`/ui/`):** stage without starting; assign characters; start,
  pause, resume, single-step, stop, inspect, and export.
- **Authoring UI:** manage characters at `/ui/characters/`, scenarios at
  `/ui/scenarios/`, and reusable world elements at `/ui/elements/`.
- **Research UI:** explore one run at `/ui/datasets/{run_id}/` or manage,
  aggregate, export, and delete finalized datasets at `/ui/data/`.

## Privacy warning

SQLite databases and complete or private-enabled exports can contain character
profiles, synthesized situations, prompts, model text and tool calls, retrieved
memories/information, and authoritative state. Filtered queries and exports
exclude `PRIVATE_RESEARCH` by default; complete exports do not. Treat research
artifacts as restricted data and apply appropriate consent, access, retention,
and redaction controls.

## Documentation

- [Documentation by audience and task](docs/README.md)
- [Architecture and authority boundaries](docs/ARCHITECTURE.md)
- [Runtime semantics](docs/RUNTIME.md)
- [Configuration](docs/CONFIGURATION.md)
- [Scenario and element authoring](docs/SCENARIO_EDITOR_GUIDE.md)
- [Character authoring](docs/CHARACTER_PROFILE_GUIDE.md)
- [Actions, tools, and events](docs/ACTIONS_AND_EVENTS.md)
- [Research data](docs/DATA_COLLECTION.md)
- [API and UI workflows](docs/API_AND_UI.md)
- [Status and roadmap](docs/STATUS_AND_ROADMAP.md)
- [Development history and legacy archive](docs/legacy/README.md)
