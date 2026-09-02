import json
from collections import Counter

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.adapters.llm import ScriptedModelClient
from stage0_sim.application.agents.context import build_character_observation
from stage0_sim.application.agents.contracts import ModelToolCall, ModelTurn
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.elements import ElementKind, element_content_hash
from stage0_sim.application.scenario import (
    CityWorldDefinition,
    ScenarioDefinition,
    create_runner,
)
from stage0_sim.application.scenario_resolution import load_and_resolve_scenario
from stage0_sim.domain.components import (
    ConsumableComponent,
    ContainerComponent,
    CustodyComponent,
    DriveComponent,
    MovementComponent,
    ObjectIntrinsicComponent,
    OccupancySlotsComponent,
    OpenableComponent,
    PhysicalInteractionRegistry,
    PhysicalObjectIdentityComponent,
    PhysicalRelationKind,
    PhysicalStateComponent,
    PortableComponent,
    ReadableComponent,
    ScentSourceComponent,
    SenseTransmission,
    SpatialIndex,
    SpatialLocationComponent,
    SpatialParentRelationComponent,
    SupportComponent,
    UsableComponent,
    WearableComponent,
    validate_spatial_relation_acyclicity,
)
from stage0_sim.domain.economy import TransactionOperation
from stage0_sim.domain.systems.interactions import physical_object_is_exposed
from stage0_sim.domain.world import (
    STANDING_CHARACTER_FOOTPRINT,
    CityWorld,
    TravelMode,
    find_path,
    find_transport_route,
)
from tests.helpers.paths import EXAMPLE_CHARACTERS, EXAMPLE_ELEMENTS, EXAMPLE_SCENARIOS

SCENARIO_PATH = EXAMPLE_SCENARIOS / "willowbrook-saturday-morning.json"
CHARACTER_IDS = {
    "resident-alex": "alex-chen",
    "resident-jordan": "jordan-lee",
    "resident-maya": "maya-thompson",
}
HOME_NODES = (
    "node-willowbrook-cedar-court",
    "node-willowbrook-maple-row-12",
    "node-willowbrook-maple-row-18",
)
CAFE_ID = "building-willowbrook-corner-cup"
CAFE_ROOM_ID = f"{CAFE_ID}.interior"
CAFE_NODE_ID = "node-willowbrook-corner-cup"
WILLOWBROOK_PREFIX = "willowbrook-saturday-morning."
PORTABLE_DEFINITIONS = {
    f"{WILLOWBROOK_PREFIX}book",
    f"{WILLOWBROOK_PREFIX}bottle",
    f"{WILLOWBROOK_PREFIX}mug",
    f"{WILLOWBROOK_PREFIX}phone",
    f"{WILLOWBROOK_PREFIX}glasses",
}
REPRESENTATIVE_ROOM_TARGETS = {
    "building-willowbrook-cedar-court": (
        "building-willowbrook-cedar-court.interior.home-desk"
    ),
    "building-willowbrook-maple-row-12": (
        "building-willowbrook-maple-row-12.interior.sofa"
    ),
    "building-willowbrook-maple-row-18": (
        "building-willowbrook-maple-row-18.interior.sofa"
    ),
    "building-willowbrook-corner-cup": (
        "transaction-point-willowbrook-cafe-counter"
    ),
    "building-willowbrook-market": (
        "transaction-point-willowbrook-market-checkout"
    ),
    "building-willowbrook-library-annex": (
        "station-willowbrook-library-reading-table"
    ),
}


def _load_scenario():
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(EXAMPLE_ELEMENTS),
    ).scenario


def _turn(call_id: str, name: str, arguments: dict[str, object]) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(ModelToolCall(call_id, name, arguments),),
        finish_reason="tool_calls",
        provider="scripted",
        model="willowbrook-regression",
        latency_ms=0,
    )


def _create_willowbrook_runner():
    scenario = _load_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(EXAMPLE_CHARACTERS),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(()),
    )


