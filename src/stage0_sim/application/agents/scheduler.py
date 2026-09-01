from dataclasses import dataclass
from typing import Literal

from stage0_sim.application.agents.context import build_character_observation
from stage0_sim.application.agents.contracts import CharacterDecisionRequest
from stage0_sim.application.agents.coordinator import AgentWorkCoordinator
from stage0_sim.application.data_capture import (
    DecisionId,
    RecordCategory,
    RecordJoinIds,
    RecordSource,
)
from stage0_sim.domain.components import (
    AffordanceExecutionComponent,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ControllerComponent,
    DriveComponent,
    NpcComponent,
    PendingSpeechComponent,
    PerceptionComponent,
    PlanComponent,
    System1State,
    TransactionRequestComponent,
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
            environment_fact_types = {
                item.fact.fact_type
                for item in context.registry.get_component(
                    agent_id, PerceptionComponent
                ).inbox
                if item.fact.fact_type
                in {
                    "time_updated",
                    "weather_changed",
                    "availability_changed",
                }
            }
            environment_update_due = bool(environment_fact_types)
            trigger = (
                "environment_update"
                if environment_fact_types
                & {"weather_changed", "availability_changed"}
                else "time_update"
                if environment_update_due
                else "idle"
            )
            is_npc = context.registry.has_component(agent_id, NpcComponent)
            npc_service_due = not is_npc or any(
                request.operator_id == agent_id
                and request.status == "awaiting_authorization"
                for _, request in context.registry.query(
                    TransactionRequestComponent
                )
            )
            actor_kind: Literal["character", "npc"] = (
                "npc" if is_npc else "character"
            )
            budget_failure = coordinator.budget_failure(actor_kind)
            gates = {
                "controller_enabled": controller.enabled,
                "request_not_pending": not controller.request_pending,
                "decision_time_due": (
                    context.clock.simulation_time
                    >= controller.next_decision_time
                    or environment_update_due
                ),
                "plan_idle": plan.current is None and not plan.queue,
                "system1_normal": drive.state is System1State.NORMAL,
                "affordance_idle": not context.registry.has_component(
                    agent_id, AffordanceExecutionComponent
                ),
                "speech_idle": not context.registry.has_component(
                    agent_id, PendingSpeechComponent
                ),
                "npc_service_due": npc_service_due,
                "budget_available": budget_failure is None,
            }
            reasons = [
                name for name, allowed in gates.items() if not allowed
            ]
            eligible = not reasons
            decision_id = (
                f"decision:{agent_id}:{controller.decision_sequence + 1:08d}"
                if eligible
                else None
            )
            if coordinator.research_recorder is not None:
                coordinator.research_recorder.record(
                    "cognition_evaluation",
                    {
                    "evaluation_id": (
                        f"cognition-evaluation:{agent_id}:"
                        f"{context.clock.tick:08d}"
                    ),
                    "eligible": eligible,
                    "reasons": reasons,
                    "gates": gates,
                    "trigger": trigger,
                    "actor_kind": actor_kind,
                    "environment_fact_types": sorted(
                        environment_fact_types
                    ),
                    "next_decision_time": controller.next_decision_time,
                    "state_revision": controller.state_revision,
                    "request_count": coordinator.request_count,
                    "input_tokens": coordinator.input_tokens,
                    "output_tokens": coordinator.output_tokens,
                    "budget_failure": budget_failure,
                    "decision_id": decision_id,
                    },
                    category=RecordCategory.DECISION,
                    source=RecordSource.APPLICATION,
                    subject_id=agent_id,
                    correlation_id=decision_id,
                    joins=RecordJoinIds(
                        decision_id=(
                            DecisionId(decision_id)
                            if decision_id is not None
                            else None
                        )
                    ),
                )
            if not eligible and reasons != ["budget_available"]:
                continue
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
            if decision_id is None:
                raise AssertionError("eligible cognition requires decision ID")
            context.events.emit(
                "cognition.eligible",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={
                    "decision_id": decision_id,
                    "trigger": trigger,
                    "execution_mode": coordinator.execution_mode,
                },
                correlation_id=decision_id,
            )
            profile = context.registry.get_component(
                agent_id, CharacterProfileComponent
            )
            situation = context.registry.get_component(
                agent_id, CharacterSituationComponent
            )
            request = CharacterDecisionRequest(
                decision_id=decision_id,
                run_id=context.events.run_id,
                agent_id=agent_id,
                requested_tick=context.clock.tick,
                state_revision=controller.state_revision,
                trigger=trigger,
                character_description=profile.description,
                situation_description=(
                    situation.description or situation.briefing
                ),
                situation_content_hash=situation.content_hash,
                situation_input_hash=situation.input_hash,
                profile_id=profile.profile_id,
                profile_template_version=profile.template_version,
                profile_content_hash=profile.content_hash,
                observation=build_character_observation(context, agent_id),
                memories=(),
                allowed_tools=controller.tool_allowlist,
                actor_kind=actor_kind,
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
                    "situation_content_hash": request.situation_content_hash,
                    "situation_input_hash": request.situation_input_hash,
                    "allowed_tools": list(request.allowed_tools),
                    "execution_mode": coordinator.execution_mode,
                    "fact_ids": [
                        fact.fact_id for fact in request.observation.facts
                    ],
                },
                correlation_id=decision_id,
            )
            coordinator.submit(request)
