from __future__ import annotations

import multiprocessing as mp
import queue
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol


@dataclass(frozen=True)
class ModelProfile:
    name: str
    architecture: str
    context_size: int
    temperature: float
    max_output_tokens: int | None
    parser_name: str
    thinking_mode: str
    chat_template_source: str
    supports_native_tools: bool
    quantization: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelRequest:
    temperature: float
    max_output_tokens: int | None = None
    top_p: float = 0.95
    top_k: int = 20
    min_p: float = 0.0
    repeat_penalty: float = 1.0


@dataclass(frozen=True)
class ModelChunk:
    text: str = ""
    reasoning: str = ""
    first: bool = False


class ModelProvider(Protocol):
    def stream(self, messages: list[dict[str, str]], request: ModelRequest) -> Iterator[ModelChunk]: ...
    def metadata(self) -> ModelProfile: ...
    def cancel_current(self, grace_seconds: float = 1.5) -> None: ...
    def close(self) -> None: ...


def _profile_from_metadata(model_path: str, metadata: dict[str, Any], requested_context: int) -> ModelProfile:
    filename = Path(model_path).name
    lowered = filename.casefold()
    architecture = str(metadata.get("general.architecture", "unknown"))
    name = str(metadata.get("general.name", filename))
    template = str(metadata.get("tokenizer.chat_template", ""))
    quantization = str(metadata.get("general.file_type", metadata.get("general.quantization_version", "unknown")))
    warnings: list[str] = []

    source = "gguf-metadata" if template else "runtime-default"
    combined = f"{name} {architecture} {template}".casefold()
    if "qwen3.5" in combined or "qwen35" in combined or "qwen3.5" in lowered:
        parser = "qwen35"
        default_temperature = 0.6
        thinking = "template-default; per-request control unavailable in direct binding"
    elif "qwen" in combined or "qwen" in lowered:
        parser = "hermes"
        default_temperature = 0.6
        thinking = "template-dependent"
    else:
        parser = "natural"
        default_temperature = 0.2
        thinking = "unknown"
        warnings.append("Model protocol was not recognized; using the explicit natural protocol.")
    if not template:
        warnings.append("GGUF chat template was not exposed; runtime/default template is being used.")

    return ModelProfile(
        name=name,
        architecture=architecture,
        context_size=requested_context,
        temperature=default_temperature,
        max_output_tokens=None,
        parser_name=parser,
        thinking_mode=thinking,
        chat_template_source=source,
        supports_native_tools=False,
        quantization=quantization,
        warnings=tuple(warnings),
    )


def _worker_main(
    model_path: str,
    load_options: dict[str, Any],
    requests: Any,
    responses: Any,
    cancel_event: Any,
) -> None:
    try:
        from llama_cpp import Llama

        model = Llama(model_path=model_path, verbose=False, **load_options)
        metadata = {str(key): value for key, value in dict(getattr(model, "metadata", {}) or {}).items()}
        profile = _profile_from_metadata(model_path, metadata, int(load_options["n_ctx"]))
        responses.put({"type": "ready", "profile": asdict(profile)})
    except Exception:
        responses.put({"type": "fatal", "error": traceback.format_exc()})
        return

    while True:
        command = requests.get()
        if command.get("type") == "close":
            return
        if command.get("type") != "generate":
            continue
        request_id = command["request_id"]
        cancel_event.clear()
        try:
            kwargs: dict[str, Any] = {
                "messages": command["messages"],
                "temperature": command["request"]["temperature"],
                "top_p": command["request"]["top_p"],
                "top_k": command["request"]["top_k"],
                "min_p": command["request"]["min_p"],
                "repeat_penalty": command["request"]["repeat_penalty"],
                "stream": True,
            }
            maximum = command["request"].get("max_output_tokens")
            if isinstance(maximum, int) and maximum > 0:
                kwargs["max_tokens"] = maximum

            first = True
            for chunk in model.create_chat_completion(**kwargs):
                if cancel_event.is_set():
                    responses.put({"type": "cancelled", "request_id": request_id})
                    break
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta", {})
                text = delta.get("content") or ""
                reasoning = delta.get("reasoning_content") or ""
                if text or reasoning:
                    responses.put({
                        "type": "chunk",
                        "request_id": request_id,
                        "text": text,
                        "reasoning": reasoning,
                        "first": first,
                    })
                    first = False
            else:
                responses.put({"type": "done", "request_id": request_id})
        except Exception:
            responses.put({"type": "error", "request_id": request_id, "error": traceback.format_exc()})


