from pathlib import Path

import pytest

from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.application.characters import prepare_scenario
from stage0_sim.application.navigation import RecursiveRoutePlanner
from stage0_sim.application.scenario import (
    ScenarioDefinition,
    create_runner,
    load_scenario,
)
from stage0_sim.domain.components import SpatialLocationComponent
from stage0_sim.domain.world import (
    CityWorld,
    Coordinate,
    GridTopology,
    Locator,
    RouteLeg,
    Space,
    SpaceRegistry,
    SparseGraphTopology,
    Transition,
    TravelMode,
    TraversalContext,
    WorldGrid,
    WorldMap,
    find_path,
    find_transport_route,
)

ROOT = Path(__file__).parents[1]
CITY_SCENARIO_PATH = ROOT / "scenarios" / "sparse-city-car-demo.json"
CHARACTER_DIRECTORY = ROOT / "characters"


def _city_runner(scenario: ScenarioDefinition | None = None):
    scenario = scenario or load_scenario(CITY_SCENARIO_PATH)
    prepared = prepare_scenario(
        scenario,
        FileSystemCharacterLibrary(CHARACTER_DIRECTORY),
    )
    return create_runner(
        scenario,
        resolved_characters=prepared.runtime_characters(),
    )


def test_locator_key_is_canonical_and_detached_from_mutable_json() -> None:
    reference = {
        "z": [3, {"b": True, "a": None}],
        "a": {"y": 2, "x": 1},
    }
    first = Locator("space", reference)
    second = Locator(
        "space",
        {
            "a": {"x": 1, "y": 2},
            "z": [3, {"a": None, "b": True}],
        },
    )

    reference["z"] = []

    assert first == second
    assert first.stable_key == second.stable_key
    assert first.local_reference == second.local_reference
    assert first.stable_key == '"space":{"a":{"x":1,"y":2},"z":[3,{"a":null,"b":true}]}'


def test_registry_revision_queries_and_reverse_transitions_are_deterministic() -> None:
    first_topology = GridTopology("space-b", WorldMap(WorldGrid(2, 1)))
    second_topology = GridTopology("space-a", WorldMap(WorldGrid(2, 1)))
    registry = SpaceRegistry()
    registry.register_space(Space("space-b", first_topology))
    registry.register_space(Space("space-a", second_topology))
    registry.register_containment("space-b", "space-a")
    portal = Transition(
        id="portal",
        from_locator=first_topology.locator(Coordinate(1, 0)),
        to_locator=second_topology.locator(Coordinate(0, 0)),
        traversal_kind="portal",
        executor_id="portal",
        cost_model_id="constant",
        bidirectional=True,
    )
    registry.register_transition(portal)
    registry.register_destination("target", second_topology.locator(Coordinate(1, 0)))
    revision = registry.revision
    registry.register_destination("target", second_topology.locator(Coordinate(1, 0)))

    assert registry.revision == revision == 5
    assert [space.id for space in registry.spaces()] == ["space-a", "space-b"]
    assert [space.id for space in registry.child_spaces("space-b")] == ["space-a"]
    assert [space.id for space in registry.parent_spaces("space-a")] == ["space-b"]
    assert registry.transitions_from(portal.from_locator) == (portal,)
    reverse = registry.transitions_from(portal.to_locator)
    assert len(reverse) == 1
    assert reverse[0].id == "portal:reverse"
    assert reverse[0].to_locator == portal.from_locator
    assert registry.destination_locators("target") == (
        second_topology.locator(Coordinate(1, 0)),
    )


def test_grid_topology_delegates_to_deterministic_astar() -> None:
    world = WorldMap(
        WorldGrid(3, 3, frozenset({Coordinate(1, 1)}))
    )
    topology = GridTopology("building", world)
    origin = topology.locator(Coordinate(0, 1))
    destination = topology.locator(Coordinate(2, 1))

    expected = find_path(world.grid, Coordinate(0, 1), Coordinate(2, 1))
    route = topology.plan_local_route(origin, destination, TraversalContext())

    assert route is not None
    assert expected is not None
    assert tuple(
        topology.coordinate(leg.destination) for leg in route.legs
    ) == expected
    assert route.total_cost == len(expected)


def test_sparse_graph_topology_matches_existing_transport_route() -> None:
    runner = _city_runner()
    city = runner.registry.get_resource(CityWorld)
    topology = SparseGraphTopology(city.id, city)
    origin = topology.node_locator("node-home-entrance")
    destination = topology.node_locator("node-office-entrance")

    expected = find_transport_route(
        city,
        "node-home-entrance",
        "node-office-entrance",
        TravelMode.CAR,
    )
    route = topology.plan_local_route(
        origin,
        destination,
        TraversalContext(requested_mode=TravelMode.CAR.value),
    )

    assert expected is not None
    assert route is not None
    assert [leg.transition_id for leg in route.legs] == [
        leg.edge_id for leg in expected
    ]
    assert [leg.metadata["mode"] for leg in route.legs] == [
        leg.mode.value for leg in expected
    ]
    assert route.total_cost == sum(leg.duration_seconds for leg in expected)
    assert topology.resolve(
        {"kind": "edge", "edge_id": expected[0].edge_id, "progress": 0.5}
    ) == topology.edge_locator(expected[0].edge_id, 0.5)


