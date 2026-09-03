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
| `STAGE0_DATASET_DATABASE` | `stage0-v12.sqlite3` | Schema-qualified SQLite filename inside the data directory |
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

## Controller and engagement compiler settings

Scenario source version 8 separates ordinary controller limits from
`cognition.engagement_compiler`:

| Scenario field | Default | Purpose |
| --- | ---: | --- |
| `cognition.engagement_compiler.model_profile` | `default` | Model profile for `engagement_compilation` |
| `timeout_seconds` | `30` | Compiler request timeout |
| `max_output_tokens` | `768` | Per-response compiler limit |
| `max_concurrency` | `2` | Concurrent compiler work |
| `max_requests` | unset | Per-run compiler request budget |
| `max_input_tokens` | unset | Per-run compiler input-token budget |
| `max_total_output_tokens` | unset | Per-run compiler output-token budget |

These counters are independent from the controller's corresponding cognition
budgets. Both operations use the same configured `ModelClient`, provider
endpoint, deployment output-token ceiling, and deployment concurrency ceiling.
Changing the compiler model profile does not create a second provider client.
`STAGE0_LLM_RECORD_PATH` records both operations, and the `replay` provider
replays both by request hash. Provider failure, timeout, or budget exhaustion
fails compilation explicitly.

Top-level scenario `engagement` settings configure validation bands and domain
effects: group/invocation/public-text limits; short/medium/long activity
durations; low/medium/high effort energy costs; calming/activating stress
deltas; quiet/normal/loud sound ranges; and the bounded alarming-listener
stress delta. They are simulation policy, not model-supplied numeric writes.

The bundled `stage0-fake-llm` endpoint distinguishes ordinary character
controller requests from `engagement_compilation` requests and returns strict
deterministic `compile_engagement` calls for expressive, auditory, and bounded
activity examples. See `examples\scenarios\engagement-demo.json`.

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

See [LLM operations and smoke testing](LLM_OPERATIONS.md) for bounded live
provider checks, recording/replay, and local or rented endpoint guidance.
