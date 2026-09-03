from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from stage0_sim.application.migrations.constants import (
    CURRENT_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSIONS,
)
from stage0_sim.application.migrations.models import (
    MigrationContext,
    MigrationResult,
    ResourceKind,
)

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class _StepOutput:
    payload: JsonObject
    warnings: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()


MigrationTransform = Callable[[JsonObject, MigrationContext], _StepOutput]


@dataclass(frozen=True, slots=True)
class _MigrationStep:
    to_version: int
    transform: MigrationTransform


class MigrationRegistry:
    def __init__(self) -> None:
        self._steps: dict[tuple[ResourceKind, int], _MigrationStep] = {}

    def register(
        self,
        kind: ResourceKind,
        from_version: int,
        to_version: int,
        transform: MigrationTransform,
    ) -> None:
        key = (kind, from_version)
        if key in self._steps:
            raise ValueError(
                f"duplicate {kind.value} migration from version {from_version}"
            )
        if to_version != from_version + 1:
            raise ValueError("content migrations must connect adjacent versions")
        self._steps[key] = _MigrationStep(to_version, transform)

    def path(self, kind: ResourceKind, from_version: int) -> tuple[int, ...]:
        current = CURRENT_SCHEMA_VERSIONS[kind]
        if from_version not in SUPPORTED_SCHEMA_VERSIONS[kind]:
            raise ValueError(
                f"unsupported {kind.value} schema version {from_version}; "
                f"supported versions are "
                f"{sorted(SUPPORTED_SCHEMA_VERSIONS[kind])}"
            )
        versions = [from_version]
        seen: set[int] = set()
        version = from_version
        while version != current:
            if version in seen:
                raise ValueError(
                    f"cyclic {kind.value} migration path at version {version}"
                )
            seen.add(version)
            step = self._steps.get((kind, version))
            if step is None:
                raise ValueError(
                    f"incomplete {kind.value} migration path from version "
                    f"{from_version}: no transform from version {version}"
                )
            version = step.to_version
            versions.append(version)
        return tuple(versions)

    def validate_integrity(self) -> None:
        for kind, versions in SUPPORTED_SCHEMA_VERSIONS.items():
            current = CURRENT_SCHEMA_VERSIONS[kind]
            for version in sorted(versions):
                self.path(kind, version)
            unexpected = sorted(
                version
                for registered_kind, version in self._steps
                if registered_kind is kind and version >= current
            )
            if unexpected:
                raise ValueError(
                    f"{kind.value} has transforms at or after current version: "
                    f"{unexpected}"
                )

    def migrate(
        self,
        kind: ResourceKind,
        resource_id: str,
        raw: JsonObject,
        context: MigrationContext | None = None,
    ) -> MigrationResult:
        target = CURRENT_SCHEMA_VERSIONS[kind]
        raw_version = raw.get("schema_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            return MigrationResult(
                resource_kind=kind,
                resource_id=resource_id,
                from_version=None,
                to_version=target,
                errors=["schema_version must be an integer"],
            )
        try:
            versions = self.path(kind, raw_version)
            payload = copy.deepcopy(raw)
            warnings: list[str] = []
            changed_paths: list[str] = []
            migration_context = context or MigrationContext()
            for version in versions[:-1]:
                step = self._steps[(kind, version)]
                output = step.transform(payload, migration_context)
                payload = output.payload
                warnings.extend(output.warnings)
                changed_paths.extend(output.changed_paths)
            canonical = _validate_current(kind, payload)
        except (TypeError, ValueError, ValidationError) as error:
            return MigrationResult(
                resource_kind=kind,
                resource_id=resource_id,
                from_version=raw_version,
                to_version=target,
                errors=[str(error)],
            )
        return MigrationResult(
            resource_kind=kind,
            resource_id=resource_id,
            from_version=raw_version,
            to_version=target,
            canonical_json=canonical,
            warnings=sorted(dict.fromkeys(warnings)),
            changed_paths=sorted(dict.fromkeys(changed_paths)),
        )


