from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingSpeechComponent:
    decision_id: str
    tool_call_id: str
    target_id: str
    text: str
    channel: str = "voice"
