"""Run persistence adapters."""

from stage0_sim.adapters.persistence.sqlite import (
    RUN_SCOPED_TABLES,
    SQLiteDatasetStore,
)

__all__ = ["RUN_SCOPED_TABLES", "SQLiteDatasetStore"]
