import tomllib

from stage0_sim import __version__
from tests.helpers.paths import REPOSITORY_ROOT


def test_package_version_has_one_authoritative_source() -> None:
    metadata = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "version" not in metadata["project"]
    assert "version" in metadata["project"]["dynamic"]
    assert metadata["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "stage0_sim.__version__"
    }
    assert __version__ == "0.3.0"


def test_package_metadata_declares_supported_python_and_readme() -> None:
    metadata = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = metadata["project"]

    assert project["requires-python"] == ">=3.12"
    assert project["readme"] == "README.md"
    assert "Programming Language :: Python :: 3.14" in project["classifiers"]


def test_installed_smoke_uses_installed_version_as_authority() -> None:
    smoke = (
        REPOSITORY_ROOT / "tools" / "installed_smoke.py"
    ).read_text(encoding="utf-8")

    assert "assert __version__ ==" not in smoke
    assert '"version": __version__' in smoke
