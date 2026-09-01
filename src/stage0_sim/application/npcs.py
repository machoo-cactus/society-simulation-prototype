import hashlib
import json
import re
from dataclasses import dataclass

from stage0_sim.domain.components import (
    ActivityComponent,
    CharacterProfileComponent,
    CharacterSituationComponent,
    ControllerComponent,
    ConversationComponent,
    DriveComponent,
    MovementComponent,
    NpcComponent,
    PerceptionComponent,
    PlanComponent,
    PositionComponent,
    SensesComponent,
    SpatialLocationComponent,
    TransactionRequestComponent,
)
from stage0_sim.domain.events import JsonValue
from stage0_sim.domain.npcs import (
    NpcPoolRegistry,
    NpcRole,
    NpcRoleRegistry,
    NpcStaffingAssignment,
    NpcStaffingState,
)
from stage0_sim.domain.systems import SystemContext
from stage0_sim.domain.world import SpatialScale, WorldLocation


@dataclass(frozen=True, slots=True)
class NpcStaffingSystem:
    name: str = "npc_staffing"
    order: int = 190

    def update(self, context: SystemContext) -> None:
        if not context.registry.has_resource(NpcPoolRegistry):
            return
        pool = context.registry.get_resource(NpcPoolRegistry)
        for customer_id, request in context.registry.query(
            TransactionRequestComponent
        ):
            if request.status != "awaiting_staff":
                continue
            staffing = pool.staffing(request.point_id)
            if staffing.npc_id is None:
                self._spawn(context, staffing)
            if staffing.npc_id is None:
                continue
            request.operator_id = staffing.npc_id
            request.status = "awaiting_authorization"
            context.events.emit(
                "transaction.staff_assigned",
                simulation_tick=context.clock.tick,
                simulation_time=context.clock.simulation_time,
                agent_id=customer_id,
                payload={
                    "request_id": request.request_id,
                    "point_id": request.point_id,
                    "offer_id": request.offer_id,
                    "operator_id": staffing.npc_id,
                },
            )

    def _spawn(
        self,
        context: SystemContext,
        staffing: NpcStaffingState,
    ) -> None:
        assignment = staffing.assignment
        blocking_id = _blocking_entity(context, assignment)
        if blocking_id is not None:
            if staffing.last_spawn_blocked_tick != context.clock.tick:
                context.events.emit(
                    "npc.spawn_blocked",
                    simulation_tick=context.clock.tick,
                    simulation_time=context.clock.simulation_time,
                    payload={
                        "point_id": assignment.point_id,
                        "role_id": assignment.role_id,
                        "position": assignment.staff_position.to_payload(),
                        "blocked_by": blocking_id,
                    },
                )
                staffing.last_spawn_blocked_tick = context.clock.tick
            return
        role = context.registry.get_resource(NpcRoleRegistry).role(
            assignment.role_id
        )
        staffing.spawn_sequence += 1
        npc_id = _npc_id(assignment.point_id, staffing.spawn_sequence)
        _materialize_npc(
            context,
            npc_id=npc_id,
            role=role,
            assignment=assignment,
            spawn_sequence=staffing.spawn_sequence,
            control_mode=context.registry.get_resource(
                NpcPoolRegistry
            ).effective_mode,
        )
        staffing.npc_id = npc_id
        context.events.emit(
            "npc.spawned",
            simulation_tick=context.clock.tick,
            simulation_time=context.clock.simulation_time,
            agent_id=npc_id,
            payload={
                "role_id": role.id,
                "role_name": role.name,
                "point_id": assignment.point_id,
                "position": assignment.staff_position.to_payload(),
                "place_id": assignment.place_id,
                "spawn_sequence": staffing.spawn_sequence,
                "control_mode": context.registry.get_resource(
                    NpcPoolRegistry
                ).effective_mode.value,
                "transient": True,
            },
        )


def _materialize_npc(
    context: SystemContext,
    *,
    npc_id: str,
    role: NpcRole,
    assignment: NpcStaffingAssignment,
    spawn_sequence: int,
    control_mode: object,
) -> None:
    from stage0_sim.domain.npcs import NpcControlMode

    if not isinstance(control_mode, NpcControlMode):
        raise TypeError("NPC control mode must be resolved before spawning")
    role_payload: dict[str, JsonValue] = {
        "id": role.id,
        "name": role.name,
        "briefing": role.briefing,
        "tool_allowlist": list(role.tool_allowlist),
    }
    content_hash = hashlib.sha256(
        json.dumps(
            role_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    registry = context.registry
    registry.create_entity(npc_id)
    registry.add_component(
        npc_id,
        NpcComponent(
            role_id=role.id,
            role_name=role.name,
            staffed_point_id=assignment.point_id,
            spawn_sequence=spawn_sequence,
            spawned_at=context.clock.simulation_time,
            control_mode=control_mode,
        ),
    )
    registry.add_component(
        npc_id,
        CharacterProfileComponent(
            profile_id=f"npc-role:{role.id}",
            template_id="npc-role-v1",
            template_version=1,
            content_hash=content_hash,
            display_name=role.name,
            description=role.briefing,
            ui_data={"npc_role": role_payload},
        ),
    )
    registry.add_component(
        npc_id,
        CharacterSituationComponent(
            slot_id=npc_id,
            label=role.name,
            briefing=role.briefing,
            description=role.briefing,
            content_hash=content_hash,
        ),
    )
    registry.add_component(
        npc_id,
        PositionComponent(assignment.staff_position),
    )
    if assignment.place_id is not None:
        registry.add_component(
            npc_id,
            SpatialLocationComponent(
                WorldLocation(
                    scale=SpatialScale.BUILDING,
                    place_id=assignment.place_id,
                    local_coordinate=assignment.staff_position,
                )
            ),
        )
    registry.add_component(npc_id, MovementComponent())
    registry.add_component(npc_id, ActivityComponent())
    registry.add_component(npc_id, DriveComponent())
    registry.add_component(npc_id, PlanComponent())
    registry.add_component(
        npc_id,
        ControllerComponent(
            enabled=True,
            tool_allowlist=role.tool_allowlist,
        ),
    )
    registry.add_component(
        npc_id,
        SensesComponent(
            vision_range=role.vision_range,
            recognition_range=role.recognition_range,
            hearing_multiplier=role.hearing_multiplier,
        ),
    )
    registry.add_component(npc_id, PerceptionComponent())
    registry.add_component(npc_id, ConversationComponent())


def _blocking_entity(
    context: SystemContext,
    assignment: NpcStaffingAssignment,
) -> str | None:
    for entity_id, position in context.registry.query(PositionComponent):
        if position.coordinate != assignment.staff_position:
            continue
        if assignment.place_id is None:
            if not context.registry.has_component(
                entity_id, SpatialLocationComponent
            ):
                return entity_id
            continue
        if not context.registry.has_component(
            entity_id, SpatialLocationComponent
        ):
            continue
        location = context.registry.get_component(
            entity_id, SpatialLocationComponent
        ).location
        if location.place_id == assignment.place_id:
            return entity_id
    return None


def _npc_id(point_id: str, spawn_sequence: int) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", point_id.casefold()).strip("-")
    return f"npc-{stem}-{spawn_sequence:04d}"
