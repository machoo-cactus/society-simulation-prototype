import shutil
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest

from stage0_sim.config import Settings
from tests.helpers.paths import CATALOG_CHARACTERS, CATALOG_ELEMENTS


@pytest.fixture(autouse=True)
def isolated_api_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    character_directory = tmp_path / "_runtime" / "characters"
    shutil.copytree(CATALOG_CHARACTERS, character_directory)
    settings = Settings(
        data_directory=tmp_path / "runs",
        character_directory=character_directory,
        scenario_directory=tmp_path / "scenarios",
        element_directory=CATALOG_ELEMENTS,
    )
    app_module = import_module("stage0_sim.api.app")
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    yield
