from pathlib import Path

import pytest

from tests.tier_policy import classify_test_tier

TEST_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = TEST_ROOT.parent


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("unit/domain/test_navigation.py", "quick"),
        (
            "unit/application/test_completion_work.py",
            "source_regression",
        ),
        ("integration/api/test_health.py", "quick"),
        (
            "integration/catalogs/test_documentation.py",
            "quick",
        ),
        (
            "integration/cli/test_packaging_metadata.py",
            "quick",
        ),
        (
            "integration/simulation/test_tool_agents.py",
            "source_regression",
        ),
        (
            "startup/test_application_startup.py",
            "startup_contract",
        ),
        ("e2e/web/test_operator_runtime.py", "browser"),
    ],
)
def test_tier_assigns_expected_modules(
    relative_path: str,
    expected: str,
) -> None:
    assert classify_test_tier(Path(relative_path)) == expected


def test_every_test_module_has_exactly_one_base_tier() -> None:
    modules = sorted(TEST_ROOT.rglob("test_*.py"))
    assert modules
    tiers = {
        module.relative_to(TEST_ROOT): classify_test_tier(
            module.relative_to(TEST_ROOT)
        )
        for module in modules
    }
    assert set(tiers.values()) == {
        "quick",
        "source_regression",
        "startup_contract",
        "browser",
    }


def test_unknown_test_location_is_rejected() -> None:
    with pytest.raises(ValueError, match="has no CI tier"):
        classify_test_tier(Path("misc/test_unowned.py"))


def test_ci_exposes_every_tier_and_full_aggregate() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    for job in (
        "static",
        "quick",
        "source-regression",
        "startup",
        "browser",
        "package",
        "windows",
        "compatibility",
        "installed-wheel",
        "full-validation",
    ):
        assert f"\n  {job}:" in workflow
    for command in (
        "python -m pytest -m quick",
        "python -m pytest -m source_regression",
        "python -m pytest -m startup_contract",
        "python -m pytest -m browser",
    ):
        assert command in workflow

    aggregate = workflow.split("\n  full-validation:", maxsplit=1)[1]
    for dependency in (
        "static",
        "quick",
        "source-regression",
        "startup",
        "browser",
        "package",
        "windows",
        "compatibility",
        "installed-wheel",
    ):
        assert f"      - {dependency}\n" in aggregate
