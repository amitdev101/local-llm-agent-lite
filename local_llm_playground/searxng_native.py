"""Install and run the Playground's Docker-free Windows SearXNG instance."""

import hashlib
import os
import socket
import subprocess
import sys
import sysconfig
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from zipfile import ZipFile

import httpx

from addons import AddonError
from searxng_local import prepare_settings, test_search


RELEASE = "2026.11.11-248b8ffc0"
SOURCE_URL = "https://codeload.github.com/searxng/searxng/zip/248b8ffc0"
SOURCE_HASH = "9cf958260a6cd22cb48244c245a46b94d755f33b6d189e7bdf4292b23381e656"
ARCHIVE_ROOT = "searxng-248b8ffc0/"
PORT = 8889
URL = f"http://127.0.0.1:{PORT}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def unsupported_reason() -> str:
    if sys.platform != "win32":
        return "Install without Docker is available on Windows."
    if sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] not in {(3, 11), (3, 12)}:
        return "Run Playground with 64-bit Python 3.11 or 3.12 to install SearXNG without Docker."
    return ""


def download_source(destination: Path) -> None:
    archive_data = bytearray()
    deadline = time.monotonic() + 90
    try:
        with httpx.stream("GET", SOURCE_URL, timeout=15) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                archive_data.extend(chunk)
                if len(archive_data) > 32 * 1024 * 1024 or time.monotonic() >= deadline:
                    raise AddonError(502, "The SearXNG download exceeded its size or time limit. Try again.")
    except httpx.HTTPError as error:
        raise AddonError(502, "Could not download SearXNG from GitHub. Check your internet connection and try again.") from error
    if hashlib.sha256(archive_data).hexdigest() != SOURCE_HASH:
        raise AddonError(502, "The SearXNG download failed its checksum. Try again or use another setup option.")

    with TemporaryDirectory(prefix="source-", dir=destination.parent) as staging:
        with ZipFile(BytesIO(archive_data)) as archive:
            # The Linux deployment templates include filenames Windows cannot extract.
            for name in archive.namelist():
                entry = name.removeprefix(ARCHIVE_ROOT)
                if entry.startswith("searx/") or entry in {"requirements.txt", "LICENSE"}:
                    archive.extract(name, staging)
        source = Path(staging) / ARCHIVE_ROOT
        valkey = source / "searx" / "valkeydb.py"
        # This upstream release uses POSIX account lookup only in its Valkey error log.
        code = valkey.read_text(encoding="utf-8").replace("import pwd\n", "")
        lines = [line for line in code.splitlines() if "_pw = pwd.getpwuid" not in line]
        valkey.write_text("\n".join(
            '        logger.exception("Could not connect to Valkey.")' if "_pw.pw_name, _pw.pw_uid" in line else line
            for line in lines
        ) + "\n", encoding="utf-8")
        (source / "searx" / "version_frozen.py").write_text(
            f"VERSION_STRING = VERSION_TAG = {RELEASE.replace('-', '+')!r}\n"
            f"DOCKER_TAG = {RELEASE!r}\n"
            "GIT_URL = 'https://github.com/searxng/searxng'\nGIT_BRANCH = 'master'\n",
            encoding="utf-8",
        )
        source.rename(destination)


class NativeSearxng:
    def __init__(self, directory: Path):
        self.directory = directory / ".local" / "native"
        self.template = directory / "settings.example.yml"
        self.runner = directory / "run_native.py"
        self.source = self.directory / RELEASE
        self.venv = self.directory / "venv"
        self.python = self.venv / "Scripts" / "python.exe"
        self.ready = self.directory / f"{RELEASE}.ready"
        self.install_log = self.directory / "install.log"
        self.server_log = self.directory / "server.log"
        self.process: subprocess.Popen | None = None
        self.lock = Lock()

    def _state(self) -> str:
        if self.process and self.process.poll() is None:
            return "running"
        if self.ready.is_file() and self.python.is_file() and (self.source / "searx" / "webapp.py").is_file():
            return "stopped"
        return "not_installed"

    def status(self) -> dict:
        reason = unsupported_reason()
        return {
            "url": URL, "folder": str(self.directory),
            "container": "unknown" if reason else self._state(),
            "has_logs": self.install_log.is_file() or self.server_log.is_file(),
            **({"message": reason} if reason else {}),
        }

    def _install(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.source.is_dir():
            download_source(self.source)
        with self.install_log.open("wb") as log:
            options = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
                       "creationflags": NO_WINDOW, "check": True}
            try:
                if not self.python.is_file():
                    subprocess.run([sys.executable, "-I", "-m", "venv", str(self.venv)], timeout=60, **options)
                subprocess.run([
                    str(self.python), "-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                    "--only-binary=:all:", "--timeout", "15", "--retries", "2",
                    "-r", str(self.source / "requirements.txt"), "waitress==3.0.2",
                ], timeout=300, **options)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                raise AddonError(502, "Could not install SearXNG's Python dependencies. Open View logs for details.") from error
        self.ready.touch()

    def _start(self) -> dict:
        if self._state() == "running":
            return {"container": "running", **test_search(URL)}
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", PORT))
            except OSError as error:
                raise AddonError(409, f"Port {PORT} is unavailable. Free it or use another SearXNG setup option.") from error
        if self._state() == "not_installed":
            self._install()
        settings = prepare_settings(self.template, self.directory / "config")
        cache = self.directory / "cache"
        cache.mkdir(exist_ok=True)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("SEARXNG_")}
        environment.update(TEMP=str(cache), TMP=str(cache))
        with self.server_log.open("wb") as log:
            self.process = subprocess.Popen(
                [str(self.python), "-I", "-u", str(self.runner), str(self.source), str(settings), str(PORT)],
                cwd=self.directory, env=environment, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW,
            )
        with httpx.Client(timeout=1, trust_env=False) as client:
            deadline = time.monotonic() + 35
            while self._state() == "running" and time.monotonic() < deadline:
                try:
                    with client.stream("GET", f"{URL}/healthz") as response:
                        if response.status_code == 200 and self._state() == "running":
                            return {"container": "running", **test_search(URL)}
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        self.close()
        return {"container": self._state(), "search_error": "SearXNG did not respond after starting. Open View logs for details."}

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        self.process = None

    def _logs(self) -> str:
        excerpts = []
        for label, path in (("Installation", self.install_log), ("Server", self.server_log)):
            if path.is_file():
                with path.open("rb") as log:
                    log.seek(max(0, path.stat().st_size - 6000))
                    excerpts.append(f"{label}\n{log.read(6000).decode('utf-8', errors='replace')}")
        return "\n\n".join(excerpts)

    def manage(self, action: str) -> dict:
        reason = unsupported_reason()
        if reason:
            raise AddonError(409, reason)
        if not self.lock.acquire(blocking=False):
            raise AddonError(409, "A local SearXNG operation is already running.")
        try:
            if action == "start":
                return self._start()
            if action == "logs":
                return {"container": self._state(), "logs": self._logs()}
            if action == "stop":
                self.close()
                return {"container": self._state()}
            if self._state() != "running":
                raise AddonError(409, "Start local SearXNG before testing search.")
            return {"container": "running", **test_search(URL)}
        except OSError as error:
            raise AddonError(500, "Could not access the native SearXNG installation. Check the Local files folder and permissions.") from error
        finally:
            self.lock.release()