def test_city_registry_contains_all_entrances_and_destinations() -> None:
    payload = load_scenario(CITY_SCENARIO_PATH).model_dump(mode="json")
    payload["world"]["buildings"][0]["entrances"].append(
        {
            "id": "entrance-home-side",
            "local_coordinate": {"x": 2, "y": 0},
            "neighborhood_node_id": "node-home-entrance",
        }
    )
    runner = _city_runner(ScenarioDefinition.model_validate(payload))
    registry = runner.registry.get_resource(SpaceRegistry)
    city = runner.registry.get_resource(CityWorld)

    assert [space.id for space in registry.spaces()] == [
        "building-home",
        "building-office",
        city.id,
    ]
    assert [space.id for space in registry.child_spaces(city.id)] == [
        "building-home",
        "building-office",
    ]
    assert [transition.id for transition in registry.transitions()] == [
        "entrance-home",
        "entrance-home-side",
        "entrance-office",
    ]
    for transition in registry.transitions():
        assert registry.transitions_from(transition.from_locator) == (transition,)
        reverse = registry.transitions_from(transition.to_locator)
        assert any(
            item.id == f"{transition.id}:reverse"
            and item.to_locator == transition.from_locator
            for item in reverse
        )
    assert {
        locator.space_id
        for locator in registry.destination_locators("building-home")
    } == {"building-home"}
    assert len(registry.destination_locators("building-home")) == 2
    outdoor = registry.destination_locators("place-central-square")
    assert len(outdoor) == 1
    assert outdoor[0].space_id == city.id
    assert outdoor[0].local_reference == {
        "kind": "node",
        "node_id": "node-central-junction",
    }
    assert registry.destination_locators("home-entry")
    assert all(
        locator.space_id == "building-home"
        for locator in registry.destination_locators("home-entry")
    )
    spatial = runner.registry.get_component(
        "agent-001",
        SpatialLocationComponent,
    )
    assert spatial.locator == Locator(
        "building-home",
        {"kind": "coordinate", "x": 2, "y": 1},
    )


def test_legacy_scenario_builds_implicit_grid_registry() -> None:
    runner = create_runner(load_scenario(ROOT / "scenarios" / "navigation.json"))
    registry = runner.registry.get_resource(SpaceRegistry)

    assert [space.id for space in registry.spaces()] == ["implicit-building"]
    assert isinstance(registry.space("implicit-building").topology, GridTopology)
    assert registry.destination_locators("lounge")
    assert registry.destination_locators("sofa-1") == (
        Locator(
            "implicit-building",
            {"kind": "coordinate", "x": 10, "y": 6},
        ),
    )


def test_explicit_transition_cannot_collide_with_synthesized_reverse_id() -> None:
    topology = _city_runner().registry.get_resource(SpaceRegistry)
    transition = topology.transition("entrance-home").reverse()

    with pytest.raises(ValueError, match="reserved"):
        topology.register_transition(transition)


def test_topology_metadata_is_detached_and_cannot_mutate_planning() -> None:
    first = GridTopology("space-a", WorldMap(WorldGrid(1, 1)))
    second = GridTopology("space-b", WorldMap(WorldGrid(1, 1)))
    space_metadata = {"nested": {"labels": ["original"]}}
    transition_metadata = {
        "cost": 2,
        "nested": {"weights": [1, 2]},
    }
    leg_metadata = {"nested": {"mode": ["walk"]}}
    context_metadata = {"nested": {"preference": ["quiet"]}}
    first_space = Space(
        "space-a",
        first,
        metadata=space_metadata,
    )
    transition = Transition(
        id="portal",
        from_locator=first.locator(Coordinate(0, 0)),
        to_locator=second.locator(Coordinate(0, 0)),
        traversal_kind="portal",
        executor_id="portal",
        cost_model_id="constant",
        metadata=transition_metadata,
    )
    leg = RouteLeg(
        origin=transition.from_locator,
        destination=transition.to_locator,
        traversal_kind="portal",
        executor_id="portal",
        cost=2,
        transition_id="portal",
        metadata=leg_metadata,
    )
    context = TraversalContext(metadata=context_metadata)
    registry = SpaceRegistry()
    registry.register_space(first_space)
    registry.register_space(Space("space-b", second))
    registry.register_transition(transition)

    space_metadata["nested"]["labels"].append("mutated")
    transition_metadata["cost"] = 99
    transition_metadata["nested"]["weights"].append(99)
    leg_metadata["nested"]["mode"].append("car")
    context_metadata["nested"]["preference"].append("busy")
    observed_nested = transition.metadata["nested"]
    assert isinstance(observed_nested, dict)
    observed_nested["weights"].append(100)

    route = RecursiveRoutePlanner().plan(
        registry,
        transition.from_locator,
        (transition.to_locator,),
        context,
    )

    assert first_space.metadata == {"nested": {"labels": ["original"]}}
    assert transition.metadata == {
        "cost": 2,
        "nested": {"weights": [1, 2]},
    }
    assert leg.metadata == {"nested": {"mode": ["walk"]}}
    assert context.metadata == {"nested": {"preference": ["quiet"]}}
    assert route.legs[0].cost == 2
