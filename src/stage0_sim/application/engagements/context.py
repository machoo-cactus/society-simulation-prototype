import hashlib
import json
from dataclasses import asdict, dataclass
from typing import cast

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    ObservedTarget,
)
from stage0_sim.application.engagements.catalog import EngagementCapabilityCatalog
from stage0_sim.domain.engagements import EngagementSpecification
from stage0_sim.domain.events import JsonValue

ENGAGEMENT_COMPILER_SCENE_VERSION = "engagement-compiler-scene.v1"


class CompilerSceneError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CompilerActor:
    actor_id: str
    display_name: str
    public_state_json: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "actor_id": self.actor_id,
            "display_name": self.display_name,
            "public_state": cast(dict[str, JsonValue], json.loads(self.public_state_json)),
        }


@dataclass(frozen=True, slots=True)
class CompilerObservedReference:
    reference_id: str
    kind: str
    name: str
    available: bool
    last_observed_tick: int | None
    supported_actions: tuple[str, ...]
    available_interactions: tuple[str, ...]
    public_state_json: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "reference_id": self.reference_id,
            "kind": self.kind,
            "name": self.name,
            "available": self.available,
            "last_observed_tick": self.last_observed_tick,
            "supported_actions": list(self.supported_actions),
            "available_interactions": list(self.available_interactions),
            "public_state": cast(dict[str, JsonValue], json.loads(self.public_state_json)),
        }


@dataclass(frozen=True, slots=True)
class EngagementCompilerScene:
    scene_version: str
    decision_id: str
    run_id: str
    requested_tick: int
    state_revision: int
    engagement_id: str
    intent: str
    actor: CompilerActor
    references: tuple[CompilerObservedReference, ...]
    offered_specialized_tools: tuple[str, ...]
    environment_json: str
    catalog_json: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "scene_version": self.scene_version,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "requested_tick": self.requested_tick,
            "state_revision": self.state_revision,
            "engagement": {
                "engagement_id": self.engagement_id,
                "intent": self.intent,
                "reference_ids": [
                    reference.reference_id for reference in self.references
                ],
            },
            "actor": self.actor.to_payload(),
            "references": [reference.to_payload() for reference in self.references],
            "offered_specialized_tools": list(self.offered_specialized_tools),
            "environment": cast(
                dict[str, JsonValue],
                json.loads(self.environment_json),
            ),
            "capability_catalog": cast(
                dict[str, JsonValue],
                json.loads(self.catalog_json),
            ),
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_payload())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def build_engagement_compiler_scene(
    request: CharacterDecisionRequest,
    engagement: EngagementSpecification,
    catalog: EngagementCapabilityCatalog,
) -> EngagementCompilerScene:
    observation = request.observation
    if request.agent_id != observation.agent_id:
        raise CompilerSceneError("decision actor does not match observation actor")

    targets_by_id: dict[str, ObservedTarget] = {}
    for target in observation.targets:
        if target.id in targets_by_id:
            raise CompilerSceneError(f"duplicate observed target ID: {target.id}")
        targets_by_id[target.id] = target

    unknown_references = sorted(
        set(engagement.reference_ids) - set(targets_by_id)
    )
    if unknown_references:
        raise CompilerSceneError(
            f"engagement references are not observed: {unknown_references}"
        )

    actor_public_state: dict[str, JsonValue] = {
        "location_id": observation.location_id,
        "activity": observation.activity,
        "satiety": observation.satiety,
        "energy": observation.energy,
        "stress": observation.stress,
        "spatial_location": observation.spatial_location,
        "senses": observation.senses,
        "equipment": observation.equipment,
        "carried_load": observation.carried_load,
    }
    environment: dict[str, JsonValue] = {
        "simulation_time": observation.simulation_time,
        "location_id": observation.location_id,
        "spatial_location": observation.spatial_location,
        "calendar_time": (
            cast(dict[str, JsonValue], asdict(observation.calendar_time))
            if observation.calendar_time is not None
            else None
        ),
        "environment": (
            cast(dict[str, JsonValue], asdict(observation.environment))
            if observation.environment is not None
            else None
        ),
    }
    references = tuple(
        _reference(targets_by_id[reference_id])
        for reference_id in sorted(engagement.reference_ids)
    )
    return EngagementCompilerScene(
        scene_version=ENGAGEMENT_COMPILER_SCENE_VERSION,
        decision_id=request.decision_id,
        run_id=request.run_id,
        requested_tick=request.requested_tick,
        state_revision=request.state_revision,
        engagement_id=engagement.engagement_id,
        intent=engagement.intent,
        actor=CompilerActor(
            actor_id=request.agent_id,
            display_name=observation.display_name,
            public_state_json=_canonical_json(actor_public_state),
        ),
        references=references,
        offered_specialized_tools=tuple(
            sorted(
                tool
                for tool in request.allowed_tools
                if tool not in {"engage", "check_environment"}
            )
        ),
        environment_json=_canonical_json(environment),
        catalog_json=_canonical_json(catalog.to_payload()),
    )


def _reference(target: ObservedTarget) -> CompilerObservedReference:
    return CompilerObservedReference(
        reference_id=target.id,
        kind=target.kind,
        name=target.name,
        available=target.available,
        last_observed_tick=target.last_observed_tick,
        supported_actions=tuple(sorted(target.supported_actions)),
        available_interactions=tuple(sorted(target.available_interactions)),
        public_state_json=_canonical_json(target.public_state or {}),
    )


def _canonical_json(value: JsonValue | dict[str, JsonValue]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
