from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

from datetime import datetime
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from llama_cpp import Llama
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
    with chat_path(chat_id).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(chat_id: str) -> list[dict]:
    path = chat_path(chat_id)
    if not path.exists():
        raise HTTPException(404, "Chat not found")
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
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
    created_at = events[0].get("time", "") if events else ""
    for index, event in enumerate(events):
        kind = event.get("type")
        if kind in {"created", "renamed"}:
            title = event.get("title", title)
        elif kind == "settings":
            settings.update(event.get("values", {}))
        elif kind == "branched":
            branch_of = {"chat_id": event["chat_id"], "message_id": event["message_id"]}
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

    def load(self, settings: dict, remember: bool = False) -> dict:
        path = Path(str(settings["model_path"])).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise HTTPException(400, "Select a valid GGUF model")
        if not self.generation_lock.acquire(blocking=False):
            raise HTTPException(409, "Stop generation before changing model")
        self.status = "loading"
        self.error = ""
        started = time.perf_counter()
        try:
            self.llm = Llama(
                model_path=str(path),
                n_ctx=int(settings["context_size"]),
                n_gpu_layers=int(settings["gpu_layers"]),
                n_threads=int(settings["threads"]),
                n_threads_batch=int(settings["threads"]),
                use_mmap=True,
                verbose=False,
            )
            self.model_path = str(path)
            self.loaded_settings = {key: settings[key] for key in ("context_size", "gpu_layers", "threads")}
            self.loaded_settings["model_path"] = str(path)
            self.status = "ready"
            remembered = True
            if remember:
                try:
                    write_config(self.loaded_settings)
                except OSError:
                    logger.exception("Model loaded but loading settings could not be remembered")
                    remembered = False
            logger.info("Model loaded in %.2fs: %s", time.perf_counter() - started, path)
            return {"status": "ready", "model_path": self.model_path, "error": "",
                    "thinking_support": thinking_support(self.model_path),
                    "loaded_settings": dict(self.loaded_settings), "load_settings_saved": remembered}
        except Exception as error:
            self.llm = None
            self.model_path = ""
            self.loaded_settings = {}
            self.status = "error"
            self.error = str(error)
            logger.exception("Model load failed")
            raise HTTPException(500, str(error)) from error
        finally:
            self.generation_lock.release()


runtime = ModelRuntime()
app = FastAPI(title="Local LLM Playground")
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")


class LoadRequest(BaseModel):
    model_path: str
    context_size: int = Field(4096, ge=256, le=131072)
    gpu_layers: int = Field(0, ge=-1, le=1000)
    threads: int = Field(DEFAULTS["threads"], ge=1, le=256)


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


class BranchRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=100)
    message: str | None = Field(None, min_length=1, max_length=100000)


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


def event(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/status")
def status() -> dict:
    model_path = runtime.model_path if runtime.llm is not None else ""
    return {
        "status": runtime.status if runtime.status == "loading" else "generating" if runtime.generation_lock.locked() else runtime.status,
        "model_path": model_path,
        "thinking_support": thinking_support(model_path) if model_path else "unknown",
        "loaded_settings": runtime.loaded_settings if model_path else {},
        "error": runtime.error,
    }


@app.get("/api/models")
def models() -> list[dict]:
    return [{
        "path": str(path.resolve()),
        "name": path.name,
        "size": path.stat().st_size,
        "thinking_support": thinking_support(str(path)),
    } for path in sorted((PROJECT_ROOT / "models").glob("*.gguf"))]


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


@app.get("/api/chats")
def list_chats() -> list[dict]:
    chats = []
    for path in CHATS.glob("*.jsonl"):
        try:
            view = chat_view(path.stem)
            chats.append({key: view[key] for key in ("id", "title", "updated_at")})
        except Exception:
            logger.exception("Could not read chat %s", path)
    return sorted(chats, key=lambda item: item["updated_at"], reverse=True)


@app.post("/api/chats")
def create_chat(request: ChatCreate) -> dict:
    chat_id = str(uuid.uuid4())
    append_event(chat_id, {"type": "created", "title": "New chat"})
    defaults = {key: value for key, value in read_config().items() if key in ResponseSettings.model_fields}
    values = ResponseSettings(**{**defaults, **request.model_dump(exclude_unset=True)}).model_dump()
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
    append_event(branch_id, {"type": "created", "title": f"{source['title'][:85]} · Alternative"})
    append_event(branch_id, {"type": "branched", "chat_id": chat_id, "message_id": request.message_id})
    for index, item in enumerate(source["messages"][:user_index + 1]):
        if item["type"] == "user":
            values = {key: value for key, value in item["request"].items() if key in ResponseSettings.model_fields}
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


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, request: ChatUpdate) -> dict:
    read_events(chat_id)
    append_event(chat_id, {"type": "renamed", "title": request.title.strip()})
    return chat_view(chat_id)


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str) -> dict:
    path = chat_path(chat_id)
    if not path.exists():
        raise HTTPException(404, "Chat not found")
    acquired = runtime.generation_lock.acquire(blocking=False)
    if not acquired and runtime.active_chat_id in {None, chat_id}:
        raise HTTPException(409, "Stop this chat's response before deleting it")
    try:
        usage_store.snapshot(CHATS)
        path.unlink()
    finally:
        if acquired:
            runtime.generation_lock.release()
    return {"deleted": True}


@app.post("/api/generation/stop")
def stop_generation() -> dict:
    runtime.cancel.set()
    return {"stopping": runtime.generation_lock.locked()}


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
        if (request.expected_model_path is not None
                and Path(request.expected_model_path).expanduser().resolve() != Path(runtime.model_path).resolve()):
            raise HTTPException(409, "The loaded model changed. Select and load the model you want, then try again.")
        chat = chat_view(chat_id)
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
            if not visible_message:
                raise HTTPException(400, "Enter a message")
            request_id = request.request_id or str(uuid.uuid4())
            history = []
            pending_user = None
            for item in saved[:target] if target is not None else saved:
                if item["type"] == "user":
                    pending_user = item
                elif pending_user and item.get("status", "complete") == "complete":
                    history.extend([
                        {"role": "user", "content": pending_user["content"]},
                        {"role": "assistant", "content": item.get("content", "")},
                    ])
                    pending_user = None
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
            messages.extend(history)
            messages.append({"role": "user", "content": effective_message})

            context_size = int(runtime.loaded_settings.get("context_size") or read_config()["context_size"])
            trimmed_messages = 0
            while len(messages) > system_count + 1:
                estimated = len(runtime.llm.tokenize(json.dumps(messages).encode("utf-8")))
                if estimated <= int(context_size * 0.80):
                    break
                del messages[system_count:system_count + 2]
                trimmed_messages += 2

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
            prompt_tokens = len(runtime.llm.tokenize(json.dumps(messages).encode("utf-8")))
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
            for chunk in runtime.llm.create_chat_completion(**kwargs):
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
