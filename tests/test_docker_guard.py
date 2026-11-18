"""The Docker guard hook refuses volume-deleting commands and lets the rest through."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parents[1] / ".claude" / "hooks" / "docker_guard.py"
spec = importlib.util.spec_from_file_location("docker_guard", HOOK)
assert spec is not None and spec.loader is not None
docker_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docker_guard)

FULL_PATH = "/c/Users/someone/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"

REFUSED = [
    "docker compose down -v",
    "docker compose down --volumes",
    "docker compose down --volumes=true",
    "docker compose -p stat-cave -f infra/docker-compose.yml down --remove-orphans -v",
    "docker compose down -t 5 -v",
    "docker compose down -tv",
    "docker-compose down -v",
    "DOCKER COMPOSE DOWN -V",
    "cd infra && docker compose down -v && docker compose up -d",
    "docker compose down \\\n  -v",
    "docker volume rm stat-cave_seaweed-data",
    "docker volume remove stat-cave_polaris-db",
    "docker volume prune",
    "docker volume prune -f",
    "docker system prune",
    "docker system prune -a --volumes",
    "echo start; docker ps; docker volume rm x",
    f"{FULL_PATH} volume rm x",
    f'"{FULL_PATH}" compose down -v',
    f'D="{FULL_PATH}"; "$D" volume prune -f',
    '& "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe" system prune',
    '$d = "C:\\Docker\\docker.exe"; & $d compose down `\n  --volumes',
    'docker "volume" "rm" x',
    "docker   volume    rm x",
]

ALLOWED = [
    "docker compose down",
    "docker compose down --remove-orphans",
    "docker compose down && docker compose up -d",
    "docker compose down; docker run -v data:/data alpine ls",
    "docker compose down 2>&1 | grep -v Removed",
    "docker compose up -d",
    "docker compose config --quiet",
    "docker compose ps",
    "docker volume ls",
    "docker volume inspect stat-cave_seaweed-data",
    "docker system df",
    "docker run --rm -v stat-cave_seaweed-data:/data:ro alpine ls /data",
    "docker ps -a",
    "git status",
    "ls -v",
    "uv run pytest",
    "",
]


@pytest.mark.parametrize("command", REFUSED)
def test_refused(command: str) -> None:
    assert docker_guard.refused(command) is not None


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed(command: str) -> None:
    assert docker_guard.refused(command) is None


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, check=False
    )


def test_hook_exit_codes() -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "docker volume prune"}}
    blocked = run_hook(json.dumps(payload))
    assert blocked.returncode == 2
    assert "docker volume prune" in blocked.stderr

    payload["tool_input"]["command"] = "docker compose down"
    assert run_hook(json.dumps(payload)).returncode == 0


def test_hook_refuses_a_payload_it_cannot_read() -> None:
    assert run_hook("not json").returncode == 2
    assert run_hook("{}").returncode == 2
