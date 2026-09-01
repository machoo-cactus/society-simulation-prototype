import json
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.helpers.paths import (
    EXAMPLE_SCENARIOS,
    REPOSITORY_ROOT,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("STAGE0_RUN_PLAYWRIGHT") != "1",
    reason="set STAGE0_RUN_PLAYWRIGHT=1 to run browser UI tests",
)


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
    assignment_details = page.get_by_text(
        "Character assignments",
        exact=True,
    ).locator("..")
    expect(assignment_details).to_have_attribute("open", "")
    expect(page.get_by_role("button", name="Validate assignments")).to_be_focused()
    expect(
        page.get_by_role("button", name="Regenerate character situations")
    ).to_be_enabled()
    page.get_by_role("button", name="Regenerate character situations").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Character situations were regenerated"
    )

    page.get_by_role("button", name="Start run").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Simulation started"
    )
    expect(
        page.get_by_role("button", name="Regenerate character situations")
    ).to_be_disabled()
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
    root = REPOSITORY_ROOT
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        root / "src" / "stage0_sim" / "resources" / "demo.json"
    )
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "validated and staged"
    )
    expect(page.get_by_text("browser-survival-demo", exact=True)).to_be_visible()

    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()
    expect(
        page.get_by_role("link", name="Download complete run dataset")
    ).to_be_visible()
    page.get_by_role("button", name="Zoom in").click()
    expect(page.get_by_role("status", name="Zoom level")).to_have_text("125%")
    page.get_by_role("link", name="Focus map").click()
    expect(page.get_by_role("link", name="Exit focus")).to_be_visible()
    page.get_by_role("link", name="Exit focus").click()
    expect(page.get_by_role("link", name="Focus map")).to_be_visible()
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


def test_dataset_explorer_uses_accessible_server_rendered_queries(
    page: Page,
) -> None:
    page.goto("/ui/")
    page.get_by_role("button", name="Load example scenario").click()
    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()
    page.get_by_role("button", name="Single step").click()
    page.get_by_role("button", name="Stop").click()
    page.get_by_role("link", name="Explore research dataset").click()

    expect(
        page.get_by_role("heading", name="Research Dataset Explorer")
    ).to_be_visible()
    expect(
        page.get_by_role(
            "heading",
            name="Run summary and capture completeness",
        )
    ).to_be_visible()
    expect(page.get_by_text("Capture complete", exact=True)).to_be_visible()
    expect(page.get_by_label("Include private research data")).not_to_be_checked()
    expect(
        page.get_by_text(
            re.compile(r"Warning: enabling this control can display prompts"),
        )
    ).to_be_visible()
    assert (
        page.locator('[data-record-visibility="PRIVATE_RESEARCH"]').count()
        == 0
    )

    navigation_count = page.evaluate(
        "performance.getEntriesByType('navigation').length"
    )
    page.get_by_label("Dataset view", exact=True).select_option("records")
    page.get_by_label("Records per page").fill("1")
    page.get_by_label("Primary entity ID").fill("agent-001")
    page.get_by_text(
        "Advanced time, schema, and lineage filters",
        exact=True,
    ).click()
    page.get_by_role("button", name="Apply dataset filters").click()
    expect(page.get_by_role("heading", name="Raw records")).to_be_visible()
    expect(page.get_by_text("1 ordered result on this page.")).to_be_visible()
    expect(page.get_by_label("Primary entity ID")).to_have_value("agent-001")
    expect(
        page.get_by_text(
            "Advanced time, schema, and lineage filters",
            exact=True,
        ).locator("..")
    ).to_have_attribute("open", "")
    expect(
        page.get_by_role("button", name="Apply dataset filters")
    ).to_be_focused()
    assert page.evaluate(
        "performance.getEntriesByType('navigation').length"
    ) == navigation_count

    record_summary = page.locator(".dataset-records summary").first
    record_summary.click()
    expect(
        page.locator(".dataset-records details").first.locator("pre")
    ).to_contain_text('"record_id"')
    expect(page.get_by_role("link", name="Next page")).to_be_visible()
    page.get_by_role("link", name="Next page").click()
    expect(page.get_by_label("Primary entity ID")).to_have_value("agent-001")
    expect(page.get_by_label("Records per page")).to_have_value("1")
    expect(page.get_by_role("link", name="First page")).to_be_visible()

    page.get_by_label("Visibility", exact=True).select_option(
        "PRIVATE_RESEARCH"
    )
    page.get_by_label("Include private research data").check()
    page.get_by_role("button", name="Apply dataset filters").click()
    expect(
        page.get_by_role("alert").filter(
            has_text="Private research data is displayed"
        )
    ).to_be_visible()
    expect(page.get_by_label("Include private research data")).to_be_checked()
    expect(
        page.locator('[data-record-visibility="PRIVATE_RESEARCH"]').first
    ).to_be_visible()
    private_summary = page.locator(".dataset-records summary").first
    private_summary.click()
    expect(
        page.locator(".dataset-records details").first.locator("pre")
    ).to_contain_text('"visibility": "PRIVATE_RESEARCH"')
    expect(
        page.get_by_role("link", name="Download filtered NDJSON")
    ).to_have_attribute(
        "href",
        re.compile(
            r"entity_id=agent-001.*visibility=PRIVATE_RESEARCH"
            r".*include_private=true"
        ),
    )
    expect(
        page.get_by_role("link", name="Download filtered analysis bundle")
    ).to_have_attribute("href", re.compile(r"include_private=true"))
    with page.expect_download() as ndjson_download:
        page.get_by_role("link", name="Download filtered NDJSON").click()
    ndjson = ndjson_download.value
    assert ndjson.suggested_filename.endswith("-records.ndjson")
    assert '"visibility":"PRIVATE_RESEARCH"' in Path(ndjson.path()).read_text(
        encoding="utf-8"
    )
    with page.expect_download() as bundle_download:
        page.get_by_role(
            "link",
            name="Download filtered analysis bundle",
        ).click()
    assert bundle_download.value.suggested_filename.endswith("-analysis.zip")

    page.get_by_role("link", name="Schema and data dictionary").click()
    expect(
        page.get_by_role("heading", name="Schema and data dictionary")
    ).to_be_visible()
    expect(page.get_by_text("Complete data dictionary JSON")).to_be_visible()
    ids = page.locator("[id]").evaluate_all(
        "(elements) => elements.map((element) => element.id)"
    )
    assert len(ids) == len(set(ids))
    expect(page.get_by_role("main")).to_be_visible()
    expect(page.get_by_role("navigation", name="Application")).to_be_visible()


