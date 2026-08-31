# Large-Scale World, Hierarchical Movement, and Transport Plan

> Legacy implementation record. Sparse-city behavior is now part of the active
> architecture documented in `docs/CONCEPT_GUIDE.md`.

**Status:** Initial sparse-city milestone implemented
**Date:** 2026-08-30  
**Scope:** Sparse city representation, movement between buildings, city travel,
multi-level operator UI, controller travel tools, and a minimal car-trip demo  
**Runnable scenario:** `scenarios/sparse-city-car-demo.json`

## 0. Updated implementation baseline

This plan now builds on the implemented UI and telemetry architecture:

- telemetry schema `stage0.telemetry.v2`;
- separate immutable bootstrap, replaceable runtime snapshot, and durable
  domain-event cursors;
- automatic snapshot plus event-history recovery after disconnects;
- native ES modules for API, protocol, UI state, and composition;
- explicit UI lifecycle states and control selectors;
- character names, vision/hearing indicators, speech bubbles, transcripts, and
  operator overlay controls.

Large-world data follows the same separation:

```text
bootstrap
  city bounds, districts, buildings, entrances, nodes, edges, vehicles,
  character profiles

runtime snapshot
  hierarchical character location, active travel, edge progress,
  mutable vehicle position

durable events
  travel, building, vehicle, perception, speech, and failures
```

Static city geometry is cached from WebSocket bootstrap or scale-specific REST
endpoints. It is not repeated in the 10 Hz runtime snapshot.

The implemented first milestone includes:

- legacy-grid compatibility through `SpatialLocationComponent`;
- schema-version-2 city worlds with districts, buildings, local maps,
  entrances, outdoor places, sparse nodes/edges, and vehicles;
- deterministic mixed access routes for WALK, CYCLE, and CAR;
- explicit hierarchical location and travel state;
- `TRAVEL_TO` plan actions and `travel_to` controller tool contracts;
- safe-node System 1 interruption;
- city/building REST projections;
- AUTO/MANUAL building, neighborhood, and city operator views;
- pan/zoom city rendering with vehicle and character progress;
- the runnable sparse car demonstration.

Direct metro edges use the same deterministic travel progression and emit
boarding/alighting events. Scheduled headways, validated line definitions, and
transfer-specific waiting remain a later phase.

## 1. Goal

Extend the current detailed building-sized grid into a sparse but fully
materialized city. Characters must be able to:

- move within a building using the existing detailed grid behavior;
- leave one building and move through nearby exterior space;
- travel between distant buildings;
- use walking, cycling, car, or metro;
- remain explicitly located throughout the trip;
- be followed in the UI at the spatial scale appropriate to the focused
  character.

The initial city should resemble the intended final hierarchy without trying to
model every building, road tile, vehicle, timetable, or resident.

The simulation remains authoritative. A controller selects a destination and
transport mode, but deterministic systems decide whether the route is possible,
which legs are required, how long each leg takes, and whether the character
arrives.

## 2. Current-state diagnosis

The legacy local implementation assumes one spatial scale:

- `WorldMap` contains one rectangular `WorldGrid`;
- every `PositionComponent` is one `(x, y)` tile;
- every movement path is a tuple of adjacent grid coordinates;
- A* searches the entire grid with unit-cost edges;
- `MOVE_TO` resolves only a zone or station within that grid;
- perception measures visual range and hearing paths in grid cells;
- local movement and perception use the active `WorldMap`;
- local building rendering fits a detailed grid into one canvas.

The updated telemetry layer no longer sends full static data in every snapshot,
and the browser already supports recoverable event delivery and multiple
operator overlays. City support must extend those contracts rather than create
a second transport channel.

This is appropriate inside a building but should not be stretched into a
city-wide tile map. A mostly empty city grid would:

- allocate and serialize large areas that contain no meaningful detail;
- make route search and occupancy checks proportional to irrelevant space;
- force building interiors, roads, and metro lines into the same coordinate
  semantics;
- make a character or building too small to inspect in the browser;
- make perception range ambiguous across meters, rooms, streets, and districts;
- couple future regional scales to one increasingly overloaded grid.

The existing `go_to` tool also has no transport mode or trip lifecycle. A
`MoveIntent` contains only a target ID, and successful commit becomes one local
`MOVE_TO` plan action.

## 3. Spatial hierarchy

Implement three levels now:

