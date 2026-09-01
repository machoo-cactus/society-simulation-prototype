import json
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.helpers.paths import (
    EXAMPLE_SCENARIOS,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("STAGE0_RUN_PLAYWRIGHT") != "1",
    reason="set STAGE0_RUN_PLAYWRIGHT=1 to run browser UI tests",
)


def test_character_crud_round_trip_is_role_driven(
    page: Page, tmp_path: Path
) -> None:
    page.goto("/ui/characters/?selected=alex-chen")
    expect(page.get_by_role("heading", name="Character Library")).to_be_visible()
    expect(page.get_by_role("heading", name="Alex Chen")).to_be_visible()

    page.get_by_role("link", name="New character").click()
    page.get_by_label("Character ID").fill("playwright-created")
    page.get_by_label("Display name").fill("Created in Browser")
    page.get_by_label("Birth date").fill("1992-04-03")
    page.get_by_label("Height (cm)").fill("171.5")
    page.get_by_label("Currency code").fill("CAD")
    page.get_by_label("Total debt").fill("12000")
    page.get_by_label("Family members (JSON records)").fill(
        '[{"member_id":"sibling","display_name":"Taylor",'
        '"relationship":"Sibling","living_status":"alive"}]'
    )
    page.get_by_label("Allergies (JSON records)").fill(
        '[{"substance":"Pollen","reaction":"Rhinitis","severity":"mild"}]'
    )
    page.get_by_role("button", name="Create character").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Created Created in Browser"
    )
    expect(page.get_by_label("Birth date")).to_have_value("1992-04-03")
    expect(page.get_by_label("Height (cm)")).to_have_value("171.5")
    expect(page.get_by_label("Total debt")).to_have_value("12000")
    expect(page.get_by_label("Family members (JSON records)")).to_contain_text(
        '"member_id": "sibling"'
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
        '{"schema_version":2,"id":"playwright-imported",'
        '"template_id":"human-v1","identity":{'
        '"display_name":"Imported in Browser","birth_date":"1990-01-01"}}',
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
    page.get_by_text("Scenario and environment settings", exact=True).click()
    scenario_name = page.get_by_label("Name", exact=True).first
    scenario_name.fill("Playwright Scenario")
    page.get_by_label("Dt", exact=True).fill("0")
    page.get_by_role("button", name="Create scenario").click()
    expect(page.get_by_role("alert", name="Scenario validation failed")).to_contain_text(
        "greater than 0"
    )
    page.get_by_text("Scenario and environment settings", exact=True).click()
    expect(page.get_by_label("Dt", exact=True)).to_have_value("0")

    page.get_by_label("Dt", exact=True).fill("1")
    page.get_by_role("button", name="Create scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved Playwright Scenario"
    )

    page.get_by_text("Scenario and environment settings", exact=True).click()
    page.get_by_label("Name", exact=True).first.fill(
        "Unsaved Playwright Stage"
    )
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.get_by_role("heading", name="Operator Console")).to_be_visible()
    expect(page.get_by_text("Unsaved Playwright Stage", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Start run")).to_be_enabled()

    page.goto("/ui/scenarios/?selected=playwright-scenario")
    page.get_by_text("Scenario and environment settings", exact=True).click()
    expect(page.get_by_label("Name", exact=True).first).to_have_value(
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


def test_scenario_editor_uses_map_selection_and_city_drill_down(page: Page) -> None:
    page.goto("/ui/scenarios/?selected=navigation")
    expect(page.get_by_role("img", name="Grid world")).to_be_visible()
    station = page.locator(".scenario-object-explorer").get_by_role(
        "link", name=re.compile(r"Kitchen Fridge")
    )
    station.click()
    inspector = page.locator("#scenario-object-inspector")
    expect(inspector.get_by_role("heading", name="Selected object")).to_be_visible()
    inspector.get_by_label("Name", exact=True).fill("Edited map station")
    inspector.get_by_role("button", name="Apply selected changes").click()
    expect(
        page.get_by_role("link", name="Edit station Edited map station")
    ).to_be_visible()

    viewport = page.locator("#scenario-world-render")
    viewport.hover(position={"x": 300, "y": 220})
    page.mouse.wheel(0, -500)
    expect(page.get_by_role("status", name="Editor zoom level")).not_to_have_text(
        "100%"
    )
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1

    page.goto("/ui/scenarios/?selected=reference-city-restaurants")
    edge = page.locator(".scenario-object-explorer").get_by_role(
        "link", name=re.compile(r"market-walk")
    )
    edge.click()
    expect(page.locator("#scenario-object-inspector")).to_contain_text(
        "transport › edges"
    )
    building = page.locator(".scenario-object-explorer").get_by_role(
        "link", name=re.compile(r"Standard Restaurant")
    ).first
    building.click()
    local_map = page.get_by_role("link", name=re.compile(r"^Open .* interior$"))
    expect(local_map).to_be_visible()
    local_map.click()
    expect(
        page.get_by_role("img", name=re.compile(r"^Building interior ·"))
    ).to_be_visible()
    expect(page.get_by_role("navigation", name="World hierarchy")).to_contain_text(
        "Market Zone"
    )


def test_saved_building_references_keep_isolated_overrides(
    page: Page,
) -> None:
    page.goto("/ui/scenarios/?selected=reference-city-restaurants")
    explorer = page.locator(".scenario-object-explorer")
    expect(
        explorer.get_by_role(
            "link",
            name=re.compile(r"Standard Restaurant|East Market Restaurant"),
        )
    ).to_have_count(2)

    explorer.get_by_role("link", name=re.compile(r"Market Zone")).click()
    inspector = page.locator("#scenario-object-inspector")
    expect(inspector.get_by_text("standard-restaurant", exact=True)).to_be_visible()
    expect(
        inspector.get_by_text(re.compile(r"SHA-256 [0-9a-f]{64}")).first
    ).to_be_visible()
    inspector.get_by_role("button", name="Add Standard Restaurant").click()
    expect(
        explorer.get_by_role(
            "link",
            name=re.compile(r"Standard Restaurant|East Market Restaurant"),
        )
    ).to_have_count(3)

    explorer.get_by_role(
        "link",
        name=re.compile(r"Standard Restaurant"),
    ).first.click()
    expect(
        inspector.get_by_role("heading", name="Inherited building definition")
    ).to_be_visible()
    inherited = inspector.get_by_role(
        "region",
        name="Inherited building definition",
    )
    expect(inherited).to_contain_text("Restaurant Dining Room")
    expect(inherited).to_contain_text("Restaurant Checkout")
    expect(inherited).to_contain_text("NPC Restaurant Server")

    inspector.locator("summary", has_text=re.compile(r"^Overrides$")).click()
    inspector.get_by_label("Include Name").select_option("present")
    inspector.get_by_role("button", name="Apply selected changes").click()
    inspector.locator("summary", has_text=re.compile(r"^Overrides$")).click()
    inspector.get_by_label("Name", exact=True).fill("West Browser Restaurant")
    page.get_by_role("button", name="Save scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved reference-city-restaurants"
    )
    expect(
        explorer.get_by_role("link", name=re.compile("West Browser Restaurant"))
    ).to_be_visible()
    expect(
        explorer.get_by_role("link", name=re.compile("East Market Restaurant"))
    ).to_be_visible()

    explorer.get_by_role(
        "link",
        name=re.compile("West Browser Restaurant"),
    ).click()
    inspector.get_by_role("button", name="Reset building overrides").click()
    page.get_by_role("button", name="Save scenario").click()
    expect(
        explorer.get_by_role("link", name=re.compile("Standard Restaurant"))
    ).to_have_count(2)
    expect(
        explorer.get_by_role("link", name=re.compile("East Market Restaurant"))
    ).to_be_visible()

    download = page.request.get(
        "/ui/scenarios/reference-city-restaurants/download"
    )
    assert download.ok
    saved = download.json()
    buildings = saved["world"]["city_zones"][0]["buildings"]
    assert len(buildings) == 3
    assert {item["element"]["id"] for item in buildings} == {
        "standard-restaurant"
    }
    assert "local_maps" not in saved["world"]


def test_saved_missing_and_hash_drift_dependencies_block_staging(
    page: Page,
    ui_library_paths: tuple[Path, Path],
) -> None:
    scenario_directory, element_directory = ui_library_paths
    reference_path = scenario_directory / "reference-city-restaurants.json"
    missing_path = scenario_directory / "missing-browser-dependency.json"
    source = json.loads(reference_path.read_text(encoding="utf-8"))
    source["name"] = "Missing Browser Dependency"
    source["world"]["city_zones"][0]["buildings"][0]["element"]["id"] = (
        "missing-browser-building"
    )
    missing_path.write_text(json.dumps(source, indent=2), encoding="utf-8")

    page.goto("/ui/")
    page.get_by_label("Saved scenario").select_option(
        "missing-browser-dependency"
    )
    page.get_by_role("button", name="Stage selected saved scenario").click()
    expect(page.locator(".notice[role=alert]")).to_contain_text(
        "unknown element: missing-browser-building"
    )
    expect(page.get_by_role("button", name="Start run")).to_be_disabled()

    building_path = element_directory / "standard-restaurant.json"
    original = building_path.read_text(encoding="utf-8")
    try:
        changed = json.loads(original)
        changed["description"] = "Changed after the scenario was saved."
        building_path.write_text(
            json.dumps(changed, indent=2),
            encoding="utf-8",
        )
        page.goto("/ui/")
        page.get_by_label("Saved scenario").select_option(
            "reference-city-restaurants"
        )
        page.get_by_role("button", name="Stage selected saved scenario").click()
        expect(page.locator(".notice[role=alert]")).to_contain_text(
            "content hash changed"
        )
        expect(page.get_by_role("button", name="Start run")).to_be_disabled()
    finally:
        building_path.write_text(original, encoding="utf-8")
        missing_path.unlink(missing_ok=True)


def test_element_library_crud_is_accessible_and_hash_protected(page: Page) -> None:
    page.goto("/ui/elements/?kind=npc_role")
    expect(page.get_by_role("heading", name="Element Library")).to_be_visible()
    page.get_by_label("Element resource ID").fill("playwright-server")
    page.get_by_label("Element definition JSON").fill(
        json.dumps(
            {
                "schema_version": 1,
                "id": "playwright-server",
                "name": "Playwright Server",
                "description": "Synthetic browser fixture.",
                "kind": "npc_role",
                "briefing": "Serve deterministic test requests.",
                "tool_allowlist": [
                    "serve_transaction",
                    "say",
                    "wait",
                    "skip",
                ],
                "vision_range": 6,
                "recognition_range": 4,
                "hearing_multiplier": 1.0,
            },
            indent=2,
        )
    )
    page.get_by_role("button", name="Create element").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved Playwright Server."
    )
    expect(page.get_by_role("link", name=re.compile("Playwright Server"))).to_be_visible()

    page.get_by_text("Duplicate playwright-server", exact=True).click()
    page.get_by_label("New element resource ID").fill("playwright-server-copy")
    page.get_by_role("button", name="Duplicate element").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Duplicated playwright-server as playwright-server-copy."
    )

    page.get_by_text("Delete playwright-server-copy", exact=True).click()
    page.get_by_label(
        "I understand deletion is blocked while another element references this resource."
    ).check()
    page.get_by_role("button", name="Delete element").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Deleted playwright-server-copy."
    )


