from stage0_sim.application.agents.contracts import (
    CharacterDecisionRequest,
    CharacterObservation,
)
from stage0_sim.application.agents.prompts import (
    GENERAL_CHARACTER_CONTROLLER_PROMPT,
    build_messages,
)
from stage0_sim.application.information_context import InformationContextCapsule
from stage0_sim.application.scenario import (
    CharacterProfileDefinition,
    ResolvedCharacterProfile,
    ScenarioDefinition,
    create_runner,
)
from stage0_sim.domain.components import (
    CharacterProfileComponent,
    CharacterSituationComponent,
)
from stage0_sim.domain.information import InformationSource, TimeRange


def test_character_and_scenario_situation_are_built_separately() -> None:
    character = CharacterProfileDefinition.model_validate(
        {
            "identity": {
                "display_name": "Alex Chen",
                "age": 34,
                "occupation": "Research engineer",
            },
            "personality": {
                "traits": ["methodical"],
                "speech_style": "Warm but concise",
            },
            "motivations": {"values": ["accuracy"]},
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
    )
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "profiles",
            "world": {"width": 1, "height": 1},
            "entities": [
                {
                    "id": "agent-001",
                    "components": {
                        "position": {"x": 0, "y": 0},
                        "homeostasis": {},
                        "character_slot": {
                            "label": "Lead analyst",
                            "briefing": "Finish the report before leaving.",
                        },
                        "planner": {"daily_goals": ["Finish the report"]},
                    },
                }
            ],
        }
    )

    runner = create_runner(
        scenario,
        resolved_characters={
            "agent-001": ResolvedCharacterProfile(
                character_id="alex",
                profile=character,
            )
        },
    )
    profile = runner.registry.get_component(
        "agent-001", CharacterProfileComponent
    )
    situation = runner.registry.get_component(
        "agent-001", CharacterSituationComponent
    )

    assert profile.profile_id == "alex"
    assert profile.display_name == "Alex Chen"
    assert situation.label == "Lead analyst"
    assert situation.briefing == "Finish the report before leaving."
    assert "| Age | 34 |" in profile.description
    assert "| Speech Style | Warm but concise |" in profile.description
    assert "| Risk tolerance | low |" in profile.description
    assert len(profile.content_hash) == 64


def test_prompt_separates_general_character_and_dynamic_context() -> None:
    observation = CharacterObservation(
        agent_id="agent-001",
        display_name="Alex Chen",
        goals=("Finish the report",),
        current_priorities=("Check the evidence",),
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
        situation_description="Finish the report before leaving.",
        profile_id="alex",
        profile_template_version=1,
        profile_content_hash="profile-hash",
        observation=observation,
        memories=(),
        allowed_tools=("wait",),
    )

    messages = build_messages(request)

    assert len(messages) == 4
    assert messages[0].role == "system"
    assert messages[0].content == GENERAL_CHARACTER_CONTROLLER_PROMPT
    assert "Character description" in messages[1].content
    assert "Alex Chen" in messages[1].content
    assert "temporary context" in messages[2].content
    assert "Finish the report before leaving." in messages[2].content
    assert '"simulation_time":12' in messages[3].content
    assert "Character description" not in messages[3].content


def test_prompt_uses_bounded_capsules_without_dumping_full_profile() -> None:
    observation = CharacterObservation(
        agent_id="agent-001",
        display_name="Alex Chen",
        goals=("Drive to work",),
        current_priorities=(),
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
        situation_description="Reach the office for the morning shift.",
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