| Level | Name | Purpose | Representation |
|---|---|---|---|
| 0 | Intra-building | Rooms, stations, doors, people, exact local movement | Existing detailed tile grid |
| 1 | Inter-building | Nearby buildings, entrances, sidewalks, parking, cycle access, local streets | Sparse neighborhood graph with map geometry |
| 2 | Intra-city | Districts, major buildings, arterial roads, metro lines, long trips | Sparse city graph with map geometry |

Reserve but do not implement:

- region/inter-city;
- national;
- global.

Use stable enum values rather than UI-only names:

```text
BUILDING
NEIGHBORHOOD
CITY
```

The UI labels may remain "Intra-building", "Inter-building", and "Intra-city".

## 4. Core design: nested places plus sparse transport graphs

## 4.1 City world

Replace the assumption that `WorldMap` is one grid with a root world containing:

```text
CityWorld
  city map bounds and display geometry
  districts
  buildings
  outdoor places
  transport networks
  vehicles
  local detailed maps
```

This should be an additive migration. Retain `WorldMap` as the detailed local
map type rather than rewriting working interior behavior.

Suggested domain contracts:

```python
@dataclass(frozen=True, slots=True)
class WorldLocation:
    scale: SpatialScale
    place_id: str
    local_coordinate: Coordinate | None = None
    network_node_id: str | None = None
    edge_id: str | None = None
    edge_progress: float | None = None


@dataclass(frozen=True, slots=True)
class Building:
    id: str
    name: str
    district_id: str
    city_position: MapPoint
    footprint: MapPolygon
    local_map_id: str
    entrances: tuple[BuildingEntrance, ...]


@dataclass(frozen=True, slots=True)
class BuildingEntrance:
    id: str
    local_coordinate: Coordinate
    neighborhood_node_id: str
```

`WorldLocation` always answers where a character is. During a car or metro leg,
the character is on a specific network edge with deterministic progress, not
teleported or temporarily absent.

## 4.2 Sparse networks

Represent exterior travel as typed graph networks:

```text
pedestrian network
cycle network
road network
metro network
```

Each network has stable nodes and edges:

```python
@dataclass(frozen=True, slots=True)
class TransportNode:
    id: str
    kind: NodeKind
    city_position: MapPoint
    place_id: str | None


@dataclass(frozen=True, slots=True)
class TransportEdge:
    id: str
    from_node_id: str
    to_node_id: str
    allowed_modes: frozenset[TravelMode]
    distance_meters: float
    geometry: tuple[MapPoint, ...]
    speed_limit_mps: float | None
    bidirectional: bool
```

Route search operates on relevant graph nodes, not every city tile. Geometry is
for rendering and progress interpolation; graph connectivity is authoritative.

## 4.3 Place hierarchy

Every place should have explicit ancestry:

```text
city
  -> district
      -> building or outdoor place
          -> local map
              -> zone
                  -> tile/station
```

Add a `PlaceIndex` resource for:

- resolving an ID to place type;
- finding parent/child relationships;
- mapping an interior target to its building;
- mapping a building to usable entrances;
- mapping entrances to transport nodes;
- producing breadcrumbs for telemetry and controller context.

IDs remain globally unique within a scenario. Do not infer type from an ID
prefix.

## 5. Position and movement components

## 5.1 Position migration

Preserve `PositionComponent.coordinate` for local compatibility during the
migration, but introduce the authoritative hierarchical component:

```python
@dataclass(slots=True)
class SpatialLocationComponent:
    location: WorldLocation
```

Migration sequence:

1. Add `SpatialLocationComponent` alongside `PositionComponent`.
2. For legacy scenarios, create one implicit building/local map and derive the
   hierarchical location.
3. Local movement systems continue updating `PositionComponent` and mirror the
   result to `SpatialLocationComponent`.
4. Cross-building travel switches the location away from a local coordinate.
5. Once all readers use hierarchical locations, reduce
   `PositionComponent` to a local-grid concern or replace it with
   `LocalPositionComponent`.

## 5.2 Travel state

Do not overload `MovementComponent` with multi-leg travel. Add:

```python
@dataclass(slots=True)
class TravelComponent:
    destination_id: str | None
    requested_mode: TravelMode | None
    route: tuple[TravelLeg, ...]
    current_leg_index: int
    leg_elapsed_seconds: float
    status: TravelStatus
    correlation_id: str | None
```

Travel states:

```text
IDLE
  -> ROUTE_REQUESTED
  -> ROUTE_PLANNED
  -> ACCESSING_MODE
  -> TRAVELLING
  -> TRANSFERRING
  -> ENTERING_DESTINATION
  -> ARRIVED

failure/interruption:
  -> BLOCKED
  -> CANCELLED
```