class LlamaCppProvider:
    def __init__(
        self,
        model_path: str | Path,
        *,
        context_size: int = 32768,
        gpu_layers: int = 0,
        threads: int | None = None,
    ) -> None:
        self.model_path = str(Path(model_path).expanduser().resolve(strict=True))
        self.load_options = {
            "n_ctx": context_size,
            "n_gpu_layers": gpu_layers,
            "n_threads": threads or max(1, (mp.cpu_count() or 4) - 1),
        }
        self._context = mp.get_context("spawn")
        self._requests: Any = None
        self._responses: Any = None
        self._cancel: Any = None
        self._process: mp.Process | None = None
        self._profile: ModelProfile | None = None
        self._active_request: str | None = None

    def _start(self) -> None:
        if self._process and self._process.is_alive() and self._profile is not None:
            return
        if self._process:
            self._terminate_worker()
        self._requests = self._context.Queue(maxsize=8)
        self._responses = self._context.Queue(maxsize=256)
        self._cancel = self._context.Event()
        self._process = self._context.Process(
            target=_worker_main,
            args=(self.model_path, self.load_options, self._requests, self._responses, self._cancel),
            name="myllm-provider",
            daemon=True,
        )
        self._process.start()
        deadline = time.monotonic() + 600
        try:
            while time.monotonic() < deadline:
                try:
                    item = self._responses.get(timeout=0.25)
                except queue.Empty:
                    if not self._process.is_alive():
                        raise RuntimeError("Model worker exited while loading the model.")
                    continue
                if item["type"] == "ready":
                    self._profile = ModelProfile(**item["profile"])
                    return
                if item["type"] == "fatal":
                    self.close()
                    raise RuntimeError("Model failed to load:\n" + item["error"])
        except BaseException:
            self._terminate_worker()
            raise
        self.close()
        raise TimeoutError("Model worker did not load within 10 minutes.")

    def metadata(self) -> ModelProfile:
        self._start()
        assert self._profile is not None
        return self._profile

    def stream(self, messages: list[dict[str, str]], request: ModelRequest) -> Iterator[ModelChunk]:
        self._start()
        request_id = f"request-{time.time_ns()}"
        self._active_request = request_id
        self._cancel.clear()
        self._requests.put({
            "type": "generate",
            "request_id": request_id,
            "messages": messages,
            "request": asdict(request),
        })
        cancel_deadline: float | None = None
        try:
            while True:
                try:
                    item = self._responses.get(timeout=0.1)
                except queue.Empty:
                    if not self._process or not self._process.is_alive():
                        raise RuntimeError("Model worker exited during generation.")
                    if self._cancel.is_set():
                        cancel_deadline = cancel_deadline or time.monotonic() + 1.5
                        if time.monotonic() >= cancel_deadline:
                            self._terminate_worker()
                            raise InterruptedError("Model generation was force-stopped.")
                    continue
                if item.get("request_id") != request_id:
                    continue
                if item["type"] == "chunk":
                    yield ModelChunk(item.get("text", ""), item.get("reasoning", ""), bool(item.get("first")))
                elif item["type"] == "done":
                    return
                elif item["type"] == "cancelled":
                    raise InterruptedError("Model generation was cancelled.")
                elif item["type"] == "error":
                    raise RuntimeError("Model generation failed:\n" + item["error"])
        finally:
            self._active_request = None

    def cancel_current(self, grace_seconds: float = 1.5) -> None:
        if not self._process or not self._process.is_alive():
            return
        if self._profile is None:
            self._terminate_worker()
            return
        if not self._active_request:
            return
        self._cancel.set()
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and self._process.is_alive() and self._active_request:
            time.sleep(0.05)
        if self._active_request:
            self._terminate_worker()

    def _terminate_worker(self) -> None:
        if not self._process:
            return
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
        self._process.close()
        self._process = None
        self._profile = None

    def close(self) -> None:
        if not self._process:
            return
        if self._process.is_alive() and self._requests:
            try:
                self._requests.put({"type": "close"}, timeout=0.2)
                self._process.join(timeout=2)
            except Exception:
                pass
        self._terminate_worker()