def test_data_management_is_server_rendered_and_enhanced(page: Page) -> None:
    page.goto("/ui/")
    page.get_by_role("button", name="Load example scenario").click()
    run_ids: list[str] = []
    for _ in range(2):
        page.get_by_role("button", name="Start run").click()
        page.get_by_role("button", name="Pause").click()
        page.get_by_role("button", name="Single step").click()
        page.get_by_role("button", name="Stop").click()
        explorer_href = page.get_by_role(
            "link",
            name="Explore research dataset",
        ).get_attribute("href")
        assert explorer_href is not None
        run_ids.append(explorer_href.split("/")[3])

    page.get_by_role("link", name="Data", exact=True).click()
    expect(page.get_by_role("heading", name="Data Management")).to_be_visible()
    expect(page.get_by_role("navigation", name="Application")).to_be_visible()
    navigation_count = page.evaluate(
        "performance.getEntriesByType('navigation').length"
    )

    search = page.get_by_label("Search run or scenario")
    search.fill(run_ids[0])
    page.get_by_role("button", name="Apply catalog filters").click()
    expect(page.get_by_label(f"Select {run_ids[0]}")).to_be_visible()
    page.get_by_role("button", name="Add current page").click()
    expect(page.get_by_text("1 run selected across catalog pages.")).to_be_visible()

    search.fill(run_ids[1])
    page.get_by_role("button", name="Apply catalog filters").click()
    page.get_by_role("button", name="Add current page").click()
    expect(page.get_by_text("2 runs selected across catalog pages.")).to_be_visible()
    assert page.evaluate(
        "performance.getEntriesByType('navigation').length"
    ) == navigation_count

    private_warning = page.get_by_text(
        re.compile("Aggregate statistics include PRIVATE_RESEARCH-derived rows")
    )
    expect(private_warning).to_be_visible()
    expect(page.get_by_text(re.compile("Pooled:"))).to_be_visible()
    expect(page.get_by_text(re.compile("Macro per run:"))).to_be_visible()
    page.get_by_label("Exclude private-derived aggregates").check()
    page.get_by_role(
        "button",
        name="Apply aggregate privacy setting",
    ).click()
    expect(private_warning).not_to_be_visible()

    with page.expect_download() as json_download:
        page.get_by_role("link", name="Download aggregate JSON").click()
    assert json_download.value.suggested_filename.endswith(".json")
    with page.expect_download() as csv_download:
        page.get_by_role("link", name="Download aggregate CSV").click()
    assert csv_download.value.suggested_filename.endswith(".csv")

    page.get_by_role("button", name="Delete selected runs").click()
    expect(page.get_by_text("Affected table counts", exact=True)).to_be_visible()
    page.get_by_label("Confirm permanent deletion").check()
    page.get_by_label(re.compile(r"Type DELETE 2 RUNS")).fill("DELETE 2 RUNS")
    page.get_by_role("button", name="Permanently delete 2 runs").click()
    expect(page.get_by_role("status")).to_contain_text(
        "Permanently deleted 2 runs"
    )
    expect(page.get_by_text("0 runs selected across catalog pages.")).to_be_visible()


