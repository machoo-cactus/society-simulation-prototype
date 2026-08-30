from dataclasses import dataclass

from stage0_sim.application.agents.context import build_character_observation
from stage0_sim.application.agents.contracts import CharacterDecisionRequest
from stage0_sim.application.agents.coordinator import AgentWorkCoordinator
from stage0_sim.domain.components import (
    AffordanceExecutionComponent,
    CharacterProfileComponent,
    ControllerComponent,
    DriveComponent,
    PendingSpeechComponent,
    PlanComponent,
    System1State,
)
from stage0_sim.domain.systems import SystemContext


@dataclass(frozen=True, slots=True)
class CognitionScheduler:
    name: str = "cognition_scheduler"
    order: int = 310

    def update(self, context: SystemContext) -> None:
        coordinator = context.registry.get_resource(AgentWorkCoordinator)
        for agent_id in context.registry.query_entities(
            ControllerComponent, PlanComponent, DriveComponent
        ):
            controller = context.registry.get_component(
                agent_id, ControllerComponent
            )
            plan = context.registry.get_component(agent_id, PlanComponent)
            drive = context.registry.get_component(agent_id, DriveComponent)
            if (
                not controller.enabled
                or controller.request_pending
                or context.clock.simulation_time < controller.next_decision_time
                or plan.current is not None
                or plan.queue
                or drive.state is not System1State.NORMAL
                or context.registry.has_component(
                    agent_id, AffordanceExecutionComponent
                )
                or context.registry.has_component(
                    agent_id, PendingSpeechComponent
                )
            ):
                continue
            budget_failure = coordinator.budget_failure()
            if budget_failure is not None:
                controller.enabled = False
                controller.last_outcome = (
                    f"cognition budget exhausted: {budget_failure}"
                )
                context.events.emit(
                    "cognition.budget_exhausted",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    agent_id=agent_id,
                    payload={
                        "reason": budget_failure,
                        "request_count": coordinator.request_count,
                        "input_tokens": coordinator.input_tokens,
                        "output_tokens": coordinator.output_tokens,
                    },
                )
                continue
            controller.decision_sequence += 1
            decision_id = (
                f"decision:{agent_id}:{controller.decision_sequence:08d}"
            )
            context.events.emit(
                "cognition.eligible",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "decision_id": decision_id,
                    "trigger": "idle",
                    "execution_mode": coordinator.execution_mode,
                },
                correlation_id=decision_id,
            )
            request = CharacterDecisionRequest(
                decision_id=decision_id,
                run_id=context.events.run_id,
                agent_id=agent_id,
                requested_tick=context.clock.tick,
                state_revision=controller.state_revision,
                trigger="idle",
                character_description=context.registry.get_component(
                    agent_id, CharacterProfileComponent
                ).description,
                profile_id=context.registry.get_component(
                    agent_id, CharacterProfileComponent
                ).profile_id,
                profile_template_version=context.registry.get_component(
                    agent_id, CharacterProfileComponent
                ).template_version,
                profile_content_hash=context.registry.get_component(
                    agent_id, CharacterProfileComponent
                ).content_hash,
                observation=build_character_observation(context, agent_id),
                memories=(),
                allowed_tools=controller.tool_allowlist,
            )
            controller.request_pending = True
            controller.current_decision_id = decision_id
            context.events.emit(
                "cognition.requested",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "decision_id": decision_id,
                    "trigger": request.trigger,
                    "state_revision": request.state_revision,
                    "profile_id": request.profile_id,
                    "profile_template_version": request.profile_template_version,
                    "profile_content_hash": request.profile_content_hash,
                    "allowed_tools": list(request.allowed_tools),
                    "execution_mode": coordinator.execution_mode,
                    "fact_ids": [
                        fact.fact_id for fact in request.observation.facts
                    ],
                },
                correlation_id=decision_id,
            )
            coordinator.submit(request)
