import asyncio
from pathlib import Path

import pytest

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.character_synthesis import (
    SITUATION_SYSTEM_PROMPT,
    CharacterSituationArtifact,
    CharacterSituationGenerationMetadata,
    CharacterSituationSynthesisError,
    CharacterSituationSynthesisInput,
    ModelCharacterSituationSynthesizer,
    SynthesizedCharacterSituation,
    build_synthesis_input,
    canonical_hash,
)
from stage0_sim.application.characters import CharacterDefinition
from stage0_sim.application.manager import SimulationManager
from stage0_sim.application.scenario import ScenarioDefinition
from stage0_sim.domain.components import CharacterSituationComponent


def _character(character_id: str, display_name: str) -> CharacterDefinition:
    return CharacterDefinition.model_validate(
        {
            "schema_version": 2,
            "id": character_id,
            "identity": {
                "display_name": display_name,
                "birth_date": "1990-02-14",
            },
            "body_measurements": {
                "measured_on": "2026-08-12",
                "height_cm": 178.0,
            },
            "financial_situation": {
                "as_of_date": "2026-08-31",
                "currency": "CAD",
                "total_debt": 18000,
            },
            "health": {
                "as_of_date": "2026-08-12",
                "vision": "Myopia corrected with glasses",
            },
            "presentation": {
                "aesthetic_identity": "Quietly practical",
                "context_variations": [
                    "Uses lighter layers and comfortable shoes while travelling"
                ],
            },
            "dispositions": {
                "summary": "Usually observant and deliberate",
                "novelty_response": "Curious after first orienting to the setting",
            },
        }
    )


def _scenario(*, enabled: bool, second_slot: bool = False) -> ScenarioDefinition:
    entities = [
        {
            "id": "agent-001",
            "components": {
                "character_slot": {
                    "label": "Vacation traveller",
                    "briefing": "Beginning a quiet coastal vacation.",
                    "default_character_id": "alex",
                },
                "goals": {
                    "goals": [
                        {
                            "id": "settle-in",
                            "description": "Settle into the guest house",
                            "priority": 50,
                        },
                        {
                            "id": "find-check-in",
                            "description": "Find the check-in desk",
                            "priority": 100,
                        },
                    ],
                },
            },
        }
    ]
    if second_slot:
        entities.append(
            {
                "id": "agent-002",
                "components": {
                    "character_slot": {
                        "label": "Travel companion",
                        "default_character_id": "jordan",
                    }
                },
            }
        )
    return ScenarioDefinition.model_validate(
        {
            "name": "vacation",
            "character_situation_synthesis": {"enabled": enabled},
            "entities": entities,
        }
    )


def _turn(
    *,
    summary: str = "Alex arrives dressed for a relaxed coastal stay.",
    relationship_target: str | None = None,
) -> ModelTurn:
    relationships = (
        [{"target_entity_id": relationship_target, "context": "Travelling together"}]
        if relationship_target is not None
        else []
    )
    return ModelTurn(
        text=None,
        tool_calls=(
            ModelToolCall(
                call_id="situation-1",
                name="instantiate_character_situation",
                arguments={
                    "schema_version": 1,
                    "summary": summary,
                    "role_context": "A guest beginning a vacation",
                    "presentation": {
                        "outfit": "Breathable overshirt, walking trousers, and trainers",
                        "grooming": "Neat but relaxed",
                        "accessories": ["sunglasses"],
                    },
                    "carried_personal_items": ["small canvas day bag"],
                    "recent_context": "Arrived after a morning train journey.",
                    "current_affect": "Attentive and mildly excited",
                    "disposition_manifestations": [
                        "Pauses to understand the unfamiliar check-in process"
                    ],
                    "relationship_context": relationships,
                    "assumptions": [
                        "The scenario does not specify a dress code"
                    ],
                },
            ),
        ),
        finish_reason="tool_calls",
        provider="scripted",
        model="situation-test",
        latency_ms=4.0,
        input_tokens=100,
        output_tokens=80,
    )


