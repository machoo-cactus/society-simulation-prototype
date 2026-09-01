import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from importlib.resources import files
from pathlib import Path
from typing import Any


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-directory", type=Path, required=True)
    args = parser.parse_args()
    work_directory = args.work_directory.resolve()
    work_directory.mkdir(parents=True, exist_ok=True)
    os.chdir(work_directory)

    from stage0_sim import __version__

    package = files("stage0_sim")
    assert __version__ == "0.2.0"
    assert package.joinpath("resources", "demo.json").is_file()
    assert package.joinpath("resources", "demo-character.json").is_file()
    assert package.joinpath("web", "templates", "base.html").is_file()
    assert package.joinpath("web", "static", "styles.css").is_file()

    data = work_directory / "data"
    environment = os.environ.copy()
    environment.update(
        {
            "STAGE0_CHARACTER_DIRECTORY": str(data / "characters"),
            "STAGE0_SCENARIO_DIRECTORY": str(data / "scenarios"),
            "STAGE0_ELEMENT_DIRECTORY": str(data / "elements"),
            "STAGE0_DATA_DIRECTORY": str(data / "runs"),
        }
    )
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "stage0_sim.cli",
            "run",
            "demo",
            "--ticks",
            "1",
            "--characters-dir",
            str(data / "cli-characters"),
            "--elements-dir",
            str(data / "cli-elements"),
            "--database",
            str(data / "runs" / "cli.sqlite3"),
            "--output",
            str(data / "runs" / "cli-events.jsonl"),
        ],
        cwd=work_directory,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if cli.returncode != 0:
        raise RuntimeError(cli.stderr or cli.stdout)

    port = _free_port()
    server = subprocess.Popen(
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
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if server.poll() is not None:
                output = server.stdout.read() if server.stdout is not None else ""
                raise RuntimeError(f"installed API exited during startup:\n{output}")
            try:
                status, body = _request(f"{base_url}/health")
                if status == 200:
                    assert json.loads(body) == {
                        "status": "ok",
                        "version": "0.2.0",
                    }
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("installed API did not become healthy")

        status, body = _request(f"{base_url}/ui/")
        assert status == 200
        assert b"Operator Console" in body
        status, _ = _request(f"{base_url}/ui/assets/styles.css")
        assert status == 200

        demo = json.loads(
            package.joinpath("resources", "demo.json").read_text(
                encoding="utf-8"
            )
        )
        demo_character = json.loads(
            package.joinpath(
                "resources",
                "demo-character.json",
            ).read_text(encoding="utf-8")
        )
        status, _ = _request(
            f"{base_url}/characters",
            payload=demo_character,
        )
        assert status == 201
        status, body = _request(
            f"{base_url}/simulation/scenarios",
            payload={"scenario": demo, "character_assignments": {}},
        )
        assert status == 201
        scenario_id = json.loads(body)["scenario_id"]
        status, body = _request(
            f"{base_url}/simulation/runs",
            payload={"scenario_id": scenario_id, "realtime": False},
        )
        assert status == 201
        run_id = json.loads(body)["run_id"]
        status, _ = _request(
            f"{base_url}/simulation/runs/{run_id}/pause",
            payload={},
        )
        assert status == 200
        status, _ = _request(
            f"{base_url}/simulation/runs/{run_id}/step",
            payload={},
        )
        assert status == 200
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    print("installed wheel smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