Each `TravelLeg` is explicit:

```text
EXIT_BUILDING
WALK
CYCLE
DRIVE
ENTER_METRO
WAIT_FOR_METRO
RIDE_METRO
TRANSFER_METRO
EXIT_METRO
PARK
ENTER_BUILDING
```

The initial milestone can use only the legs needed by direct trips, while
retaining the typed vocabulary.

## 6. Transport modes

## 6.1 Shared rules

```python
class TravelMode(StrEnum):
    WALK = "WALK"
    CYCLE = "CYCLE"
    CAR = "CAR"
    METRO = "METRO"
```

Route cost should initially be deterministic generalized travel time:

```text
access time
+ edge distance / configured speed
+ transfer time
+ fixed parking or metro wait time
```

Use stable tie-breaking by:

```text
total travel seconds
number of transfers
mode preference order
edge ID sequence
```

Do not add stochastic traffic in the first implementation. Scenario-defined
edge closures, capacities, and fixed delays are enough to exercise failure and
rerouting.

## 6.2 Walking

- available to all mobile characters unless a profile/capability explicitly
  restricts it;
- uses interior walking plus pedestrian-network edges;
- may enter buildings through public/authorized entrances;
- is the fallback access leg for cycle, car, and metro;
- uses a scenario-configurable walking speed.

## 6.3 Cycling

- requires a bicycle resource at the origin or an available cycle-share dock;
- uses pedestrian access legs and cycle-network edges;
- requires parking/return at a permitted node;
- cannot use road or metro edges unless explicitly allowed;
- initial capacity is one rider per bicycle.

## 6.4 Car

- requires an available car and permitted driver/rider access;
- uses walk-to-car, road, parking, and walk-from-car legs;
- the car is a materialized entity with its own location;
- character and car move together during `DRIVE`;
- initial implementation may treat the controlling character as the driver;
- fixed parking access time is scenario-configurable;
- no fuel, traffic lights, collision physics, or parking search in the first
  milestone.

The model should distinguish:

```text
DRIVER
PASSENGER
```

Only driver behavior is needed for the first car demonstration, but the state
must not imply every occupant controls the vehicle.

## 6.5 Metro

- uses walk-to-station, entry, wait, ride, exit, and walk-from-station legs;
- metro stations and lines are explicit;
- the first implementation may use deterministic fixed headway and ride time;
- trains may initially be scheduled service records rather than individually
  simulated rolling stock;
- the character remains located on a line segment during a ride;
- missed service, closed station, and disconnected line are explicit failures.

Do not simulate every train until character interaction with individual trains
becomes a requirement.

## 7. Routing architecture

Use hierarchical route planning rather than one algorithm across all scales:

```text
target resolution
  -> origin building exit route
  -> access node selection
  -> mode/network route
  -> destination entrance selection
  -> destination local route
```

Recommended ports:

```python
class LocalRoutePlanner(Protocol):
    def route(... ) -> LocalRoute | None: ...


class TransportRoutePlanner(Protocol):
    def route(
        origin: WorldLocation,
        destination: ResolvedDestination,
        mode: TravelMode,
        policy: TravelPolicy,
    ) -> TravelRoute | RouteFailure: ...
```

The existing deterministic grid A* remains the local planner. Add deterministic
Dijkstra/A* over transport nodes for exterior legs. Network edge weights are
travel seconds, not tile count.

Avoid one global occupancy set. Occupancy belongs to the relevant local map,
vehicle, station, or constrained network node. Ordinary road edges do not need
per-character collision avoidance in the first sparse model.

## 8. Domain system ordering

Add focused systems instead of expanding `MovementSystem` into a city engine:

```text
Plan/intent commit
  -> TravelRequestSystem
  -> HierarchicalRoutePlanningSystem
  -> LocalPathfindingSystem
  -> TransportAccessSystem
  -> TravelProgressSystem
  -> LocalMovementSystem
  -> ArrivalTransitionSystem
  -> PerceptionSystem
  -> Cognition scheduling
```

Exact order values must be chosen against the current stack and documented.
Same-tick semantics should ensure:

- a committed travel intent does not move a character before route validation;
- a completed edge updates location before perception;
- arrival into a building makes the new local map available before the next
  controller observation;
- System 1 can interrupt travel before progress is applied.

