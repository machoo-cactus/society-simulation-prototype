from pathlib import Path
from typing import Literal

TestTier = Literal[
    "quick",
    "source_regression",
    "startup_contract",
    "browser",
]

_QUICK_INTEGRATION_MODULES = frozenset(
    {
        Path("integration/api/test_health.py"),
        Path("integration/catalogs/test_documentation.py"),
        Path("integration/cli/test_packaging_metadata.py"),
    }
)
_SOURCE_REGRESSION_UNIT_MODULES = frozenset(
    {
        Path("unit/application/test_completion_work.py"),
    }
)


def classify_test_tier(relative_path: Path) -> TestTier:
    normalized = Path(relative_path.as_posix())
    if normalized in _SOURCE_REGRESSION_UNIT_MODULES:
        return "source_regression"
    if normalized.parts[:1] == ("unit",):
        return "quick"
    if normalized in _QUICK_INTEGRATION_MODULES:
        return "quick"
    if normalized.parts[:1] == ("integration",):
        return "source_regression"
    if normalized.parts[:1] == ("startup",):
        return "startup_contract"
    if normalized.parts[:2] == ("e2e", "web"):
        return "browser"
    raise ValueError(f"test module has no CI tier: {relative_path}")
