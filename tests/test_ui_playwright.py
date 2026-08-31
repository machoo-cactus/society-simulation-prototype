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
    scenarios = temporary / "scenarios"
    shutil.copytree(root / "scenarios", scenarios)
    data = temporary / "runs"
    port = _free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "STAGE0_CHARACTER_DIRECTORY": str(characters),
            "STAGE0_SCENARIO_DIRECTORY": str(scenarios),
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
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1
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
    expect(page.locator("details", has_text="Character assignments")).to_have_attribute(
        "open", ""
    )
    expect(page.get_by_role("button", name="Validate assignments")).to_be_focused()

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
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation paused"
    )
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

    page.get_by_role("combobox", name="Character").select_option("agent-001")
    page.get_by_role("button", name="Inspect").click()
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
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


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
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


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


def test_scenario_editor_keeps_save_and_stage_separate(page: Page) -> None:
    page.goto("/ui/scenarios/?new=1")
    expect(page.get_by_role("heading", name="Scenario Library")).to_be_visible()
    page.get_by_label("Scenario resource ID").fill("playwright-scenario")
    definition = page.get_by_role("group", name="Scenario Definition")
    scenario_name = definition.get_by_label("Name", exact=True).first
    scenario_name.fill("Playwright Scenario")
    definition.get_by_label("Dt", exact=True).fill("0")
    page.get_by_role("button", name="Create scenario").click()
    expect(page.get_by_role("alert", name="Scenario validation failed")).to_contain_text(
        "greater than 0"
    )
    expect(definition.get_by_label("Dt", exact=True)).to_have_value("0")

    definition.get_by_label("Dt", exact=True).fill("1")
    page.get_by_role("button", name="Create scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved Playwright Scenario"
    )

    definition = page.get_by_role("group", name="Scenario Definition")
    definition.get_by_label("Name", exact=True).first.fill(
        "Unsaved Playwright Stage"
    )
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.get_by_role("heading", name="Operator Console")).to_be_visible()
    expect(page.get_by_text("Unsaved Playwright Stage", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Start run")).to_be_enabled()

    page.goto("/ui/scenarios/?selected=playwright-scenario")
    definition = page.get_by_role("group", name="Scenario Definition")
    expect(definition.get_by_label("Name", exact=True).first).to_have_value(
        "Playwright Scenario"
    )
    page.get_by_text("Delete playwright-scenario", exact=True).click()
    page.get_by_label(
        "I understand this permanently deletes the scenario file."
    ).check()
    page.get_by_role("button", name="Delete scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Deleted playwright-scenario"
    )


def test_large_city_scenario_submits_the_native_structured_form(page: Page) -> None:
    page.goto("/ui/scenarios/?selected=greyford-office-evening")
    expect(page.get_by_role("heading", name="greyford-office-evening")).to_be_visible()
    page.get_by_role("button", name="Save scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved greyford-office-evening"
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
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation paused"
    )
    page.get_by_text("Advanced scale override", exact=True).click()
    page.get_by_role("combobox", name="Scale").select_option("city")
    page.get_by_role("button", name="Apply view").click()
    expect(page.get_by_role("img", name=re.compile(r"City ·"))).to_be_visible()


def test_city_map_supports_unfocused_inspection_follow_and_semantic_zoom(
    page: Page,
) -> None:
    root = Path(__file__).parents[1]
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        root / "scenarios" / "sparse-city-car-demo.json"
    )
    page.get_by_role("button", name="Validate and stage").click()
    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()

    expect(
        page.get_by_text(
            "Not inspecting a character. The world map remains free to pan and zoom.",
            exact=True,
        )
    ).to_be_visible()
    marker = page.get_by_role("link", name=re.compile(r"^Inspect "))
    expect(marker).to_be_visible()

    viewport = page.locator("#world-render")
    camera_before = page.locator("[data-map-zoom]").get_attribute("data-camera-x")
    marker.click()
    expect(page.get_by_role("combobox", name="Character")).not_to_have_value("")
    expect(page.get_by_label("Follow inspected character")).not_to_be_checked()
    expect(page.locator("[data-map-zoom]")).to_have_attribute(
        "data-camera-x", camera_before or "0.5"
    )

    page.get_by_label("Follow inspected character").check()
    page.get_by_role("button", name="Apply view").click()
    expect(page.get_by_label("Follow inspected character")).to_be_checked()

    page.get_by_role("combobox", name="Character").select_option("")
    page.get_by_role("button", name="Inspect").click()
    expect(page.get_by_role("combobox", name="Character")).to_have_value("")
    expect(page.get_by_label("Follow inspected character")).not_to_be_checked()

    page.get_by_text("Advanced scale override", exact=True).click()
    page.get_by_role("combobox", name="Scale").select_option("auto")
    page.get_by_role("button", name="Apply view").click()
    viewport.hover(position={"x": 300, "y": 220})
    page.mouse.wheel(0, -500)
    expect(page.get_by_text("Detail: Neighborhood", exact=True)).to_be_visible()
    page.mouse.wheel(0, -500)
    expect(page.get_by_text("Detail: Building", exact=True)).to_be_visible()
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


def test_dense_city_labels_do_not_overlap_at_high_zoom(page: Page) -> None:
    root = Path(__file__).parents[1]
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        root / "scenarios" / "greyford-office-evening.json"
    )
    page.get_by_role("button", name="Validate and stage").click()
    page.get_by_text("Advanced scale override", exact=True).click()
    page.get_by_role("combobox", name="Scale").select_option("city")
    page.get_by_role("button", name="Apply view").click()

    viewport = page.locator("#world-render")
    viewport.hover(position={"x": 300, "y": 220})
    page.mouse.wheel(0, -1000)
    expect(page.get_by_role("status", name="Zoom level")).to_have_text("300%")
    labels = page.locator(".city-label")
    expect(labels.first).to_be_visible()
    boxes = labels.evaluate_all(
        """elements => elements.map((element) => {
            const box = element.getBBox();
            return {left: box.x, top: box.y, right: box.x + box.width,
                    bottom: box.y + box.height};
        })"""
    )
    for index, box in enumerate(boxes):
        for other in boxes[index + 1 :]:
            assert (
                box["right"] + 2 <= other["left"]
                or other["right"] + 2 <= box["left"]
                or box["bottom"] + 2 <= other["top"]
                or other["bottom"] + 2 <= box["top"]
            )


