from dataclasses import dataclass

from stage0_sim.application.navigation.knowledge import (
    KnownDestination,
    KnownTopologyProjection,
)


@dataclass(frozen=True, slots=True)
class DestinationResolutionError(ValueError):
    reason: str
    target_id: str

    def __str__(self) -> str:
        return f"{self.reason}: {self.target_id}"


class DestinationResolver:
    def resolve(
        self,
        projection: KnownTopologyProjection,
        character_id: str,
        target_id: str,
    ) -> KnownDestination:
        for destination in projection.destinations(character_id):
            if destination.id == target_id:
                if not destination.available:
                    raise DestinationResolutionError(
                        destination.availability_reason
                        or "destination_unavailable",
                        target_id,
                    )
                if not destination.locators:
                    raise DestinationResolutionError(
                        "known_destination_has_no_locator",
                        target_id,
                    )
                return destination
        raise DestinationResolutionError("unknown_destination", target_id)
