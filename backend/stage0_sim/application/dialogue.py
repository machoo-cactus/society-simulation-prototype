from dataclasses import dataclass

from stage0_sim.application.cognition import (
    DialogueContext,
    DialogueGenerator,
    DialogueResult,
)
from stage0_sim.application.memory import EpisodicMemoryStore


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
            )
        )
