import json
from dataclasses import asdict

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ModelMessage,
)

PROMPT_VERSION = "tool-controller-v1"


def build_messages(request: CharacterDecisionRequest) -> tuple[ModelMessage, ...]:
    observation = request.observation
    system = (
        f"You are the executive controller for a simulated person named "
        f"{observation.display_name}. Choose the person's next intentional action. "
        "You are not the person, simulation engine, or narrator. Do not claim an "
        "action happened. Use exactly one available action tool. Use say only for "
        "exact in-world words. Refer only to supplied IDs; if no useful action is "
        "available, call wait. Give only a short reason, never hidden reasoning."
    )
    payload = {
        "trigger": request.trigger,
        "observation": asdict(observation),
        "memories": list(request.memories),
        "allowed_tools": list(request.allowed_tools),
    }
    return (
        ModelMessage(role="system", content=system),
        ModelMessage(
            role="user",
            content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )
