from __future__ import annotations

import json
import os
import sys

from datetime import datetime
from pathlib import Path

from llama_cpp import Llama

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / ".myllm" / "config.json"


def load_settings() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_model(settings: dict) -> Path:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"Model does not exist: {path}")

    models = sorted((ROOT / "models").glob("*.gguf"))
    configured = str(Path(settings.get("model_path", "")).resolve())

    print("Available models:\n")
    for index, path in enumerate(models, start=1):
        marker = " [configured]" if str(path.resolve()) == configured else ""
        print(f"{index}. {path.name}{marker}")
    print(f"{len(models) + 1}. Enter model path")

    while True:
        choice = input("\nSelect model: ").strip()
        if choice.isdigit():
            number = int(choice)
            if 1 <= number <= len(models):
                return models[number - 1].resolve()
            if number == len(models) + 1:
                path = Path(input("GGUF path: ").strip().strip('"')).expanduser()
                path = path.resolve()
                if path.is_file():
                    return path
                print("Model file not found.")
                continue
        print("Enter a valid menu number.")


def make_log_path() -> Path:
    now = datetime.now()
    directory = ROOT / "model_chat_logs" / now.strftime("%Y-%m-%d")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"model-chat-{now:%Y-%m-%d-%H%M%S}.jsonl"


def log_event(path: Path, event: str, **values) -> None:
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        **values,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    settings = load_settings()
    model_path = find_model(settings)
    log_path = make_log_path()
    context = int(settings.get("context_size", 8192))
    temperature = float(settings.get("temperature", 0.18))
    threads = max(1, (os.cpu_count() or 4) - 2)

    print(f"Model: {model_path.name}")
    print(f"Context: {context} | Temperature: {temperature}")
    print(f"Log: {log_path}")
    print("Commands: /reset, /exit\n")

    log_event(
        log_path,
        "start",
        model=str(model_path),
        context=context,
        temperature=temperature,
    )

    llm = Llama(
        model_path=str(model_path),
        n_ctx=context,
        n_gpu_layers=int(settings.get("gpu_layers", 0)),
        n_threads=threads,
        n_threads_batch=threads,
        use_mmap=True,
        verbose=False,
    )
    messages: list[dict[str, str]] = []

    while True:
        try:
            prompt = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not prompt:
            continue
        if prompt.lower() == "/exit":
            break
        if prompt.lower() == "/reset":
            messages.clear()
            log_event(log_path, "reset")
            print("Conversation reset.\n")
            continue

        messages.append({"role": "user", "content": prompt})
        log_event(log_path, "user", content=prompt)

        print("Model> ", end="", flush=True)
        response = ""

        try:
            stream = llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            for chunk in stream:
                text = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if text:
                    response += text
                    print(text, end="", flush=True)
        except KeyboardInterrupt:
            print("\n[interrupted]", end="")
            log_event(log_path, "interrupted", partial=response)
        except Exception as error:
            print(f"\n[error] {error}", end="")
            log_event(log_path, "error", message=str(error), partial=response)

        print("\n")
        if response:
            messages.append({"role": "assistant", "content": response})
            log_event(log_path, "assistant", content=response)


if __name__ == "__main__":
    main()
