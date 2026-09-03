from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = REPOSITORY_ROOT / "data"
CATALOG_SCENARIOS = CATALOG_ROOT / "scenarios"
CATALOG_CHARACTERS = CATALOG_ROOT / "characters"
CATALOG_ELEMENTS = CATALOG_ROOT / "elements"
FIXTURES_ROOT = REPOSITORY_ROOT / "tests" / "fixtures"
SCENARIO_FIXTURES = FIXTURES_ROOT / "scenarios"
PACKAGED_DEMO = REPOSITORY_ROOT / "src" / "stage0_sim" / "resources" / "demo.json"


def catalog_scenario_path(filename: str) -> Path:
    return CATALOG_SCENARIOS / filename


def scenario_fixture_path(filename: str) -> Path:
    return SCENARIO_FIXTURES / filename
