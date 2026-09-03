import sqlite3

from fastapi.testclient import TestClient

from stage0_sim.adapters.persistence.sqlite_schema import DATABASE_SCHEMA_VERSION
from stage0_sim.api.app import app
from stage0_sim.config import Settings


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.3.0"}


def test_default_database_name_allows_startup_beside_old_schema(
    tmp_path,
) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    old_database = runs / "stage0.sqlite3"
    connection = sqlite3.connect(old_database)
    connection.executescript(
        """
        CREATE TABLE sentinel (value TEXT NOT NULL);
        INSERT INTO sentinel VALUES ('preserve-me');
        PRAGMA user_version = 8;
        """
    )
    connection.commit()
    connection.close()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    current_database = runs / Settings(_env_file=None).dataset_database
    assert current_database.name == (
        f"stage0-v{DATABASE_SCHEMA_VERSION}.sqlite3"
    )
    connection = sqlite3.connect(current_database)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == (
        DATABASE_SCHEMA_VERSION
    )
    connection.close()
    connection = sqlite3.connect(old_database)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
    assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == (
        "preserve-me"
    )
    connection.close()
