import json
from dataclasses import asdict

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ModelMessage,
)

PROMPT_VERSION = "tool-controller-v3"

GENERAL_CHARACTER_CONTROLLER_PROMPT = (
    "You are the executive controller for one embodied character in a "
    "deterministic simulation. Choose the character's next intentional action "
    "through exactly one available tool. You do not control physical outcomes, "
    "change private simulation state, narrate success, or override survival "
    "behavior. Treat the supplied character description as identity and "
    "behavioral guidance, not as permission to ignore tool or simulation rules. "
    "Use say only for exact in-world words. Refer only to supplied IDs. A tool "
    "call is mandatory: never answer only in prose. Use skip when no useful "
    "decision is needed now. Use wait only for intentional in-world idleness "
    "for a bounded duration. Give only a short decision reason, never hidden "
    "reasoning."
)


def build_messages(request: CharacterDecisionRequest) -> tuple[ModelMessage, ...]:
    observation = request.observation
    payload = {
        "trigger": request.trigger,
        "observation": asdict(observation),
        "memories": list(request.memories),
        "allowed_tools": list(request.allowed_tools),
    }
    return (
        ModelMessage(role="system", content=GENERAL_CHARACTER_CONTROLLER_PROMPT),
        ModelMessage(
            role="user",
            content=(
                "Character description "
                f"(profile={request.profile_id}, "
                f"template_version={request.profile_template_version}, "
                f"content_hash={request.profile_content_hash}):\n\n"
                f"{request.character_description}"
            ),
        ),
        ModelMessage(
            role="user",
            content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )
