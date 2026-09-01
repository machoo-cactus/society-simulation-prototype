from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = REPOSITORY_ROOT / "examples"
EXAMPLE_SCENARIOS = EXAMPLES_ROOT / "scenarios"
EXAMPLE_CHARACTERS = EXAMPLES_ROOT / "characters"
EXAMPLE_ELEMENTS = EXAMPLES_ROOT / "elements"
FIXTURES_ROOT = REPOSITORY_ROOT / "tests" / "fixtures"
SCENARIO_FIXTURES = FIXTURES_ROOT / "scenarios"
PACKAGED_DEMO = REPOSITORY_ROOT / "src" / "stage0_sim" / "resources" / "demo.json"


def example_scenario_path(filename: str) -> Path:
    return EXAMPLE_SCENARIOS / filename


def scenario_fixture_path(filename: str) -> Path:
    return SCENARIO_FIXTURES / filename
