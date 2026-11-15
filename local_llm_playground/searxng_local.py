"""Manage the SearXNG container owned by this Playground checkout."""

import hashlib
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from threading import Lock

import httpx

from addons import AddonError, search_web


IMAGE = "ghcr.io/searxng/searxng:2026.11.11-248b8ffc0@sha256:71a410e383ea1c1f973a0c38e566757c1f0c5f3976a33be2c70918fcd93bdcc7"
PORT = 8888
URL = f"http://127.0.0.1:{PORT}"
OWNER_LABEL = "local-llm-playground.searxng"


def prepare_settings(template: Path, config: Path) -> Path:
    config.mkdir(parents=True, exist_ok=True)
    path = config / "settings.yml"
    if not path.exists():
        settings = template.read_text(encoding="utf-8")
        path.write_text(settings.replace("REPLACE_WITH_LOCAL_SECRET", secrets.token_hex(32)), encoding="utf-8")
    return path


def test_search(url: str) -> dict:
    try:
        return {"search_count": len(search_web(url, "SearXNG"))}
    except AddonError as error:
        return {"search_error": str(error)}


def docker_command(docker: list[str], *args: str, timeout: int = 5) -> str:
    try:
        result = subprocess.run(
            [*docker, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        raise AddonError(504, "Docker took too long to respond. Check Docker and try again.") from error
    except OSError as error:
        raise AddonError(503, "Could not run Docker. Check that the Docker CLI is available.") from error
    if result.returncode:
        detail = result.stderr.strip()[-900:]
        if "port is already allocated" in detail.lower() or "address already in use" in detail.lower():
            raise AddonError(409, f"Port {PORT} is already in use. Free it or connect to your existing SearXNG instance.")
        raise AddonError(502, f"Docker could not complete the operation. {detail}".strip())
    return (result.stdout + result.stderr if args[:2] == ("container", "logs") else result.stdout).strip()


def local_docker() -> list[str]:
    executable = shutil.which("docker")
    if not executable:
        raise AddonError(424, "Install Docker to let Playground set up SearXNG automatically.")
    cli = [executable]
    context = os.environ.get("DOCKER_CONTEXT")
    endpoint = os.environ.get("DOCKER_HOST") if not context else None
    if not endpoint:
        context = context or docker_command(cli, "context", "show")
        endpoint = docker_command(cli, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}", "--", context)
    if not endpoint.startswith(("npipe:////./pipe/", "unix:///")):
        raise AddonError(409, "Select a local Docker engine to set up SearXNG on this device.")
    docker = [*cli, "--context", context] if context else [*cli, "--host", endpoint]
    try:
        os_type = docker_command(docker, "info", "--format", "{{.OSType}}")
    except AddonError as error:
        raise AddonError(503, "Docker is installed, but the engine is unavailable. Start Docker and check again.") from error
    if os_type != "linux":
        raise AddonError(409, "Switch Docker to Linux containers, then check again.")
    return docker


class LocalSearxng:
    def __init__(self, directory: Path):
        self.directory = directory / ".local"
        self.template = directory / "settings.example.yml"
        self.owner = hashlib.sha256(os.path.normcase(str(directory)).encode()).hexdigest()[:16]
        self.name = f"playground-searxng-{self.owner}"
        self.lock = Lock()

    def _container(self, docker: list[str]) -> tuple[str, str]:
        identifier = docker_command(
            docker, "container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"name=^/{self.name}$",
        )
        if not identifier:
            return "", "not_installed"
        label, _, running = docker_command(
            docker, "container", "inspect", "--format",
            '{{index .Config.Labels "' + OWNER_LABEL + '"}}|{{.State.Running}}', identifier,
        ).partition("|")
        if label != self.owner:
            raise AddonError(409, "A container with this name already exists outside Playground. Connect to its URL instead.")
        return identifier, "running" if running == "true" else "stopped"

    def status(self) -> dict:
        status = {"url": URL, "folder": str(self.directory), "container": "unknown"}
        try:
            docker = local_docker()
            _, status["container"] = self._container(docker)
        except AddonError as error:
            return {**status, "message": str(error), "needs_docker": error.status == 424}
        return status

    def _create(self, docker: list[str]) -> str:
        config = self.directory / "config"
        cache = self.directory / "cache"
        try:
            prepare_settings(self.template, config)
            cache.mkdir(exist_ok=True)
        except OSError as error:
            raise AddonError(500, "Could not prepare tools/searxng/.local. Check that the folder is writable.") from error
        return docker_command(
            docker, "container", "create", "--name", self.name, "--label", f"{OWNER_LABEL}={self.owner}",
            "--restart", "unless-stopped", "--publish", f"127.0.0.1:{PORT}:8080",
            "--volume", f"{config}:/etc/searxng", "--volume", f"{cache}:/var/cache/searxng",
            "--log-driver", "local", IMAGE, timeout=300,
        )

    def _wait_for_start(self, docker: list[str]) -> dict:
        with httpx.Client(timeout=1, trust_env=False) as client:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with client.stream("GET", f"{URL}/healthz") as response:
                        if response.status_code == 200:
                            return {"container": "running", **test_search(URL)}
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        _, state = self._container(docker)
        return {"container": state, "search_error": "SearXNG did not respond after starting. Check the container logs."}

    def manage(self, action: str) -> dict:
        if not self.lock.acquire(blocking=False):
            raise AddonError(409, "A local SearXNG operation is already running.")
        try:
            docker = local_docker()
            identifier, state = self._container(docker)
            if action == "start":
                if not identifier:
                    identifier = self._create(docker)
                if state != "running":
                    docker_command(docker, "container", "start", identifier, timeout=20)
                return self._wait_for_start(docker)
            if not identifier:
                raise AddonError(409, "Set up local SearXNG first.")
            if action == "logs":
                logs = docker_command(docker, "container", "logs", "--tail", "60", identifier)
                return {"container": state, "logs": logs[-12000:]}
            if action == "stop":
                if state == "running":
                    docker_command(docker, "container", "stop", identifier, timeout=20)
                return {"container": "stopped"}
            if state != "running":
                raise AddonError(409, "Start local SearXNG before testing search.")
            return {"container": state, **test_search(URL)}
        finally:
            self.lock.release()
