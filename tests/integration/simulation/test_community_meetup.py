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
    CharacterEmbodimentComponent,
    ConsumableComponent,
    ContainerComponent,
    CustodyComponent,
    DriveComponent,
    EquipmentSlot,
    EquipmentStateComponent,
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
from tests.helpers.paths import (
    CATALOG_CHARACTERS,
    CATALOG_ELEMENTS,
    CATALOG_SCENARIOS,
)

SCENARIO_PATH = CATALOG_SCENARIOS / "community-meetup.json"
CHARACTER_ASSIGNMENTS = {
    "resident-alex": "alex-chen",
    "resident-jordan": "jordan-lee",
    "resident-maya": "maya-thompson",
}
HOME_NODES = (
    "node-community-apartments",
    "node-community-townhouse-west",
    "node-community-townhouse-east",
)
CAFE_ID = "building-community-cafe"
CAFE_ROOM_ID = f"{CAFE_ID}.interior"
CAFE_NODE_ID = "node-community-cafe"
PORTABLE_DEFINITIONS = {
    "common.book",
    "common.bottle",
    "common.glasses",
    "common.mug",
    "common.phone",
}
REPRESENTATIVE_ROOM_TARGETS = {
    "building-community-apartments": (
        "building-community-apartments.interior.home-desk"
    ),
    "building-community-townhouse-west": (
        "building-community-townhouse-west.interior.sofa"
    ),
    "building-community-townhouse-east": (
        "building-community-townhouse-east.interior.sofa"
    ),
    CAFE_ID: "building-community-cafe.interior.counter",
    "building-community-market": (
        "building-community-market.interior.checkout"
    ),
    "building-community-library": (
        "building-community-library.interior.reading-table"
    ),
    "building-community-transit": (
        "building-community-transit.interior.ticket-machine"
    ),
}


def _load_scenario() -> ScenarioDefinition:
    return load_and_resolve_scenario(
        SCENARIO_PATH,
        FileSystemElementLibrary(CATALOG_ELEMENTS),
    ).scenario


def _turn(call_id: str, name: str, arguments: dict[str, object]) -> ModelTurn:
    return ModelTurn(
        text=None,
        tool_calls=(ModelToolCall(call_id, name, arguments),),
        finish_reason="tool_calls",
        provider="scripted",
        model="community-regression",
        latency_ms=0,
    )


def _create_community_runner():
    scenario = _load_scenario()
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CATALOG_CHARACTERS),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(()),
    )


def test_community_meetup_source_is_current_and_character_neutral() -> None:
    scenario = _load_scenario()
    assert scenario.schema_version == 8
    assert scenario.calendar is not None
    assert scenario.calendar.start_datetime.weekday() == 5
    assert scenario.character_situation_synthesis.enabled is False
    assert isinstance(scenario.world, CityWorldDefinition)
    assert len(scenario.world.districts) == 1
    assert len(scenario.world.buildings) == 7
    assert len(scenario.world.outdoor_places) == 2
    assert {role.id for role in scenario.npc_roles} == {
        "hospitality.barista"
    }

    cafe_room = next(
        room for room in scenario.world.rooms if room.id == CAFE_ROOM_ID
    )
    cafe_point = next(
        point
        for point in cafe_room.world.transaction_points
        if point.id == "building-community-cafe.interior.counter"
    )
    assert cafe_point.operation is TransactionOperation.STAFFED
    assert cafe_point.staffing is not None
    assert cafe_point.staffing.role_id == "hospitality.barista"
    assert len(cafe_room.world.stations) == 6

    for entity in scenario.entities:
        assert "plan" not in entity.components
        assert entity.components["controller"]["enabled"] is True
        goals = entity.components["goals"]["goals"]
        assert len(goals) == 1
        assert goals[0]["completion_policy"] == "all"
        assert goals[0]["criteria"] == [
            {
                "type": "simulation_time",
                "simulation_time": 3600.0,
            }
        ]
        assert {
            "engage",
            "interact_with",
            "read_text",
            "write_text",
        } <= set(entity.components["controller"]["tool_allowlist"])
        assert {
            document["content"]["destination_id"]
            for document in entity.components["information"]["documents"]
        } == {
            "building-community-cafe",
            "building-community-library",
            "building-community-market",
            "building-community-transit",
        }

    library = FileSystemCharacterLibrary(CATALOG_CHARACTERS)
    prepared = prepare_scenario(scenario, library)
    assert prepared.assignments == CHARACTER_ASSIGNMENTS
    maya_payload = json.dumps(
        library.get("maya-thompson").model_dump(mode="json"),
        sort_keys=True,
    ).casefold()
    assert "community-meetup" not in maya_payload
    assert "resident-alex" not in maya_payload

    text_content = scenario.text_content.model_dump(mode="json")
    assert text_content["groups"][0]["member_ids"] == list(
        CHARACTER_ASSIGNMENTS
    )
    assert text_content["collections"][0]["members"] == [
        "shared-community-note"
    ]
    assert {
        grant["operation"]
        for grant in text_content["artifacts"][0]["access_policy"]["grants"]
    } >= {"read", "append", "replace"}


