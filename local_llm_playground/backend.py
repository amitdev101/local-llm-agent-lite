from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid

from collections import deque
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from model_worker import ProcessModel as Llama
import psutil
from pydantic import BaseModel, Field
from token_usage import TokenUsageStore
from model_downloads import DownloadError, ModelDownloads, repository_id

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
CHATS = DATA / "chats"
LOGS = ROOT / "logs"
CONFIG = DATA / "config.json"
MODEL_CAPABILITIES = ROOT / "model_capabilities.json"
for directory in (CHATS, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

log_file = LOGS / f"playground-{datetime.now():%Y-%m-%d-%H%M%S}.log.txt"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(log_file, encoding="utf-8"),
              logging.StreamHandler()],
)
logger = logging.getLogger("playground")
settings_lock = threading.Lock()
chat_lock = threading.RLock()
usage_store = TokenUsageStore(DATA / "token_usage.jsonl")
downloads = ModelDownloads(PROJECT_ROOT / "models", DATA / "downloads.json")

DEFAULTS = {
    "model_path": "",
    "context_size": 8192,
    "gpu_layers": 0,
    "threads": max(1, (os.cpu_count() or 4) - 2),
    "temperature": 0.15,
    "top_p": 0.95,
    "top_k": 40,
    "min_p": 0.05,
    "repeat_penalty": 1.0,
    "seed": -1,
    "thinking": False,
    "system_prompt": "",
}


def read_config() -> dict:
    try:
        loaded = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        loaded = {}
    return {**DEFAULTS, **(loaded if isinstance(loaded, dict) else {})}


