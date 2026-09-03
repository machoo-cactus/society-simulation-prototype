import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict

from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
    EnvironmentObservation,
    ModelRequest,
    ModelToolCall,
    ModelTurn,
    ObservedTarget,
)
from stage0_sim.application.engagements import (
    AUDITORY_EXPRESSION,
    COMPILE_ENGAGEMENT_TOOL,
    EXPRESSIVE_BEHAVIOR,
    CapabilityCatalogError,
    CapabilityRegistration,
    CompilationDisposition,
    EngagementCompiler,
    EngagementCompilerResponseError,
    EngagementCompilerValidationError,
    build_engagement_compiler_scene,
    build_v1_capability_catalog,
)
from stage0_sim.application.engagements.contracts import NormalizedArgument
from stage0_sim.application.scenario import (
    EngagementCompilerSettingsDefinition,
    EngagementSettingsDefinition,
)
from stage0_sim.domain.engagements import EngagementSpecification

pytestmark = pytest.mark.model_contract


class _RecordingClient:
    synchronous = True

    def __init__(self, turn: ModelTurn) -> None:
        self.turn = turn
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return self.turn


class _ExtensionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str


def _extension_normalizer(
    model: BaseModel,
    settings: EngagementSettingsDefinition,
) -> tuple[NormalizedArgument, ...]:
    del settings
    return (NormalizedArgument("subject_id", str(model.subject_id)),)


def _request(
    *,
    memories: tuple[str, ...] = ("private memory",),
    character_description: str = "private profile prose",
    targets: tuple[ObservedTarget, ...] | None = None,
) -> CharacterDecisionRequest:
    return CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run-1",
        agent_id="actor",
        requested_tick=8,
        state_revision=17,
        trigger="idle",
        character_description=character_description,
        profile_id="profile-private",
        profile_template_version=2,
        profile_content_hash="private-profile-hash",
        observation=CharacterObservation(
            agent_id="actor",
            display_name="Alex",
            simulation_time=12.5,
            location_id="room",
            activity="IDLE",
            satiety=80.0,
            energy=70.0,
            stress=10.0,
            targets=targets
            or (
                ObservedTarget(
                    id="target",
                    kind="character",
                    name="Morgan",
                    public_state={"posture": "standing"},
                ),
                ObservedTarget(
                    id="unrelated",
                    kind="character",
                    name="Private stranger",
                    public_state={"private_vitals": {"energy": 1}},
                ),
            ),
            facts=(),
            recent_outcome=None,
            spatial_location={"room_id": "room", "private_path": "excluded"},
            environment=EnvironmentObservation(
                values={"weather": {"condition": "CLEAR"}},
            ),
        ),
        memories=memories,
        allowed_tools=("perform", "say", "engage"),
        situation_description="private plan and situation prose",
        situation_content_hash="private-situation-hash",
    )


def _specification() -> EngagementSpecification:
    return EngagementSpecification(
        engagement_id="engagement-1",
        intent="Wave toward Morgan.",
        reference_ids=("target",),
    )


