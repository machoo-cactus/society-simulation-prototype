import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from stage0_sim.application.agents.contracts import (
    ModelClient,
    ModelClientError,
    ModelMessage,
    ModelRequest,
    ToolDefinition,
)
from stage0_sim.application.characters import (
    CharacterDefinition,
    character_content_hash,
)
from stage0_sim.application.scenario import (
    CharacterSlotDefinition,
    CityWorldDefinition,
    ScenarioDefinition,
    WorldDefinition,
)
from stage0_sim.domain.events import JsonValue

SITUATION_PROMPT_VERSION = "character-situation-synthesis-v1"
SITUATION_SCHEMA_VERSION = 1
SITUATION_TOOL_NAME = "instantiate_character_situation"

SITUATION_SYSTEM_PROMPT = (
    "You instantiate one richly defined reusable person in one authored "
    "simulation scenario. Preserve the stable character dossier. Produce only "
    "the short-term manifestation of the person's established presentation "
    "style, usual dispositions, communication, habits, relationships, and "
    "coping patterns under the supplied scenario facts. Do not rewrite stable "
    "identity or invent new usual traits. Birth date, body measurements, "
    "financial snapshots, family records, and health records are fixed dossier "
    "facts: do not alter, contradict, or replace them. Do not create goals, priorities, "
    "memories, vitals, locations, resources, permissions, capabilities, "
    "relationships, affordances, action outcomes, or world facts. Current "
    "clothing, grooming, carried personal items, affect, and role stance are "
    "descriptive context only and never grant simulation mechanics. Record "
    "underspecified choices as assumptions. Return exactly one required tool "
    "call and no prose-only answer."
)


class SituationPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outfit: str = Field(default="", max_length=1200)
    grooming: str = Field(default="", max_length=800)
    accessories: list[str] = Field(default_factory=list, max_length=12)


class SituationRelationshipContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_entity_id: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=1200)


class SynthesizedCharacterSituation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SITUATION_SCHEMA_VERSION, ge=1, le=1)
    summary: str = Field(min_length=1, max_length=1600)
    role_context: str = Field(default="", max_length=1600)
    presentation: SituationPresentation = Field(
        default_factory=SituationPresentation
    )
    carried_personal_items: list[str] = Field(default_factory=list, max_length=16)
    recent_context: str = Field(default="", max_length=2000)
    current_affect: str = Field(default="", max_length=1000)
    disposition_manifestations: list[str] = Field(
        default_factory=list,
        max_length=16,
    )
    relationship_context: list[SituationRelationshipContext] = Field(
        default_factory=list,
        max_length=16,
    )
    assumptions: list[str] = Field(default_factory=list, max_length=16)


class AssignedRelationshipContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_entity_id: str
    target_character_id: str
    target_display_name: str
    relationship: str
    sentiment: str = ""
    notes: str = ""


class CharacterSituationSynthesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    character_id: str
    character_profile: dict[str, JsonValue]
    scenario_context: dict[str, JsonValue]
    assigned_relationships: list[AssignedRelationshipContext] = Field(
        default_factory=list
    )


class CharacterSituationGenerationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated: bool
    prompt_version: str
    provider: str
    model: str
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float = 0.0
    request: dict[str, JsonValue] | None = None
    result: dict[str, JsonValue] | None = None


class CharacterSituationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    character_id: str
    profile_content_hash: str
    input_hash: str
    content_hash: str
    description: str
    data: SynthesizedCharacterSituation
    generation: CharacterSituationGenerationMetadata


class CharacterSituationSynthesisError(ValueError):
    pass


class CharacterSituationSynthesizer(Protocol):
    async def synthesize(
        self,
        synthesis_input: CharacterSituationSynthesisInput,
    ) -> CharacterSituationArtifact: ...