def test_model_synthesis_uses_deterministic_request_and_strict_output() -> None:
    async def exercise() -> None:
        client = ScriptedModelClient((_turn(),))
        synthesizer = ModelCharacterSituationSynthesizer(client)
        synthesis_input = CharacterSituationSynthesisInput(
            entity_id="agent-001",
            character_id="alex",
            character_profile=_character("alex", "Alex").model_dump(mode="json"),
            scenario_context={"scenario_name": "vacation"},
        )

        artifact = await synthesizer.synthesize(synthesis_input)

        assert artifact.entity_id == "agent-001"
        assert artifact.data.presentation.outfit.startswith("Breathable")
        assert artifact.generation.provider == "scripted"
        assert artifact.input_hash == canonical_hash(
            synthesis_input.model_dump(mode="json")
        )
        assert synthesis_input.character_profile["identity"]["birth_date"] == (
            "1990-02-14"
        )
        assert synthesis_input.character_profile["health"]["vision"] == (
            "Myopia corrected with glasses"
        )
        assert "fixed dossier facts" in SITUATION_SYSTEM_PROMPT
        assert len(artifact.content_hash) == 64
        assert "Current Presentation" in artifact.description

    asyncio.run(exercise())


def test_model_synthesis_rejects_unassigned_relationship_target() -> None:
    async def exercise() -> None:
        synthesizer = ModelCharacterSituationSynthesizer(
            ScriptedModelClient((_turn(relationship_target="agent-999"),))
        )
        synthesis_input = CharacterSituationSynthesisInput(
            entity_id="agent-001",
            character_id="alex",
            character_profile=_character("alex", "Alex").model_dump(mode="json"),
            scenario_context={"scenario_name": "vacation"},
        )
        with pytest.raises(
            CharacterSituationSynthesisError,
            match="unassigned relationship targets",
        ):
            await synthesizer.synthesize(synthesis_input)

    asyncio.run(exercise())


def test_bounded_projection_includes_starting_environment() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "environment",
            "calendar": {"start_datetime": "2026-08-31T09:00:00+08:00"},
            "weather": {
                "initial": {
                    "condition": "CLEAR",
                    "temperature_c": 24,
                }
            },
            "world": {
                "width": 3,
                "height": 3,
                "zones": [
                    {
                        "id": "lobby",
                        "name": "Guest lobby",
                        "type": "LOBBY",
                        "bounds": {"x": 0, "y": 0, "width": 2, "height": 2},
                    }
                ],
                "stations": [
                    {
                        "id": "desk",
                        "name": "Check-in desk",
                        "position": {"x": 1, "y": 1},
                        "supported_actions": ["WORK"],
                    }
                ],
            },
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 1, "y": 1},
                        "character_slot": {
                            "label": "Guest",
                            "default_character_id": "alex",
                        },
                    },
                }
            ],
        }
    )
    character = _character("alex", "Alex")

    synthesis_input = build_synthesis_input(
        scenario=scenario,
        entity_id="agent-001",
        character_id="alex",
        character=character,
        assignments={"agent-001": "alex"},
        characters={"alex": character},
    )

    environment = synthesis_input.scenario_context["initial_environment"]
    assert isinstance(environment, dict)
    assert environment["zones"][0]["name"] == "Guest lobby"
    assert environment["stations_at_position"][0]["id"] == "desk"