def test_willowbrook_source_is_current_complete_and_character_neutral() -> None:
    scenario = _load_scenario()
    assert scenario.calendar is not None
    assert scenario.calendar.start_datetime.weekday() == 5
    assert scenario.character_situation_synthesis.enabled is False
    assert isinstance(scenario.world, CityWorldDefinition)
    assert len(scenario.world.districts) == 1
    assert len(scenario.world.buildings) == 6
    assert len(scenario.world.outdoor_places) == 2
    assert {role.id for role in scenario.npc_roles} == {
        "willowbrook-saturday-morning.cafe-barista"
    }

    cafe_room = next(room for room in scenario.world.rooms if room.id == CAFE_ROOM_ID)
    cafe_point = next(
        point
        for point in cafe_room.world.transaction_points
        if point.id == "transaction-point-willowbrook-cafe-counter"
    )
    assert cafe_point.operation is TransactionOperation.STAFFED
    assert cafe_point.staffing is not None
    assert cafe_point.staffing.role_id == "willowbrook-saturday-morning.cafe-barista"
    station_ids = {
        station.id for station in cafe_room.world.stations
    }
    assert {
        "station-willowbrook-cafe-window-seat",
        "station-willowbrook-cafe-corner-seat",
        "station-willowbrook-cafe-garden-seat",
    } <= station_ids
    assert len(station_ids) >= 6
    assert "interact_with" in scenario.cognition.tool_allowlist

    for entity in scenario.entities:
        assert "plan" not in entity.components
        assert entity.components["controller"]["enabled"] is True
        documents = entity.components["information"]["documents"]
        assert len(documents) == 1
        assert documents[0]["kind"] == "knowledge.place"
        assert documents[0]["content"]["destination_id"] == CAFE_ID
        assert "interact_with" in entity.components["controller"]["tool_allowlist"]

    library = FileSystemCharacterLibrary(EXAMPLE_CHARACTERS)
    prepared = prepare_scenario(scenario, library)
    assert prepared.assignments == CHARACTER_IDS

    maya_payload = json.dumps(
        library.get("maya-thompson").model_dump(mode="json"),
        sort_keys=True,
    ).casefold()
    for scenario_specific_text in (
        "willowbrook",
        "corner cup",
        "alex chen",
        "jordan lee",
        "resident-alex",
        "resident-jordan",
    ):
        assert scenario_specific_text not in maya_payload


def test_willowbrook_catalog_has_reusable_physical_object_families() -> None:
    library = FileSystemElementLibrary(EXAMPLE_ELEMENTS)
    expected = {
        "exterior-door",
        "interior-door",
        "window",
        "dining-chair",
        "home-sofa",
        "home-bed",
        "bedside-table",
        "home-table",
        "home-desk",
        "office-chair",
        "kitchen-counter",
        "bookcase",
        "cabinet",
        "market-shelf",
        "market-display",
        "cafe-table",
        "cafe-seat",
        "library-table",
        "library-desk",
        "lamp",
        "book",
        "bottle",
        "mug",
        "phone",
        "glasses",
        "mirror",
    }

    for suffix in expected:
        element_id = f"{WILLOWBROOK_PREFIX}{suffix}"
        element = library.get(element_id, ElementKind.OBJECT)
        assert element.schema_version == 3
        assert element.physical is not None
        assert len(element.physical.footprint.cells) > 1
        assert len(element_content_hash(element)) == 64

    book = library.get(
        f"{WILLOWBROOK_PREFIX}book",
        ElementKind.OBJECT,
    )
    assert book.physical is not None
    assert book.physical.capabilities.portable is not None
    assert book.physical.capabilities.readable is not None
    assert book.physical.capabilities.support is not None

    bottle = library.get(
        f"{WILLOWBROOK_PREFIX}bottle",
        ElementKind.OBJECT,
    )
    mug = library.get(f"{WILLOWBROOK_PREFIX}mug", ElementKind.OBJECT)
    phone = library.get(f"{WILLOWBROOK_PREFIX}phone", ElementKind.OBJECT)
    assert bottle.physical is not None
    assert mug.physical is not None
    assert phone.physical is not None
    assert bottle.physical.capabilities.consumable is not None
    assert mug.physical.capabilities.consumable is not None
    assert phone.physical.capabilities.usable is not None
    glasses = library.get(
        f"{WILLOWBROOK_PREFIX}glasses",
        ElementKind.OBJECT,
    )
    window = library.get(
        f"{WILLOWBROOK_PREFIX}window",
        ElementKind.OBJECT,
    )
    assert glasses.physical is not None
    assert glasses.physical.capabilities.wearable is not None
    assert glasses.physical.intrinsics.mass_kg == 0.03
    assert mug.physical.capabilities.scent_source is not None
    assert window.physical is not None
    assert window.physical.obstruction.vision.value == "TRANSPARENT"
    assert window.physical.obstruction.hearing is SenseTransmission.BLOCK
    assert window.physical.obstruction.smell is SenseTransmission.BLOCK


