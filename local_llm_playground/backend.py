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

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
CHATS = DATA / "chats"
LOGS = ROOT / "logs"
CONFIG = DATA / "config.json"
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
}


def read_config() -> dict:
    try:
        loaded = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        loaded = {}
    return {**DEFAULTS, **loaded}


def write_config(values: dict) -> dict:
    config = {**read_config(), **values}
    CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


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
    created_at = events[0].get("time", "") if events else ""
    for index, event in enumerate(events):
        kind = event.get("type")
        if kind in {"created", "renamed"}:
            title = event.get("title", title)
        elif kind == "settings":
            settings.update(event.get("values", {}))
        elif kind == "user":
            generation_request = {
                key: value for key, value in settings.items()
                if key in GenerateRequest.model_fields
            }
            generation_request.update({
                "message": event.get("content", ""),
                "request_id": event.get("request_id") or f"legacy-{index}",
            })
            messages.append({**event, "request": generation_request})
        elif kind == "assistant":
            messages.append({**event, "request": generation_request})
    return {
        "id": chat_id,
        "title": title,
        "created_at": created_at,
        "updated_at": events[-1].get("time", created_at) if events else created_at,
        "settings": settings,
        "messages": messages,
    }


class ModelRuntime:

    def __init__(self) -> None:
        self.llm: Llama | None = None
        self.model_path = ""
        self.status = "not_loaded"
        self.error = ""
        self.cancel = threading.Event()
        self.generation_lock = threading.Lock()

    def load(self, settings: dict) -> None:
        if self.generation_lock.locked():
            raise HTTPException(409, "Stop generation before changing model")
        path = Path(str(settings["model_path"])).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise HTTPException(400, "Select a valid GGUF model")
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
            self.status = "ready"
            logger.info("Model loaded in %.2fs: %s", time.perf_counter() - started, path)
        except Exception as error:
            self.llm = None
            self.status = "error"
            self.error = str(error)
            logger.exception("Model load failed")
            raise HTTPException(500, str(error)) from error


runtime = ModelRuntime()
app = FastAPI(title="Local LLM Playground")
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")


class LoadRequest(BaseModel):
    model_path: str
    context_size: int = Field(4096, ge=256, le=131072)
    gpu_layers: int = Field(0, ge=-1, le=1000)
    threads: int = Field(DEFAULTS["threads"], ge=1, le=256)


class ChatCreate(BaseModel):
    system_prompt: str = ""
    thinking: bool = False
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class ChatUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class GenerateRequest(BaseModel):
    message: str = Field(min_length=1)
    request_id: str | None = Field(None, min_length=1, max_length=80, pattern=r"^[\w-]+$")
    system_prompt: str = ""
    thinking: bool = False
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    top_p: float = Field(0.95, ge=0.0, le=1.0)
    top_k: int = Field(40, ge=0, le=1000)
    min_p: float = Field(0.05, ge=0.0, le=1.0)
    repeat_penalty: float = Field(1.0, ge=0.0, le=2.0)
    seed: int = -1