def _validate_current(kind: ResourceKind, payload: JsonObject) -> JsonObject:
    if kind is ResourceKind.CHARACTER:
        from stage0_sim.application.characters import CharacterDefinition

        return CharacterDefinition.model_validate(payload).model_dump(mode="json")
    if kind is ResourceKind.ELEMENT:
        from stage0_sim.application.elements import SCENARIO_ELEMENT_ADAPTER

        return SCENARIO_ELEMENT_ADAPTER.validate_python(payload).model_dump(
            mode="json"
        )
    from stage0_sim.application.elements import ScenarioSourceDefinition

    return ScenarioSourceDefinition.model_validate(payload).model_dump(mode="json")


def _character_v1_to_v2(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    del context
    payload = copy.deepcopy(raw)
    if not isinstance(payload.get("id"), str):
        raise ValueError("legacy character requires a string id")
    identity = payload.get("identity")
    if not isinstance(identity, dict) or not isinstance(
        identity.get("display_name"), str
    ):
        raise ValueError("legacy character requires identity.display_name")
    warnings: list[str] = []
    changed = ["$.schema_version"]
    preserved: list[dict[str, Any]] = []

    age = identity.pop("age", None)
    if age is not None:
        preserved.append(
            _custom_field(
                "identity_age",
                "Legacy identity age",
                age,
            )
        )
        warnings.append(
            "preserved identity.age because an age cannot be converted to a "
            "birth date without a reference date"
        )
        changed.extend(
            [
                "$.identity.age",
                "$.custom_sections[id=migration-v1-legacy]",
            ]
        )

    appearance = payload.get("appearance")
    if isinstance(appearance, dict):
        height = appearance.pop("height", None)
        if height not in (None, ""):
            height_cm = _parse_height_cm(height)
            if height_cm is not None:
                measurements = payload.setdefault("body_measurements", {})
                if not isinstance(measurements, dict):
                    raise ValueError("body_measurements must be an object")
                existing = measurements.get("height_cm")
                if existing not in (None, height_cm):
                    preserved.append(
                        _custom_field(
                            "appearance_height",
                            "Legacy appearance height",
                            height,
                        )
                    )
                    warnings.append(
                        "preserved appearance.height because body_measurements."
                        "height_cm already contains a different value"
                    )
                else:
                    measurements["height_cm"] = height_cm
                changed.extend(
                    ["$.appearance.height", "$.body_measurements.height_cm"]
                )
            else:
                preserved.append(
                    _custom_field(
                        "appearance_height",
                        "Legacy appearance height",
                        height,
                    )
                )
                warnings.append(
                    "preserved appearance.height because it was not an "
                    "unambiguous centimetre measurement"
                )
                changed.extend(
                    [
                        "$.appearance.height",
                        "$.custom_sections[id=migration-v1-legacy]",
                    ]
                )

    if preserved:
        sections = payload.setdefault("custom_sections", [])
        if not isinstance(sections, list):
            raise ValueError("custom_sections must be an array")
        if any(
            isinstance(section, dict)
            and section.get("id") == "migration-v1-legacy"
            for section in sections
        ):
            raise ValueError(
                "legacy character already uses reserved custom section ID "
                "migration-v1-legacy"
            )
        sections.append(
            {
                "id": "migration-v1-legacy",
                "title": "Preserved version 1 data",
                "prompt_visible": True,
                "ui_visible": True,
                "fields": preserved,
            }
        )
    payload["schema_version"] = 2
    return _StepOutput(payload, tuple(warnings), tuple(changed))


def _custom_field(key: str, label: str, value: Any) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "prompt_visible": True,
        "ui_visible": True,
    }


_HEIGHT_CM_PATTERN = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?:cm|centimetres?|centimeters?)\s*$",
    re.IGNORECASE,
)