def test_willowbrook_runtime_projects_intrinsics_effects_and_self_senses() -> None:
    runner = _create_willowbrook_runner()
    registry = runner.registry
    glasses_id = (
        "building-willowbrook-cedar-court.interior.desk-glasses"
    )
    mug_ids = [
        object_id
        for object_id in registry.query_entities(ScentSourceComponent)
    ]

    assert registry.has_component(glasses_id, ObjectIntrinsicComponent)
    assert registry.has_component(glasses_id, WearableComponent)
    assert mug_ids
    assert registry.get_component(
        glasses_id,
        WearableComponent,
    ).effects[0].value == 36
    assert registry.get_component(
        mug_ids[0],
        ScentSourceComponent,
    ).emission_range == 54
    observation = build_character_observation(
        runner.context,
        "resident-alex",
    )
    assert observation.senses is not None
    assert observation.senses["vision_range"] > 0
    assert observation.equipment == {}
    assert observation.carried_load is not None
    assert observation.carried_load["known_mass_kg"] == 0


def test_every_willowbrook_building_has_furnished_physical_coverage() -> None:
    scenario = _load_scenario()
    objects_by_building: dict[str, list] = {}
    for world_object in scenario.world.objects:
        objects_by_building.setdefault(world_object.building_id, []).append(
            world_object
        )

    minimum_counts = {
        "building-willowbrook-cedar-court": 29,
        "building-willowbrook-maple-row-12": 31,
        "building-willowbrook-maple-row-18": 31,
        "building-willowbrook-corner-cup": 23,
        "building-willowbrook-market": 15,
        "building-willowbrook-library-annex": 25,
    }
    assert all(
        len(objects_by_building[building_id]) >= minimum
        for building_id, minimum in minimum_counts.items()
    )

    required_definitions = {
        "building-willowbrook-cedar-court": {
            "exterior-door",
            "interior-door",
            "window",
            "home-bed",
            "home-sofa",
            "home-desk",
            "kitchen-counter",
            "bookcase",
            "book",
            "bottle",
            "mug",
            "phone",
        },
        "building-willowbrook-maple-row-12": {
            "exterior-door",
            "interior-door",
            "window",
            "home-bed",
            "home-sofa",
            "home-desk",
            "bookcase",
            "book",
            "bottle",
            "mug",
            "phone",
        },
        "building-willowbrook-maple-row-18": {
            "exterior-door",
            "interior-door",
            "window",
            "home-bed",
            "home-sofa",
            "home-desk",
            "bookcase",
            "book",
            "bottle",
            "mug",
            "phone",
        },
        "building-willowbrook-corner-cup": {
            "exterior-door",
            "window",
            "cafe-counter",
            "cafe-table",
            "cafe-seat",
            "cabinet",
            "mug",
            "bottle",
            "phone",
            "book",
        },
        "building-willowbrook-market": {
            "exterior-door",
            "window",
            "market-shelf",
            "market-display",
            "market-checkout",
            "cabinet",
            "bottle",
            "phone",
        },
        "building-willowbrook-library-annex": {
            "exterior-door",
            "window",
            "bookcase",
            "library-table",
            "library-desk",
            "dining-chair",
            "book",
            "phone",
            "lamp",
        },
    }
    for building_id, required in required_definitions.items():
        definitions = {
            world_object.definition_id.removeprefix(WILLOWBROOK_PREFIX)
            for world_object in objects_by_building[building_id]
        }
        assert required <= definitions
        assert all(
            world_object.physical is not None
            and len(world_object.physical.footprint.cells) > 1
            for world_object in objects_by_building[building_id]
        )

    rooms_by_building = Counter(
        room.building_id
        for room in scenario.world.rooms
        if room.world.zones
    )
    assert rooms_by_building == Counter(
        {
            building_id: 1
            for building_id in required_definitions
        }
    )