def _turn(arguments: dict[str, object], *, text: str | None = None) -> ModelTurn:
    return ModelTurn(
        text=text,
        tool_calls=(
            ModelToolCall(
                call_id="compile-1",
                name=COMPILE_ENGAGEMENT_TOOL,
                arguments=arguments,
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="compiler-test",
        latency_ms=2.0,
    )


def _valid_group(
    *,
    group_id: str = "gesture",
    invocation_id: str = "gesture-1",
) -> dict[str, object]:
    return {
        "group_id": group_id,
        "required_atomic": True,
        "public_text": "Alex waves toward Morgan.",
        "invocations": [
            {
                "invocation_id": invocation_id,
                "capability": EXPRESSIVE_BEHAVIOR,
                "arguments": {
                    "subject_id": "actor",
                    "target_id": "target",
                    "public_text": "Alex waves.",
                    "expression_band": "moderate",
                },
            }
        ],
    }


def _compiled_arguments(*groups: dict[str, object]) -> dict[str, object]:
    return {
        "disposition": "compiled",
        "summary": "Alex makes a visible gesture.",
        "groups": list(groups or (_valid_group(),)),
    }


def test_catalog_v1_is_deterministic_extensible_and_rejects_duplicates() -> None:
    first = build_v1_capability_catalog()
    second = build_v1_capability_catalog()

    assert first.to_payload() == second.to_payload()
    assert [entry.name for entry in first.entries()] == [
        AUDITORY_EXPRESSION,
        "bounded_activity",
        EXPRESSIVE_BEHAVIOR,
    ]
    extension = CapabilityRegistration(
        name="custom_expression",
        description="A test-only registered expression.",
        consequence_tier=0,
        arguments_model=_ExtensionArguments,
        normalizer=_extension_normalizer,
    )
    first.register(extension)
    with pytest.raises(CapabilityCatalogError, match="duplicate"):
        first.register(extension)


def test_scene_hash_is_deterministic_and_excludes_private_context() -> None:
    catalog = build_v1_capability_catalog()
    first = build_engagement_compiler_scene(
        _request(),
        _specification(),
        catalog,
    )
    second = build_engagement_compiler_scene(
        _request(
            memories=("different secret",),
            character_description="different private profile",
        ),
        _specification(),
        catalog,
    )

    assert first == second
    assert hash(first) == hash(second)
    assert first.content_hash == second.content_hash
    serialized = first.canonical_json()
    assert "private memory" not in serialized
    assert "private profile" not in serialized
    assert "private plan" not in serialized
    assert "Private stranger" not in serialized
    assert "private_vitals" not in serialized
    assert '"reference_id":"target"' in serialized
    assert '"posture":"standing"' in serialized
    assert '"offered_specialized_tools":["perform","say"]' in serialized


def test_unknown_engagement_reference_is_rejected_before_model_call() -> None:
    async def exercise() -> None:
        client = _RecordingClient(_turn(_compiled_arguments()))
        compiler = EngagementCompiler(client)
        unknown = EngagementSpecification(
            engagement_id="engagement-1",
            intent="Wave at someone unseen.",
            reference_ids=("missing",),
        )

        with pytest.raises(
            EngagementCompilerValidationError,
            match="not observed",
        ):
            await compiler.compile_engagement(_request(), unknown)
        assert client.requests == []

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "turn",
    [
        ModelTurn(
            text=None,
            tool_calls=(),
            finish_reason="stop",
            provider="scripted",
            model="test",
            latency_ms=0.0,
        ),
        ModelTurn(
            text=None,
            tool_calls=(
                ModelToolCall("one", COMPILE_ENGAGEMENT_TOOL, {}),
                ModelToolCall("two", COMPILE_ENGAGEMENT_TOOL, {}),
            ),
            finish_reason="tool_calls",
            provider="scripted",
            model="test",
            latency_ms=0.0,
        ),
    ],
)
def test_compiler_rejects_missing_or_multiple_tool_calls(turn: ModelTurn) -> None:
    async def exercise() -> None:
        with pytest.raises(
            EngagementCompilerResponseError,
            match="exactly one tool call",
        ):
            await EngagementCompiler(_RecordingClient(turn)).compile_engagement(
                _request(),
                _specification(),
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "turn, match",
    [
        (
            _turn({**_compiled_arguments(), "unexpected": True}),
            "invalid compile_engagement output",
        ),
        (
            _turn(_compiled_arguments(), text="Narrated success."),
            "does not accept prose",
        ),
        (
            replace(
                _turn(_compiled_arguments()),
                tool_calls=(ModelToolCall("wrong", "other_tool", {}),),
            ),
            "unexpected engagement compilation tool",
        ),
    ],
)
def test_compiler_strictly_rejects_malformed_response_shapes(
    turn: ModelTurn,
    match: str,
) -> None:
    async def exercise() -> None:
        with pytest.raises(EngagementCompilerResponseError, match=match):
            await EngagementCompiler(_RecordingClient(turn)).compile_engagement(
                _request(),
                _specification(),
            )

    asyncio.run(exercise())


def test_compiler_preserves_valid_and_rejected_groups() -> None:
    async def exercise() -> None:
        invalid_group = {
            "group_id": "invalid",
            "invocations": [
                {
                    "invocation_id": "invalid-1",
                    "capability": "invented_state_write",
                    "arguments": {
                        "subject_id": "actor",
                        "component_path": "health.energy",
                        "value": 1000,
                    },
                }
            ],
        }
        result = await EngagementCompiler(
            _RecordingClient(
                _turn(_compiled_arguments(_valid_group(), invalid_group))
            )
        ).compile_engagement(_request(), _specification())

        assert result.disposition is CompilationDisposition.COMPILED
        assert [group.group_id for group in result.valid_groups] == ["gesture"]
        assert [group.group_id for group in result.rejected_groups] == ["invalid"]
        assert result.rejected_groups[0].issues[0].code == "unknown_capability"

    asyncio.run(exercise())


def test_capability_arguments_reject_arbitrary_numeric_state_writes() -> None:
    async def exercise() -> None:
        group = _valid_group()
        invocation = group["invocations"][0]
        assert isinstance(invocation, dict)
        arguments = invocation["arguments"]
        assert isinstance(arguments, dict)
        arguments["energy_delta"] = -999

        with pytest.raises(
            EngagementCompilerValidationError,
            match="no valid capability groups",
        ):
            await EngagementCompiler(
                _RecordingClient(_turn(_compiled_arguments(group)))
            ).compile_engagement(_request(), _specification())

    asyncio.run(exercise())


def test_compiler_rejects_invalid_invocation_references_and_duplicate_ids() -> None:
    async def exercise() -> None:
        bad_reference = _valid_group(group_id="bad-reference", invocation_id="same")
        invocation = bad_reference["invocations"][0]
        assert isinstance(invocation, dict)
        arguments = invocation["arguments"]
        assert isinstance(arguments, dict)
        arguments["target_id"] = "unrelated"
        duplicate = _valid_group(group_id="duplicate", invocation_id="same")
        payload = _compiled_arguments(
            _valid_group(group_id="valid", invocation_id="valid-1"),
            bad_reference,
            duplicate,
        )

        result = await EngagementCompiler(
            _RecordingClient(_turn(payload))
        ).compile_engagement(_request(), _specification())

        rejected = {group.group_id: group for group in result.rejected_groups}
        assert rejected["bad-reference"].issues[0].code == "invalid_target_reference"
        assert any(
            issue.code == "duplicate_invocation_id"
            for issue in rejected["duplicate"].issues
        )

    asyncio.run(exercise())


def test_compiler_uses_configured_model_request_and_normalizes_bands() -> None:
    async def exercise() -> None:
        group = {
            "group_id": "warning",
            "invocations": [
                {
                    "invocation_id": "warning-1",
                    "capability": AUDITORY_EXPRESSION,
                    "arguments": {
                        "subject_id": "actor",
                        "target_id": "target",
                        "public_text": "Look out!",
                        "mode": "speech",
                        "sound_band": "loud",
                        "effort_band": "high",
                        "listener_effect": "alarming",
                    },
                }
            ],
        }
        client = _RecordingClient(_turn(_compiled_arguments(group)))
        compiler = EngagementCompiler(
            client,
            compiler_settings=EngagementCompilerSettingsDefinition(
                model_profile="compiler-profile",
                timeout_seconds=7.5,
                max_output_tokens=321,
            ),
            engagement_settings=EngagementSettingsDefinition(
                loud_sound_range=25,
                high_effort_energy_cost=7.0,
                alarming_listener_stress_delta=3.0,
            ),
        )

        result = await compiler.compile_engagement(_request(), _specification())

        model_request = client.requests[0]
        assert model_request.model == "compiler-profile"
        assert model_request.timeout_seconds == 7.5
        assert model_request.max_output_tokens == 321
        assert model_request.prompt_version == "engagement_compilation.v1"
        assert len(model_request.tools) == 1
        assert model_request.tools[0].name == COMPILE_ENGAGEMENT_TOOL
        scene = json.loads(model_request.messages[1].content or "{}")
        assert scene["capability_catalog"]["version"] == "engagement-capabilities.v1"
        normalized = {
            item.name: item.value
            for item in result.valid_groups[0].invocations[0].arguments
        }
        assert normalized["sound_range"] == 25
        assert normalized["energy_cost"] == 7.0
        assert normalized["listener_stress_delta"] == 3.0

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "arguments, disposition, specialized_tool",
    [
        (
            _compiled_arguments(),
            CompilationDisposition.COMPILED,
            None,
        ),
        (
            {
                "disposition": "specialized_tool_required",
                "summary": "Use ordinary speech.",
                "specialized_tool": "say",
                "reason": "The offered say tool covers this attempt.",
            },
            CompilationDisposition.SPECIALIZED_TOOL_REQUIRED,
            "say",
        ),
        (
            {
                "disposition": "unsupported",
                "summary": "The requested material outcome is unavailable.",
                "reason": "No V1 capability can mutate material custody.",
            },
            CompilationDisposition.UNSUPPORTED,
            None,
        ),
    ],
)
def test_compiler_returns_all_three_dispositions(
    arguments: dict[str, object],
    disposition: CompilationDisposition,
    specialized_tool: str | None,
) -> None:
    async def exercise() -> None:
        result = await EngagementCompiler(
            _RecordingClient(_turn(arguments))
        ).compile_engagement(_request(), _specification())

        assert result.disposition is disposition
        assert result.specialized_tool == specialized_tool

    asyncio.run(exercise())
