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
        if fact.fact_type == "physical_object_seen":
            return f"{subject} is visible."
        if fact.fact_type == "physical_object_lost":
            return f"{subject} is no longer visible."
        if fact.fact_type == "physical_object_state_changed":
            return f"{subject}'s visible state changed."
        if fact.fact_type == "physical_interaction_observed":
            return (
                f"{subject} {fact.properties.get('status', 'performed')} "
                f"{fact.properties.get('verb', 'an interaction')} with "
                f"{fact.properties.get('target_name', fact.object_id or 'an object')}."
            )
        if fact.fact_type == "text_activity_observed":
            return (
                f"{subject} is {fact.properties.get('activity', 'using text')} "
                f"with {fact.properties.get('target_name', fact.object_id or 'an object')}."
            )
        if fact.fact_type == "text_arrived":
            return (
                f"New text arrived for {fact.properties.get('display_label', 'an address')}."
            )
        if fact.fact_type == "heard_speech":
            return f'{subject} said: "{fact.properties.get("text", "")}"'
        if fact.fact_type in {"scent_detected", "scent_changed"}:
            return (
                "You smell "
                f"{fact.properties.get('description', 'an unidentified scent')}."
            )
        if fact.fact_type == "scent_lost":
            return (
                f"The scent from {subject} is no longer detectable."
            )
        if fact.fact_type == "time_updated":
            return (
                f"It is {fact.properties.get('time', 'an unknown time')} on "
                f"{fact.properties.get('weekday', 'an unknown day')}, "
                f"{fact.properties.get('date', 'an unknown date')}."
            )
        if fact.fact_type == "weather_changed":
            environment = fact.properties.get("environment")
            weather_value = (
                environment.get("weather")
                if isinstance(environment, dict)
                else None
            )
            weather = weather_value if isinstance(weather_value, dict) else {}
            return (
                f"The weather is now {weather.get('condition', 'unknown')} at "
                f"{weather.get('temperature_c', 'unknown')} C, with "
                f"{weather.get('precipitation_mm_per_hour', 'unknown')} mm/h "
                "precipitation."
            )
        if fact.fact_type == "availability_changed":
            current = fact.properties.get("current")
            current_values = current if isinstance(current, dict) else {}
            state = "open" if current_values.get("available") else "closed"
            return (
                f"{fact.properties.get('resource_id', 'A place')} is now "
                f"{state} ({current_values.get('reason', 'unknown reason')})."
            )
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
