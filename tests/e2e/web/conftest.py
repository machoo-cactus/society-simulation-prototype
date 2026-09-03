import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from tests.helpers.paths import (
    CATALOG_CHARACTERS,
    CATALOG_ELEMENTS,
    CATALOG_SCENARIOS,
    REPOSITORY_ROOT,
)

_UI_SCENARIO_DIRECTORY: Path | None = None
_UI_ELEMENT_DIRECTORY: Path | None = None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    global _UI_ELEMENT_DIRECTORY, _UI_SCENARIO_DIRECTORY
    temporary = tmp_path_factory.mktemp("playwright-ui")
    characters = temporary / "characters"
    shutil.copytree(CATALOG_CHARACTERS, characters)
    scenarios = temporary / "scenarios"
    scenarios.mkdir()
    for name in (
        "baseline.json",
        "grid-navigation.json",
        "neighborhood-errand.json",
        "community-meetup.json",
        "open-city-day.json",
    ):
        payload = json.loads(
            (CATALOG_SCENARIOS / name).read_text(encoding="utf-8")
        )
        (scenarios / name).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    elements = temporary / "elements"
    shutil.copytree(CATALOG_ELEMENTS, elements)
    _UI_SCENARIO_DIRECTORY = scenarios
    _UI_ELEMENT_DIRECTORY = elements
    data = temporary / "runs"
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "STAGE0_CHARACTER_DIRECTORY": str(characters),
            "STAGE0_SCENARIO_DIRECTORY": str(scenarios),
            "STAGE0_ELEMENT_DIRECTORY": str(elements),
            "STAGE0_DATA_DIRECTORY": str(data),
        }
    )
    environment.pop("STAGE0_LLM_PROVIDER", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "stage0_sim.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            pytest.fail(f"UI server exited during startup:\n{output}")
        try:
            with urllib.request.urlopen(
                f"{base_url}/health",
                timeout=0.5,
            ) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        process.terminate()
        pytest.fail("UI server did not become healthy within 20 seconds")
    yield base_url
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    _UI_SCENARIO_DIRECTORY = None
    _UI_ELEMENT_DIRECTORY = None


@pytest.fixture
def ui_library_paths(ui_server: str) -> tuple[Path, Path]:
    assert _UI_SCENARIO_DIRECTORY is not None
    assert _UI_ELEMENT_DIRECTORY is not None
    return _UI_SCENARIO_DIRECTORY, _UI_ELEMENT_DIRECTORY


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser, ui_server: str) -> Iterator[Page]:
    context = browser.new_context(
        base_url=ui_server,
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()
    browser_errors: list[str] = []
    page.on("pageerror", lambda error: browser_errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            browser_errors.append(message.text)
            if message.type == "error"
            else None
        ),
    )
    yield page
    context.close()
    assert browser_errors == []