async def compose_character_situations(
    *,
    scenario: ScenarioDefinition,
    assignments: Mapping[str, str],
    characters: Mapping[str, CharacterDefinition],
    synthesizer: CharacterSituationSynthesizer | None,
) -> dict[str, CharacterSituationArtifact]:
    if scenario.character_situation_synthesis.enabled and synthesizer is None:
        raise CharacterSituationSynthesisError(
            "character situation synthesis is enabled but no model provider "
            "is configured"
        )
    artifacts: dict[str, CharacterSituationArtifact] = {}
    for entity in scenario.entities:
        character_id = assignments.get(entity.id)
        if character_id is None:
            continue
        character = characters[character_id]
        try:
            if scenario.character_situation_synthesis.enabled:
                if synthesizer is None:
                    raise AssertionError("synthesizer was validated above")
                artifact = await synthesizer.synthesize(
                    build_synthesis_input(
                        scenario=scenario,
                        entity_id=entity.id,
                        character_id=character_id,
                        character=character,
                        assignments=assignments,
                        characters=characters,
                    )
                )
            else:
                artifact = authored_situation_artifact(
                    scenario=scenario,
                    entity_id=entity.id,
                    character_id=character_id,
                    character=character,
                )
        except (CharacterSituationSynthesisError, ModelClientError) as error:
            raise CharacterSituationSynthesisError(
                f"character situation synthesis failed for slot {entity.id} "
                f"with character {character_id}: {error}"
            ) from error
        artifacts[entity.id] = artifact
    return artifacts


@dataclass(slots=True)
class ModelCharacterSituationSynthesizer:
    model_client: ModelClient
    model: str = "default"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 1024

    async def synthesize(
        self,
        synthesis_input: CharacterSituationSynthesisInput,
    ) -> CharacterSituationArtifact:
        input_payload = synthesis_input.model_dump(mode="json")
        input_hash = canonical_hash(input_payload)
        request_id = f"character-situation:{input_hash}"
        request = ModelRequest(
            request_id=request_id,
            correlation_id=request_id,
            messages=(
                ModelMessage(role="system", content=SITUATION_SYSTEM_PROMPT),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        input_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            ),
            tools=(
                ToolDefinition(
                    name=SITUATION_TOOL_NAME,
                    description=(
                        "Instantiate the assigned character's non-authoritative "
                        "short-term situation from the supplied stable dossier "
                        "and authored scenario facts."
                    ),
                    input_schema=SynthesizedCharacterSituation.model_json_schema(),
                ),
            ),
            model=self.model,
            timeout_seconds=self.timeout_seconds,
            max_output_tokens=self.max_output_tokens,
            prompt_version=SITUATION_PROMPT_VERSION,
        )
        turn = await self.model_client.complete(request)
        if len(turn.tool_calls) != 1:
            raise CharacterSituationSynthesisError(
                "situation synthesis requires exactly one tool call"
            )
        call = turn.tool_calls[0]
        if call.name != SITUATION_TOOL_NAME:
            raise CharacterSituationSynthesisError(
                f"unexpected situation synthesis tool: {call.name}"
            )
        try:
            data = SynthesizedCharacterSituation.model_validate(call.arguments)
        except ValueError as error:
            raise CharacterSituationSynthesisError(
                f"invalid situation synthesis output: {error}"
            ) from error
        allowed_targets = {
            relationship.target_entity_id
            for relationship in synthesis_input.assigned_relationships
        }
        unknown_targets = sorted(
            {
                relationship.target_entity_id
                for relationship in data.relationship_context
            }
            - allowed_targets
        )
        if unknown_targets:
            raise CharacterSituationSynthesisError(
                "situation synthesis referenced unassigned relationship "
                f"targets: {unknown_targets}"
            )
        description = render_situation(data)
        return CharacterSituationArtifact(
            entity_id=synthesis_input.entity_id,
            character_id=synthesis_input.character_id,
            profile_content_hash=canonical_hash(
                synthesis_input.character_profile
            ),
            input_hash=input_hash,
            content_hash=canonical_hash(data.model_dump(mode="json")),
            description=description,
            data=data,
            generation=CharacterSituationGenerationMetadata(
                generated=True,
                prompt_version=SITUATION_PROMPT_VERSION,
                provider=turn.provider,
                model=turn.model,
                provider_request_id=turn.provider_request_id,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                latency_ms=turn.latency_ms,
                request=cast(dict[str, JsonValue], asdict(request)),
                result=cast(dict[str, JsonValue], asdict(turn)),
            ),
        )