def write_config(values: dict) -> dict:
    with settings_lock:
        config = {**read_config(), **values}
        temporary = CONFIG.with_name(f".{CONFIG.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
            temporary.replace(CONFIG)
        finally:
            temporary.unlink(missing_ok=True)
        return config


def thinking_support(model_path: str) -> str:
    """Explicit support for this app's /think and /no_think soft switch."""
    try:
        entries = json.loads(MODEL_CAPABILITIES.read_text(encoding="utf-8"))
        if not isinstance(entries, dict):
            return "unknown"
        name = Path(model_path.replace("\\", "/")).name.casefold()
        value = next((value for key, value in entries.items() if key.casefold() == name), "unknown")
        return value if isinstance(value, str) and value in {"supported", "unsupported", "unknown"} else "unknown"
    except (OSError, ValueError):
        return "unknown"


def chat_path(chat_id: str) -> Path:
    if not chat_id or any(character not in "0123456789abcdef-" for character in chat_id):
        raise HTTPException(400, "Invalid chat ID")
    return CHATS / f"{chat_id}.jsonl"


def append_event(chat_id: str, event: dict) -> None:
    event = {"time": datetime.now().isoformat(timespec="seconds"), **event}
    with chat_lock, chat_path(chat_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(chat_id: str) -> list[dict]:
    path = chat_path(chat_id)
    with chat_lock:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError as error:
            raise HTTPException(404, "Chat not found") from error
    events = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            continue
    return events


def chat_view(chat_id: str) -> dict:
    events = read_events(chat_id)
    title = "New chat"
    settings = {}
    messages = []
    generation_request = None
    branch_of = None
    visibility = "active"
    created_at = events[0].get("time", "") if events else ""
    for index, event in enumerate(events):
        kind = event.get("type")
        if kind in {"created", "renamed"}:
            title = event.get("title", title)
        elif kind == "settings":
            settings.update(event.get("values", {}))
        elif kind == "branched":
            branch_of = {"chat_id": event["chat_id"], "message_id": event["message_id"]}
        elif kind == "visibility" and event.get("value") in ("active", "archived", "trashed"):
            visibility = event["value"]
        elif kind == "user":
            generation_request = {
                key: value for key, value in settings.items()
                if key in GenerateRequest.model_fields
            }
            generation_request.update({
                "message": event.get("content", ""),
                "request_id": event.get("request_id") or f"legacy-{index}",
            })
            messages.append({**event, "id": generation_request["request_id"], "request": generation_request})
        elif kind == "assistant":
            messages.append({**event, "id": event.get("message_id") or f"legacy-{index}", "request": generation_request})
    return {
        "id": chat_id,
        "title": title,
        "created_at": created_at,
        "updated_at": events[-1].get("time", created_at) if events else created_at,
        "settings": settings,
        "messages": messages,
        "branch_of": branch_of,
        "visibility": visibility,
    }


class ModelRuntime:

    def __init__(self) -> None:
        self.llm: Llama | None = None
        self.model_path = ""
        self.status = "not_loaded"
        self.error = ""
        self.loaded_settings = {}
        self.active_chat_id = None
        self.cancel = threading.Event()
        self.generation_lock = threading.Lock()
        self.lifecycle_lock = threading.RLock()
        self.load_id = None
        self.load_cancel = None
        self.load_done = None
        self.cancelled_loads = deque(maxlen=128)
        self.auto_load_suppressed = False

    @staticmethod
    def close_model(model):
        if model is not None and callable(getattr(model, "close", None)):
            model.close()

    def snapshot(self) -> dict:
        with self.lifecycle_lock:
            model_path = self.model_path if self.llm is not None and self.status == "ready" else ""
            state = self.status
            if state == "ready" and not getattr(self.llm, "alive", True):
                state = self.status = "error"
                self.error = "The model worker stopped. Load the model again to continue."
                model_path = ""
            return {"status": "generating" if state == "ready" and self.generation_lock.locked() else state,
                    "model_path": model_path, "thinking_support": thinking_support(model_path) if model_path else "unknown",
                    "loaded_settings": dict(self.loaded_settings) if model_path else {}, "error": self.error,
                    "auto_load_suppressed": self.auto_load_suppressed,
                    "can_unload": self.llm is not None or self.load_cancel is not None}

    def load(self, settings: dict, remember: bool = False) -> dict:
        path = Path(str(settings["model_path"])).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise HTTPException(400, "Select a valid GGUF model")
        identifier = settings.get("load_id") or str(uuid.uuid4())
        with self.lifecycle_lock:
            if identifier in self.cancelled_loads:
                return {**self.snapshot(), "load_cancelled": True}
            if self.status == "unloading" or not self.generation_lock.acquire(blocking=False):
                raise HTTPException(409, "Wait for the current model operation or stop generation before changing model")
            cancelled = self.load_cancel = threading.Event()
            done = self.load_done = threading.Event()
            self.load_id = identifier
            self.status = "loading"
            self.error = ""
        started = time.perf_counter()
        candidate = None
        try:
            with self.lifecycle_lock:
                if cancelled.is_set():
                    return {**self.snapshot(), "load_cancelled": True}
                self.close_model(self.llm)
                self.llm = None
                self.model_path = ""
                self.loaded_settings = {}
                candidate = self.llm = Llama(
                    model_path=str(path),
                    n_ctx=int(settings["context_size"]),
                    n_gpu_layers=int(settings["gpu_layers"]),
                    n_threads=int(settings["threads"]),
                    n_threads_batch=int(settings["threads"]),
                    use_mmap=True,
                    verbose=False,
                )
            if callable(getattr(candidate, "wait_ready", None)):
                candidate.wait_ready()
            with self.lifecycle_lock:
                if cancelled.is_set():
                    return {**self.snapshot(), "load_cancelled": True}
                self.model_path = str(path)
                self.loaded_settings = {key: settings[key] for key in ("context_size", "gpu_layers", "threads")}
                self.loaded_settings["model_path"] = str(path)
                self.status = "ready"
                self.auto_load_suppressed = False
                remembered = True
                if remember:
                    try:
                        write_config(self.loaded_settings)
                    except OSError:
                        logger.exception("Model loaded but loading settings could not be remembered")
                        remembered = False
                logger.info("Model loaded in %.2fs: %s", time.perf_counter() - started, path)
                return {**self.snapshot(), "status": "ready", "load_settings_saved": remembered}
        except Exception as error:
            try:
                self.close_model(candidate)
            except Exception as cleanup_error:
                logger.exception("Model worker cleanup failed")
                error = cleanup_error
            with self.lifecycle_lock:
                if self.llm is candidate and not getattr(candidate, "alive", False):
                    self.llm = None
                self.model_path = ""
                self.loaded_settings = {}
                if cancelled.is_set() and self.llm is None:
                    return {**self.snapshot(), "load_cancelled": True}
                self.status = "error"
                self.error = str(error)
            logger.exception("Model load failed")
            raise HTTPException(500, str(error)) from error
        finally:
            with self.lifecycle_lock:
                self.load_cancel = self.load_done = None
                self.generation_lock.release()
                done.set()
                if cancelled.is_set() and self.status == "unloading" and self.llm is None:
                    self.status = "not_loaded"

    def unload(self, load_id=None) -> dict:
        with self.lifecycle_lock:
            if load_id:
                # Remember early cancellation even if its Load request hasn't arrived.
                self.cancelled_loads.append(load_id)
                if self.load_id is not None and self.load_id != load_id:
                    raise HTTPException(409, "Another model operation replaced this request. Refresh its status before unloading.")
            if self.status == "unloading":
                return self.snapshot()
            loading = self.load_cancel is not None
            acquired = False
            if not loading:
                acquired = self.generation_lock.acquire(blocking=False)
                if not acquired:
                    raise HTTPException(409, "Stop the response before unloading the model")
            self.status = "unloading"
            self.error = ""
            self.auto_load_suppressed = True
            if loading:
                self.load_cancel.set()
            done, model = self.load_done, self.llm
        try:
            self.close_model(model)
            if done is not None:
                done.wait(timeout=5)
            with self.lifecycle_lock:
                self.llm = None
                self.model_path = ""
                self.loaded_settings = {}
                self.load_id = None
                if done is None or done.is_set():
                    self.status = "not_loaded"
                return self.snapshot()
        except Exception as error:
            with self.lifecycle_lock:
                self.status = "error"
                self.error = str(error)
            raise HTTPException(500, str(error)) from error
        finally:
            if acquired:
                self.generation_lock.release()


runtime = ModelRuntime()


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        runtime.close_model(runtime.llm)


app = FastAPI(title="Local LLM Playground", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")


class LoadRequest(BaseModel):
    model_path: str
    context_size: int = Field(4096, ge=256, le=131072)
    gpu_layers: int = Field(0, ge=-1, le=1000)
    threads: int = Field(DEFAULTS["threads"], ge=1, le=256)
    load_id: str | None = Field(None, min_length=1, max_length=80, pattern=r"^[\w-]+$")


class UnloadRequest(BaseModel):
    load_id: str | None = Field(None, min_length=1, max_length=80, pattern=r"^[\w-]+$")


class ResponseSettings(BaseModel):
    system_prompt: str = ""
    thinking: bool = False
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    top_p: float = Field(0.95, ge=0.0, le=1.0)
    top_k: int = Field(40, ge=0, le=1000)
    min_p: float = Field(0.05, ge=0.0, le=1.0)
    repeat_penalty: float = Field(1.0, ge=0.0, le=2.0)
    seed: int = -1


class ChatCreate(ResponseSettings):
    pass


class ChatUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ChatVisibility(BaseModel):
    visibility: Literal["active", "archived", "trashed"]


class BackupChat(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[0-9a-f-]+$")
    events: list[dict] = Field(max_length=200000)


class BackupRestore(BaseModel):
    version: Literal[1]
    chats: list[BackupChat] = Field(max_length=10000)


class BranchRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=100)
    message: str | None = Field(None, min_length=1, max_length=100000)
    response_settings: ResponseSettings | None = None


class DownloadRequest(BaseModel):
    repo_id: str = Field(min_length=3, max_length=300)
    filename: str = Field(min_length=1, max_length=500)
    revision: str = Field("main", min_length=1, max_length=80)
    save_name: str | None = Field(None, max_length=220)


class DownloadControl(BaseModel):
    action: str = Field(pattern=r"^(pause|resume|cancel)$")


class SettingsUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    model_path: str | None = None
    context_size: int | None = Field(None, ge=256, le=131072)
    gpu_layers: int | None = Field(None, ge=-1, le=1000)
    threads: int | None = Field(None, ge=1, le=256)
    system_prompt: str | None = None
    thinking: bool | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    top_k: int | None = Field(None, ge=0, le=1000)
    min_p: float | None = Field(None, ge=0.0, le=1.0)
    repeat_penalty: float | None = Field(None, ge=0.0, le=2.0)
    seed: int | None = None


class GenerateRequest(ResponseSettings):
    message: str = Field(min_length=1)
    request_id: str | None = Field(None, min_length=1, max_length=80, pattern=r"^[\w-]+$")
    expected_model_path: str | None = Field(None, min_length=1)


class ContextPreviewRequest(GenerateRequest):
    chat_id: str | None = None


def event(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/status")
def status() -> dict:
    return runtime.snapshot()


@app.get("/api/models")
def models() -> list[dict]:
    return [{
        "path": str(path.resolve()),
        "name": path.name,
        "size": path.stat().st_size,
        "thinking_support": thinking_support(str(path)),
    } for path in sorted((PROJECT_ROOT / "models").glob("*.gguf"))]


@app.get("/api/model-guidance")
def model_guidance() -> dict:
    memory = psutil.virtual_memory()
    folder = downloads.models_dir
    disk = shutil.disk_usage(folder if folder.is_dir() else folder.parent)
    threads = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    return {"available_ram": memory.available, "total_ram": memory.total, "free_disk": disk.free,
            "cpu_starter": {"context_size": 2048 if memory.available < 4 * 1024 ** 3 else 4096,
                            "gpu_layers": 0, "threads": min(threads, 8)}}


@app.get("/api/settings")
def get_settings() -> dict:
    return read_config()


def download_result(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except DownloadError as error:
        raise HTTPException(error.status, str(error)) from error
    except OSError as error:
        logger.exception("Model download storage failed")
        raise HTTPException(500, "Could not save the download. Check folder permissions and free disk space.") from error


@app.get("/api/model-catalog")
def model_catalog() -> dict:
    entries = []
    try:
        values = json.loads((ROOT / "model_catalog.json").read_text(encoding="utf-8"))
        for item in values if isinstance(values, list) else []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                entries.append({"name": item["name"], "repo_id": repository_id(item["repo_id"]),
                                "description": str(item.get("description", ""))})
    except (OSError, ValueError, KeyError, TypeError):
        return {"entries": [], "error": "The suggested-model catalog could not be read. You can still enter a repository."}
    return {"entries": entries, "error": None}


@app.get("/api/model-repository")
def model_repository(repo_id: str, revision: str = "main") -> dict:
    result = download_result(downloads.inspect, repo_id, revision)
    for file in result["files"]:
        file["thinking_support"] = thinking_support(file["save_name"])
        file["path"] = str((downloads.models_dir / file["save_name"]).resolve())
    return result


@app.get("/api/model-downloads")
def model_downloads() -> dict:
    return downloads.snapshot()


@app.post("/api/model-downloads")
def start_model_download(request: DownloadRequest) -> dict:
    return download_result(downloads.start, request.repo_id, request.filename, request.revision, request.save_name)


@app.post("/api/model-downloads/{identifier}")
def control_model_download(identifier: str, request: DownloadControl) -> dict:
    return download_result(downloads.control, identifier, request.action)


@app.get("/api/usage")
def token_usage(chat_id: str | None = None) -> dict:
    if chat_id is not None:
        chat_path(chat_id)
    return usage_store.snapshot(CHATS, chat_id)


@app.patch("/api/settings")
def save_settings(values: SettingsUpdate) -> dict:
    return write_config(values.model_dump(exclude_unset=True, exclude_none=True))


@app.post("/api/model/load")
def load_model(request: LoadRequest) -> dict:
    values = request.model_dump()
    return runtime.load(values, remember=True)


@app.post("/api/model/unload")
def unload_model(request: UnloadRequest | None = None) -> dict:
    return runtime.unload(request.load_id if request else None)


@app.get("/api/chats")
def list_chats() -> list[dict]:
    chats = [{key: view[key] for key in ("id", "title", "updated_at", "visibility", "branch_of")} for view in chat_views()]
    return sorted(chats, key=lambda item: item["updated_at"], reverse=True)


def chat_views() -> Iterator[dict]:
    for path in CHATS.glob("*.jsonl"):
        try:
            yield chat_view(path.stem)
        except Exception:
            logger.exception("Could not read chat %s", path)


@app.get("/api/chat-search")
def search_chats(q: str = Query(min_length=1, max_length=120),
                 visibility: Literal["active", "archived", "trashed"] = "active") -> list[dict]:
    query = q.strip()
    if not query:
        return []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    for chat in chat_views():
        if chat["visibility"] != visibility:
            continue
        summary = {"id": chat["id"], "title": chat["title"], "updated_at": chat["updated_at"]}
        matches = 0
        for message in reversed(chat["messages"]):
            content = message.get("content", "")
            match = pattern.search(content) if isinstance(content, str) else None
            if not match:
                continue
            start, end = max(0, match.start() - 45), min(len(content), match.end() + 75)
            snippet = " ".join(content[start:end].split())
            results.append({**summary, "message_id": message["id"], "role": message["type"],
                            "snippet": ("…" if start else "") + snippet + ("…" if end < len(content) else "")})
            matches += 1
            if matches == 3:
                break
        if not matches and isinstance(chat["title"], str) and pattern.search(chat["title"]):
            results.append({**summary, "message_id": None, "role": None, "snippet": ""})
    return sorted(results, key=lambda item: item["updated_at"], reverse=True)[:60]


@app.get("/api/chat-backup")
def chat_backup() -> dict:
    with chat_lock:
        chats = [{"id": path.stem, "events": read_events(path.stem)} for path in sorted(CHATS.glob("*.jsonl"))
                 if re.fullmatch(r"[0-9a-f-]{1,80}", path.stem)]
    return {"version": 1, "chats": chats}


def backup_lines(events: list[dict]) -> bytes:
    """Validate fields used by the chat reader before writing any restored files."""
    for row in events:
        kind = row.get("type")
        if not isinstance(row.get("time", ""), str):
            raise ValueError("Invalid chat timestamp")
        if kind in ("created", "renamed"):
            if not isinstance(row.get("title"), str) or not row["title"].strip():
                raise ValueError("Invalid chat title")
        elif kind == "settings":
            if not isinstance(row.get("values"), dict):
                raise ValueError("Invalid response settings")
            ResponseSettings(**row["values"])
        elif kind == "branched":
            if not isinstance(row.get("chat_id"), str) or not isinstance(row.get("message_id"), str):
                raise ValueError("Invalid branch reference")
            chat_path(row["chat_id"])
        elif kind == "visibility":
            ChatVisibility(visibility=row.get("value"))
        elif kind in ("user", "assistant"):
            if not isinstance(row.get("content"), str):
                raise ValueError("Invalid message text")
            for key in ("request_id", "message_id", "status", "error"):
                if row.get(key) is not None and not isinstance(row[key], str):
                    raise ValueError("Invalid message details")
            if row.get("metrics") is not None and not isinstance(row["metrics"], dict):
                raise ValueError("Invalid response metrics")
        else:
            raise ValueError("Unrecognized chat event")
    return "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in events).encode("utf-8")


@app.post("/api/chat-backup/restore")
def restore_chat_backup(request: BackupRestore) -> dict:
    try:
        prepared = [(chat_path(chat.id), backup_lines(chat.events)) for chat in request.chats]
    except (ValueError, HTTPException) as error:
        raise HTTPException(400, "The backup contains invalid chat data") from error
    restored, skipped = 0, 0
    try:
        with chat_lock:
            for path, lines in prepared:
                if path.exists():
                    skipped += 1
                    continue
                temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_bytes(lines)
                    if os.name == "nt":
                        temporary.rename(path)
                    else:
                        os.link(temporary, path)
                    restored += 1
                except FileExistsError:
                    skipped += 1
                finally:
                    temporary.unlink(missing_ok=True)
    except OSError as error:
        logger.exception("Could not finish restoring chat backup")
        raise HTTPException(500, f"Restore stopped after {restored} chats. Check available disk space and retry.") from error
    return {"restored": restored, "skipped": skipped}


@app.post("/api/chats")
def create_chat(request: ChatCreate) -> dict:
    chat_id = str(uuid.uuid4())
    defaults = {key: value for key, value in read_config().items() if key in ResponseSettings.model_fields}
    values = ResponseSettings(**{**defaults, **request.model_dump(exclude_unset=True)}).model_dump()
    with chat_lock:
        append_event(chat_id, {"type": "created", "title": "New chat"})
        append_event(chat_id, {"type": "settings", "values": values})
    return chat_view(chat_id)


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str) -> dict:
    return chat_view(chat_id)


@app.post("/api/chats/{chat_id}/branch")
def branch_chat(chat_id: str, request: BranchRequest) -> dict:
    if request.message is not None and not request.message.strip():
        raise HTTPException(400, "Enter a message")
    source = chat_view(chat_id)
    if source["visibility"] != "active":
        raise HTTPException(409, "Restore this chat before creating an alternative")
    target = next((index for index, item in enumerate(source["messages"])
                   if item["id"] == request.message_id and item["type"] == "assistant"), None)
    if target is None:
        raise HTTPException(404, "Response not found")
    if source["messages"][target].get("status", "complete") != "complete":
        raise HTTPException(409, "Use Retry for a failed or stopped response")
    user_index = next((index for index in range(target - 1, -1, -1)
                       if source["messages"][index]["type"] == "user"), None)
    if user_index is None:
        raise HTTPException(409, "The response has no source prompt")
    branch_id = str(uuid.uuid4())
    with chat_lock:
        append_event(branch_id, {"type": "created", "title": f"{source['title'][:85]} · Alternative"})
        append_event(branch_id, {"type": "branched", "chat_id": chat_id, "message_id": request.message_id})
        for index, item in enumerate(source["messages"][:user_index + 1]):
            if item["type"] == "user":
                values = {key: value for key, value in item["request"].items() if key in ResponseSettings.model_fields}
                if index == user_index and request.response_settings is not None:
                    values = request.response_settings.model_dump()
                append_event(branch_id, {"type": "settings", "values": values})
            copied = {key: value for key, value in item.items() if key not in {"id", "request", "time"}}
            if item["type"] == "user":
                copied["request_id"] = item["request"]["request_id"]
                if index == user_index and request.message is not None:
                    copied["content"] = request.message.strip()
                    copied["request_id"] = str(uuid.uuid4())
            else:
                copied["message_id"] = item["id"]
                copied["usage_inherited"] = True
            append_event(branch_id, copied)
    return chat_view(branch_id)


@app.get("/api/chats/{chat_id}/compare/{message_id}")
def compare_responses(chat_id: str, message_id: str) -> dict:
    def answer(chat, identifier):
        return next((item for item in chat["messages"] if item["id"] == identifier
                     and item["type"] == "assistant" and item.get("request")
                     and item.get("status", "complete") == "complete"), None)

    def card(chat, item):
        return {"chat_id": chat["id"], "title": chat["title"], "message_id": item["id"],
                "content": item["content"], "request": item["request"], "metrics": item.get("metrics"),
                "visibility": chat["visibility"]}

    def same_prompt(first, second):
        return all(first["request"][key] == second["request"][key] for key in ("request_id", "message"))

    chat = chat_view(chat_id)
    selected = answer(chat, message_id)
    if selected is None:
        raise HTTPException(404, "Completed response not found")
    relation = chat["branch_of"]
    if relation and not selected.get("usage_inherited") and chat_path(relation["chat_id"]).exists():
        source = chat_view(relation["chat_id"])
        original = answer(source, relation["message_id"])
        if source["visibility"] != "trashed" and original and same_prompt(selected, original):
            chat, selected = source, original

    relation = {"chat_id": chat["id"], "message_id": selected["id"]}
    alternatives = []
    for other in chat_views():
        if other["branch_of"] != relation or other["visibility"] == "trashed":
            continue
        item = next((item for item in other["messages"] if item["type"] == "assistant"
                     and not item.get("usage_inherited") and item.get("request") and same_prompt(item, selected)
                     and item.get("status", "complete") == "complete"), None)
        if item:
            alternatives.append(card(other, item))
    return {"original": card(chat, selected), "alternatives": alternatives}


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, request: ChatUpdate) -> dict:
    with chat_lock:
        read_events(chat_id)
        append_event(chat_id, {"type": "renamed", "title": request.title.strip()})
    return chat_view(chat_id)


@app.patch("/api/chats/{chat_id}/visibility")
def set_chat_visibility(chat_id: str, request: ChatVisibility) -> dict:
    with chat_lock:
        if runtime.active_chat_id == chat_id:
            raise HTTPException(409, "Stop this chat's response before moving it")
        chat = chat_view(chat_id)
        if chat["visibility"] != request.visibility:
            append_event(chat_id, {"type": "visibility", "value": request.visibility})
        return {**chat_view(chat_id), "previous_visibility": chat["visibility"]}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str) -> dict:
    path = chat_path(chat_id)
    if not path.exists():
        raise HTTPException(404, "Chat not found")
    acquired = runtime.generation_lock.acquire(blocking=False)
    if not acquired and runtime.active_chat_id in {None, chat_id}:
        raise HTTPException(409, "Stop this chat's response before deleting it")
    try:
        with chat_lock:
            usage_store.snapshot(CHATS)
            try:
                path.unlink()
            except FileNotFoundError as error:
                raise HTTPException(404, "Chat not found") from error
    finally:
        if acquired:
            runtime.generation_lock.release()
    return {"deleted": True}


@app.post("/api/generation/stop")
def stop_generation() -> dict:
    runtime.cancel.set()
    return {"stopping": runtime.generation_lock.locked()}


def require_loaded_model(expected_path: str | None) -> None:
    if runtime.llm is None or runtime.status != "ready":
        raise HTTPException(409, "Load a model before sending")
    if expected_path is not None and Path(expected_path).expanduser().resolve() != Path(runtime.model_path).resolve():
        raise HTTPException(409, "The loaded model changed. Select and load the model you want, then try again.")


def estimate_prompt_tokens(messages: list[dict]) -> int:
    return len(runtime.llm.tokenize(json.dumps(messages).encode("utf-8")))


def prepare_context(request: GenerateRequest, saved: list[dict]) -> dict:
    visible_message = request.message.strip()
    if not visible_message:
        raise HTTPException(400, "Enter a message")
    support = thinking_support(runtime.model_path)
    thinking_enabled = support == "supported" and request.thinking
    effective_message = visible_message
    if support == "supported":
        directive = "/think" if thinking_enabled else "/no_think"
        effective_message += f"\n\n{directive}"
    messages = []
    if request.system_prompt.strip():
        messages.append({"role": "system", "content": request.system_prompt.strip()})
    system_count = len(messages)
    pending_user = None
    for item in saved:
        if item["type"] == "user":
            pending_user = item
        elif pending_user and item.get("status", "complete") == "complete":
            messages.extend([
                {"role": "user", "content": pending_user["content"]},
                {"role": "assistant", "content": item.get("content", "")},
            ])
            pending_user = None
    messages.append({"role": "user", "content": effective_message})

    context_size = int(runtime.loaded_settings.get("context_size") or read_config()["context_size"])
    trimmed_messages = 0
    while len(messages) > system_count + 1:
        if estimate_prompt_tokens(messages) <= int(context_size * 0.80):
            break
        del messages[system_count:system_count + 2]
        trimmed_messages += 2
    return {"messages": messages, "context_size": context_size, "trimmed_messages": trimmed_messages,
            "thinking": thinking_enabled, "thinking_support": support}


@app.post("/api/context-preview")
def preview_context(request: ContextPreviewRequest) -> dict:
    if not runtime.generation_lock.acquire(blocking=False):
        raise HTTPException(409, "Wait for the current model operation before previewing context")
    try:
        require_loaded_model(request.expected_model_path)
        chat = chat_view(request.chat_id) if request.chat_id else {"visibility": "active", "messages": []}
        if chat["visibility"] != "active":
            raise HTTPException(409, "Restore this chat before previewing context")
        context = prepare_context(request, chat["messages"])
        return {**context, "prompt_tokens": estimate_prompt_tokens(context["messages"])}
    finally:
        runtime.generation_lock.release()


@app.post("/api/chats/{chat_id}/generate")
def generate(chat_id: str, request: GenerateRequest) -> StreamingResponse:
    if runtime.llm is None:
        raise HTTPException(409, "Load a model first")
    if not runtime.generation_lock.acquire(blocking=False):
        raise HTTPException(409, "A generation is already active")
    runtime.active_chat_id = chat_id

    runtime.cancel.clear()
    completed = None
    try:
        require_loaded_model(request.expected_model_path)
        chat = chat_view(chat_id)
        if chat["visibility"] != "active":
            raise HTTPException(409, "Restore this chat before sending a message")
        saved = chat["messages"]
        target = next((index for index, item in enumerate(saved)
                       if item["type"] == "user" and request.request_id
                       and item["request"]["request_id"] == request.request_id), None)
        if target is not None:
            if any(item["type"] == "user" for item in saved[target + 1:]):
                raise HTTPException(409, "Only the latest response can be retried")
            if saved[target]["content"] != request.message.strip():
                raise HTTPException(409, "This request ID belongs to a different prompt")
            # Retry the original response settings, even after reopening the chat.
            request = GenerateRequest(**{**saved[target]["request"], "expected_model_path": request.expected_model_path})
            completed = next((item for item in reversed(saved[target + 1:])
                              if item.get("status", "complete") == "complete"), None)

        if completed is None:
            visible_message = request.message.strip()
            request_id = request.request_id or str(uuid.uuid4())
            context = prepare_context(request, saved[:target])
            messages = context["messages"]
            context_size = context["context_size"]
            trimmed_messages = context["trimmed_messages"]
            support = context["thinking_support"]
            thinking_enabled = context["thinking"]

            settings = request.model_dump(exclude={"message", "request_id", "expected_model_path"})
            settings["model"] = runtime.model_path
            append_event(chat_id, {"type": "settings", "values": settings})
            if target is None:
                append_event(chat_id, {"type": "user", "content": visible_message,
                                       "status": "complete", "request_id": request_id})
                if not any(item["type"] == "user" for item in saved):
                    append_event(chat_id, {"type": "renamed", "title": visible_message[:60]})
    except Exception:
        runtime.cancel.clear()
        runtime.active_chat_id = None
        runtime.generation_lock.release()
        raise

    if completed is not None:
        # A lost connection may hide a successful answer. Replay it without inference.
        runtime.active_chat_id = None
        runtime.generation_lock.release()

        def replay() -> Iterator[str]:
            yield event({"type": "start", "message_id": completed["id"]})
            yield event({"type": "token", "text": completed.get("content", "")})
            yield event({"type": "done", "metrics": completed.get("metrics")})

        return StreamingResponse(replay(), media_type="application/x-ndjson")

    def stream() -> Iterator[str]:
        response = ""
        started = time.perf_counter()
        first_token = None
        message_id = str(uuid.uuid4())
        metrics = None

        def response_metrics():
            elapsed = time.perf_counter() - started
            prompt_tokens = estimate_prompt_tokens(messages)
            completion_tokens = len(runtime.llm.tokenize(response.encode("utf-8"))) if response else 0
            return {"first_token_seconds": round((first_token or time.perf_counter()) - started, 3),
                    "total_seconds": round(elapsed, 3), "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens, "token_counts_estimated": True,
                    "tokens_per_second": round(completion_tokens / elapsed, 2) if elapsed else 0,
                    "context_size": context_size, "context_used": prompt_tokens + completion_tokens,
                    "temperature": request.temperature, "thinking": thinking_enabled,
                    "thinking_support": support, "model": Path(runtime.model_path).name}

        def persist(status, error=None):
            nonlocal metrics
            try:
                metrics = response_metrics()
            except Exception:
                logger.exception("Token counts unavailable for response %s", message_id)
            append_event(chat_id, {"type": "assistant", "content": response, "status": status,
                                   "request_id": request_id, "message_id": message_id,
                                   "metrics": metrics, **({"error": error} if error else {})})
            try:
                usage_store.record(chat_id, message_id, metrics, status)
            except OSError:
                # The saved chat metrics can rebuild the ledger on the next visit.
                logger.exception("Could not update token usage ledger")
        try:
            yield event({"type": "start", "message_id": message_id})
            if trimmed_messages:
                yield event({
                    "type": "context_trimmed",
                    "messages": trimmed_messages,
                    "message": f"Excluded {trimmed_messages} older message(s) from this request.",
                })
            kwargs = {
                "messages": messages,
                "temperature": request.temperature,
                "top_p": request.top_p,
                "top_k": request.top_k,
                "min_p": request.min_p,
                "repeat_penalty": request.repeat_penalty,
                "seed": request.seed,
                "stream": True,
            }
            with closing(runtime.llm.create_chat_completion(**kwargs)) as chunks:
                for chunk in chunks:
                    if runtime.cancel.is_set():
                        break
                    text = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if not text:
                        continue
                    if first_token is None:
                        first_token = time.perf_counter()
                    response += text
                    yield event({"type": "token", "text": text})

            stopped = runtime.cancel.is_set()
            persist("interrupted" if stopped else "complete")
            terminal = {"type": "stopped" if stopped else "done", "metrics": metrics}
            logger.info("Generation %s", "stopped" if stopped else "completed")
        except GeneratorExit:
            persist("interrupted")
            raise
        except Exception as error:
            logger.exception("Generation failed")
            persist("error", str(error))
            terminal = {"type": "error", "message": str(error)}
        finally:
            runtime.cancel.clear()
            runtime.active_chat_id = None
            runtime.generation_lock.release()
        # The UI can issue its next request as soon as it receives this record.
        yield event(terminal)

    return StreamingResponse(stream(), media_type="application/x-ndjson")