def test_live_updates_do_not_reload_or_discard_active_input(page: Page) -> None:
    page.goto("/ui/")
    page.get_by_role("button", name="Load example scenario").click()
    page.get_by_role("button", name="Start run").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation started"
    )

    tick_value = page.locator("dt", has_text="Tick").locator(
        "xpath=following-sibling::dd"
    )
    tick_before = int(tick_value.inner_text())
    page.wait_for_function(
        """before => {
            const term = [...document.querySelectorAll("dt")]
                .find((element) => element.textContent === "Tick");
            return term && Number(term.nextElementSibling.textContent) > before;
        }""",
        arg=tick_before,
        timeout=5000,
    )
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1

    viewport = page.locator("#world-render")
    viewport.hover(position={"x": 300, "y": 220})
    page.mouse.wheel(0, -1000)
    expect(page.get_by_role("status", name="Zoom level")).to_have_text("300%")
    page.wait_for_timeout(300)
    viewport.evaluate("element => { element.scrollLeft = 120; }")
    tick_before_scroll_check = int(tick_value.inner_text())
    page.wait_for_function(
        """before => {
            const term = [...document.querySelectorAll("dt")]
                .find((element) => element.textContent === "Tick");
            return term && Number(term.nextElementSibling.textContent) > before;
        }""",
        arg=tick_before_scroll_check,
        timeout=5000,
    )
    assert abs(viewport.evaluate("element => element.scrollLeft") - 120) <= 2

    search = page.get_by_role("searchbox", name="Search")
    search.fill("unfinished filter")
    search.focus()
    page.wait_for_timeout(1300)
    expect(search).to_have_value("unfinished filter")
    expect(search).to_be_focused()

    page.get_by_role("button", name="Pause").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation paused"
    )