def event(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/status")
def status() -> dict:
    return {
        "status": "generating" if runtime.generation_lock.locked() else runtime.status,
        "model_path": runtime.model_path,
        "error": runtime.error,
    }


@app.get("/api/models")
def models() -> list[dict]:
    return [{
        "path": str(path.resolve()),
        "name": path.name,
        "size": path.stat().st_size
    } for path in sorted((PROJECT_ROOT / "models").glob("*.gguf"))]


@app.get("/api/settings")
def get_settings() -> dict:
    return read_config()


@app.patch("/api/settings")
def save_settings(values: dict) -> dict:
    allowed = set(DEFAULTS)
    return write_config({key: value for key, value in values.items() if key in allowed})


@app.post("/api/model/load")
def load_model(request: LoadRequest) -> dict:
    values = request.model_dump()
    runtime.load(values)
    write_config(values)
    return {"status": runtime.status, "model_path": runtime.model_path}


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
    append_event(chat_id, {"type": "settings", "values": request.model_dump()})
    return chat_view(chat_id)


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str) -> dict:
    return chat_view(chat_id)


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
    path.unlink()
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

    runtime.cancel.clear()
    completed = None
    try:
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
            request = GenerateRequest(**saved[target]["request"])
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
            directive = "/think" if request.thinking else "/no_think"
            messages = []
            if request.system_prompt.strip():
                messages.append({"role": "system", "content": request.system_prompt.strip()})
            system_count = len(messages)
            messages.extend(history)
            messages.append({"role": "user", "content": f"{visible_message}\n\n{directive}"})

            context_size = int(read_config()["context_size"])
            trimmed_messages = 0
            while len(messages) > system_count + 1:
                estimated = len(runtime.llm.tokenize(json.dumps(messages).encode("utf-8")))
                if estimated <= int(context_size * 0.80):
                    break
                del messages[system_count:system_count + 2]
                trimmed_messages += 2

            settings = request.model_dump(exclude={"message", "request_id"})
            settings["model"] = runtime.model_path
            append_event(chat_id, {"type": "settings", "values": settings})
            if target is None:
                append_event(chat_id, {"type": "user", "content": visible_message,
                                       "status": "complete", "request_id": request_id})
                if not any(item["type"] == "user" for item in saved):
                    append_event(chat_id, {"type": "renamed", "title": visible_message[:60]})
    except Exception:
        runtime.cancel.clear()
        runtime.generation_lock.release()
        raise

    if completed is not None:
        # A lost connection may hide a successful answer. Replay it without inference.
        runtime.generation_lock.release()

        def replay() -> Iterator[str]:
            yield event({"type": "start"})
            yield event({"type": "token", "text": completed.get("content", "")})
            yield event({"type": "done", "metrics": completed.get("metrics")})

        return StreamingResponse(replay(), media_type="application/x-ndjson")

    def stream() -> Iterator[str]:
        response = ""
        started = time.perf_counter()
        first_token = None
        message_id = str(uuid.uuid4())
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

            elapsed = time.perf_counter() - started
            stopped = runtime.cancel.is_set()
            prompt_tokens = len(runtime.llm.tokenize(json.dumps(messages).encode("utf-8")))
            completion_tokens = len(runtime.llm.tokenize(
                response.encode("utf-8"))) if response else 0
            metrics = {
                "first_token_seconds": round((first_token or time.perf_counter()) - started, 3),
                "total_seconds": round(elapsed, 3),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens_per_second": round(completion_tokens / elapsed, 2) if elapsed else 0,
                "context_size": context_size,
                "context_used": prompt_tokens + completion_tokens,
                "temperature": request.temperature,
                "thinking": request.thinking,
                "model": Path(runtime.model_path).name,
            }
            append_event(
                chat_id,
                {
                    "type": "assistant",
                    "content": response,
                    "status": "interrupted" if stopped else "complete",
                    "request_id": request_id,
                    "metrics": metrics,
                },
            )
            terminal = {"type": "stopped" if stopped else "done", "metrics": metrics}
            logger.info("Generation %s in %.2fs", "stopped" if stopped else "completed", elapsed)
        except GeneratorExit:
            append_event(chat_id, {
                "type": "assistant", "content": response, "status": "interrupted",
                "request_id": request_id,
            })
            raise
        except Exception as error:
            logger.exception("Generation failed")
            append_event(chat_id, {
                "type": "assistant",
                "content": response,
                "status": "error",
                "request_id": request_id,
                "error": str(error)
            })
            terminal = {"type": "error", "message": str(error)}
        finally:
            runtime.cancel.clear()
            runtime.generation_lock.release()
        # The UI can issue its next request as soon as it receives this record.
        yield event(terminal)

    return StreamingResponse(stream(), media_type="application/x-ndjson")