class _CountingSynthesizer:
    def __init__(self, fail_entity_id: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_entity_id = fail_entity_id

    async def synthesize(
        self,
        synthesis_input: CharacterSituationSynthesisInput,
    ) -> CharacterSituationArtifact:
        self.calls.append(synthesis_input.entity_id)
        if synthesis_input.entity_id == self.fail_entity_id:
            raise CharacterSituationSynthesisError("scripted failure")
        data = SynthesizedCharacterSituation(
            summary=f"Situation for {synthesis_input.entity_id}"
        )
        return CharacterSituationArtifact(
            entity_id=synthesis_input.entity_id,
            character_id=synthesis_input.character_id,
            profile_content_hash=canonical_hash(
                synthesis_input.character_profile
            ),
            input_hash=canonical_hash(synthesis_input.model_dump(mode="json")),
            content_hash=canonical_hash(data.model_dump(mode="json")),
            description=data.summary,
            data=data,
            generation=CharacterSituationGenerationMetadata(
                generated=True,
                prompt_version="test",
                provider="test",
                model="test",
            ),
        )


def test_synthesis_waits_for_complete_valid_assignment_map(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        library = FileSystemCharacterLibrary(tmp_path / "characters")
        library.create(_character("alex", "Alex"))
        synthesizer = _CountingSynthesizer()
        manager = SimulationManager(
            SQLiteDatasetStore(tmp_path / "runs.sqlite3"),
            character_library=library,
            situation_synthesizer=synthesizer,
        )
        incomplete = ScenarioDefinition.model_validate(
            {
                "name": "incomplete",
                "character_situation_synthesis": {"enabled": True},
                "entities": [
                    {
                        "id": "agent-001",
                        "components": {
                            "character_slot": {"label": "Assigned"},
                        },
                    },
                    {
                        "id": "agent-002",
                        "components": {
                            "character_slot": {
                                "label": "Missing",
                                "default_character_id": "missing",
                            },
                        },
                    },
                ],
            }
        )

        with pytest.raises(ValueError):
            await manager.add_scenario(
                incomplete,
                {"agent-001": "alex"},
            )
        assert synthesizer.calls == []
        await manager.close()

    asyncio.run(exercise())


def test_multi_character_synthesis_is_atomic(tmp_path: Path) -> None:
    async def exercise() -> None:
        library = FileSystemCharacterLibrary(tmp_path / "characters")
        library.create(_character("alex", "Alex"))
        library.create(_character("jordan", "Jordan"))
        synthesizer = _CountingSynthesizer(fail_entity_id="agent-002")
        manager = SimulationManager(
            SQLiteDatasetStore(tmp_path / "runs.sqlite3"),
            character_library=library,
            situation_synthesizer=synthesizer,
        )
        previous_id = await manager.add_scenario(_scenario(enabled=False))

        with pytest.raises(
            CharacterSituationSynthesisError,
            match="slot agent-002",
        ):
            await manager.add_scenario(
                _scenario(enabled=True, second_slot=True)
            )

        assert synthesizer.calls == ["agent-001", "agent-002"]
        previous = manager.get_scenario(previous_id)
        assert previous.situations["agent-001"].generation.generated is False
        await manager.close()

    asyncio.run(exercise())


def test_frozen_situation_is_used_by_runner_and_dataset(tmp_path: Path) -> None:
    async def exercise() -> None:
        library = FileSystemCharacterLibrary(tmp_path / "characters")
        library.create(_character("alex", "Alex"))
        manager = SimulationManager(
            SQLiteDatasetStore(tmp_path / "runs.sqlite3"),
            character_library=library,
            situation_synthesizer=_CountingSynthesizer(),
        )
        scenario_id = await manager.add_scenario(_scenario(enabled=True))
        prepared = manager.get_scenario(scenario_id)
        run_id = await manager.start_run(scenario_id, realtime=False)
        component = manager.get_run(run_id).runner.registry.get_component(
            "agent-001",
            CharacterSituationComponent,
        )
        manifest = prepared.dataset_payload()

        assert component.description == "Situation for agent-001"
        assert component.content_hash == prepared.situations["agent-001"].content_hash
        assert manifest["resolved_character_situations"]["agent-001"][
            "generation"
        ]["generated"] is True
        await manager.close()

    asyncio.run(exercise())