def build_synthesis_input(
    *,
    scenario: ScenarioDefinition,
    entity_id: str,
    character_id: str,
    character: CharacterDefinition,
    assignments: Mapping[str, str],
    characters: Mapping[str, CharacterDefinition],
) -> CharacterSituationSynthesisInput:
    entity = next(
        (candidate for candidate in scenario.entities if candidate.id == entity_id),
        None,
    )
    if entity is None:
        raise CharacterSituationSynthesisError(
            f"unknown scenario entity for synthesis: {entity_id}"
        )
    slot = CharacterSlotDefinition.model_validate(
        entity.components["character_slot"]
    )
    selected_components: dict[str, JsonValue] = {
        name: cast(dict[str, JsonValue], entity.components[name])
        for name in (
            "goals",
            "homeostasis",
            "activity",
            "position",
            "spatial_location",
        )
        if name in entity.components
    }
    scenario_context: dict[str, JsonValue] = {
        "scenario_name": scenario.name,
        "slot": {
            "label": slot.label,
            "briefing": slot.briefing,
            "synthesis_guidance": slot.synthesis_guidance,
        },
        "initial_components": selected_components,
        "calendar_start": (
            scenario.calendar.start_datetime.isoformat()
            if scenario.calendar is not None
            else None
        ),
        "initial_weather": (
            scenario.weather.initial.model_dump(mode="json")
            if scenario.weather is not None
            else None
        ),
        "initial_environment": _initial_environment_context(
            scenario,
            entity.components,
        ),
    }
    assigned_relationships: list[AssignedRelationshipContext] = []
    assigned_entities_by_character: dict[str, list[str]] = {}
    for assigned_entity_id, assigned_character_id in assignments.items():
        assigned_entities_by_character.setdefault(
            assigned_character_id,
            [],
        ).append(assigned_entity_id)
    for relationship in character.relationships:
        for target_entity_id in sorted(
            assigned_entities_by_character.get(relationship.target_id, [])
        ):
            target = characters[relationship.target_id]
            assigned_relationships.append(
                AssignedRelationshipContext(
                    target_entity_id=target_entity_id,
                    target_character_id=relationship.target_id,
                    target_display_name=target.identity.display_name,
                    relationship=relationship.relationship,
                    sentiment=relationship.sentiment,
                    notes=relationship.notes,
                )
            )
    return CharacterSituationSynthesisInput(
        entity_id=entity_id,
        character_id=character_id,
        character_profile=character.model_dump(mode="json"),
        scenario_context=scenario_context,
        assigned_relationships=assigned_relationships,
    )


def _initial_environment_context(
    scenario: ScenarioDefinition,
    components: Mapping[str, Mapping[str, object]],
) -> dict[str, JsonValue]:
    world = scenario.world
    if isinstance(world, WorldDefinition):
        position = components.get("position")
        if position is None:
            return {}
        return _local_environment_context(
            world,
            int(cast(int | str, position["x"])),
            int(cast(int | str, position["y"])),
        )
    if not isinstance(world, CityWorldDefinition):
        return {}
    spatial = components.get("spatial_location")
    if spatial is None:
        return {}
    place_id = str(spatial["place_id"])
    result: dict[str, JsonValue] = {}
    room = next(
        (item for item in world.rooms if item.id == place_id),
        None,
    )
    building = (
        next(
            (
                item
                for item in world.buildings
                if room is not None and item.id == room.building_id
            ),
            None,
        )
        if room is not None
        else None
    )
    outdoor = next(
        (item for item in world.outdoor_places if item.id == place_id),
        None,
    )
    if room is not None and building is not None:
        result["place"] = {
            "id": room.id,
            "name": room.name,
            "kind": "room",
            "building_id": building.id,
            "building_name": building.name,
            "available": building.available,
            "environment": building.environment.model_dump(mode="json"),
        }
        local_world = room.world
    elif outdoor is not None:
        result["place"] = {
            "id": outdoor.id,
            "name": outdoor.name,
            "kind": "outdoor",
            "available": outdoor.available,
            "environment": outdoor.environment.model_dump(mode="json"),
        }
        local_world = None
    else:
        local_world = None
    local_coordinate = spatial.get("local_coordinate")
    if local_world is not None and isinstance(local_coordinate, Mapping):
        result["local"] = _local_environment_context(
            local_world,
            int(cast(int | str, local_coordinate["x"])),
            int(cast(int | str, local_coordinate["y"])),
        )
    return result


