from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


DUAL_MODEL_DIRECTORY = Path(__file__).resolve().parents[1]
RESULTS_DIRECTORY = DUAL_MODEL_DIRECTORY / "temperature_results"
SAMPLING = {
    "top_p": 0.9,
    "top_k": 40,
    "min_p": 0.05,
}
DEFAULT_SEEDS = (11, 42, 73)


def parse_numbers(value: str, number_type: type) -> tuple:
    try:
        values = tuple(number_type(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use comma-separated numbers.") from error

    if not values:
        raise argparse.ArgumentTypeError("Provide at least one number.")

    return values


def create_completion(
    model,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    seed: int,
):
    return create_messages_completion(
        model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature,
        seed,
    )


def create_messages_completion(
    model,
    messages: list[dict[str, str]],
    temperature: float,
    seed: int,
):
    started = time.perf_counter()
    response = model.create_chat_completion(
        messages=messages,
        temperature=temperature,
        seed=seed,
        stream=False,
        **SAMPLING,
    )
    duration_seconds = time.perf_counter() - started
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

    if not isinstance(content, str) or not content.strip():
        raise ValueError("The model returned no text.")

    return content, response.get("usage", {}), duration_seconds, response


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
