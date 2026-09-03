import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stage0_sim import __version__
from stage0_sim.adapters.persistence.sqlite_schema import DATABASE_SCHEMA_VERSION

DEFAULT_DATABASE = f"stage0-v{DATABASE_SCHEMA_VERSION}.sqlite3"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _environment(**overrides: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("STAGE0_")
    }
    environment.update(overrides)
    return environment


def _request(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, response.read()


def _start_server(
    work_directory: Path,
    *,
    environment: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], str]:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "stage0_sim.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=work_directory,
        env=environment or _environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, f"http://127.0.0.1:{port}"


def _stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _output(process: subprocess.Popen[str]) -> str:
    return process.stdout.read() if process.stdout is not None else ""


@contextmanager
def _healthy_server(
    work_directory: Path,
    *,
    environment: dict[str, str] | None = None,
) -> Iterator[str]:
    process, base_url = _start_server(
        work_directory,
        environment=environment,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(
                    "application exited during startup:\n" + _output(process)
                )
            try:
                status, _ = _request(f"{base_url}/health")
                if status == 200:
                    break
            except (OSError, urllib.error.HTTPError):
                time.sleep(0.1)
        else:
            raise AssertionError("application did not become healthy")
        yield base_url
    finally:
        _stop_server(process)


def _create_old_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sentinel (value TEXT NOT NULL);
        INSERT INTO sentinel VALUES ('preserve-me');
        PRAGMA user_version = 8;
        """
    )
    connection.commit()
    connection.close()


def _schema_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def test_source_application_starts_and_restarts_with_defaults(
    tmp_path: Path,
) -> None:
    current_database = (
        tmp_path / "data" / "runs" / DEFAULT_DATABASE
    )

    for _ in range(2):
        with _healthy_server(tmp_path) as base_url:
            status, body = _request(f"{base_url}/health")
            assert status == 200
            assert json.loads(body) == {
                "status": "ok",
                "version": __version__,
            }
            assert _request(f"{base_url}/ui/")[0] == 200
            assert _request(f"{base_url}/ui/assets/styles.css")[0] == 200

    assert current_database.is_file()
    assert _schema_version(current_database) == DATABASE_SCHEMA_VERSION


def test_source_startup_preserves_old_default_database(
    tmp_path: Path,
) -> None:
    old_database = tmp_path / "data" / "runs" / "stage0.sqlite3"
    _create_old_database(old_database)

    with _healthy_server(tmp_path):
        pass

    current_database = old_database.parent / DEFAULT_DATABASE
    assert _schema_version(current_database) == DATABASE_SCHEMA_VERSION
    assert _schema_version(old_database) == 8
    connection = sqlite3.connect(old_database)
    try:
        assert connection.execute(
            "SELECT value FROM sentinel"
        ).fetchone()[0] == "preserve-me"
    finally:
        connection.close()


def test_explicit_incompatible_database_fails_startup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data" / "runs" / "explicit.sqlite3"
    _create_old_database(database)
    process, _ = _start_server(
        tmp_path,
        environment=_environment(
            STAGE0_DATA_DIRECTORY=str(database.parent),
            STAGE0_DATASET_DATABASE=database.name,
        ),
    )
    try:
        output, _ = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        _stop_server(process)
        raise AssertionError(
            "application unexpectedly stayed running with an incompatible "
            "explicit database"
        ) from None

    assert process.returncode not in {None, 0}
    assert "unsupported SQLite schema version 8" in output
    assert f"expected {DATABASE_SCHEMA_VERSION}" in output
    assert _schema_version(database) == 8
