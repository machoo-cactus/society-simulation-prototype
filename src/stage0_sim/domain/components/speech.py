from dataclasses import dataclass

from stage0_sim.domain.components.planning import ActionInstance


@dataclass(frozen=True, slots=True)
class PendingSpeechComponent:
    decision_id: str
    tool_call_id: str
    target_id: str
    text: str
    channel: str = "voice"
    action_instance: ActionInstance | None = None
