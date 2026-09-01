from importlib.resources import files

from stage0_sim.application.characters import (
    CharacterDefinition,
    CharacterLibrary,
    CharacterNotFoundError,
)
from stage0_sim.application.elements import ScenarioSourceDefinition

PACKAGE_RESOURCES = files("stage0_sim").joinpath("resources")
BUNDLED_DEMO = PACKAGE_RESOURCES.joinpath("demo.json")
BUNDLED_DEMO_CHARACTER = PACKAGE_RESOURCES.joinpath("demo-character.json")


def bundled_demo_source() -> ScenarioSourceDefinition:
    return ScenarioSourceDefinition.model_validate_json(
        BUNDLED_DEMO.read_text(encoding="utf-8")
    )


def ensure_bundled_demo_character(
    library: CharacterLibrary,
) -> CharacterDefinition:
    character = CharacterDefinition.model_validate_json(
        BUNDLED_DEMO_CHARACTER.read_text(encoding="utf-8")
    )
    try:
        return library.get(character.id)
    except CharacterNotFoundError:
        return library.create(character)
