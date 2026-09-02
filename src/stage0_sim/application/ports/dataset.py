from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from stage0_sim.application.data_capture import (
    DatasetRecord,
    RecordRelation,
    RunnerPhase,
)
from stage0_sim.application.data_management import DatasetManagementRepository
from stage0_sim.application.data_query import DatasetQueryRepository
from stage0_sim.application.memory import MemoryPersistence
from stage0_sim.domain.events import JsonValue


class DatasetCaptureRepository(MemoryPersistence, Protocol):
    def begin_run(
        self,
        *,
        run_id: str,
        seed: int,
        dt: float,
        initial_speed: float,
        scenario: dict[str, JsonValue],
    ) -> None: ...

    def append(self, record: DatasetRecord) -> None: ...

    def add_record_relation(self, relation: RecordRelation) -> None: ...

    def append_state_sample(
        self,
        *,
        run_id: str,
        state_sample_id: str,
        record_id: str,
        subject_id: str | None,
        phase: RunnerPhase,
        simulation_tick: int,
        simulation_time: float,
        state: dict[str, JsonValue],
    ) -> None: ...

    def append_state_delta(
        self,
        *,
        run_id: str,
        state_delta_id: str,
        record_id: str,
        subject_id: str | None,
        from_sample_id: str | None,
        to_sample_id: str | None,
        simulation_tick: int,
        delta: dict[str, JsonValue],
    ) -> None: ...

    def append_physical_object_state(
        self,
        *,
        run_id: str,
        physical_state_id: str,
        record_id: str,
        object_id: str,
        definition_id: str,
        name: str,
        room_id: str,
        anchor_x: int,
        anchor_y: int,
        orientation: str,
        phase: RunnerPhase,
        simulation_tick: int,
        simulation_time: float,
        movement_obstruction: str,
        vision_obstruction: str,
        hearing_transmission: str,
        smell_transmission: str,
        blocks_movement: bool,
        blocks_vision: bool,
        blocks_hearing: bool,
        blocks_smell: bool,
        mass_kg: float | None,
        size_class: str | None,
        is_open: bool | None,
        is_locked: bool | None,
        parent_id: str | None,
        relation_kind: str | None,
        slot_id: str | None,
        custodian_id: str | None,
        held_by_id: str | None,
        spatial_index_revision: int | None,
        topology_revision: int | None,
        state: dict[str, JsonValue],
    ) -> None: ...

    def append_physical_relation_sample(
        self,
        *,
        run_id: str,
        relation_sample_id: str,
        record_id: str,
        object_id: str,
        entity_kind: str,
        room_id: str | None,
        parent_id: str,
        parent_kind: str,
        relation_kind: str,
        slot_id: str | None,
        custodian_id: str | None,
        held_by_id: str | None,
        phase: RunnerPhase,
        simulation_tick: int,
        simulation_time: float,
        spatial_index_revision: int | None,
        topology_revision: int | None,
        relation: dict[str, JsonValue],
    ) -> None: ...

    def append_goal(
        self,
        *,
        run_id: str,
        goal_id: str,
        record_id: str,
        subject_id: str | None,
        description: str,
        status: str,
        goal: dict[str, JsonValue],
    ) -> None: ...

    def append_goal_transition(
        self,
        *,
        run_id: str,
        goal_transition_id: str,
        record_id: str,
        goal_id: str,
        simulation_tick: int,
        from_status: str | None,
        to_status: str,
        transition: dict[str, JsonValue],
    ) -> None: ...

    def append_decision(
        self,
        *,
        run_id: str,
        decision_id: str,
        record_id: str,
        subject_id: str | None,
        simulation_tick: int,
        status: str,
        selected_option_id: str | None,
        context: dict[str, JsonValue],
        outcome: dict[str, JsonValue] | None = None,
    ) -> None: ...

    def append_decision_option(
        self,
        *,
        run_id: str,
        decision_id: str,
        option_id: str,
        record_id: str,
        option_index: int,
        option_type: str,
        selected: bool,
        option: dict[str, JsonValue],
    ) -> None: ...

    def append_model_request(
        self,
        *,
        run_id: str,
        model_request_id: str,
        record_id: str,
        decision_id: str | None,
        subject_id: str | None,
        operation: str,
        provider: str | None,
        model: str | None,
        status: str,
        request: dict[str, JsonValue],
        response: dict[str, JsonValue] | None = None,
    ) -> None: ...

    def append_model_turn(
        self,
        *,
        run_id: str,
        model_request_id: str,
        turn_index: int,
        record_id: str,
        role: str,
        content: dict[str, JsonValue],
        usage: dict[str, JsonValue] | None = None,
    ) -> None: ...

    def append_tool_execution(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        record_id: str,
        decision_id: str | None,
        action_id: str | None,
        subject_id: str | None,
        tool_name: str,
        status: str,
        input_data: dict[str, JsonValue],
        output_data: dict[str, JsonValue] | None = None,
    ) -> None: ...

    def append_decision_episode(
        self,
        *,
        run_id: str,
        decision_id: str,
        record_id: str,
        subject_id: str | None,
        action_id: str | None,
        goal_id: str | None,
        tool_call_id: str | None,
        status: str,
        selected_option_id: str | None,
        requested_tick: int,
        terminal_tick: int,
        requested_at: float,
        terminal_at: float,
        terminal_reason: str | None,
        delays: dict[str, JsonValue],
        episode: dict[str, JsonValue],
    ) -> None: ...

    def append_memory_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
        record_id: str,
        subject_id: str | None,
        operation_type: str,
        status: str,
        memory_id: str | None,
        request: dict[str, JsonValue],
        result: dict[str, JsonValue],
    ) -> None: ...

    def append_information_retrieval(
        self,
        *,
        run_id: str,
        retrieval_id: str,
        record_id: str,
        subject_id: str | None,
        status: str,
        query: dict[str, JsonValue],
        result: dict[str, JsonValue],
    ) -> None: ...

    def append_action_instance(
        self,
        *,
        run_id: str,
        action_id: str,
        record_id: str,
        plan_id: str | None,
        goal_id: str | None,
        decision_id: str | None,
        tool_call_id: str | None,
        subject_id: str | None,
        action_type: str,
        status: str,
        origin: str,
        plan_revision: int | None,
        created_tick: int,
        created_at: float,
        root_correlation_id: str,
        action: dict[str, JsonValue],
    ) -> None: ...

    def append_action_transition(
        self,
        *,
        run_id: str,
        action_transition_id: str,
        record_id: str,
        action_id: str,
        simulation_tick: int,
        from_status: str | None,
        to_status: str,
        transition: dict[str, JsonValue],
    ) -> None: ...

    def append_plan(
        self,
        *,
        run_id: str,
        plan_id: str,
        record_id: str,
        subject_id: str | None,
        revision: int,
        origin: str,
        status: str,
        root_correlation_id: str | None,
        plan: dict[str, JsonValue],
    ) -> None: ...

    def append_goal_action_link(
        self,
        *,
        run_id: str,
        goal_id: str,
        action_id: str,
        record_id: str,
        link_kind: str,
        ordinal: int,
    ) -> None: ...

    def append_action_episode(
        self,
        *,
        run_id: str,
        action_id: str,
        record_id: str,
        subject_id: str | None,
        terminal_status: str,
        created_tick: int,
        terminal_tick: int,
        created_at: float,
        terminal_at: float,
        elapsed_simulation_time: float,
        source_event_ids: tuple[str, ...],
        episode: dict[str, JsonValue],
    ) -> None: ...

    def append_interaction(
        self,
        *,
        run_id: str,
        interaction_id: str,
        record_id: str,
        interaction_type: str,
        start_tick: int,
        end_tick: int | None,
        status: str,
        context: dict[str, JsonValue],
        outcome: dict[str, JsonValue] | None = None,
        interaction_verb: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        destination_id: str | None = None,
        slot_id: str | None = None,
        goal_id: str | None = None,
        action_id: str | None = None,
        decision_id: str | None = None,
        tool_call_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def append_interaction_participant(
        self,
        *,
        run_id: str,
        interaction_id: str,
        participant_id: str,
        role: str,
        participant: dict[str, JsonValue],
    ) -> None: ...

    def append_interaction_event(
        self,
        *,
        run_id: str,
        interaction_id: str,
        event_id: str,
        record_id: str,
        event_index: int,
        event_type: str,
        simulation_tick: int,
        event: dict[str, JsonValue],
    ) -> None: ...

    def append_interaction_episode(
        self,
        *,
        run_id: str,
        interaction_id: str,
        record_id: str,
        interaction_type: str,
        status: str,
        start_tick: int,
        terminal_tick: int,
        started_at: float,
        terminal_at: float,
        duration: float,
        initiating_goal_id: str | None,
        initiating_decision_id: str | None,
        initiating_action_id: str | None,
        initiating_tool_call_id: str | None,
        content_visibility: str,
        episode: dict[str, JsonValue],
        interaction_verb: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        destination_id: str | None = None,
        slot_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def append_perception_fact(
        self,
        *,
        run_id: str,
        fact_id: str,
        record_id: str,
        source_event_id: str | None,
        fact_type: str,
        subject_id: str | None,
        object_id: str | None,
        location_id: str | None,
        modality: str,
        disclosure: str,
        created_tick: int,
        fact: dict[str, JsonValue],
    ) -> None: ...

    def append_perception_delivery(
        self,
        *,
        run_id: str,
        delivery_id: str,
        fact_id: str,
        record_id: str,
        observer_id: str,
        status: str,
        reason: str | None,
        perceived_tick: int,
        fact_age: float,
        salience: float | None,
        delivery: dict[str, JsonValue],
    ) -> None: ...

    def append_goal_episode(
        self,
        *,
        run_id: str,
        goal_id: str,
        record_id: str,
        subject_id: str | None,
        terminal_status: str,
        activated_tick: int,
        terminal_tick: int,
        activated_at: float,
        terminal_at: float,
        duration: float,
        episode: dict[str, JsonValue],
    ) -> None: ...

    def append_resource_sample(
        self,
        *,
        run_id: str,
        resource_sample_id: str,
        record_id: str,
        resource_id: str,
        resource_type: str,
        simulation_tick: int,
        phase: RunnerPhase,
        capacity: int | None,
        occupancy: int,
        queue_length: int,
        utilization: float | None,
        sample: dict[str, JsonValue],
    ) -> None: ...

    def append_resource_flow(
        self,
        *,
        run_id: str,
        resource_flow_id: str,
        record_id: str,
        resource_id: str,
        subject_id: str | None,
        simulation_tick: int,
        flow_type: str,
        amount: float | None,
        flow: dict[str, JsonValue],
    ) -> None: ...

    def append_memory_relation(
        self,
        *,
        run_id: str,
        relation_id: str,
        record_id: str,
        memory_id: str,
        subject_id: str | None,
        relation_type: str,
        source_type: str,
        source_id: str,
        relation: dict[str, JsonValue],
    ) -> None: ...

    def append_opportunity_sample(
        self,
        *,
        run_id: str,
        opportunity_sample_id: str,
        record_id: str,
        subject_id: str | None,
        simulation_tick: int,
        selected_option_id: str | None,
        context: dict[str, JsonValue],
        options: list[JsonValue],
    ) -> None: ...

    def append_transition_sample(
        self,
        *,
        run_id: str,
        transition_sample_id: str,
        record_id: str,
        subject_id: str | None,
        action_id: str | None,
        start_tick: int,
        end_tick: int,
        elapsed_simulation_time: float,
        outcome: str,
        state_before: dict[str, JsonValue],
        action: dict[str, JsonValue],
        exogenous_context: dict[str, JsonValue],
        state_after: dict[str, JsonValue],
    ) -> None: ...

    def append_population_sample(
        self,
        *,
        run_id: str,
        population_sample_id: str,
        record_id: str,
        simulation_tick: int,
        phase: RunnerPhase,
        population: dict[str, JsonValue],
    ) -> None: ...

    def flush(self) -> None: ...

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        final_tick: int,
        final_simulation_time: float,
    ) -> None: ...


class DatasetStore(
    DatasetCaptureRepository,
    DatasetQueryRepository,
    DatasetManagementRepository,
    Protocol,
):
    path: Path

    def persisted_events(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        include_private: bool = False,
    ) -> tuple[tuple[dict[str, JsonValue], ...], int]: ...

    def iter_jsonl(self, run_id: str) -> Iterator[str]: ...

    def close(self) -> None: ...