Implemented ordering places travel at order `175`: after homeostasis and System
1 arbitration (`170`), alongside post-arbitration speech, and before affordance
execution (`180`) and local movement (`200`). A newly critical drive therefore
marks travel for interruption before that tick's travel progress; characters
already on an edge continue deterministically to its next safe node.

## 9. System 1 during city travel

Survival remains non-bypassable, but corrective targets are no longer always on
the current grid.

Initial policy:

1. Search the current building/local place for a reachable corrective station.
2. If none exists, search known nearby buildings within a configured maximum
   correction time.
3. Select the reachable corrective target with deterministic minimum total
   route time.
4. Use walking unless the scenario explicitly allows another emergency mode.
5. If the character is already driving or riding metro, finish or interrupt the
   current leg according to a deterministic safety rule before rerouting.

For the first milestone:

- driving System 1 interruption continues to the next safe road node or parking
  node before changing route;
- metro travel continues to the next station before changing route;
- if no correction is reachable, enter `BLOCKED_SURVIVAL`.

Do not allow instant vehicle exit or teleportation from an edge.

## 10. Controller tools and observation changes

## 10.1 Keep local `go_to`

Retain:

```text
go_to(target_id, reason)
```

Use it for destinations reachable within the current building/local map.
Offering it only for local targets keeps controller intent clear and avoids
silently choosing transport.

## 10.2 Add `travel_to`

Add:

```json
{
  "name": "travel_to",
  "arguments": {
    "target_id": "building-office",
    "mode": "CAR",
    "reason": "Drive to the office"
  }
}
```

Strict schema:

```text
target_id: known building, outdoor place, district landmark, or station
mode: WALK | CYCLE | CAR | METRO
reason: optional private decision note
```

The tool returns a `TravelIntent`; it does not construct or commit a route.

Validation layers:

- the target was present in structured character knowledge/observation;
- the selected mode is offered for this decision;
- the character has required mode capability;
- a bicycle/car is known and accessible when required;
- the target is not already the current place;
- System 1 is normal;
- no incompatible travel/action is active;
- the route is revalidated at commit and execution.

Stable rejection reasons:

```text
unknown_destination
destination_not_known
mode_not_available
vehicle_not_available
station_not_available
route_not_found
travel_in_progress
system1_preemption
stale_decision
```

## 10.3 Observation additions

Controller context should add bounded structured fields:

```text
location hierarchy:
  city, district, building/place, local zone

known destinations:
  ID, type, last-known status, approximate travel options

available travel modes:
  mode, access resource, estimated deterministic travel time

active travel:
  destination, mode, leg type, public progress summary

nearby transport:
  parking, cycle docks, metro stations and known service status
```

Do not expose:

- hidden city graph internals;
- private destinations or routes of other characters;
- exact future path unless self-state needs it;
- omniscient traffic/service state that the character has not perceived or
  been told.

Tools must authorize from structured destination/mode records, not rendered
prose.

## 11. Perception across scales

Perception resolution must be local to the observer's current spatial context.

### Intra-building

Retain current line-of-sight and path-distance hearing on the local grid.

### Inter-building

Initial visible facts:

- a recognizable character exited or entered a building;
- a nearby character is walking/cycling on a named local connection;
- a character entered or exited a car;
- a car departed or arrived nearby;
- a metro entrance was used if locally visible.

### Intra-city

Ordinary characters do not visually perceive city-wide movement. City-level
facts for controllers come from:

- self travel state;
- public announcements;
- explicit communication;
- initialized map/common knowledge.

The city UI may display omniscient travel for the operator without making it a
character observation.

Never reveal another character's selected destination merely because their icon
is moving along a city route.

## 12. UI: three spatial levels integrated with telemetry v2

## 12.1 Focus model

Add explicit UI state:

```text
focusedAgentId
viewLevel: BUILDING | NEIGHBORHOOD | CITY
viewMode: AUTO | MANUAL
focusedPlaceId
camera center/zoom
```

These fields extend the existing reducer/selector state. They must not be
encoded into the simulation-control lifecycle state.

Default to `AUTO`. The view level follows the focused character:

| Focused character state | Automatic view |
|---|---|
| Inside a building | Intra-building |
| Outside between nearby buildings, approaching parking/station | Inter-building |
| On a long road or metro edge | Intra-city |

When the focused character changes, recalculate the appropriate view and camera
bounds. Manual mode allows the operator to select any implemented level without
changing simulation state.

## 12.2 Intra-building view

