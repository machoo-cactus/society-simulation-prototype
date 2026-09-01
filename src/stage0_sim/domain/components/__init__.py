"""ECS component definitions."""

from stage0_sim.domain.components.affordance import (
    AffordanceExecutionComponent,
    AffordanceRequestComponent,
)
from stage0_sim.domain.components.cognition import (
    CharacterProfileComponent,
    CharacterSituationComponent,
    ControllerComponent,
    ConversationComponent,
)
from stage0_sim.domain.components.goals import (
    ActionOutcome,
    ActionOutcomeCriterion,
    EventMatchCriterion,
    GoalComparator,
    GoalCompletionPolicy,
    GoalComponent,
    GoalCriterion,
    GoalCriterionEffect,
    GoalDefinition,
    GoalEvidence,
    GoalLocationKind,
    GoalRuntime,
    GoalStateComponent,
    GoalStatus,
    InteractionCountCriterion,
    InteractionType,
    LocationMatchCriterion,
    PossessionThresholdCriterion,
    SimulationTimeCriterion,
    StateComparisonCriterion,
)
from stage0_sim.domain.components.information import InformationNamespaceComponent
from stage0_sim.domain.components.memory import MemoryComponent
from stage0_sim.domain.components.navigation import (
    NavigationComponent,
    NavigationPrimitive,
    NavigationPrimitiveKind,
    NavigationStatus,
)
from stage0_sim.domain.components.npcs import NpcComponent
from stage0_sim.domain.components.perception import (
    KnowledgeRecord,
    PerceptionComponent,
    SensesComponent,
)
from stage0_sim.domain.components.physiology import (
    ActivityComponent,
    ActivityRates,
    ActivityType,
    HomeostasisComponent,
    HomeostasisConfiguration,
    default_activity_rates,
)
from stage0_sim.domain.components.planning import (
    ActionGoalLink,
    ActionInstance,
    ActionOrigin,
    ActionType,
    GoalLinkKind,
    LineageIdGenerator,
    PlanAction,
    PlanComponent,
)
from stage0_sim.domain.components.possessions import (
    PossessionsComponent,
    TransactionExecutionComponent,
    TransactionRequestComponent,
)
from stage0_sim.domain.components.spatial import MovementComponent, PositionComponent
from stage0_sim.domain.components.speech import PendingSpeechComponent
from stage0_sim.domain.components.survival import (
    DriveComponent,
    DriveThreshold,
    DriveType,
    System1Configuration,
    System1State,
    default_drive_thresholds,
)
from stage0_sim.domain.components.travel import (
    SpatialLocationComponent,
    TravelComponent,
)

__all__ = [
    "ActionType",
    "ActionGoalLink",
    "ActionInstance",
    "ActionOrigin",
    "AffordanceExecutionComponent",
    "AffordanceRequestComponent",
    "ActivityComponent",
    "ActivityRates",
    "ActivityType",
    "CharacterProfileComponent",
    "CharacterSituationComponent",
    "ConversationComponent",
    "ControllerComponent",
    "DriveComponent",
    "DriveThreshold",
    "DriveType",
    "EventMatchCriterion",
    "GoalComparator",
    "GoalCompletionPolicy",
    "GoalComponent",
    "GoalCriterion",
    "GoalCriterionEffect",
    "GoalDefinition",
    "GoalEvidence",
    "GoalLocationKind",
    "GoalLinkKind",
    "GoalRuntime",
    "GoalStateComponent",
    "GoalStatus",
    "HomeostasisComponent",
    "HomeostasisConfiguration",
    "InformationNamespaceComponent",
    "InteractionCountCriterion",
    "InteractionType",
    "MemoryComponent",
    "LineageIdGenerator",
    "MovementComponent",
    "NavigationComponent",
    "NavigationPrimitive",
    "NavigationPrimitiveKind",
    "NavigationStatus",
    "NpcComponent",
    "KnowledgeRecord",
    "PendingSpeechComponent",
    "PerceptionComponent",
    "PlanAction",
    "PlanComponent",
    "PositionComponent",
    "PossessionsComponent",
    "PossessionThresholdCriterion",
    "SensesComponent",
    "SpatialLocationComponent",
    "SimulationTimeCriterion",
    "StateComparisonCriterion",
    "System1Configuration",
    "System1State",
    "TravelComponent",
    "TransactionExecutionComponent",
    "TransactionRequestComponent",
    "ActionOutcome",
    "ActionOutcomeCriterion",
    "LocationMatchCriterion",
    "default_activity_rates",
    "default_drive_thresholds",
]