def test_doors_relations_slots_and_hidden_storage_materialize_validly() -> None:
    runner = _create_willowbrook_runner()
    city = runner.registry.get_resource(CityWorld)
    relations: dict[str, SpatialParentRelationComponent] = {}
    slotted_counts: Counter[tuple[str, str, PhysicalRelationKind]] = Counter()

    for building in city.buildings:
        assert len(building.entrances) == 1
        door_id = runner.registry.get_resource(
            PhysicalInteractionRegistry
        ).door_for_transition(building.entrances[0].id)
        assert door_id is not None
        identity = runner.registry.get_component(
            door_id,
            PhysicalObjectIdentityComponent,
        )
        openable = runner.registry.get_component(door_id, OpenableComponent)
        state = runner.registry.get_component(door_id, PhysicalStateComponent)
        assert identity.definition_id == f"{WILLOWBROOK_PREFIX}exterior-door"
        assert state.pose.room_id == building.entrances[0].room_id
        assert not openable.is_open
        assert not openable.is_locked

    portable_count = 0
    occupancy_targets = 0
    for entity_id in runner.registry.entities():
        if runner.registry.has_component(
            entity_id,
            SpatialParentRelationComponent,
        ):
            relation = runner.registry.get_component(
                entity_id,
                SpatialParentRelationComponent,
            )
            relations[entity_id] = relation
            if relation.kind in {
                PhysicalRelationKind.ON_SUPPORT,
                PhysicalRelationKind.IN_CONTAINER,
            }:
                assert relation.slot_id is not None
                slots = runner.registry.get_component(
                    relation.parent_id,
                    OccupancySlotsComponent,
                )
                selected = slots.slot(relation.slot_id)
                assert relation.kind in selected.accepted_relations
                slotted_counts[
                    (relation.parent_id, relation.slot_id, relation.kind)
                ] += 1
        if runner.registry.has_component(entity_id, PortableComponent):
            portable_count += 1
            assert not runner.registry.has_component(entity_id, CustodyComponent)
            assert relations[entity_id].kind in {
                PhysicalRelationKind.ON_SUPPORT,
                PhysicalRelationKind.IN_CONTAINER,
            }
        if runner.registry.has_component(entity_id, OccupancySlotsComponent):
            slots = runner.registry.get_component(
                entity_id,
                OccupancySlotsComponent,
            )
            if any(
                PhysicalRelationKind.OCCUPIES_SLOT in item.accepted_relations
                for item in slots.slots
            ):
                occupancy_targets += 1

    validate_spatial_relation_acyclicity(relations)
    for (parent_id, slot_id, _), count in slotted_counts.items():
        capacity = runner.registry.get_component(
            parent_id,
            OccupancySlotsComponent,
        ).slot(slot_id).capacity
        assert count <= capacity

    assert portable_count >= 40
    assert occupancy_targets >= 20
    assert sum(
        count
        for (_, _, relation), count in slotted_counts.items()
        if relation is PhysicalRelationKind.ON_SUPPORT
    ) >= 35
    assert sum(
        count
        for (_, _, relation), count in slotted_counts.items()
        if relation is PhysicalRelationKind.IN_CONTAINER
    ) >= 5

    assert runner.registry.has_component(
        "building-willowbrook-cedar-court.interior.home-desk",
        SupportComponent,
    )
    assert runner.registry.has_component(
        "building-willowbrook-cedar-court.interior.kitchen-counter",
        ContainerComponent,
    )
    assert runner.registry.has_component(
        "building-willowbrook-library-annex.interior.reading-book",
        ReadableComponent,
    )
    assert runner.registry.has_component(
        "building-willowbrook-market.interior.shelf-bottle-a",
        ConsumableComponent,
    )
    assert runner.registry.has_component(
        "building-willowbrook-cedar-court.interior.entry-phone",
        UsableComponent,
    )
    assert not physical_object_is_exposed(
        runner.registry,
        "building-willowbrook-cedar-court.interior.stored-mug",
    )


