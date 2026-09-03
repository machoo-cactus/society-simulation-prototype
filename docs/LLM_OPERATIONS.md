# LLM Operations and Smoke Testing

**Owner:** Local or rented OpenAI-compatible model-service setup, bounded live
conformance checks, recording, replay, and diagnosis.

Normal development and CI use scripted, fake, and replay clients. A live model
service is optional and should be started only when testing a novel or changed
controller/compiler protocol.

## Configure an endpoint

Set deployment values in the environment or `.env`:

```powershell
$env:STAGE0_LLM_PROVIDER = "openai-compatible"
$env:STAGE0_LLM_BASE_URL = "http://127.0.0.1:8080/v1"
$env:STAGE0_LLM_MODEL = "model-name"
$env:STAGE0_LLM_API_KEY = ""
```

The endpoint may be local or on a rented machine. Keep it behind an appropriate
network boundary; the Stage 0 application does not provide deployment
authentication. Credentials and provider URLs never belong in scenarios,
tracked examples, test fixtures, or diagnostics.

## Deterministic contract checks

Run these without a live provider:

```powershell
python -m pytest -m model_contract
```

They cover strict controller/compiler requests, fake responses, malformed
output, timeout/failure behavior, recording, replay, and OpenAI-compatible
transport shape.

## Bounded live smoke

The smoke makes one provider attempt per selected operation and prints only
safe metadata:

```powershell
# One controller tool-call request
python tools\live_llm_smoke.py --operation controller

# Controller plus engagement compiler protocol
python tools\live_llm_smoke.py --operation both
```

Use `--timeout-seconds` and `--max-output-tokens` to lower the default bounds.
The smoke requires exactly one strict tool call and rejects prose, malformed
arguments, unsupported tools, and invalid compiler proposals. It does not run a
long simulation or treat provider output as a committed outcome.

Run the engagement operation only when the changed feature uses the compiler.
Ordinary code changes must not wait for a provider to be available.

## Recording and replay

Set `STAGE0_LLM_RECORD_PATH` only when a representative provider exchange is
needed for later deterministic replay:

```powershell
$env:STAGE0_LLM_RECORD_PATH = "data\runs\model-smoke.jsonl"
python tools\live_llm_smoke.py --operation both

$env:STAGE0_LLM_PROVIDER = "replay"
$env:STAGE0_LLM_REPLAY_PATH = "data\runs\model-smoke.jsonl"
```

Recordings can contain prompts, tool arguments, and model output. Treat them as
restricted research artifacts; do not commit them by default.

## Diagnose failures

| Failure | Check |
| --- | --- |
| Connection or 503 | Service readiness, model load, URL, firewall, and timeout |
| 401/403 | API key and reverse-proxy policy |
| No tool call or prose | Model/tool-choice support and prompt-template settings |
| Invalid arguments | JSON-schema/tool-call compatibility of the served model |
| Compiler rejection | `compile_engagement` schema, capability names, references, and output limits |
| Replay miss | Request, prompt version, tool schema, model profile, or recording changed |

Use sanitized logs. Never print authorization headers or copy private character
context into issue reports.

