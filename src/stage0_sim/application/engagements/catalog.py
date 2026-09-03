import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from stage0_sim.application.engagements.contracts import (
    AuditoryExpressionArguments,
    BoundedActivityArguments,
    DurationBand,
    EffortBand,
    ExpressiveBehaviorArguments,
    ListenerEffectBand,
    NormalizedArgument,
    SoundBand,
    StressEffectBand,
)
from stage0_sim.application.scenario import EngagementSettingsDefinition
from stage0_sim.domain.events import JsonValue

ENGAGEMENT_CAPABILITY_CATALOG_VERSION = "engagement-capabilities.v1"
EXPRESSIVE_BEHAVIOR = "expressive_behavior"
AUDITORY_EXPRESSION = "auditory_expression"
BOUNDED_ACTIVITY = "bounded_activity"

type CapabilityNormalizer = Callable[
    [BaseModel, EngagementSettingsDefinition],
    tuple[NormalizedArgument, ...],
]


class CapabilityCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    name: str
    description: str
    consequence_tier: int
    arguments_model: type[BaseModel]
    normalizer: CapabilityNormalizer


@dataclass(frozen=True, slots=True)
class CapabilityCatalogEntry:
    name: str
    description: str
    consequence_tier: int
    input_schema_json: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "description": self.description,
            "consequence_tier": self.consequence_tier,
            "input_schema": cast(dict[str, JsonValue], json.loads(self.input_schema_json)),
        }


class EngagementCapabilityCatalog:
    def __init__(self, version: str) -> None:
        if not version.strip():
            raise CapabilityCatalogError("catalog version must not be empty")
        self.version = version
        self._registrations: dict[str, CapabilityRegistration] = {}

    def register(self, registration: CapabilityRegistration) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", registration.name):
            raise CapabilityCatalogError(
                f"invalid capability name: {registration.name!r}"
            )
        if registration.name in self._registrations:
            raise CapabilityCatalogError(
                f"duplicate capability registration: {registration.name}"
            )
        if not registration.description.strip():
            raise CapabilityCatalogError("capability description must not be empty")
        if registration.consequence_tier < 0:
            raise CapabilityCatalogError("capability consequence tier must be non-negative")
        self._registrations[registration.name] = registration

    def registration(self, name: str) -> CapabilityRegistration | None:
        return self._registrations.get(name)

    def entries(self) -> tuple[CapabilityCatalogEntry, ...]:
        return tuple(
            CapabilityCatalogEntry(
                name=registration.name,
                description=registration.description,
                consequence_tier=registration.consequence_tier,
                input_schema_json=json.dumps(
                    registration.arguments_model.model_json_schema(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            for registration in sorted(
                self._registrations.values(),
                key=lambda item: item.name,
            )
        )

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "version": self.version,
            "capabilities": [entry.to_payload() for entry in self.entries()],
        }


def build_v1_capability_catalog() -> EngagementCapabilityCatalog:
    catalog = EngagementCapabilityCatalog(ENGAGEMENT_CAPABILITY_CATALOG_VERSION)
    catalog.register(
        CapabilityRegistration(
            name=EXPRESSIVE_BEHAVIOR,
            description=(
                "Produce visible, harmless expressive behavior by the initiating "
                "actor without changing arbitrary state."
            ),
            consequence_tier=0,
            arguments_model=ExpressiveBehaviorArguments,
            normalizer=_normalize_expressive_behavior,
        )
    )
    catalog.register(
        CapabilityRegistration(
            name=AUDITORY_EXPRESSION,
            description=(
                "Produce a bounded auditory expression whose range, effort, and "
                "listener effect are selected only through configured bands."
            ),
            consequence_tier=1,
            arguments_model=AuditoryExpressionArguments,
            normalizer=_normalize_auditory_expression,
        )
    )
    catalog.register(
        CapabilityRegistration(
            name=BOUNDED_ACTIVITY,
            description=(
                "Attempt a local bounded activity with configured duration, effort, "
                "and stress-effect bands."
            ),
            consequence_tier=1,
            arguments_model=BoundedActivityArguments,
            normalizer=_normalize_bounded_activity,
        )
    )
    return catalog


def _normalize_expressive_behavior(
    model: BaseModel,
    settings: EngagementSettingsDefinition,
) -> tuple[NormalizedArgument, ...]:
    del settings
    arguments = cast(ExpressiveBehaviorArguments, model)
    return _arguments(
        expression_band=arguments.expression_band.value,
        public_text=arguments.public_text,
        subject_id=arguments.subject_id,
        target_id=arguments.target_id,
    )


def _normalize_auditory_expression(
    model: BaseModel,
    settings: EngagementSettingsDefinition,
) -> tuple[NormalizedArgument, ...]:
    arguments = cast(AuditoryExpressionArguments, model)
    sound_range = {
        SoundBand.QUIET: settings.quiet_sound_range,
        SoundBand.NORMAL: settings.normal_sound_range,
        SoundBand.LOUD: settings.loud_sound_range,
    }[arguments.sound_band]
    energy_cost = {
        EffortBand.LOW: settings.low_effort_energy_cost,
        EffortBand.MEDIUM: settings.medium_effort_energy_cost,
        EffortBand.HIGH: settings.high_effort_energy_cost,
    }[arguments.effort_band]
    listener_stress_delta = (
        settings.alarming_listener_stress_delta
        if arguments.listener_effect is ListenerEffectBand.ALARMING
        else 0.0
    )
    return _arguments(
        effort_band=arguments.effort_band.value,
        energy_cost=energy_cost,
        listener_effect=arguments.listener_effect.value,
        listener_stress_delta=listener_stress_delta,
        mode=arguments.mode.value,
        public_text=arguments.public_text,
        sound_band=arguments.sound_band.value,
        sound_range=sound_range,
        subject_id=arguments.subject_id,
        target_id=arguments.target_id,
    )


def _normalize_bounded_activity(
    model: BaseModel,
    settings: EngagementSettingsDefinition,
) -> tuple[NormalizedArgument, ...]:
    arguments = cast(BoundedActivityArguments, model)
    duration_seconds = {
        DurationBand.SHORT: settings.short_activity_seconds,
        DurationBand.MEDIUM: settings.medium_activity_seconds,
        DurationBand.LONG: settings.long_activity_seconds,
    }[arguments.duration_band]
    energy_cost = {
        EffortBand.LOW: settings.low_effort_energy_cost,
        EffortBand.MEDIUM: settings.medium_effort_energy_cost,
        EffortBand.HIGH: settings.high_effort_energy_cost,
    }[arguments.effort_band]
    stress_delta = {
        StressEffectBand.CALMING: settings.calming_stress_delta,
        StressEffectBand.NEUTRAL: 0.0,
        StressEffectBand.ACTIVATING: settings.activating_stress_delta,
    }[arguments.stress_effect]
    return _arguments(
        activity=arguments.activity,
        duration_band=arguments.duration_band.value,
        duration_seconds=duration_seconds,
        effort_band=arguments.effort_band.value,
        energy_cost=energy_cost,
        stress_delta=stress_delta,
        stress_effect=arguments.stress_effect.value,
        subject_id=arguments.subject_id,
        target_id=arguments.target_id,
    )


def _arguments(**values: str | int | float | bool | None) -> tuple[NormalizedArgument, ...]:
    return tuple(
        NormalizedArgument(name=name, value=value)
        for name, value in sorted(values.items())
    )