def test_representative_room_routes_have_physical_clearance() -> None:
    runner = _create_willowbrook_runner()
    city = runner.registry.get_resource(CityWorld)
    index = runner.registry.get_resource(SpatialIndex)
    interactions = runner.registry.get_resource(PhysicalInteractionRegistry)

    for building_id, target_id in REPRESENTATIVE_ROOM_TARGETS.items():
        building = city.building(building_id)
        entrance = building.entrances[0]
        room = city.room(entrance.room_id)
        paths = [
            find_path(
                room.world.grid,
                entrance.local_coordinate,
                anchor,
                footprint=STANDING_CHARACTER_FOOTPRINT,
                spatial_index=index,
                room_id=room.id,
            )
            for anchor in interactions.approach_anchors(target_id)
        ]
        assert any(path is not None for path in paths), (
            building_id,
            target_id,
        )


def test_every_home_has_a_walking_route_to_the_cafe() -> None:
    scenario = _load_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(EXAMPLE_CHARACTERS),
    )
    runner = create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(()),
    )
    city = runner.registry.get_resource(CityWorld)

    for home_node in HOME_NODES:
        route = find_transport_route(
            city,
            home_node,
            CAFE_NODE_ID,
            TravelMode.WALK,
        )
        assert route
        assert all(leg.mode is TravelMode.WALK for leg in route)


def test_scripted_first_decisions_can_move_all_residents_to_the_cafe() -> None:
    scenario = _load_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(EXAMPLE_CHARACTERS),
    )
    turns = [
        _turn(
            f"navigate-{index}",
            "navigate_to",
            {"target_id": CAFE_ID},
        )
        for index in range(3)
    ]
    turns.extend(
        _turn(f"wait-{index}", "wait", {"duration_seconds": 60})
        for index in range(60)
    )
    runner = create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(tuple(turns)),
    )

    runner.run_for(500)

    for entity_id in CHARACTER_IDS:
        location = runner.registry.get_component(
            entity_id,
            SpatialLocationComponent,
        ).location
        assert location.place_id == CAFE_ROOM_ID


def test_system1_uses_a_reachable_physical_station_approach_pose() -> None:
    payload = _load_scenario().model_dump(mode="json")
    alex = next(
        entity
        for entity in payload["entities"]
        if entity["id"] == "resident-alex"
    )
    alex["components"]["homeostasis"]["satiety"] = 10
    for entity in payload["entities"]:
        entity["components"]["controller"]["enabled"] = False
    runner = create_runner(ScenarioDefinition.model_validate(payload))

    runner.run_for(1)

    drive = runner.registry.get_component("resident-alex", DriveComponent)
    movement = runner.registry.get_component(
        "resident-alex",
        MovementComponent,
    )
    target_id = drive.target_station_id
    assert target_id is not None
    approaches = runner.registry.get_resource(
        PhysicalInteractionRegistry
    ).approach_anchors(target_id)
    assert drive.target_position in approaches
    assert movement.destination == drive.target_position
    assert drive.target_position != runner.registry.get_component(
        target_id,
        PhysicalStateComponent,
    ).pose.anchor

    assert not any(
        event.event_type
        in {
            "tool.rejected",
            "navigation.failed",
            "travel.route_failed",
            "action.failed",
        }
        for event in runner.events.events
    )


def test_alex_can_deterministically_use_the_phone_on_the_entry_cabinet() -> None:
    payload = _load_scenario().model_dump(mode="json")
    for entity in payload["entities"]:
        entity["components"]["controller"]["enabled"] = (
            entity["id"] == "resident-alex"
        )
    scenario = ScenarioDefinition.model_validate(payload)
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(EXAMPLE_CHARACTERS),
    )
    phone_id = "building-willowbrook-cedar-court.interior.entry-phone"
    runner = create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(
            (
                _turn(
                    "use-entry-phone",
                    "interact_with",
                    {
                        "verb": "USE",
                        "target_id": phone_id,
                    },
                ),
            )
        ),
    )

    runner.run_for(4)

    completed = [
        event
        for event in runner.events.events
        if event.event_type == "interaction.completed"
        and event.agent_id == "resident-alex"
    ]
    assert len(completed) == 1
    assert completed[0].payload["verb"] == "USE"
    assert completed[0].payload["target_id"] == phone_id
    assert completed[0].payload["use_kind"] == "mobile-phone"
    assert not any(
        event.event_type in {
            "tool.rejected",
            "interaction.failed",
            "action.failed",
        }
        for event in runner.events.events
    )
