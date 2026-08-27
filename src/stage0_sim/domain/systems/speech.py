from dataclasses import dataclass

from stage0_sim.domain.components import (
    ConversationComponent,
    DriveComponent,
    PendingSpeechComponent,
    PositionComponent,
    System1State,
)
from stage0_sim.domain.systems import SystemContext


@dataclass(frozen=True, slots=True)
class SpeechSystem:
    name: str = "speech"
    order: int = 175

    def update(self, context: SystemContext) -> None:
        for agent_id in tuple(
            context.registry.query_entities(PendingSpeechComponent)
        ):
            speech = context.registry.get_component(
                agent_id, PendingSpeechComponent
            )
            failure = self._failure(context, agent_id, speech.target_id)
            if failure is not None:
                context.events.emit(
                    "speech.failed",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "target_id": speech.target_id,
                        "reason": failure,
                        "tool_call_id": speech.tool_call_id,
                        "decision_id": speech.decision_id,
                    },
                    correlation_id=speech.decision_id,
                )
            else:
                if context.registry.has_component(
                    agent_id, ConversationComponent
                ):
                    context.registry.get_component(
                        agent_id, ConversationComponent
                    ).turns.append(speech.text)
                context.events.emit(
                    "speech.started",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "target_id": speech.target_id,
                        "text": speech.text,
                        "channel": speech.channel,
                        "tool_call_id": speech.tool_call_id,
                        "decision_id": speech.decision_id,
                    },
                    correlation_id=speech.decision_id,
                )
            context.registry.remove_component(agent_id, PendingSpeechComponent)

    @staticmethod
    def _failure(
        context: SystemContext, agent_id: str, target_id: str
    ) -> str | None:
        if target_id == agent_id or target_id not in context.registry.entities():
            return "unknown_target"
        for participant in (agent_id, target_id):
            if not context.registry.has_component(participant, PositionComponent):
                return "target_unavailable"
            if (
                context.registry.has_component(participant, DriveComponent)
                and context.registry.get_component(
                    participant, DriveComponent
                ).state
                is not System1State.NORMAL
            ):
                return "system1_preemption"
        return None