def test_community_catalog_uses_reusable_physical_object_families() -> None:
    library = FileSystemElementLibrary(CATALOG_ELEMENTS)
    expected = {
        "civic.library-table",
        "civic.service-desk",
        "common.book",
        "common.bookcase",
        "common.bottle",
        "common.cabinet",
        "common.dining-chair",
        "common.exterior-door",
        "common.glasses",
        "common.interior-door",
        "common.lamp",
        "common.mirror",
        "common.mug",
        "common.office-chair",
        "common.phone",
        "common.window",
        "hospitality.cafe-counter",
        "hospitality.seat",
        "hospitality.table",
        "mobility.ticket-machine",
        "residential.bed",
        "residential.bedside-table",
        "residential.desk",
        "residential.dining-table",
        "residential.kitchen-counter",
        "residential.sofa",
        "retail.display",
        "retail.market-checkout",
        "retail.shelf",
    }
    for element_id in expected:
        element = library.get(element_id, ElementKind.OBJECT)
        assert element.schema_version == 4
        assert element.physical is not None
        assert len(element.physical.footprint.cells) > 1
        assert len(element_content_hash(element)) == 64

    book = library.get("common.book", ElementKind.OBJECT)
    bottle = library.get("common.bottle", ElementKind.OBJECT)
    mug = library.get("common.mug", ElementKind.OBJECT)
    phone = library.get("common.phone", ElementKind.OBJECT)
    glasses = library.get("common.glasses", ElementKind.OBJECT)
    window = library.get("common.window", ElementKind.OBJECT)
    assert book.physical.capabilities.portable is not None
    assert book.physical.capabilities.readable is not None
    assert book.physical.capabilities.support is not None
    assert bottle.physical.capabilities.consumable is not None
    assert mug.physical.capabilities.consumable is not None
    assert phone.physical.capabilities.usable is not None
    assert glasses.physical.capabilities.wearable is not None
    assert glasses.physical.intrinsics.mass_kg == 0.03
    assert mug.physical.capabilities.scent_source is not None
    assert window.physical.obstruction.vision.value == "TRANSPARENT"
    assert window.physical.obstruction.hearing is SenseTransmission.BLOCK
    assert window.physical.obstruction.smell is SenseTransmission.BLOCK


def test_community_runtime_projects_intrinsics_equipment_and_senses() -> None:
    runner = _create_community_runner()
    registry = runner.registry
    glasses_id = "building-community-apartments.interior.desk-glasses"
    mug_ids = tuple(registry.query_entities(ScentSourceComponent))

    assert registry.has_component(glasses_id, ObjectIntrinsicComponent)
    assert registry.has_component(glasses_id, WearableComponent)
    assert registry.get_component(
        glasses_id, WearableComponent
    ).effects[0].value == 36
    assert mug_ids
    assert registry.get_component(
        mug_ids[0], ScentSourceComponent
    ).emission_range == 54

    embodiment = registry.get_component(
        "resident-alex", CharacterEmbodimentComponent
    )
    equipment = registry.get_component(
        "resident-alex", EquipmentStateComponent
    )
    assert embodiment.equipment_slot_capacities[EquipmentSlot.EYES] == 1
    assert equipment.equipped_object_ids == {}

    observation = build_character_observation(
        runner.context, "resident-alex"
    )
    assert observation.senses == {
        "vision_range": 72,
        "recognition_range": 54,
        "hearing_range": 90,
        "smell_range": 18,
    }
    assert observation.equipment == {}
    assert observation.carried_load["known_mass_kg"] == 0


def test_every_community_building_has_furnished_physical_coverage() -> None:
    scenario = _load_scenario()
    objects_by_building: dict[str, list] = {}
    for world_object in scenario.world.objects:
        objects_by_building.setdefault(
            world_object.building_id, []
        ).append(world_object)

    minimum_counts = {
        "building-community-apartments": 30,
        "building-community-townhouse-west": 30,
        "building-community-townhouse-east": 30,
        "building-community-cafe": 20,
        "building-community-market": 12,
        "building-community-library": 20,
        "building-community-transit": 5,
    }
    assert set(objects_by_building) == set(minimum_counts)
    assert all(
        len(objects_by_building[building_id]) >= minimum
        for building_id, minimum in minimum_counts.items()
    )
    assert all(
        world_object.physical is not None
        and len(world_object.physical.footprint.cells) > 1
        for objects in objects_by_building.values()
        for world_object in objects
    )
    assert {
        item.definition_id
        for item in objects_by_building["building-community-cafe"]
    } >= {
        "common.exterior-door",
        "common.phone",
        "hospitality.cafe-counter",
        "hospitality.seat",
        "hospitality.table",
    }
    assert {
        item.definition_id
        for item in objects_by_building["building-community-transit"]
    } >= {
        "common.exterior-door",
        "mobility.ticket-machine",
    }