def test_transaction_point_and_possessions_render_in_runtime_ui(
    page: Page,
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (
            EXAMPLE_SCENARIOS / "greyford-rivermarket-exchange.json"
        ).read_text(encoding="utf-8")
    )
    components = payload["entities"][0]["components"]
    components["plan"]["queue"] = [
        {
            "action": "TRANSACT",
            "target": "transaction-point-greyford-rivermarket-checkout",
            "offer_id": "redeem-returnable-bottle",
        }
    ]
    components["spatial_location"] = {
        "scale": "BUILDING",
        "place_id": "building-greyford-rivermarket-grocer-demo.interior",
        "local_coordinate": {"x": 8, "y": 3},
    }
    scenario_path = tmp_path / "transaction-ui.json"
    scenario_path.write_text(json.dumps(payload), encoding="utf-8")

    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(scenario_path)
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "validated and staged"
    )
    page.get_by_role("combobox", name="NPC control").select_option(
        "deterministic"
    )
    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()
    inspector = page.get_by_role(
        "complementary", name="Character inspector"
    )
    inspector.get_by_role("checkbox", name="Live refresh").uncheck()
    character = inspector.get_by_role("combobox", name="Character")
    character.select_option(
        "character-greyford-rivermarket-shopper"
    )
    expect(character).to_have_value(
        "character-greyford-rivermarket-shopper"
    )
    inspector.get_by_role("button", name="Inspect").click()
    expect(
        inspector.get_by_role("heading", name="Possessions")
    ).to_be_visible()
    world = page.get_by_role("region", name="World")
    world.get_by_text("Advanced scale override", exact=True).click()
    world.get_by_role("combobox", name="Scale").select_option("building")
    world.get_by_role("checkbox", name="Follow inspected character").check()
    world.get_by_role("button", name="Apply view").click()

    expect(
        world.get_by_role(
            "img",
            name="Transaction point: Bottle Return and Checkout Counter",
        )
    ).to_be_visible()
    possessions = page.get_by_role(
        "heading", name="Possessions"
    ).locator("..")
    expect(possessions).to_contain_text(
        "Greyford cent: 475 minor currency unit"
    )
    expect(possessions).to_contain_text(
        "Empty returnable glass bottle: 1 bottle"
    )
    page.get_by_role("button", name="Single step").click()
    expect(
        world.get_by_role(
            "link", name=re.compile(r"^Rivermarket Cashier at ")
        )
    ).to_be_visible()
    expect(
        page.get_by_text("npc.spawned", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Single step").click()
    page.get_by_role("button", name="Single step").click()
    expect(possessions).to_contain_text(
        "Greyford cent: 500 minor currency unit"
    )
    world.get_by_role(
        "link", name=re.compile(r"^Rivermarket Cashier at ")
    ).click()
    expect(inspector).to_contain_text(
        "Transient NPC · Rivermarket Cashier · deterministic"
    )
    expect(inspector).to_contain_text(
        "No physiological state is tracked for this transient NPC."
    )


def test_city_scenario_has_server_rendered_city_controls(page: Page) -> None:
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        EXAMPLE_SCENARIOS / "sparse-city-car-demo.json"
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
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        EXAMPLE_SCENARIOS / "sparse-city-car-demo.json"
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
    expect(page.get_by_text("Detail: Building", exact=True)).to_be_visible()
    page.mouse.wheel(0, -500)
    expect(page.get_by_text("Detail: Room", exact=True)).to_be_visible()
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


def test_dense_city_labels_do_not_overlap_at_high_zoom(page: Page) -> None:
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        EXAMPLE_SCENARIOS / "greyford-office-evening.json"
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
        page.get_by_role("button", name="Start run").click()
        page.get_by_role("button", name="Pause").click()
        page.get_by_role("button", name="Stop").click()
        page.get_by_role("link", name="Explore research dataset").click()
        expect(
            page.get_by_role("heading", name="Research Dataset Explorer")
        ).to_be_visible()
        page.get_by_label("Dataset view", exact=True).select_option("records")
        page.get_by_label("Records per page").fill("1")
        page.get_by_role("button", name="Apply dataset filters").click()
        expect(page.get_by_role("heading", name="Raw records")).to_be_visible()
        expect(page.get_by_label("Include private research data")).not_to_be_checked()
        run_id = page.url.split("/")[5]
        page.get_by_role("link", name="Back to Data Management").click()
        expect(page.get_by_role("heading", name="Data Management")).to_be_visible()
        page.get_by_label("Search run or scenario").fill(run_id)
        page.get_by_role("button", name="Apply catalog filters").click()
        page.get_by_label(f"Select {run_id}").check()
        page.get_by_role("button", name="Add checked runs").click()
        expect(page.get_by_text("1 run selected across catalog pages.")).to_be_visible()
        expect(page.get_by_role("link", name="Download aggregate JSON")).to_be_visible()
        page.get_by_role("link", name="Simulation", exact=True).click()
        page.goto("/ui/scenarios/?new=1")
        page.get_by_label("Scenario resource ID").fill("nojs-scenario")
        page.get_by_text("Scenario and environment settings", exact=True).click()
        page.get_by_label("Name", exact=True).first.fill(
            "No JavaScript Scenario"
        )
        page.get_by_label("World type").select_option("grid")
        page.get_by_role("button", name="Apply world type").click()

        page.get_by_text("World dimensions and transport defaults", exact=True).click()
        page.get_by_label("Width", exact=True).fill("3")
        page.get_by_label("Height", exact=True).fill("2")
        page.get_by_role("button", name="Add blocked cells").click()
        inspector = page.locator("#scenario-object-inspector")
        inspector.get_by_label("X", exact=True).fill("1")
        inspector.get_by_label("Y", exact=True).fill("0")
        page.get_by_role("button", name="Create scenario").click()
        expect(page.get_by_role("heading", name="nojs-scenario")).to_be_visible()

        page.get_by_text("Scenario and environment settings", exact=True).click()
        page.get_by_label("Name", exact=True).first.fill(
            "No JavaScript Staged Draft"
        )
        page.get_by_role("button", name="Validate and stage").click()
        expect(page.get_by_role("heading", name="Operator Console")).to_be_visible()
        expect(
            page.get_by_text("No JavaScript Staged Draft", exact=True)
        ).to_be_visible()
        expect(page.get_by_role("button", name="Start run")).to_be_enabled()
        page.goto("/ui/elements/?kind=npc_role")
        page.get_by_label("Element resource ID").fill("nojs-server")
        page.get_by_label("Element definition JSON").fill(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "nojs-server",
                    "name": "No JavaScript Server",
                    "description": "",
                    "kind": "npc_role",
                    "briefing": "Serve deterministic requests.",
                    "tool_allowlist": [
                        "serve_transaction",
                        "say",
                        "wait",
                        "skip",
                    ],
                    "vision_range": 6,
                    "recognition_range": 4,
                    "hearing_multiplier": 1.0,
                }
            )
        )
        page.get_by_role("button", name="Create element").click()
        expect(page.locator(".notice[role=status]")).to_contain_text(
            "Saved No JavaScript Server."
        )
    finally:
        context.close()


def test_pages_have_unique_ids_and_named_primary_landmarks(page: Page) -> None:
    for path in (
        "/ui/",
        "/ui/characters/",
        "/ui/scenarios/?new=1",
        "/ui/data/",
    ):
        page.goto(path)
        ids = page.locator("[id]").evaluate_all(
            "(elements) => elements.map((element) => element.id)"
        )
        assert len(ids) == len(set(ids))
        expect(page.get_by_role("main")).to_be_visible()
        expect(page.get_by_role("navigation", name="Application")).to_be_visible()
