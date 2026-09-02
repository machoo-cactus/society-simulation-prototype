import io
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

DATABASE_SCHEMA_VERSION = 10
@contextmanager
def schema_initialization_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.schema.lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, io.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import importlib

            fcntl = importlib.import_module("fcntl")
            flock = cast(
                Callable[[int, int], object],
                fcntl.__dict__["flock"],
            )
            lock_ex = cast(int, fcntl.__dict__["LOCK_EX"])
            lock_un = cast(int, fcntl.__dict__["LOCK_UN"])
            flock(handle.fileno(), lock_ex)
            try:
                yield
            finally:
                flock(handle.fileno(), lock_un)

# Child tables precede their parents. This is the complete deletion and schema
# coverage registry for every SQLite table containing a run_id column.
RUN_SCOPED_TABLES = (
    "perception_deliveries",
    "interaction_participants",
    "interaction_events",
    "goal_action_links",
    "action_episodes",
    "action_transitions",
    "decision_options",
    "model_turns",
    "goal_transitions",
    "memory_relations",
    "decision_episodes",
    "goal_episodes",
    "interaction_episodes",
    "memory_operations",
    "information_retrievals",
    "physical_relation_samples",
    "physical_object_states",
    "state_samples",
    "state_deltas",
    "plans",
    "opportunity_samples",
    "transition_samples",
    "population_samples",
    "resource_samples",
    "resource_flows",
    "tool_executions",
    "perception_facts",
    "action_instances",
    "decisions",
    "model_requests",
    "goals",
    "interactions",
    "record_relations",
    "records",
    "episodic_memories",
    "information_documents",
    "runs",
)


_ANALYSIS_CORE_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS records_run_record_id
ON records(run_id, record_id);
CREATE INDEX IF NOT EXISTS records_run_category_tick
ON records(run_id, category, simulation_tick);
CREATE INDEX IF NOT EXISTS records_run_schema_tick
ON records(run_id, schema_id, schema_version, simulation_tick);
CREATE INDEX IF NOT EXISTS records_run_subject_tick
ON records(run_id, subject_id, simulation_tick);
CREATE INDEX IF NOT EXISTS records_run_visibility_sequence
ON records(run_id, visibility, sequence);

