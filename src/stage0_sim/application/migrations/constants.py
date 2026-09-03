from typing import Final, Literal

from stage0_sim.application.migrations.models import ResourceKind

CHARACTER_SCHEMA_VERSION: Final[Literal[2]] = 2
ELEMENT_SCHEMA_VERSION: Final[Literal[4]] = 4
SCENARIO_SCHEMA_VERSION: Final[Literal[8]] = 8

CURRENT_SCHEMA_VERSIONS: dict[ResourceKind, int] = {
    ResourceKind.CHARACTER: CHARACTER_SCHEMA_VERSION,
    ResourceKind.ELEMENT: ELEMENT_SCHEMA_VERSION,
    ResourceKind.SCENARIO: SCENARIO_SCHEMA_VERSION,
}

SUPPORTED_SCHEMA_VERSIONS: dict[ResourceKind, frozenset[int]] = {
    ResourceKind.CHARACTER: frozenset({1, CHARACTER_SCHEMA_VERSION}),
    ResourceKind.ELEMENT: frozenset({1, 2, 3, ELEMENT_SCHEMA_VERSION}),
    ResourceKind.SCENARIO: frozenset(
        {4, 5, 6, 7, SCENARIO_SCHEMA_VERSION}
    ),
}
