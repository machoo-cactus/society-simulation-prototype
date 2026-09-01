import ast
from pathlib import Path

from tests.helpers.paths import REPOSITORY_ROOT

PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "stage0_sim"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    return tuple(imports)


def _forbidden_imports(
    package: str,
    forbidden_prefixes: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / package).rglob("*.py")):
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                relative = path.relative_to(PACKAGE_ROOT)
                violations.append(f"{relative}: {imported}")
    return violations


def test_domain_does_not_depend_on_outer_layers() -> None:
    assert _forbidden_imports(
        "domain",
        (
            "stage0_sim.application",
            "stage0_sim.adapters",
            "stage0_sim.api",
        ),
    ) == []


def test_application_does_not_depend_on_adapters_or_api() -> None:
    assert _forbidden_imports(
        "application",
        (
            "stage0_sim.adapters",
            "stage0_sim.api",
        ),
    ) == []


def test_cross_system_transitions_do_not_call_private_helpers() -> None:
    travel = (
        PACKAGE_ROOT / "domain" / "systems" / "travel.py"
    ).read_text(encoding="utf-8")
    affordances = (
        PACKAGE_ROOT / "domain" / "systems" / "affordances.py"
    ).read_text(encoding="utf-8")

    assert "PlanExecutionSystem()._" not in travel
    assert "System1ArbitrationSystem._" not in affordances
    assert "System1ArbitrationSystem()._" not in affordances