CREATE TABLE IF NOT EXISTS record_relations (
    run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (
        run_id, record_id, relation_type, target_type, target_id, ordinal
    ),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS record_relations_target
ON record_relations(run_id, target_type, target_id, relation_type);

CREATE TABLE IF NOT EXISTS state_samples (
    run_id TEXT NOT NULL,
    state_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    phase TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    simulation_time REAL NOT NULL,
    state_json TEXT NOT NULL,
    PRIMARY KEY (run_id, state_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS state_samples_subject_tick
ON state_samples(run_id, subject_id, simulation_tick, phase);

CREATE TABLE IF NOT EXISTS state_deltas (
    run_id TEXT NOT NULL,
    state_delta_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    from_sample_id TEXT,
    to_sample_id TEXT,
    simulation_tick INTEGER NOT NULL,
    delta_json TEXT NOT NULL,
    PRIMARY KEY (run_id, state_delta_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS state_deltas_subject_tick
ON state_deltas(run_id, subject_id, simulation_tick);

CREATE TABLE IF NOT EXISTS physical_object_states (
    run_id TEXT NOT NULL,
    physical_state_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    name TEXT NOT NULL,
    room_id TEXT NOT NULL,
    anchor_x INTEGER NOT NULL,
    anchor_y INTEGER NOT NULL,
    orientation TEXT NOT NULL,
    phase TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    simulation_time REAL NOT NULL,
    movement_obstruction TEXT NOT NULL,
    vision_obstruction TEXT NOT NULL,
    hearing_transmission TEXT NOT NULL,
    smell_transmission TEXT NOT NULL,
    blocks_movement INTEGER NOT NULL,
    blocks_vision INTEGER NOT NULL,
    blocks_hearing INTEGER NOT NULL,
    blocks_smell INTEGER NOT NULL,
    mass_kg REAL,
    size_class TEXT,
    is_open INTEGER,
    is_locked INTEGER,
    parent_id TEXT,
    relation_kind TEXT,
    slot_id TEXT,
    custodian_id TEXT,
    held_by_id TEXT,
    spatial_index_revision INTEGER,
    topology_revision INTEGER,
    state_json TEXT NOT NULL,
    PRIMARY KEY (run_id, physical_state_id),
    UNIQUE (run_id, object_id, simulation_tick, phase),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS physical_object_states_object_tick
ON physical_object_states(run_id, object_id, simulation_tick, phase);
CREATE INDEX IF NOT EXISTS physical_object_states_room_tick
ON physical_object_states(run_id, room_id, simulation_tick, phase, object_id);
CREATE INDEX IF NOT EXISTS physical_object_states_parent_relation
ON physical_object_states(
    run_id, parent_id, relation_kind, simulation_tick, phase
);
CREATE INDEX IF NOT EXISTS physical_object_states_custody
ON physical_object_states(
    run_id, custodian_id, held_by_id, simulation_tick, phase
);
CREATE INDEX IF NOT EXISTS physical_object_states_open_locked
ON physical_object_states(
    run_id, is_open, is_locked, simulation_tick, phase
);

CREATE TABLE IF NOT EXISTS physical_relation_samples (
    run_id TEXT NOT NULL,
    relation_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    room_id TEXT,
    parent_id TEXT NOT NULL,
    parent_kind TEXT NOT NULL,
    relation_kind TEXT NOT NULL,
    slot_id TEXT,
    custodian_id TEXT,
    held_by_id TEXT,
    phase TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    simulation_time REAL NOT NULL,
    spatial_index_revision INTEGER,
    topology_revision INTEGER,
    relation_json TEXT NOT NULL,
    PRIMARY KEY (run_id, relation_sample_id),
    UNIQUE (run_id, object_id, simulation_tick, phase),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS physical_relation_samples_object_tick
ON physical_relation_samples(run_id, object_id, simulation_tick, phase);
CREATE INDEX IF NOT EXISTS physical_relation_samples_parent_kind
ON physical_relation_samples(
    run_id, parent_id, relation_kind, simulation_tick, phase, object_id
);
CREATE INDEX IF NOT EXISTS physical_relation_samples_room
ON physical_relation_samples(
    run_id, room_id, simulation_tick, phase, relation_kind
);
CREATE INDEX IF NOT EXISTS physical_relation_samples_custody
ON physical_relation_samples(
    run_id, custodian_id, held_by_id, simulation_tick, phase
);

CREATE TABLE IF NOT EXISTS goals (
    run_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    goal_json TEXT NOT NULL,
    PRIMARY KEY (run_id, goal_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS goals_subject_status
ON goals(run_id, subject_id, status);

CREATE TABLE IF NOT EXISTS goal_transitions (
    run_id TEXT NOT NULL,
    goal_transition_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    transition_json TEXT NOT NULL,
    PRIMARY KEY (run_id, goal_transition_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, goal_id) REFERENCES goals(run_id, goal_id)
);
CREATE INDEX IF NOT EXISTS goal_transitions_goal_tick
ON goal_transitions(run_id, goal_id, simulation_tick);

CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    simulation_tick INTEGER NOT NULL,
    status TEXT NOT NULL,
    selected_option_id TEXT,
    context_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    PRIMARY KEY (run_id, decision_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS decisions_subject_tick
ON decisions(run_id, subject_id, simulation_tick, status);

CREATE TABLE IF NOT EXISTS decision_options (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    option_index INTEGER NOT NULL,
    option_type TEXT NOT NULL,
    selected INTEGER NOT NULL,
    option_json TEXT NOT NULL,
    PRIMARY KEY (run_id, decision_id, option_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, decision_id) REFERENCES decisions(run_id, decision_id)
);
CREATE INDEX IF NOT EXISTS decision_options_selected
ON decision_options(run_id, decision_id, selected, option_index);

CREATE TABLE IF NOT EXISTS model_requests (
    run_id TEXT NOT NULL,
    model_request_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    decision_id TEXT,
    subject_id TEXT,
    operation TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (run_id, model_request_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS model_requests_subject_status
ON model_requests(run_id, subject_id, operation, status);

CREATE TABLE IF NOT EXISTS model_turns (
    run_id TEXT NOT NULL,
    model_request_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    record_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    PRIMARY KEY (run_id, model_request_id, turn_index),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, model_request_id)
        REFERENCES model_requests(run_id, model_request_id)
);

CREATE TABLE IF NOT EXISTS tool_executions (
    run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    decision_id TEXT,
    action_id TEXT,
    subject_id TEXT,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    PRIMARY KEY (run_id, tool_call_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS tool_executions_subject_status
ON tool_executions(run_id, subject_id, tool_name, status);

CREATE TABLE IF NOT EXISTS action_instances (
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    plan_id TEXT,
    goal_id TEXT,
    decision_id TEXT,
    tool_call_id TEXT,
    subject_id TEXT,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    plan_revision INTEGER,
    created_tick INTEGER NOT NULL,
    created_at REAL NOT NULL,
    root_correlation_id TEXT NOT NULL,
    action_json TEXT NOT NULL,
    PRIMARY KEY (run_id, action_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS action_instances_subject_status
ON action_instances(run_id, subject_id, action_type, status);

CREATE TABLE IF NOT EXISTS action_transitions (
    run_id TEXT NOT NULL,
    action_transition_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    transition_json TEXT NOT NULL,
    PRIMARY KEY (run_id, action_transition_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, action_id)
        REFERENCES action_instances(run_id, action_id)
);
CREATE INDEX IF NOT EXISTS action_transitions_action_tick
ON action_transitions(run_id, action_id, simulation_tick);

CREATE TABLE IF NOT EXISTS interactions (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    interaction_verb TEXT,
    actor_id TEXT,
    target_id TEXT,
    destination_id TEXT,
    slot_id TEXT,
    goal_id TEXT,
    action_id TEXT,
    decision_id TEXT,
    tool_call_id TEXT,
    correlation_id TEXT,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER,
    status TEXT NOT NULL,
    context_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS interactions_type_status
ON interactions(
    run_id, interaction_type, interaction_verb, status, start_tick
);
CREATE INDEX IF NOT EXISTS interactions_physical_target
ON interactions(
    run_id, target_id, destination_id, interaction_verb, start_tick
);
CREATE INDEX IF NOT EXISTS interactions_actor
ON interactions(run_id, actor_id, interaction_type, start_tick);
CREATE INDEX IF NOT EXISTS interactions_lineage
ON interactions(
    run_id, action_id, decision_id, tool_call_id, correlation_id
);

CREATE TABLE IF NOT EXISTS interaction_participants (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    role TEXT NOT NULL,
    participant_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id, participant_id, role),
    FOREIGN KEY (run_id, interaction_id)
        REFERENCES interactions(run_id, interaction_id)
);
CREATE INDEX IF NOT EXISTS interaction_participants_participant
ON interaction_participants(run_id, participant_id, interaction_id);

CREATE TABLE IF NOT EXISTS interaction_events (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id, event_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id),
    FOREIGN KEY (run_id, interaction_id)
        REFERENCES interactions(run_id, interaction_id)
);
CREATE INDEX IF NOT EXISTS interaction_events_tick
ON interaction_events(run_id, interaction_id, simulation_tick, event_index);

CREATE TABLE IF NOT EXISTS opportunity_samples (
    run_id TEXT NOT NULL,
    opportunity_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    simulation_tick INTEGER NOT NULL,
    selected_option_id TEXT,
    context_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    PRIMARY KEY (run_id, opportunity_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS opportunity_samples_subject_tick
ON opportunity_samples(run_id, subject_id, simulation_tick);

CREATE TABLE IF NOT EXISTS transition_samples (
    run_id TEXT NOT NULL,
    transition_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    action_id TEXT,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    elapsed_simulation_time REAL NOT NULL,
    outcome TEXT NOT NULL,
    state_before_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    exogenous_context_json TEXT NOT NULL,
    state_after_json TEXT NOT NULL,
    PRIMARY KEY (run_id, transition_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS transition_samples_subject_outcome
ON transition_samples(run_id, subject_id, outcome, start_tick);

CREATE TABLE IF NOT EXISTS population_samples (
    run_id TEXT NOT NULL,
    population_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    phase TEXT NOT NULL,
    population_json TEXT NOT NULL,
    PRIMARY KEY (run_id, population_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS population_samples_tick_phase
ON population_samples(run_id, simulation_tick, phase);
"""


_LINEAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    run_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    revision INTEGER NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    root_correlation_id TEXT,
    plan_json TEXT NOT NULL,
    PRIMARY KEY (run_id, plan_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS plans_subject_status
ON plans(run_id, subject_id, status, revision);

CREATE TABLE IF NOT EXISTS goal_action_links (
    run_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    link_kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (run_id, goal_id, action_id),
    FOREIGN KEY (run_id, goal_id) REFERENCES goals(run_id, goal_id),
    FOREIGN KEY (run_id, action_id)
        REFERENCES action_instances(run_id, action_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS goal_action_links_action
ON goal_action_links(run_id, action_id, link_kind, ordinal);

CREATE TABLE IF NOT EXISTS action_episodes (
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    terminal_status TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    created_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    elapsed_simulation_time REAL NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, action_id),
    FOREIGN KEY (run_id, action_id)
        REFERENCES action_instances(run_id, action_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS action_episodes_subject_status
ON action_episodes(run_id, subject_id, terminal_status, terminal_tick);
"""


_DERIVED_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_episodes (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    action_id TEXT,
    goal_id TEXT,
    tool_call_id TEXT,
    status TEXT NOT NULL,
    selected_option_id TEXT,
    requested_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    requested_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    terminal_reason TEXT,
    delays_json TEXT NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, decision_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS decision_episodes_subject_status
ON decision_episodes(run_id, subject_id, status, requested_tick);

CREATE TABLE IF NOT EXISTS memory_operations (
    run_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    memory_id TEXT,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, operation_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS memory_operations_subject_status
ON memory_operations(run_id, subject_id, operation_type, status);

CREATE TABLE IF NOT EXISTS information_retrievals (
    run_id TEXT NOT NULL,
    retrieval_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    status TEXT NOT NULL,
    query_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, retrieval_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS information_retrievals_subject_status
ON information_retrievals(run_id, subject_id, status);

CREATE TABLE IF NOT EXISTS interaction_episodes (
    run_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    interaction_verb TEXT,
    actor_id TEXT,
    target_id TEXT,
    destination_id TEXT,
    slot_id TEXT,
    status TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    started_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    duration REAL NOT NULL,
    initiating_goal_id TEXT,
    initiating_decision_id TEXT,
    initiating_action_id TEXT,
    initiating_tool_call_id TEXT,
    correlation_id TEXT,
    content_visibility TEXT NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS interaction_episodes_type_status
ON interaction_episodes(
    run_id, interaction_type, interaction_verb, status, start_tick
);
CREATE INDEX IF NOT EXISTS interaction_episodes_physical_target
ON interaction_episodes(
    run_id, target_id, destination_id, interaction_verb, terminal_tick
);
CREATE INDEX IF NOT EXISTS interaction_episodes_lineage
ON interaction_episodes(
    run_id, initiating_action_id, initiating_decision_id,
    initiating_tool_call_id, correlation_id
);

CREATE TABLE IF NOT EXISTS perception_facts (
    run_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    source_event_id TEXT,
    fact_type TEXT NOT NULL,
    subject_id TEXT,
    object_id TEXT,
    location_id TEXT,
    modality TEXT NOT NULL,
    disclosure TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    fact_json TEXT NOT NULL,
    PRIMARY KEY (run_id, fact_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS perception_facts_subject_tick
ON perception_facts(run_id, subject_id, created_tick, fact_type);

CREATE TABLE IF NOT EXISTS perception_deliveries (
    run_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    observer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    perceived_tick INTEGER NOT NULL,
    fact_age REAL NOT NULL,
    salience REAL,
    delivery_json TEXT NOT NULL,
    PRIMARY KEY (run_id, delivery_id),
    FOREIGN KEY (run_id, fact_id) REFERENCES perception_facts(run_id, fact_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS perception_deliveries_observer_tick
ON perception_deliveries(run_id, observer_id, perceived_tick, status);

CREATE TABLE IF NOT EXISTS goal_episodes (
    run_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    subject_id TEXT,
    terminal_status TEXT NOT NULL,
    activated_tick INTEGER NOT NULL,
    terminal_tick INTEGER NOT NULL,
    activated_at REAL NOT NULL,
    terminal_at REAL NOT NULL,
    duration REAL NOT NULL,
    episode_json TEXT NOT NULL,
    PRIMARY KEY (run_id, goal_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS goal_episodes_subject_status
ON goal_episodes(run_id, subject_id, terminal_status, activated_tick);

CREATE TABLE IF NOT EXISTS resource_samples (
    run_id TEXT NOT NULL,
    resource_sample_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    phase TEXT NOT NULL,
    capacity INTEGER,
    occupancy INTEGER NOT NULL,
    queue_length INTEGER NOT NULL,
    utilization REAL,
    sample_json TEXT NOT NULL,
    PRIMARY KEY (run_id, resource_sample_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS resource_samples_resource_tick
ON resource_samples(run_id, resource_id, simulation_tick, phase);

CREATE TABLE IF NOT EXISTS resource_flows (
    run_id TEXT NOT NULL,
    resource_flow_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    subject_id TEXT,
    simulation_tick INTEGER NOT NULL,
    flow_type TEXT NOT NULL,
    amount REAL,
    flow_json TEXT NOT NULL,
    PRIMARY KEY (run_id, resource_flow_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS resource_flows_resource_tick
ON resource_flows(run_id, resource_id, simulation_tick, flow_type);

CREATE TABLE IF NOT EXISTS memory_relations (
    run_id TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    subject_id TEXT,
    relation_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    relation_json TEXT NOT NULL,
    PRIMARY KEY (run_id, relation_id),
    FOREIGN KEY (run_id, record_id) REFERENCES records(run_id, record_id)
);
CREATE INDEX IF NOT EXISTS memory_relations_memory_type
ON memory_relations(run_id, memory_id, relation_type);
"""


_CURRENT_SCHEMA = f"""
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    dt REAL NOT NULL,
    initial_speed REAL NOT NULL,
    scenario_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    final_tick INTEGER,
    final_simulation_time REAL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    owner_instance_id TEXT,
    capture_complete INTEGER NOT NULL DEFAULT 0,
    interruption_reason TEXT
);
CREATE INDEX runs_status_started
ON runs(status, started_at DESC, run_id DESC);
CREATE INDEX runs_completed
ON runs(completed_at DESC, run_id DESC);
CREATE INDEX runs_schema_started
ON runs(schema_version, started_at DESC, run_id DESC);
CREATE INDEX runs_capture_started
ON runs(capture_complete, started_at DESC, run_id DESC);

CREATE TABLE records (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    record_id TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    record_type TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    phase TEXT NOT NULL,
    simulation_tick INTEGER NOT NULL,
    simulation_time REAL NOT NULL,
    wall_time TEXT,
    visibility TEXT NOT NULL,
    subject_id TEXT,
    related_entity_ids_json TEXT NOT NULL,
    source_event_id TEXT,
    causation_id TEXT,
    correlation_id TEXT,
    goal_id TEXT,
    plan_id TEXT,
    action_id TEXT,
    decision_id TEXT,
    model_request_id TEXT,
    tool_call_id TEXT,
    interaction_id TEXT,
    perception_fact_id TEXT,
    memory_id TEXT,
    transaction_request_id TEXT,
    operator_intervention_id TEXT,
    source_metadata_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX records_run_type_tick
ON records(run_id, record_type, simulation_tick);

CREATE TABLE episodic_memories (
    run_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    simulation_time REAL NOT NULL,
    importance REAL NOT NULL,
    text TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (run_id, memory_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX memories_run_agent_time
ON episodic_memories(run_id, agent_id, simulation_time);

CREATE TABLE information_documents (
    run_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    namespace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_json TEXT NOT NULL,
    PRIMARY KEY (run_id, document_id, revision),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX information_run_namespace_kind
ON information_documents(run_id, namespace_id, kind);

CREATE TABLE dataset_store_instances (
    instance_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX dataset_store_instances_lease
ON dataset_store_instances(closed_at, heartbeat_at);

{_ANALYSIS_CORE_SCHEMA}
{_LINEAGE_SCHEMA}
{_DERIVED_SCHEMA}
PRAGMA user_version = {DATABASE_SCHEMA_VERSION};
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current == DATABASE_SCHEMA_VERSION:
        validate_current_schema(connection)
        return
    if current != 0:
        raise RuntimeError(
            f"unsupported SQLite schema version {current}; expected "
            f"{DATABASE_SCHEMA_VERSION}. Existing databases are not migrated."
        )
    existing_objects = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    if existing_objects:
        raise RuntimeError(
            "unsupported SQLite schema version 0; expected "
            f"{DATABASE_SCHEMA_VERSION}. Existing databases are not migrated."
        )
    connection.executescript(_CURRENT_SCHEMA)
    connection.commit()
    validate_current_schema(connection)


def validate_current_schema(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    required = {*RUN_SCOPED_TABLES, "dataset_store_instances"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(
            f"SQLite schema version {DATABASE_SCHEMA_VERSION} is incomplete; "
            f"missing tables: {', '.join(missing)}"
        )
