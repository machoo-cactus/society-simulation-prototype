import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

pytestmark = pytest.mark.skipif(
    os.environ.get("STAGE0_RUN_PLAYWRIGHT") != "1",
    reason="set STAGE0_RUN_PLAYWRIGHT=1 to run browser UI tests",
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = Path(__file__).parents[1]
    temporary = tmp_path_factory.mktemp("playwright-ui")
    characters = temporary / "characters"
    shutil.copytree(root / "characters", characters)
    data = temporary / "runs"
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "STAGE0_CHARACTER_DIRECTORY": str(characters),
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
        cwd=root,
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
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.5) as response:
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


def test_operator_lifecycle_is_driven_by_accessible_controls(page: Page) -> None:
    page.goto("/ui/")
    expect(page.get_by_role("heading", name="Operator Console")).to_be_visible()
    expect(page.get_by_role("button", name="Start run")).to_be_disabled()
    expect(page.get_by_role("region", name="World")).to_be_visible()
    expect(page.get_by_role("complementary", name="Character inspector")).to_be_visible()

    page.get_by_role("button", name="Load example scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "validated and staged"
    )
    expect(page.get_by_role("img", name="Staged scenario preview")).to_be_visible()
    expect(page.get_by_role("button", name="Start run")).to_be_enabled()
    page.get_by_text("Character assignments", exact=True).click()
    page.get_by_role("combobox", name="agent-001").select_option("alex-chen")
    page.get_by_role("button", name="Validate assignments").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "assignments were validated"
    )

    page.get_by_role("button", name="Start run").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation started"
    )
    page.get_by_role("button", name="Pause").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation paused"
    )
    expect(page.get_by_role("button", name="Single step")).to_be_enabled()
    page.get_by_role("button", name="Resume").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation resumed"
    )
    page.get_by_role("button", name="Pause").click()
    page.get_by_role("combobox", name="Running speed").select_option("2")
    page.get_by_role("button", name="Set speed").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "speed set to 2x"
    )

    tick_value = page.locator("dt", has_text="Tick").locator("xpath=following-sibling::dd")
    tick_before = int(tick_value.inner_text())
    page.get_by_role("button", name="Single step").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Advanced one deterministic tick"
    )
    expect(tick_value).to_have_text(str(tick_before + 1))

    page.get_by_role("spinbutton", name="Satiety").fill("6")
    page.get_by_role("button", name="Apply supplied values").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Updated vitals for agent-001"
    )
    expect(page.get_by_text("homeostasis.mutated", exact=True)).to_be_visible()

    page.get_by_role("combobox", name="Category").select_option("system1")
    page.get_by_role("button", name="Apply filters").click()
    expect(page.get_by_text("system1.activated", exact=True)).to_be_visible()
    page.get_by_role("button", name="Clear view").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Cleared the browser event"
    )
    expect(page.get_by_text("No matching events.", exact=True)).to_be_visible()

    page.get_by_role("button", name="Stop").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation stopped"
    )
    expect(page.get_by_role("button", name="Start run")).to_be_enabled()


def test_scenario_upload_and_event_detail_use_native_browser_flows(
    page: Page,
) -> None:
    root = Path(__file__).parents[1]
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        root / "src" / "stage0_sim" / "web" / "demo.json"
    )
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "validated and staged"
    )
    expect(page.get_by_text("browser-survival-demo", exact=True)).to_be_visible()

    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()
    expect(page.get_by_role("link", name="Download run dataset")).to_be_visible()
    page.get_by_role("button", name="Zoom in").click()
    expect(page.get_by_role("status", name="Zoom level")).to_have_text("125%")
    page.get_by_role("link", name="Focus map").click()
    expect(page.get_by_role("link", name="Exit focus")).to_be_visible()
    page.get_by_text("simulation.started", exact=True).click()
    expect(page.get_by_role("heading", name="simulation.started")).to_be_visible()
    expect(page.get_by_role("button", name="Copy event JSON")).to_be_visible()
    page.get_by_role("button", name="Copy event JSON").click()
    expect(page.get_by_role("button", name="Copied")).to_be_visible()
    expect(page.get_by_role("link", name="Download JSON")).to_have_attribute(
        "download", re.compile(r"^run-.*\.json$")
    )
    page.get_by_role("link", name="Close detail").click()
    page.get_by_role("link", name="Expand log").click()
    expect(page.get_by_role("link", name="Exit expanded log")).to_be_visible()


def test_character_crud_round_trip_is_role_driven(
    page: Page, tmp_path: Path
) -> None:
    page.goto("/ui/characters/?selected=alex-chen")
    expect(page.get_by_role("heading", name="Character Library")).to_be_visible()
    expect(page.get_by_role("heading", name="Alex Chen")).to_be_visible()

    page.get_by_role("link", name="New character").click()
    page.get_by_label("Character ID").fill("playwright-created")
    page.get_by_label("Display name").fill("Created in Browser")
    page.get_by_role("button", name="Create character").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Created Created in Browser"
    )

    page.goto("/ui/characters/?selected=alex-chen")
    page.get_by_role("button", name="Duplicate").click()
    expect(page.get_by_role("heading", name="Alex Chen Copy")).to_be_visible()
    page.get_by_label("Character ID").fill("playwright-character")
    page.get_by_label("Display name").fill("Playwright Character")
    page.get_by_role("button", name="Save character").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved Playwright Character"
    )
    expect(page.get_by_role("heading", name="Playwright Character")).to_be_visible()
    expect(page.get_by_role("link", name="Download JSON")).to_be_visible()

    page.get_by_text("Delete playwright-character", exact=True).click()
    page.get_by_label(
        "I understand this permanently deletes the character file."
    ).check()
    page.get_by_role("button", name="Delete character").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Deleted playwright-character"
    )

    imported_path = tmp_path / "playwright-imported.json"
    imported_path.write_text(
        '{"schema_version":1,"id":"playwright-imported",'
        '"identity":{"display_name":"Imported in Browser"}}',
        encoding="utf-8",
    )
    page.get_by_text("Import character JSON", exact=True).click()
    page.get_by_label("Character JSON").set_input_files(imported_path)
    page.get_by_role("button", name="Import").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Imported Imported in Browser"
    )


def test_city_scenario_has_server_rendered_city_controls(page: Page) -> None:
    root = Path(__file__).parents[1]
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        root / "scenarios" / "sparse-city-car-demo.json"
    )
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.get_by_role("img", name=re.compile("Staged city preview"))).to_be_visible()
    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()
    page.get_by_role("combobox", name="Scale").select_option("city")
    page.get_by_role("button", name="Apply view").click()
    expect(page.get_by_role("img", name=re.compile(r"City ·"))).to_be_visible()


def test_pages_have_unique_ids_and_named_primary_landmarks(page: Page) -> None:
    for path in ("/ui/", "/ui/characters/"):
        page.goto(path)
        ids = page.locator("[id]").evaluate_all(
            "(elements) => elements.map((element) => element.id)"
        )
        assert len(ids) == len(set(ids))
        expect(page.get_by_role("main")).to_be_visible()
        expect(page.get_by_role("navigation", name="Application")).to_be_visible()
