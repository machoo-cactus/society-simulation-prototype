# Configuration

**Owner:** Environment settings and deployment-only configuration.

Settings use the `STAGE0_` prefix and may be placed in `.env`. Scenario JSON
must not contain credentials or provider endpoints. Relative paths are resolved
from the process working directory; run from the repository root or use
absolute paths.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STAGE0_ENVIRONMENT` | `development` | Deployment label |
| `STAGE0_CORS_ORIGINS` | `[]` | JSON list of allowed origins for separate clients |
| `STAGE0_DATA_DIRECTORY` | `data/runs` | Dataset/output directory |
| `STAGE0_DATASET_DATABASE` | `stage0.sqlite3` | SQLite filename inside the data directory |
| `STAGE0_CHARACTER_DIRECTORY` | `data/characters` | Writable character library |
| `STAGE0_SCENARIO_DIRECTORY` | `data/scenarios` | Writable scenario library |
| `STAGE0_ELEMENT_DIRECTORY` | `data/elements` | Writable element library |
| `STAGE0_LLM_PROVIDER` | unset | `openai-compatible` or `replay`; unset disables provider-backed cognition |
| `STAGE0_LLM_BASE_URL` | unset | OpenAI-compatible API root or chat-completions URL |
| `STAGE0_LLM_MODEL` | unset | Provider model identifier |
| `STAGE0_LLM_API_KEY` | unset | Optional credential; never written to datasets |
| `STAGE0_LLM_TIMEOUT_SECONDS` | `30` | Provider request timeout |
| `STAGE0_LLM_RETRY_ATTEMPTS` | `3` | Total attempts for retryable transport/HTTP failures |
| `STAGE0_LLM_RETRY_DELAY_SECONDS` | `1` | Initial retry delay |
| `STAGE0_LLM_TOOL_CHOICE` | `required` | Provider tool-choice value; `none` is invalid |
| `STAGE0_LLM_MAX_OUTPUT_TOKENS` | `512` | Deployment ceiling per response |
| `STAGE0_LLM_MAX_CONCURRENCY` | `4` | Deployment ceiling for concurrent requests |
| `STAGE0_LLM_RECORD_PATH` | unset | Sanitized request/response JSONL recording path |
| `STAGE0_LLM_REPLAY_PATH` | unset | Recording required by the `replay` provider |

Scenario cognition settings can impose stricter per-run timeout, concurrency,
request, input-token, and output-token limits, but cannot exceed deployment
ceilings where both exist.

## Catalogs and examples

The `data\` libraries are writable and ignored by Git. Tracked examples under
`examples\` are read-only references. The installed `stage0-sim run demo`
resource is packaged separately and does not depend on the checkout.

## Server settings

Host, port, workers, reload, and logging are Uvicorn concerns:

```powershell
python -m uvicorn stage0_sim.api.app:app --host 0.0.0.0 --port 8080
```

The application provides no authentication layer. Do not expose operator,
dataset, or private-export routes to untrusted networks without an appropriate
deployment boundary.
