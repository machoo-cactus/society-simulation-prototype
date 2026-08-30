from dataclasses import dataclass

from stage0_sim.application.cognition import (
    DialogueContext,
    DialogueGenerator,
    DialogueResult,
)
from stage0_sim.application.memory import (
    EpisodicMemoryStore,
    memory_context_capsules,
)
from stage0_sim.domain.components import (
    ActionType,
    ConversationComponent,
    DriveComponent,
    MemoryComponent,
    PlanComponent,
    System1State,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.systems.plans import fail_social_action, is_dialogue_capable


@dataclass(slots=True)
class MemoryAwareDialogueGenerator:
    generator: DialogueGenerator
    memory_store: EpisodicMemoryStore
    top_k: int = 5

    def generate(
        self,
        *,
        agent_id: str,
        simulation_time: float,
        prompt: str,
    ) -> DialogueResult:
        retrieved = self.memory_store.retrieve(
            agent_id=agent_id,
            query=prompt,
            simulation_time=simulation_time,
            top_k=self.top_k,
        )
        return self.generator.generate(
            DialogueContext(
                agent_id=agent_id,
                simulation_time=simulation_time,
                prompt=prompt,
                memories=tuple(item.record.text for item in retrieved),
                retrieved_information=memory_context_capsules(
                    self.memory_store,
                    retrieved,
                ),
            )
        )


@dataclass(slots=True)
class MacroDialogueSystem:
    name: str = "macro_dialogue"
    order: int = 295
    _event_cursor: int = 0

    def update(self, context: SystemContext) -> None:
        from stage0_sim.application.macro_work import (
            DialogueWork,
            MacroWorkCoordinator,
        )

        coordinator = context.registry.get_resource(MacroWorkCoordinator)
        events = context.events.events
        pending = events[self._event_cursor :]
        self._event_cursor = len(events)
        for event in pending:
            if (
                event.event_type != "plan.action_started"
                or event.agent_id is None
                or event.payload.get("action") != ActionType.SOCIALIZE.value
            ):
                continue
            agent_id = event.agent_id
            target_id = event.payload.get("target")
            if (
                not isinstance(target_id, str)
                or target_id == agent_id
                or not context.registry.has_component(
                    agent_id, ConversationComponent
                )
                or not context.registry.has_component(
                    agent_id, DriveComponent
                )
            ):
                continue
            if not is_dialogue_capable(context.registry, target_id):
                fail_social_action(
                    context,
                    agent_id,
                    target_id,
                    "invalid_social_target",
                )
                continue
            drive = context.registry.get_component(agent_id, DriveComponent)
            target_drive = context.registry.get_component(
                target_id, DriveComponent
            )
            plan = context.registry.get_component(agent_id, PlanComponent)
            conversation = context.registry.get_component(
                agent_id, ConversationComponent
            )
            if (
                plan.current is None
                or plan.current.action is not ActionType.SOCIALIZE
                or conversation.request_pending
            ):
                continue
            top_k = (
                context.registry.get_component(agent_id, MemoryComponent).top_k
                if context.registry.has_component(agent_id, MemoryComponent)
                else 1
            )
            prompt = f"Talk with {target_id} during the current social interaction."
            requested = context.events.emit(
                "dialogue.requested",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=agent_id,
                payload={"target_id": target_id, "prompt": prompt},
                causation_id=event.event_id,
                correlation_id=event.event_id,
            )
            work = DialogueWork(
                agent_id=agent_id,
                target_id=target_id,
                prompt=prompt,
                top_k=top_k,
                requested_event_id=requested.event_id,
            )
            if (
                drive.state is not System1State.NORMAL
                or target_drive.state is not System1State.NORMAL
            ):
                coordinator.cancel_dialogue(
                    context,
                    work,
                    "system1_preemption",
                )
                continue
            conversation.request_pending = True
            coordinator.enqueue_dialogue(work)
