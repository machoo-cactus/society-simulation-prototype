import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from stage0_sim.application.dataset import DATASET_SCHEMA_VERSION, DatasetRecord
from stage0_sim.application.memory import MemoryRecord
from stage0_sim.domain.events import JsonValue

_DATABASE_SCHEMA_VERSION = 2


class SQLiteDatasetStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def begin_run(
        self,
        *,
        run_id: str,
        seed: int,
        dt: float,
        initial_speed: float,
        scenario: dict[str, JsonValue],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO runs (
                run_id, schema_version, seed, dt, initial_speed,
                scenario_json, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                DATASET_SCHEMA_VERSION,
                seed,
                dt,
                initial_speed,
                _json(scenario),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._connection.commit()

    def append(self, record: DatasetRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO records (
                run_id, sequence, schema_version, record_type,
                simulation_tick, simulation_time, agent_id,
                source_event_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.sequence,
                record.schema_version,
                record.record_type,
                record.simulation_tick,
                record.simulation_time,
                record.agent_id,
                record.source_event_id,
                _json(record.payload),
            ),
        )

    def flush(self) -> None:
        self._connection.commit()

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        final_tick: int,
        final_simulation_time: float,
    ) -> None:
        self._connection.execute(
            """
            UPDATE runs
            SET status = ?, final_tick = ?, final_simulation_time = ?,
                completed_at = ?
            WHERE run_id = ?
            """,
            (
                status,
                final_tick,
                final_simulation_time,
                datetime.now(UTC).isoformat(),
                run_id,
            ),
        )
        self._connection.commit()

    def iter_jsonl(self, run_id: str) -> Iterator[str]:
        export_connection = sqlite3.connect(self.path)
        export_connection.row_factory = sqlite3.Row
        try:
            run = export_connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown persisted run: {run_id}")
            manifest: dict[str, JsonValue] = {
                "schema_version": str(run["schema_version"]),
                "record_type": "run",
                "run_id": str(run["run_id"]),
                "sequence": 0,
                "simulation_tick": 0,
                "simulation_time": 0.0,
                "payload": {
                    "seed": int(run["seed"]),
                    "dt": float(run["dt"]),
                    "initial_speed": float(run["initial_speed"]),
                    "scenario": json.loads(str(run["scenario_json"])),
                    "status": str(run["status"]),
                    "final_tick": (
                        int(run["final_tick"])
                        if run["final_tick"] is not None
                        else None
                    ),
                    "final_simulation_time": (
                        float(run["final_simulation_time"])
                        if run["final_simulation_time"] is not None
                        else None
                    ),
                },
            }
            yield _json(manifest)
            rows = export_connection.execute(
                """
                SELECT * FROM records
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            )
            for row in rows:
                record = DatasetRecord(
                    run_id=str(row["run_id"]),
                    sequence=int(row["sequence"]),
                    schema_version=str(row["schema_version"]),
                    record_type=str(row["record_type"]),
                    simulation_tick=int(row["simulation_tick"]),
                    simulation_time=float(row["simulation_time"]),
                    agent_id=(
                        str(row["agent_id"])
                        if row["agent_id"] is not None
                        else None
                    ),
                    source_event_id=(
                        str(row["source_event_id"])
                        if row["source_event_id"] is not None
                        else None
                    ),
                    payload=json.loads(str(row["payload_json"])),
                )
                yield _json(record.to_dict())
        finally:
            export_connection.close()

    def summary(self, run_id: str) -> dict[str, JsonValue]:
        run = self._connection.execute(
            """
            SELECT status, final_tick, final_simulation_time
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(f"unknown persisted run: {run_id}")
        counts = self._connection.execute(
            """
            SELECT record_type, COUNT(*) AS count
            FROM records WHERE run_id = ?
            GROUP BY record_type ORDER BY record_type
            """,
            (run_id,),
        )
        return {
            "schema_version": DATASET_SCHEMA_VERSION,
            "run_id": run_id,
            "status": str(run["status"]),
            "final_tick": (
                int(run["final_tick"]) if run["final_tick"] is not None else None
            ),
            "final_simulation_time": (
                float(run["final_simulation_time"])
                if run["final_simulation_time"] is not None
                else None
            ),
            "record_counts": {
                str(row["record_type"]): int(row["count"]) for row in counts
            },
        }

    def save_memory(self, run_id: str, record: MemoryRecord) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO episodic_memories (
                run_id, memory_id, agent_id, simulation_time, importance,
                text, embedding_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.id,
                record.agent_id,
                record.simulation_time,
                record.importance,
                record.text,
                _json(list(record.embedding)),
                _json(record.metadata),
            ),
        )
        self._connection.commit()

    def load_memories(self, run_id: str) -> tuple[MemoryRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM episodic_memories
            WHERE run_id = ?
            ORDER BY memory_id
            """,
            (run_id,),
        )
        return tuple(
            MemoryRecord(
                id=str(row["memory_id"]),
                agent_id=str(row["agent_id"]),
                text=str(row["text"]),
                simulation_time=float(row["simulation_time"]),
                importance=float(row["importance"]),
                embedding=tuple(
                    float(value)
                    for value in json.loads(str(row["embedding_json"]))
                ),
                metadata=json.loads(str(row["metadata_json"])),
            )
            for row in rows
        )

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current > _DATABASE_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current} is newer than supported "
                f"schema {_DATABASE_SCHEMA_VERSION}"
            )
        if current < 1:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
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
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS records (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    simulation_tick INTEGER NOT NULL,
                    simulation_time REAL NOT NULL,
                    agent_id TEXT,
                    source_event_id TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS records_run_type_tick
                ON records(run_id, record_type, simulation_tick);
                CREATE INDEX IF NOT EXISTS records_run_agent_tick
                ON records(run_id, agent_id, simulation_tick);
                PRAGMA user_version = 1;
                """
            )
            self._connection.commit()
            current = 1
        if current < 2:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodic_memories (
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
                CREATE INDEX IF NOT EXISTS memories_run_agent_time
                ON episodic_memories(run_id, agent_id, simulation_time);
                PRAGMA user_version = 2;
                """
            )
            self._connection.commit()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