def _local_environment_context(
    world: WorldDefinition,
    x: int,
    y: int,
) -> dict[str, JsonValue]:
    zones: list[JsonValue] = []
    for zone in world.zones:
        in_zone = (
            zone.bounds is not None
            and zone.bounds.x <= x < zone.bounds.x + zone.bounds.width
            and zone.bounds.y <= y < zone.bounds.y + zone.bounds.height
        ) or (
            zone.tiles is not None
            and any(tile.x == x and tile.y == y for tile in zone.tiles)
        )
        if in_zone:
            zones.append(
                {"id": zone.id, "name": zone.name, "type": zone.type}
            )
    stations: list[JsonValue] = [
        {
            "id": station.id,
            "name": station.name,
            "available": station.available,
            "supported_actions": (
                [action.value for action in station.supported_actions]
                if station.supported_actions is not None
                else []
            ),
            "environment": station.environment.model_dump(mode="json"),
        }
        for station in world.stations
        if station.position.x == x and station.position.y == y
    ]
    return {
        "zones": zones,
        "stations_at_position": stations,
    }


def authored_situation_artifact(
    *,
    scenario: ScenarioDefinition,
    entity_id: str,
    character_id: str,
    character: CharacterDefinition,
) -> CharacterSituationArtifact:
    entity = next(
        candidate for candidate in scenario.entities if candidate.id == entity_id
    )
    slot = CharacterSlotDefinition.model_validate(
        entity.components["character_slot"]
    )
    data = SynthesizedCharacterSituation(
        summary=slot.briefing or slot.label,
        role_context=slot.label,
    )
    source: dict[str, JsonValue] = {
        "entity_id": entity_id,
        "character_id": character_id,
        "slot": slot.model_dump(mode="json"),
    }
    return CharacterSituationArtifact(
        entity_id=entity_id,
        character_id=character_id,
        profile_content_hash=character_content_hash(character),
        input_hash=canonical_hash(source),
        content_hash=canonical_hash(data.model_dump(mode="json")),
        description=render_situation(data),
        data=data,
        generation=CharacterSituationGenerationMetadata(
            generated=False,
            prompt_version="authored-character-situation-v1",
            provider="authored",
            model="none",
        ),
    )


def render_situation(data: SynthesizedCharacterSituation) -> str:
    blocks = ["# Character Situation", "", data.summary]
    if data.role_context:
        blocks.extend(["", "## Role Context", "", data.role_context])
    presentation = data.presentation
    presentation_rows = [
        ("Outfit", presentation.outfit),
        ("Grooming", presentation.grooming),
        ("Accessories", "; ".join(presentation.accessories)),
    ]
    presentation_rows = [
        (label, value) for label, value in presentation_rows if value
    ]
    if presentation_rows:
        blocks.extend(["", "## Current Presentation", ""])
        blocks.extend(f"- {label}: {value}" for label, value in presentation_rows)
    if data.carried_personal_items:
        blocks.extend(
            [
                "",
                "## Carried Personal Items",
                "",
                *[f"- {item}" for item in data.carried_personal_items],
            ]
        )
    if data.recent_context:
        blocks.extend(["", "## Recent Context", "", data.recent_context])
    if data.current_affect:
        blocks.extend(["", "## Current Affect", "", data.current_affect])
    if data.disposition_manifestations:
        blocks.extend(
            [
                "",
                "## Disposition Manifestations",
                "",
                *[f"- {item}" for item in data.disposition_manifestations],
            ]
        )
    if data.relationship_context:
        blocks.extend(["", "## Relationship Context", ""])
        blocks.extend(
            f"- {item.target_entity_id}: {item.context}"
            for item in data.relationship_context
        )
    if data.assumptions:
        blocks.extend(
            [
                "",
                "## Assumptions",
                "",
                *[f"- {item}" for item in data.assumptions],
            ]
        )
    rendered = "\n".join(blocks)
    if len(rendered) > 12_000:
        raise CharacterSituationSynthesisError(
            "rendered character situation exceeds 12000 characters"
        )
    return rendered


def canonical_hash(value: JsonValue | dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
