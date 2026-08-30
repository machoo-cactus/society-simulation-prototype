from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
)
from stage0_sim.application.agents.prompts import (
    GENERAL_CHARACTER_CONTROLLER_PROMPT,
    build_messages,
)
from stage0_sim.application.information_context import InformationContextCapsule
from stage0_sim.application.scenario import ScenarioDefinition, create_runner
from stage0_sim.domain.components import CharacterProfileComponent
from stage0_sim.domain.information import InformationSource, TimeRange


def test_profile_catalog_resolves_override_and_custom_section() -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "profiles",
            "character_profiles": {
                "alex": {
                    "identity": {
                        "display_name": "Alex Chen",
                        "age": 34,
                        "occupation": "Research engineer",
                    },
                    "personality": {
                        "traits": ["methodical"],
                        "speech_style": "Brief and precise",
                    },
                    "motivations": {"goals": ["Finish the report"]},
                    "custom_sections": [
                        {
                            "id": "experiment",
                            "title": "Experiment",
                            "fields": [
                                {
                                    "key": "risk_tolerance",
                                    "label": "Risk tolerance",
                                    "value": "low",
                                }
                            ],
                        }
                    ],
                }
            },
            "world": {"width": 1, "height": 1},
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {},
                        "character_profile": {
                            "profile_ref": "alex",
                            "personality": {
                                "speech_style": "Warm but concise"
                            },
                        },
                    },
                }
            ],
        }
    )

    runner = create_runner(scenario)
    profile = runner.registry.get_component(
        "agent-001", CharacterProfileComponent
    )

    assert profile.profile_id == "alex"
    assert profile.display_name == "Alex Chen"
    assert profile.goals == ("Finish the report",)
    assert "| Age | 34 |" in profile.description
    assert "| Speech Style | Warm but concise |" in profile.description
    assert "| Risk tolerance | low |" in profile.description
    assert len(profile.content_hash) == 64


def test_prompt_separates_general_character_and_dynamic_context() -> None:
    observation = CharacterObservation(
        agent_id="agent-001",
        display_name="Alex Chen",
        goals=("Finish the report",),
        simulation_time=12,
        location_id="office",
        activity="WORKING",
        satiety=80,
        energy=70,
        stress=20,
        targets=(),
        facts=(),
        recent_outcome=None,
    )
    request = CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run-1",
        agent_id="agent-001",
        requested_tick=12,
        state_revision=0,
        trigger="idle",
        character_description="# Character Profile\n\n## Identity\nAlex Chen",
        profile_id="alex",
        profile_template_version=1,
        profile_content_hash="profile-hash",
        observation=observation,
        memories=(),
        allowed_tools=("wait",),
    )

    messages = build_messages(request)

    assert len(messages) == 3
    assert messages[0].role == "system"
    assert messages[0].content == GENERAL_CHARACTER_CONTROLLER_PROMPT
    assert "Character description" in messages[1].content
    assert "Alex Chen" in messages[1].content
    assert '"simulation_time":12' in messages[2].content
    assert "Character description" not in messages[2].content


def test_prompt_uses_bounded_capsules_without_dumping_full_profile() -> None:
    observation = CharacterObservation(
        agent_id="agent-001",
        display_name="Alex Chen",
        goals=("Drive to work",),
        simulation_time=12,
        location_id="home",
        activity="IDLE",
        satiety=80,
        energy=70,
        stress=20,
        targets=(),
        facts=(),
        recent_outcome=None,
    )
    request = CharacterDecisionRequest(
        decision_id="decision-1",
        run_id="run-1",
        agent_id="agent-001",
        requested_tick=12,
        state_revision=0,
        trigger="idle",
        character_description=(
            "# Character Profile\n\nFULL DOSSIER SECRET THAT MUST NOT APPEAR"
        ),
        profile_id="alex",
        profile_template_version=1,
        profile_content_hash="profile-hash",
        observation=observation,
        memories=("A remembered commute.",),
        allowed_tools=("wait",),
        retrieved_information=(
            InformationContextCapsule(
                document_id="character-dossier:agent-001",
                document_kind="character.dossier",
                source_path="$.capabilities.driving",
                rendered_content=(
                    '{"experience":"moderate","licences":["car"]}'
                ),
                source=InformationSource(
                    type="SCENARIO_PROFILE",
                    reference_ids=("alex",),
                    metadata={"template_id": "human-v1"},
                ),
                valid_time=TimeRange(start=0),
                score=0.75,
                revision=2,
                recorded_at=0,
            ),
        ),
        information_retrieval_performed=True,
        information_query="Drive to work",
    )

    messages = build_messages(request)

    assert "Retrieved information context" in messages[1].content
    assert "profile=alex" in messages[1].content
    assert "content_hash=profile-hash" in messages[1].content
    assert "Document ID: character-dossier:agent-001" in messages[1].content
    assert "Source path: $.capabilities.driving" in messages[1].content
    assert "Provenance:" in messages[1].content
    assert "Timing:" in messages[1].content
    assert '"experience":"moderate"' in messages[1].content
    assert "FULL DOSSIER SECRET" not in "\n".join(
        message.content for message in messages
    )
