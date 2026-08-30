import heapq
from dataclasses import dataclass, replace

from stage0_sim.domain.world import (
    Locator,
    Route,
    RouteLeg,
    TopologyView,
    Transition,
    TraversalContext,
)


@dataclass(frozen=True, slots=True)
class NavigationPlanningError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class RecursiveRoutePlanner:
    def plan(
        self,
        topology: TopologyView,
        origin: Locator,
        destinations: tuple[Locator, ...],
        traversal_context: TraversalContext,
        *,
        allowed_transition_ids: frozenset[str] | None = None,
    ) -> Route:
        if not destinations:
            raise NavigationPlanningError("destination_has_no_locator")
        try:
            canonical_origin = topology.resolve(
                origin.space_id,
                origin.local_reference,
            )
        except (KeyError, ValueError) as error:
            raise NavigationPlanningError("invalid_origin_locator") from error
        canonical_destinations: list[Locator] = []
        for destination in destinations:
            try:
                canonical_destinations.append(
                    topology.resolve(
                        destination.space_id,
                        destination.local_reference,
                    )
                )
            except (KeyError, ValueError) as error:
                raise NavigationPlanningError(
                    "invalid_known_destination_locator"
                ) from error
        destination_by_space: dict[str, tuple[Locator, ...]] = {}
        for destination in sorted(set(canonical_destinations)):
            destination_by_space.setdefault(destination.space_id, ())
            destination_by_space[destination.space_id] = (
                *destination_by_space[destination.space_id],
                destination,
            )
        effective_context = (
            traversal_context
            if allowed_transition_ids is None
            else replace(
                traversal_context,
                allowed_transition_ids=allowed_transition_ids,
            )
        )

        pending: list[
            tuple[
                float,
                tuple[str, ...],
                int,
                Locator,
                tuple[RouteLeg, ...],
            ]
        ] = [(0.0, (), 0, canonical_origin, ())]
        sequence = 1
        best: dict[str, tuple[float, tuple[str, ...]]] = {
            canonical_origin.stable_key: (0.0, ())
        }
        selected: tuple[
            float,
            tuple[str, ...],
            tuple[RouteLeg, ...],
            Locator,
        ] | None = None

        while pending:
            cost, signature, _, current, route_legs = heapq.heappop(pending)
            if best.get(current.stable_key) != (cost, signature):
                continue
            for destination in destination_by_space.get(current.space_id, ()):
                local = topology.space(current.space_id).topology.plan_local_route(
                    current,
                    destination,
                    effective_context,
                )
                if local is None:
                    continue
                candidate_legs = (*route_legs, *local.legs)
                candidate_cost = cost + local.total_cost
                candidate_signature = (
                    *signature,
                    *(self._leg_signature(leg) for leg in local.legs),
                )
                candidate = (
                    candidate_cost,
                    candidate_signature,
                    candidate_legs,
                    destination,
                )
                if selected is None or candidate[:2] < selected[:2]:
                    selected = candidate

            for transition in topology.registered_transitions_from_space(
                current.space_id
            ):
                if not self._transition_allowed(
                    transition,
                    allowed_transition_ids,
                ):
                    continue
                local = topology.space(current.space_id).topology.plan_local_route(
                    current,
                    transition.from_locator,
                    effective_context,
                )
                if local is None:
                    continue
                transition_leg = RouteLeg(
                    origin=transition.from_locator,
                    destination=transition.to_locator,
                    traversal_kind=transition.traversal_kind,
                    executor_id=transition.executor_id,
                    cost=self._transition_cost(transition),
                    transition_id=transition.id,
                    metadata=transition.metadata,
                )
                next_legs = (*route_legs, *local.legs, transition_leg)
                next_cost = cost + local.total_cost + transition_leg.cost
                next_signature = (
                    *signature,
                    *(self._leg_signature(leg) for leg in local.legs),
                    self._leg_signature(transition_leg),
                )
                previous = best.get(transition.to_locator.stable_key)
                candidate_key = (next_cost, next_signature)
                if previous is not None and previous <= candidate_key:
                    continue
                best[transition.to_locator.stable_key] = candidate_key
                heapq.heappush(
                    pending,
                    (
                        next_cost,
                        next_signature,
                        sequence,
                        transition.to_locator,
                        next_legs,
                    ),
                )
                sequence += 1

        if selected is None:
            raise NavigationPlanningError("route_not_found")
        return Route(
            origin=canonical_origin,
            destination=selected[3],
            legs=selected[2],
            planned_from_topology_revision=topology.revision,
        )

    @staticmethod
    def _transition_allowed(
        transition: Transition,
        allowed_transition_ids: frozenset[str] | None,
    ) -> bool:
        if allowed_transition_ids is None:
            return True
        return (
            transition.id in allowed_transition_ids
            or transition.id.removesuffix(":reverse") in allowed_transition_ids
        )

    @staticmethod
    def _transition_cost(transition: Transition) -> float:
        value = transition.metadata.get("cost")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            return float(value)
        return 1.0

    @staticmethod
    def _leg_signature(leg: RouteLeg) -> str:
        return "|".join(
            (
                leg.transition_id or "",
                leg.origin.stable_key,
                leg.destination.stable_key,
                leg.traversal_kind,
                leg.executor_id,
            )
        )