def _parse_height_cm(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        numeric = float(value)
        return numeric if 0 < numeric <= 300 else None
    if not isinstance(value, str):
        return None
    match = _HEIGHT_CM_PATTERN.fullmatch(value)
    if match is None:
        return None
    numeric = float(match.group("value"))
    return numeric if 0 < numeric <= 300 else None


def _element_v1_to_v2(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    payload = copy.deepcopy(raw)
    kind = payload.get("kind")
    if kind not in {"npc_role", "object", "room", "building"}:
        raise ValueError(f"unsupported legacy element kind: {kind!r}")
    if not isinstance(payload.get("id"), str):
        raise ValueError("legacy element requires a string id")
    changed = ["$.schema_version"]
    if kind == "object":
        if "physical" in payload:
            raise ValueError("version 1 object cannot define physical")
        payload["physical"] = {
            "footprint": {"cells": [{"x": 0, "y": 0}]},
            "obstruction": {
                "movement": "NONE",
                "vision": "TRANSPARENT",
            },
            "capabilities": {},
        }
        changed.append("$.physical")
    elif kind == "room":
        if "spatial_metric" in payload:
            raise ValueError("version 1 room cannot define spatial_metric")
        payload["spatial_metric"] = {"microcells_per_legacy_cell": 9}
        changed.append("$.spatial_metric")
        objects = payload.get("objects", [])
        if not isinstance(objects, list):
            raise ValueError("legacy room objects must be an array")
        for index, placement in enumerate(objects):
            if not isinstance(placement, dict):
                raise ValueError(f"legacy room object {index} must be an object")
            reference = placement.get("element")
            if not isinstance(reference, dict) or not isinstance(
                reference.get("id"), str
            ):
                raise ValueError(
                    f"legacy room object {index} requires an element reference"
                )
            object_id = reference["id"]
            migrated = context.element_definitions.get(object_id)
            if migrated is None:
                raise ValueError(
                    f"legacy room references object {object_id!r} before it "
                    "has been migrated"
                )
            if migrated.get("kind") != "object" or not isinstance(
                migrated.get("physical"), dict
            ):
                raise ValueError(
                    f"migrated object {object_id!r} does not contain physical data"
                )
            position = placement.get("position")
            if not isinstance(position, dict):
                raise ValueError(
                    f"legacy room object {index} requires a legacy position"
                )
            x = position.get("x")
            y = position.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                raise ValueError(
                    f"legacy room object {index} position must use integer x/y"
                )
            placement["placement"] = {
                "anchor": {"x": 9 * x + 4, "y": 9 * y + 4},
                "orientation": "NORTH",
                "parent_relation": {
                    "kind": "ON_FLOOR",
                    "parent_id": None,
                    "slot_id": None,
                },
            }
            changed.append(f"$.objects[{index}].placement")
    payload["schema_version"] = 2
    return _StepOutput(payload, changed_paths=tuple(changed))


def _scenario_v4_to_v5(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    payload = copy.deepcopy(raw)
    changed = ["$.schema_version"]
    _rewrite_element_references(payload, context.element_hashes, changed)
    payload["schema_version"] = 5
    return _StepOutput(payload, changed_paths=tuple(changed))


def _element_v2_to_v3(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    del context
    payload = copy.deepcopy(raw)
    kind = payload.get("kind")
    if kind not in {"npc_role", "object", "room", "building"}:
        raise ValueError(f"unsupported version 2 element kind: {kind!r}")
    changed = ["$.schema_version"]
    warnings: list[str] = []
    if kind == "object":
        physical = payload.get("physical")
        if not isinstance(physical, dict):
            raise ValueError("version 2 object requires physical data")
        physical.setdefault(
            "intrinsics",
            {
                "mass_kg": None,
                "dimensions_cm": None,
                "size_class": None,
            },
        )
        obstruction = physical.setdefault("obstruction", {})
        if not isinstance(obstruction, dict):
            raise ValueError("version 2 object physical.obstruction must be an object")
        obstruction.setdefault("hearing", "PASS")
        obstruction.setdefault("smell", "PASS")
        changed.extend(
            [
                "$.physical.intrinsics",
                "$.physical.obstruction.hearing",
                "$.physical.obstruction.smell",
            ]
        )
    elif kind == "npc_role":
        multiplier = payload.pop("hearing_multiplier", 1.0)
        if not isinstance(multiplier, int | float) or isinstance(multiplier, bool):
            raise ValueError("version 2 NPC hearing_multiplier must be numeric")
        payload["hearing_range"] = max(0, round(10 * float(multiplier)))
        payload["smell_range"] = 0
        changed.extend(
            ["$.hearing_multiplier", "$.hearing_range", "$.smell_range"]
        )
        if float(multiplier) != 1.0:
            warnings.append(
                "converted NPC hearing_multiplier using the legacy default "
                "hearing range of 10 cells"
            )
    payload["schema_version"] = 3
    return _StepOutput(
        payload,
        warnings=tuple(warnings),
        changed_paths=tuple(changed),
    )


def _scenario_v5_to_v6(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    payload = copy.deepcopy(raw)
    changed = ["$.schema_version"]
    warnings: list[str] = []
    perception = payload.setdefault("perception", {})
    if not isinstance(perception, dict):
        raise ValueError("version 5 scenario perception must be an object")
    legacy_hearing_range = perception.pop("hearing_range", 10)
    if (
        not isinstance(legacy_hearing_range, int)
        or isinstance(legacy_hearing_range, bool)
        or legacy_hearing_range < 0
    ):
        raise ValueError("version 5 perception.hearing_range must be non-negative")
    perception["voice_range"] = legacy_hearing_range
    changed.extend(["$.perception.hearing_range", "$.perception.voice_range"])
    if perception.get("blocked_tiles_are_opaque") is False:
        perception["blocked_tiles_are_opaque"] = True
        changed.append("$.perception.blocked_tiles_are_opaque")
        warnings.append(
            "version 6 treats blocked room cells as structural blockers for "
            "vision, hearing, and smell"
        )
    entities = payload.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError("version 5 scenario entities must be an array")
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise ValueError(f"version 5 scenario entity {index} must be an object")
        components = entity.get("components", {})
        if not isinstance(components, dict):
            raise ValueError(
                f"version 5 scenario entity {index} components must be an object"
            )
        senses = components.get("senses")
        if senses is not None:
            if not isinstance(senses, dict):
                raise ValueError(
                    f"version 5 scenario entity {index} senses must be an object"
                )
            multiplier = senses.pop("hearing_multiplier", 1.0)
            if (
                not isinstance(multiplier, int | float)
                or isinstance(multiplier, bool)
                or multiplier <= 0
            ):
                raise ValueError(
                    f"version 5 scenario entity {index} hearing multiplier "
                    "must be greater than zero"
                )
            senses["hearing_range"] = max(
                0,
                round(legacy_hearing_range * float(multiplier)),
            )
            senses["smell_range"] = 0
            changed.extend(
                [
                    f"$.entities[{index}].components.senses.hearing_multiplier",
                    f"$.entities[{index}].components.senses.hearing_range",
                    f"$.entities[{index}].components.senses.smell_range",
                ]
            )
        components.setdefault(
            "embodiment",
            {
                "max_single_object_mass_kg": 25.0,
                "max_carried_mass_kg": 35.0,
                "equipment_slots": [
                    {"slot": slot, "capacity": 1}
                    for slot in (
                        "EYES",
                        "EARS",
                        "FACE",
                        "HEAD",
                        "NECK",
                        "TORSO",
                        "BACK",
                        "WRIST",
                        "LEGS",
                        "FEET",
                    )
                ],
            },
        )
        changed.append(f"$.entities[{index}].components.embodiment")
    _rewrite_element_references(payload, context.element_hashes, changed)
    payload["schema_version"] = 6
    return _StepOutput(
        payload,
        warnings=tuple(warnings),
        changed_paths=tuple(changed),
    )


def _scenario_v6_to_v7(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    del context
    payload = copy.deepcopy(raw)
    changed = ["$.schema_version"]
    old_default_tools = [
        "navigate_to",
        "perform",
        "say",
        "wait",
        "skip",
        "transact",
        "check_environment",
    ]
    cognition = payload.get("cognition")
    if cognition is not None:
        if not isinstance(cognition, dict):
            raise ValueError("version 6 scenario cognition must be an object")
        tool_allowlist = cognition.get("tool_allowlist")
        if tool_allowlist is None or tool_allowlist == old_default_tools:
            cognition["tool_allowlist"] = [
                "navigate_to",
                "perform",
                "say",
                "engage",
                "wait",
                "skip",
                "transact",
                "check_environment",
            ]
            changed.append("$.cognition.tool_allowlist")
    payload["schema_version"] = 7
    return _StepOutput(payload, changed_paths=tuple(changed))


def _element_v3_to_v4(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    del context
    payload = copy.deepcopy(raw)
    changed = ["$.schema_version"]
    if payload.get("kind") == "object":
        physical = payload.get("physical")
        if not isinstance(physical, dict):
            raise ValueError("version 3 object requires physical data")
        capabilities = physical.get("capabilities")
        if not isinstance(capabilities, dict):
            raise ValueError(
                "version 3 object physical.capabilities must be an object"
            )
        capabilities.setdefault("content_endpoints", [])
        changed.append("$.physical.capabilities.content_endpoints")
    payload["schema_version"] = 4
    return _StepOutput(payload, changed_paths=tuple(changed))


def _scenario_v7_to_v8(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    del context
    payload = copy.deepcopy(raw)
    changed = ["$.schema_version"]
    old_default_tools = [
        "navigate_to",
        "perform",
        "say",
        "engage",
        "wait",
        "skip",
        "transact",
        "check_environment",
    ]
    new_default_tools = [
        *old_default_tools,
        "read_text",
        "write_text",
    ]
    cognition = payload.get("cognition")
    if cognition is not None:
        if not isinstance(cognition, dict):
            raise ValueError("version 7 scenario cognition must be an object")
        tool_allowlist = cognition.get("tool_allowlist")
        if tool_allowlist is None or tool_allowlist == old_default_tools:
            cognition["tool_allowlist"] = new_default_tools
            changed.append("$.cognition.tool_allowlist")
    entities = payload.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError("version 7 scenario entities must be an array")
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise ValueError(
                f"version 7 scenario entity {index} must be an object"
            )
        components = entity.get("components", {})
        if not isinstance(components, dict):
            raise ValueError(
                f"version 7 scenario entity {index} components must be an object"
            )
        controller = components.get("controller")
        if controller is None:
            continue
        if not isinstance(controller, dict):
            raise ValueError(
                f"version 7 scenario entity {index} controller must be an object"
            )
        tool_allowlist = controller.get("tool_allowlist")
        if tool_allowlist is None or tool_allowlist == old_default_tools:
            controller["tool_allowlist"] = new_default_tools
            changed.append(
                f"$.entities[{index}].components.controller.tool_allowlist"
            )
    payload.setdefault(
        "text_content",
        {
            "artifacts": [],
            "collections": [],
            "addresses": [],
            "groups": [],
        },
    )
    changed.append("$.text_content")
    payload["schema_version"] = 8
    return _StepOutput(payload, changed_paths=tuple(changed))


def _element_v4_to_v5(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    del context
    payload = copy.deepcopy(raw)
    payload["schema_version"] = 5
    return _StepOutput(payload, changed_paths=("$.schema_version",))


def _scenario_v8_to_v9(
    raw: JsonObject,
    context: MigrationContext,
) -> _StepOutput:
    payload = copy.deepcopy(raw)
    changed = ["$.schema_version"]
    homeostasis = payload.setdefault("homeostasis", {})
    if not isinstance(homeostasis, dict):
        raise ValueError("version 8 scenario homeostasis must be an object")
    for field in (
        "drink_hydration_delta",
        "read_happiness_delta",
        "social_connection_delta",
        "social_happiness_delta",
        "alarming_fear_delta",
        "calming_happiness_delta",
        "calming_fear_delta",
    ):
        homeostasis.setdefault(field, 0.0)
        changed.append(f"$.homeostasis.{field}")
    coefficients = homeostasis.setdefault("activity_coefficients", {})
    if not isinstance(coefficients, dict):
        raise ValueError(
            "version 8 scenario homeostasis.activity_coefficients must be an object"
        )
    for activity, rates in coefficients.items():
        if not isinstance(rates, dict):
            raise ValueError(
                f"version 8 activity coefficient {activity!r} must be an object"
            )
        for field in ("hydration", "social_connection", "happiness", "fear"):
            rates.setdefault(field, 0.0)
            changed.append(
                f"$.homeostasis.activity_coefficients.{activity}.{field}"
            )
    entities = payload.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError("version 8 scenario entities must be an array")
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise ValueError(f"version 8 scenario entity {index} must be an object")
        components = entity.get("components", {})
        if not isinstance(components, dict):
            raise ValueError(
                f"version 8 scenario entity {index} components must be an object"
            )
        state = components.get("homeostasis")
        if state is None:
            continue
        if not isinstance(state, dict):
            raise ValueError(
                f"version 8 scenario entity {index} homeostasis must be an object"
            )
        defaults = {
            "hydration": 100.0,
            "social_connection": 50.0,
            "happiness": 50.0,
            "fear": 0.0,
        }
        for field, value in defaults.items():
            state.setdefault(field, value)
            changed.append(
                f"$.entities[{index}].components.homeostasis.{field}"
            )
    system1 = payload.setdefault("system1", {})
    if not isinstance(system1, dict):
        raise ValueError("version 8 scenario system1 must be an object")
    enabled = ["SATIETY", "ENERGY", "STRESS"]
    system1.setdefault("enabled_drives", enabled)
    system1.setdefault("tie_break_order", enabled)
    changed.extend(
        ["$.system1.enabled_drives", "$.system1.tie_break_order"]
    )
    _rewrite_element_references(payload, context.element_hashes, changed)
    payload["schema_version"] = 9
    return _StepOutput(payload, changed_paths=tuple(changed))


def _rewrite_element_references(
    value: Any,
    hashes: dict[str, str],
    changed: list[str],
    path: str = "$",
) -> None:
    if isinstance(value, dict):
        if {"kind", "id", "content_hash"} <= value.keys():
            element_id = value.get("id")
            if not isinstance(element_id, str):
                raise ValueError(f"{path}.id must be a string")
            expected = hashes.get(element_id)
            if expected is None:
                raise ValueError(
                    f"{path} references missing migrated element {element_id!r}"
                )
            if value.get("content_hash") != expected:
                value["content_hash"] = expected
                changed.append(f"{path}.content_hash")
        for key in sorted(value):
            _rewrite_element_references(
                value[key],
                hashes,
                changed,
                f"{path}.{key}",
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _rewrite_element_references(
                child,
                hashes,
                changed,
                f"{path}[{index}]",
            )


CONTENT_MIGRATION_REGISTRY = MigrationRegistry()
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.CHARACTER,
    1,
    2,
    _character_v1_to_v2,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.ELEMENT,
    1,
    2,
    _element_v1_to_v2,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.ELEMENT,
    2,
    3,
    _element_v2_to_v3,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.ELEMENT,
    3,
    4,
    _element_v3_to_v4,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.ELEMENT,
    4,
    5,
    _element_v4_to_v5,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.SCENARIO,
    4,
    5,
    _scenario_v4_to_v5,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.SCENARIO,
    5,
    6,
    _scenario_v5_to_v6,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.SCENARIO,
    6,
    7,
    _scenario_v6_to_v7,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.SCENARIO,
    7,
    8,
    _scenario_v7_to_v8,
)
CONTENT_MIGRATION_REGISTRY.register(
    ResourceKind.SCENARIO,
    8,
    9,
    _scenario_v8_to_v9,
)
CONTENT_MIGRATION_REGISTRY.validate_integrity()