This is the existing canvas behavior scoped to one local map:

- rooms/zones and blocked tiles;
- stations and exact character positions;
- local paths;
- entrances highlighted as cross-scale connectors;
- breadcrumb: `City / District / Building / Zone`.

Do not send or render all other building grids.

## 12.3 Inter-building view

Render the focused neighborhood:

- building footprints and names;
- entrances;
- sidewalks/local street edges;
- parking, cycle docks, and metro entrances;
- nearby characters and vehicles;
- the focused route's local access/egress legs.

Use vector geometry from the sparse graph rather than a tile grid.

## 12.4 Intra-city view

Render:

- city boundary and districts;
- selected major building footprints/markers;
- arterial road graph;
- metro lines and stations;
- focused character/vehicle route and progress;
- origin, destination, transfer, and current-leg markers.

Only the focused route needs full visual emphasis. Other characters can be
aggregated or omitted from the operator view in the first implementation, but
their authoritative state remains materialized.

## 12.5 Scale-specific telemetry

Do not put every interior grid into every 10 Hz snapshot. Split telemetry:

```text
run/status snapshot
focused-agent summary
city overview
neighborhood detail for place ID
building detail for local map ID
event stream
```

Suggested API endpoints:

```text
GET /runs/{run_id}/world/city
GET /runs/{run_id}/world/neighborhoods/{place_id}
GET /runs/{run_id}/world/buildings/{building_id}
GET /runs/{run_id}/agents/{agent_id}/spatial-context
```

The WebSocket bootstrap carries the initial city layer and the latest runtime
snapshot carries focused character/vehicle progress. The scale-specific REST
endpoints remain available for local-map detail and future cache invalidation.
Recovery continues to use the existing domain-event offset; changing view level
must not reset telemetry cursors or dialogue/perception overlays.

## 13. Scenario schema

Introduce a new world schema while continuing to accept the legacy single-grid
world:

```json
{
  "world": {
    "type": "city",
    "city": {},
    "districts": [],
    "buildings": [],
    "local_maps": {},
    "transport": {
      "nodes": [],
      "edges": [],
      "metro_lines": [],
      "vehicles": []
    }
  }
}
```

Important validation:

- globally unique place, node, edge, entrance, map, station, line, and vehicle
  IDs;
- every building references an existing district and local map;
- every entrance connects a valid local tile to a valid network node;
- every edge references valid nodes and at least one mode;
- edge geometry endpoints match node positions;
- vehicle starting locations support the vehicle type;
- metro lines reference connected metro edges and stations;
- entity hierarchical locations are valid;
- scripted travel targets and modes are valid;
- legacy scenarios build an implicit one-building city wrapper.

Use explicit meters and seconds for exterior networks. Local grid cells retain
their existing abstract unit unless a scenario defines meters per tile.

## 14. Events, telemetry, and datasets

Add structured event families:

```text
travel.requested
travel.route_planned
travel.route_failed
travel.started
travel.leg_started
travel.progressed
travel.leg_completed
travel.mode_changed
travel.arrived
travel.cancelled
travel.interrupted

building.exited
building.entered
vehicle.boarded
vehicle.exited
vehicle.moved
metro.entered
metro.boarded
metro.alighted
metro.exited
```

Events should carry:

- character and vehicle IDs;
- origin/destination place IDs;
- mode and leg type;
- network/edge IDs;
- elapsed and expected seconds;
- normalized edge progress;
- request/application tick;
- causation/correlation IDs;
- stable failure reason.

Dataset trajectory records need a versioned hierarchical location:

```json
{
  "scale": "CITY",
  "place_id": "city-demo",
  "edge_id": "road-main-east",
  "edge_progress": 0.42,
  "vehicle_id": "car-001",
  "travel_mode": "CAR"
}
```

Keep local coordinate trajectories for interior legs. Do not replace them with
city coordinates.

## 15. Sparse demonstration city

The first city should be intentionally small:

```text
Demo City
  West Residential district
    Home building
    home parking node

  Central district
    Central metro station
    one public square

  East Business district
    Office building
    office parking node
```

Transport:

- one bidirectional arterial road from Home to Central to Office;
- one pedestrian path between the same major nodes;
- one short cycle route;
- one metro line with West, Central, and East stations;
- one car initially parked at Home.

Only Home and Office need tiny local interior maps. Central station can initially
be a place/transport node without a detailed interior.

The draft scenario demonstrates:

