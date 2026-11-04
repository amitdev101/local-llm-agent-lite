"""Public Hugging Face GGUF downloads with durable, resumable jobs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import threading
import time
from urllib.parse import quote, unquote, urljoin, urlparse
import uuid

import httpx


class DownloadError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def repository_id(value):
    if not isinstance(value, str):
        raise DownloadError("Use owner/model-name or its Hugging Face URL")
    value = value.strip()
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {"huggingface.co", "www.huggingface.co", "hf.co"} or parsed.username or parsed.password:
            raise DownloadError("Enter a public Hugging Face model repository")
        parts = parsed.path.strip("/").split("/")
        if len(parts) > 2 and parts[2] not in {"tree", "blob", "resolve"}:
            raise DownloadError("Enter a model repository URL")
        value = "/".join(parts[:2])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value) or ".." in value:
        raise DownloadError("Use owner/model-name or its Hugging Face URL")
    return value


def model_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,200}\.gguf", value, re.I):
        raise DownloadError("Use a GGUF filename without folders or special characters")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise DownloadError("Choose a different filename")
    return value


class ModelDownloads:
    def __init__(self, models_dir: Path, state_path: Path, transport=None):
        self.models_dir = models_dir.resolve()
        self.state_path = state_path
        self.transport = transport
        self.lock = threading.RLock()
        self.jobs = {}
        self.worker = None
        self.stop = threading.Event()
        self.stop_action = "paused"
        if state_path.is_file():
            try:
                rows = json.loads(state_path.read_text(encoding="utf-8"))
                for job in rows:
                    if not isinstance(job, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(job.get("id", ""))):
                        continue
                    repository_id(job["repo_id"]); model_name(job["save_name"])
                    if job["status"] in {"downloading", "verifying", "pausing", "cancelling"}:
                        job.update(status="paused", message="Interrupted download. Resume when ready.")
                    self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError, TypeError):
                # Corrupt state must never cause an existing model to be overwritten.
                self.jobs = {}

    def _client(self):
        return httpx.Client(transport=self.transport, timeout=httpx.Timeout(20, connect=10),
                            headers={"User-Agent": "Local-LLM-Playground", "Accept-Encoding": "identity"})

    @staticmethod
    def _allowed_url(url):
        parsed = urlparse(str(url))
        host = parsed.hostname or ""
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443} \
                or not (host == "huggingface.co" or host.endswith((".huggingface.co", ".hf.co"))):
            raise DownloadError("The download redirected outside Hugging Face's supported hosts")

    @contextmanager
    def _response(self, client, url, headers=None):
        for _ in range(6):
            self._allowed_url(url)
            with client.stream("GET", url, headers=headers or {}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise DownloadError("The model source returned an invalid redirect")
                    url = urljoin(str(response.url), location)
                    continue
                if response.status_code in {401, 403}:
                    raise DownloadError("This model requires access. Choose a public, ungated repository.", 403)
                if response.status_code == 404:
                    raise DownloadError("Repository, revision, or file not found", 404)
                if response.status_code not in {200, 206}:
                    raise DownloadError(f"The model source returned HTTP {response.status_code}. Try again.", 502)
                yield response
                return
        raise DownloadError("The model source returned too many redirects", 502)

    def inspect(self, source, revision="main"):
        repo = repository_id(source)
        if "://" in source and revision == "main":
            parts = urlparse(source).path.strip("/").split("/")
            if len(parts) >= 4 and parts[2] in {"tree", "blob", "resolve"}:
                revision = unquote(parts[3])
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", revision):
            raise DownloadError("Choose a valid repository revision")
        try:
            with self._client() as client, self._response(client, f"https://huggingface.co/api/models/{repo}/revision/{quote(revision, safe='')}?blobs=true") as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise DownloadError("This repository is too large to browse here")
                info = json.loads(body)
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise DownloadError("Could not reach Hugging Face. Check your connection and try again.", 502) from error
        if not isinstance(info, dict) or not isinstance(info.get("siblings"), list):
            raise DownloadError("The source returned invalid repository information", 502)
        if info.get("gated") or info.get("private"):
            raise DownloadError("This model requires sign-in or access approval. Choose a public model without sign-in.", 403)
        commit = info.get("sha", "")
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40,64}", commit):
            raise DownloadError("The source did not provide a valid model revision", 502)
        files = []
        for sibling in info.get("siblings", []):
            if not isinstance(sibling, dict) or not isinstance(sibling.get("rfilename"), str):
                continue
            filename = sibling.get("rfilename", "")
            path = PurePosixPath(filename)
            if path.is_absolute() or ".." in path.parts or "\\" in filename or not filename.lower().endswith(".gguf"):
                continue
            if re.search(r"-\d{5}-of-\d{5}\.gguf$", filename, re.I):
                continue  # Partial split models cannot be loaded on their own.
            try:
                name = model_name(path.name)
            except DownloadError:
                continue
            lfs = sibling.get("lfs") if isinstance(sibling.get("lfs"), dict) else {}
            size = lfs.get("size", sibling.get("size"))
            checksum = lfs.get("sha256", "")
            if not isinstance(size, int) or isinstance(size, bool) or size < 8:
                continue
            files.append({"filename": filename, "save_name": name, "size": size,
                          "sha256": checksum if isinstance(checksum, str) and re.fullmatch(r"[a-f0-9]{64}", checksum) else None,
                          "installed": (self.models_dir / name).exists()})
        return {"repo_id": repo, "revision": commit, "source_url": f"https://huggingface.co/{repo}",
                "files": sorted(files, key=lambda row: row["size"])}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f".{self.state_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(list(self.jobs.values()), indent=2), encoding="utf-8")
            temporary.replace(self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _part(self, identifier):
        path = self.models_dir / ".downloads" / f"{identifier}.part"
        try:
            path.resolve().relative_to(self.models_dir)
        except ValueError as error:
            raise DownloadError("The download folder must stay inside the models folder") from error
        return path

    def snapshot(self):
        with self.lock:
            return {"destination": str(self.models_dir), "jobs": [dict(row) for row in reversed(list(self.jobs.values()))]}

    def start(self, source, filename, revision="main", save_name=None):
        # Resolve metadata again at the chosen commit; the client cannot invent size/hash.
        info = self.inspect(source, revision)
        selected = next((row for row in info["files"] if row["filename"] == filename), None)
        if not selected:
            raise DownloadError("Choose a complete single-file GGUF from this repository")
        name = model_name(save_name or selected["save_name"])
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise DownloadError("A download is already running. Pause it before starting another.", 409)
            self.models_dir.mkdir(parents=True, exist_ok=True)
            if (self.models_dir / name).exists():
                raise DownloadError("That filename already exists. Choose another Save as name.", 409)
            if shutil.disk_usage(self.models_dir).free < selected["size"] + 1024 * 1024:
                raise DownloadError("Not enough free disk space for this model")
            identifier = uuid.uuid4().hex
            self._part(identifier)
            job = {"id": identifier, "repo_id": info["repo_id"], "revision": info["revision"],
                   **selected, "save_name": name, "downloaded": 0, "status": "paused", "message": ""}
            self.jobs[identifier] = job
            try:
                self._save()
            except OSError:
                self.jobs.pop(identifier)
                raise
            self._launch(job)
            return dict(job)

    def _launch(self, job):
        self._part(job["id"])
        self.stop = threading.Event()
        self.stop_action = "paused"
        job.update(status="downloading", message="")
        self._save()
        self.worker = threading.Thread(target=self._run, args=(job, self.stop), name="model-download", daemon=True)
        self.worker.start()

    def control(self, identifier, action):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job:
                raise DownloadError("Download not found", 404)
            if action == "resume":
                if self.worker and self.worker.is_alive():
                    raise DownloadError("Wait for the current download to pause or finish", 409)
                if job["status"] not in {"paused", "error"}:
                    raise DownloadError("This download cannot be resumed", 409)
                if (self.models_dir / job["save_name"]).exists():
                    raise DownloadError("The destination already exists. Use it from the installed-model list.", 409)
                self._launch(job)
            elif action in {"pause", "cancel"}:
                if job["status"] in {"downloading", "verifying", "pausing", "cancelling"}:
                    self.stop_action = "paused" if action == "pause" else "cancelled"
                    job["status"] = "pausing" if action == "pause" else "cancelling"
                    self.stop.set()
                elif action == "cancel" and job["status"] in {"paused", "error"}:
                    self._part(identifier).unlink(missing_ok=True)
                    job.update(status="cancelled", downloaded=0, message="")
                else:
                    raise DownloadError("This download is no longer running", 409)
                self._save()
            else:
                raise DownloadError("Unknown download action")
            return dict(job)

    def _run(self, job, stop):
        part = self._part(job["id"])
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
            offset = part.stat().st_size if part.exists() else 0
            if offset > job["size"]:
                part.unlink(); offset = 0
            if shutil.disk_usage(self.models_dir).free < job["size"] - offset + 1024 * 1024:
                raise DownloadError("Not enough free disk space. Free some space and resume.")
            with self.lock:
                job["downloaded"] = offset
            if offset < job["size"]:
                url = f"https://huggingface.co/{job['repo_id']}/resolve/{job['revision']}/{quote(job['filename'], safe='/')}"
                with self._client() as client, self._response(client, url, {"Range": f"bytes={offset}-"} if offset else {}) as response:
                    if response.status_code == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
                        if not match or int(match[1]) != offset or int(match[3]) != job["size"]:
                            raise DownloadError("The source returned an invalid resume range. Retry later.")
                    elif offset:
                        offset = 0  # A server may ignore Range; restart instead of appending twice.
                    with part.open("ab" if offset else "wb") as handle:
                        saved = time.monotonic()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if stop.is_set():
                                break
                            if offset + len(chunk) > job["size"]:
                                raise DownloadError("The source returned more data than expected")
                            handle.write(chunk); offset += len(chunk)
                            with self.lock:
                                job["downloaded"] = offset
                                if time.monotonic() - saved >= 1:
                                    self._save(); saved = time.monotonic()
                        handle.flush()
                        os.fsync(handle.fileno())
            if not stop.is_set():
                if part.stat().st_size != job["size"]:
                    raise DownloadError("The connection ended early. Resume to download the remaining bytes.")
                with self.lock:
                    job["status"] = "verifying"; self._save()
                digest = hashlib.sha256()
                with part.open("rb") as handle:
                    valid_header = handle.read(4) == b"GGUF"
                    handle.seek(0)
                    while chunk := handle.read(1024 * 1024):
                        if stop.is_set():
                            break
                        digest.update(chunk)
                if not valid_header:
                    part.unlink(missing_ok=True)
                    raise DownloadError("This file is not a GGUF model. Retry or choose another file.")
                if not stop.is_set() and job["sha256"] and digest.hexdigest() != job["sha256"]:
                    part.unlink(missing_ok=True)
                    raise DownloadError("The checksum did not match. Retry to download a fresh copy.")
                with self.lock:
                    if not stop.is_set():
                        destination = self.models_dir / job["save_name"]
                        destination.resolve().relative_to(self.models_dir)
                        if destination.exists():
                            raise DownloadError("The destination already exists. Existing models were kept.")
                        if os.name == "nt":
                            part.rename(destination)  # Windows rename refuses to replace an existing file.
                        else:
                            os.link(part, destination); part.unlink()
                        job.update(status="complete", downloaded=job["size"], path=str(destination), message="")
                        self._save()
            if stop.is_set():
                with self.lock:
                    if self.stop_action == "cancelled":
                        part.unlink(missing_ok=True)
                    job.update(status=self.stop_action, downloaded=part.stat().st_size if part.exists() else 0, message="")
                    self._save()
        except Exception as error:
            with self.lock:
                if stop.is_set():
                    if self.stop_action == "cancelled":
                        part.unlink(missing_ok=True)
                    job.update(status=self.stop_action, downloaded=part.stat().st_size if part.exists() else 0, message="")
                else:
                    message = str(error) if isinstance(error, DownloadError) else "Download interrupted. Check your connection and disk space, then resume."
                    job.update(status="error", message=message, downloaded=part.stat().st_size if part.exists() else 0)
                try:
                    self._save()
                except OSError:
                    job["message"] = "Could not save download history. Keep the partial file and try again when disk space is available."