def test_reference_scenario_resolves_shared_restaurant_elements(
    page: Page,
) -> None:
    page.goto("/ui/")
    page.get_by_label("Scenario JSON").set_input_files(
        EXAMPLE_SCENARIOS / "reference-city-restaurants.json"
    )
    page.get_by_role("button", name="Validate and stage").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "reference-city-restaurants is validated and staged"
    )
    expect(
        page.get_by_role("img", name=re.compile("Staged city preview"))
    ).to_be_visible()
    expect(page.locator("#world-render .building")).to_have_count(2)
    page.get_by_role("button", name="Start run").click()
    page.get_by_role("button", name="Pause").click()
    page.get_by_text("Advanced scale override", exact=True).click()
    page.get_by_role("combobox", name="Scale").select_option("room")
    page.get_by_role("button", name="Apply view").click()
    expect(page.get_by_text("Detail: Room", exact=True)).to_be_visible()
    expect(
        page.get_by_role("img", name=re.compile("Room view"))
    ).to_be_visible()


def test_scenario_editor_renders_staffed_transaction_positions(
    page: Page,
) -> None:
    page.goto("/ui/scenarios/?selected=reference-city-restaurants")
    building = page.locator(".scenario-object-explorer").get_by_role(
        "link", name=re.compile(r"Standard Restaurant")
    ).first
    building.click()
    page.get_by_role("link", name=re.compile(r"^Open .* interior$")).click()

    expect(
        page.get_by_role(
            "link",
            name=(
                "Edit transaction point "
                "Restaurant Checkout"
            ),
        )
    ).to_be_visible()
    expect(
        page.get_by_role(
            "img",
            name=(
                "Staff position for "
                "Restaurant Checkout"
            ),
        )
    ).to_be_visible()


def test_large_city_scenario_submits_the_native_structured_form(page: Page) -> None:
    page.goto("/ui/scenarios/?selected=reference-city-restaurants")
    expect(
        page.get_by_role("heading", name="reference-city-restaurants")
    ).to_be_visible()
    page.get_by_role("button", name="Save scenario").click()
    expect(page.locator(".notice[role=status]")).to_contain_text(
        "Saved reference-city-restaurants"
    )