```text
Alex starts at the Home entrance
  -> boards car-001 as driver
  -> drives Home Road -> Central Avenue -> Office Road
  -> parks at Office
  -> remains at the Office entrance
```

No LLM is required. Use one scripted `TRAVEL_TO` plan action so the demo tests
the deterministic domain path before exposing it through `travel_to`.

## 16. Implementation phases

## Phase A: Hierarchical location and legacy compatibility — implemented

Deliver:

- spatial scale, map point, place hierarchy, building, entrance, and
  hierarchical location contracts;
- legacy one-grid scenarios wrapped as an implicit building;
- hierarchical location in telemetry and datasets;
- no behavior change to existing local movement.

Gate:

- all existing scenarios retain identical local movement and canonical domain
  outcomes;
- every character has a valid hierarchical location;
- no city-sized dense grid is introduced.

## Phase B: Sparse city schema and focused UI levels — implemented initial scope

Deliver:

- city/district/building/local-map scenario models;
- sparse exterior network geometry;
- city, neighborhood, and building read APIs;
- UI level selector with AUTO/MANUAL mode;
- focused-agent camera and breadcrumb;
- scale-specific static layer caching.

Gate:

- selecting an indoor character opens its building;
- selecting a character on a city edge opens the city view;
- changing UI level does not mutate the simulation;
- telemetry size is proportional to the requested view, not total interior
  tiles in the city.

## Phase C: Walking between buildings — implemented direct routes

Deliver:

- building exit/entry transitions;
- pedestrian graph routing;
- multi-leg travel component and systems;
- local `go_to` plus cross-building `travel_to(mode=WALK)`;
- travel events and hierarchical trajectories.

Gate:

- a character walks from a room in Home to a room in Office through explicit
  exit, exterior, entry, and local legs;
- System 1 can interrupt the trip without teleportation.

## Phase D: Cycling and car travel — implemented initial scope

Deliver:

- bicycle and car resources/entities;
- access, board, travel, park, and exit legs;
- cycle and road graph routing;
- `travel_to` modes `CYCLE` and `CAR`;
- car demo scenario.

Gate:

- `car-001` and its driver share explicit route progress;
- unavailable vehicle and parking conditions fail explicitly;
- the barebones demo shows the focused agent driving across the city.

## Phase E: Metro — partial

Deliver:

- metro stations, lines, fixed headways, and direct/transfer routing;
- station entry/wait/ride/alight/exit legs;
- `travel_to(mode=METRO)`;
- service status in knowledge and observations.

Direct metro edge routing and boarding/alighting events are implemented.
Headways, station access state, explicit line/transfer validation, and
service-status knowledge remain deferred.

Gate:

- a character completes a deterministic metro trip;
- closed/disconnected stations fail explicitly;
- a survival interrupt waits until the next station before rerouting.

## Phase F: Scale-aware perception and optimization — partial

Deliver:

- exterior perceptible facts;
- vehicle and building entry/exit observations;
- bounded neighborhood scans;
- spatial indexes for nearby queries;
- cached route/static-map projections;
- performance budgets for larger sparse scenarios.

Current implementation keeps building-grid perception local by hierarchical
place ID and keeps city travel operator-visible without exposing destinations
to other character controllers. Exterior entry/exit perceptible facts and
spatial indexes remain future work.

Gate:

- another character may observe departure but not private destination;
- perception work scales with nearby entities/nodes rather than total city
  contents.

## 17. Minimal test strategy

Keep initial automated coverage focused:

1. legacy grid scenario produces unchanged local movement;
2. place/entrance/network references validate;
3. deterministic route tie-breaking;
4. walk and car multi-leg state progression;
5. car/driver locations remain consistent;
6. System 1 interruption stops only at a safe transition point;
7. `travel_to` rejects unavailable modes and unknown destinations;
8. scale-specific telemetry omits unrelated building interiors;
9. UI AUTO level follows a focused character from building to city and back;
10. draft car scenario reaches the Office entrance deterministically.

Use scripted controller calls or scripted plans. Live LLM tests are unnecessary.

## 18. Deferred work

Do not include in the first city implementation:

- procedural city generation;
- continuous vehicle physics;
- road-lane simulation and traffic signals;
- stochastic congestion;
- fuel, charging, ownership economics, or maintenance;
- parking search;
- individual metro train simulation;
- inter-city, regional, national, or global travel;
- background-population aggregation;
- streaming world chunks from a remote server.

The contracts should permit these additions without changing the authority model
or collapsing all scales into one grid.
