from dataclasses import dataclass

from stage0_sim.domain.perception import PerceivedFact, PerceptionPacket


@dataclass(frozen=True, slots=True)
class DeterministicPerceptionRenderer:
    version: str = "perception-v1"

    def render_fact(self, perceived: PerceivedFact) -> str:
        fact = perceived.fact
        subject = fact.properties.get("display_name") or fact.subject_id or "Someone"
        if fact.fact_type == "entity_seen":
            return f"{subject} is visible."
        if fact.fact_type == "entity_lost":
            return f"{subject} is no longer visible."
        if fact.fact_type == "entity_moved":
            zone = fact.location_id
            return f"{subject} moved{f' in {zone}' if zone else ''}."
        if fact.fact_type == "entity_entered_zone":
            return f"{subject} entered {fact.location_id}."
        if fact.fact_type == "entity_left_zone":
            return f"{subject} left {fact.location_id}."
        if fact.fact_type == "visible_activity_started":
            return f"{subject} started {fact.properties.get('activity', 'an activity')}."
        if fact.fact_type == "heard_speech":
            return f'{subject} said: "{fact.properties.get("text", "")}"'
        return f"{subject}: {fact.fact_type}."

    def render(
        self,
        observer_id: str,
        facts: tuple[PerceivedFact, ...],
        *,
        start_tick: int,
        end_tick: int,
    ) -> PerceptionPacket:
        return PerceptionPacket(
            observer_id=observer_id,
            start_tick=start_tick,
            end_tick=end_tick,
            facts=facts,
            deterministic_text=tuple(self.render_fact(fact) for fact in facts),
        )