def test_map_supports_wheel_zoom_and_drag_panning_without_navigation(
    page: Page,
) -> None:
    page.goto("/ui/")
    page.get_by_role("button", name="Load example scenario").click()
    viewport = page.locator("#world-render")
    viewport.hover(position={"x": 300, "y": 220})
    page.mouse.wheel(0, -1000)
    expect(page.get_by_role("status", name="Zoom level")).to_have_text("300%")
    page.wait_for_timeout(300)

    page.get_by_role("link", name="Focus map").click()
    expect(page.get_by_role("status", name="Zoom level")).to_have_text("300%")
    expect(page.get_by_role("link", name="Exit focus")).to_be_visible()

    metrics = viewport.evaluate(
        """element => ({
            maximum: element.scrollWidth - element.clientWidth,
            width: element.clientWidth,
            height: element.clientHeight,
        })"""
    )
    assert metrics["maximum"] > 150
    viewport.evaluate("element => { element.scrollLeft = 100; }")
    before = viewport.evaluate("element => element.scrollLeft")
    bounds = viewport.bounding_box()
    assert bounds is not None
    start_x = bounds["x"] + min(400, bounds["width"] * 0.65)
    start_y = bounds["y"] + min(260, bounds["height"] * 0.5)
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x - 100, start_y, steps=8)
    page.mouse.up()
    after = viewport.evaluate("element => element.scrollLeft")
    assert after > before + 50
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


def test_server_rendered_controls_remain_usable_without_javascript(
    browser: Browser,
    ui_server: str,
) -> None:
    context = browser.new_context(base_url=ui_server, java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto("/ui/")
        page.get_by_role("button", name="Load example scenario").click()
        expect(page.get_by_role("img", name="Staged scenario preview")).to_be_visible()
        page.get_by_role("button", name="Zoom in").click()
        expect(page.get_by_role("status", name="Zoom level")).to_have_text("125%")
        page.goto("/ui/scenarios/?new=1")
        page.get_by_label("Scenario resource ID").fill("nojs-scenario")
        definition = page.get_by_role("group", name="Scenario Definition")
        definition.get_by_label("Name", exact=True).first.fill(
            "No JavaScript Scenario"
        )
        definition.get_by_label("World type").select_option("grid")
        page.get_by_role("button", name="Apply field choices").click()

        grid = page.get_by_role("group", name="Grid world")
        grid.get_by_label("Width", exact=True).fill("3")
        grid.get_by_label("Height", exact=True).fill("2")
        grid.get_by_role("button", name="Add Blocked").click()
        blocked = page.get_by_role("region", name="Blocked item 1")
        blocked.get_by_label("X", exact=True).fill("1")
        blocked.get_by_label("Y", exact=True).fill("0")
        page.get_by_role("button", name="Create scenario").click()
        expect(page.get_by_role("heading", name="nojs-scenario")).to_be_visible()

        definition = page.get_by_role("group", name="Scenario Definition")
        definition.get_by_label("Name", exact=True).first.fill(
            "No JavaScript Staged Draft"
        )
        page.get_by_role("button", name="Validate and stage").click()
        expect(page.get_by_role("heading", name="Operator Console")).to_be_visible()
        expect(
            page.get_by_text("No JavaScript Staged Draft", exact=True)
        ).to_be_visible()
        expect(page.get_by_role("button", name="Start run")).to_be_enabled()
    finally:
        context.close()


def test_pages_have_unique_ids_and_named_primary_landmarks(page: Page) -> None:
    for path in ("/ui/", "/ui/characters/", "/ui/scenarios/?new=1"):
        page.goto(path)
        ids = page.locator("[id]").evaluate_all(
            "(elements) => elements.map((element) => element.id)"
        )
        assert len(ids) == len(set(ids))
        expect(page.get_by_role("main")).to_be_visible()
        expect(page.get_by_role("navigation", name="Application")).to_be_visible()