def test_doors_relations_slots_and_hidden_storage_materialize_validly() -> None:
    runner = _create_community_runner()
    city = runner.registry.get_resource(CityWorld)
    interactions = runner.registry.get_resource(PhysicalInteractionRegistry)
    relations: dict[str, SpatialParentRelationComponent] = {}
    slotted_counts: Counter[tuple[str, str, PhysicalRelationKind]] = Counter()

    for building in city.buildings:
        assert len(building.entrances) == 1
        door_id = interactions.door_for_transition(building.entrances[0].id)
        assert door_id is not None
        identity = runner.registry.get_component(
            door_id, PhysicalObjectIdentityComponent
        )
        state = runner.registry.get_component(
            door_id, PhysicalStateComponent
        )
        door = runner.registry.get_component(door_id, OpenableComponent)
        assert identity.definition_id == "common.exterior-door"
        assert state.pose.room_id == building.entrances[0].room_id
        assert not door.is_open
        assert not door.is_locked

    for entity_id in runner.registry.entities():
        if runner.registry.has_component(
            entity_id, SpatialParentRelationComponent
        ):
            relation = runner.registry.get_component(
                entity_id, SpatialParentRelationComponent
            )
            relations[entity_id] = relation
            if relation.kind in {
                PhysicalRelationKind.ON_SUPPORT,
                PhysicalRelationKind.IN_CONTAINER,
            }:
                assert relation.slot_id is not None
                slots = runner.registry.get_component(
                    relation.parent_id, OccupancySlotsComponent
                )
                assert relation.kind in slots.slot(
                    relation.slot_id
                ).accepted_relations
                slotted_counts[
                    (relation.parent_id, relation.slot_id, relation.kind)
                ] += 1
        if runner.registry.has_component(entity_id, PortableComponent):
            assert not runner.registry.has_component(
                entity_id, CustodyComponent
            )
            assert relations[entity_id].kind in {
                PhysicalRelationKind.ON_SUPPORT,
                PhysicalRelationKind.IN_CONTAINER,
            }

    validate_spatial_relation_acyclicity(relations)
    for (parent_id, slot_id, _), count in slotted_counts.items():
        capacity = runner.registry.get_component(
            parent_id, OccupancySlotsComponent
        ).slot(slot_id).capacity
        assert count <= capacity

    assert len(tuple(runner.registry.query_entities(PortableComponent))) >= 50
    assert len(
        tuple(runner.registry.query_entities(OccupancySlotsComponent))
    ) >= 90
    assert runner.registry.has_component(
        "building-community-apartments.interior.home-desk",
        SupportComponent,
    )
    assert runner.registry.has_component(
        "building-community-apartments.interior.kitchen-counter",
        ContainerComponent,
    )
    assert runner.registry.has_component(
        "building-community-library.interior.reading-book",
        ReadableComponent,
    )
    assert runner.registry.has_component(
        "building-community-market.interior.shelf-bottle-a",
        ConsumableComponent,
    )
    assert runner.registry.has_component(
        "building-community-apartments.interior.entry-phone",
        UsableComponent,
    )
    assert not physical_object_is_exposed(
        runner.registry,
        "building-community-apartments.interior.stored-mug",
    )


def test_representative_room_routes_have_physical_clearance() -> None:
    runner = _create_community_runner()
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


def test_every_resident_has_a_walking_route_to_the_meetup() -> None:
    runner = _create_community_runner()
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
        assert route[-1].to_node_id == CAFE_NODE_ID


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
        "resident-alex", MovementComponent
    )
    target_id = drive.target_station_id
    assert target_id is not None
    approaches = runner.registry.get_resource(
        PhysicalInteractionRegistry
    ).approach_anchors(target_id)
    assert drive.target_position in approaches
    assert movement.destination == drive.target_position
    assert drive.target_position != runner.registry.get_component(
        target_id, PhysicalStateComponent
    ).pose.anchor


def test_alex_can_use_the_authored_phone_with_a_scripted_controller() -> None:
    payload = _load_scenario().model_dump(mode="json")
    for entity in payload["entities"]:
        entity["components"]["controller"]["enabled"] = (
            entity["id"] == "resident-alex"
        )
    scenario = ScenarioDefinition.model_validate(payload)
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CATALOG_CHARACTERS),
    )
    phone_id = "building-community-apartments.interior.entry-phone"
    runner = create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
        model_client=ScriptedModelClient(
            (
                _turn(
                    "use-entry-phone",
                    "interact_with",
                    {"verb": "USE", "target_id": phone_id},
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
        event.event_type
        in {"tool.rejected", "interaction.failed", "action.failed"}
        for event in runner.events.events
    )
