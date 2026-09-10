from __future__ import annotations

from pathlib import Path
from typing import Any


FALLBACK_PROFILE = {
    "name": "Generic GGUF",
    "match": (),
    "settings": {
        "context_size": 16384,
        "max_model_output_tokens": 0,
        "temperature": 0.2,
        "top_p": 0.95,
        "top_k": 40,
        "min_p": 0.05,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    },
}


MODEL_PROFILES = (
    {
        "name": "Qwen3.5 precise coding",
        "match": ("qwen3.5", "qwen35"),
        "settings": {
            "context_size": 32768,
            "max_model_output_tokens": 0,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.0,
        },
    },
    {
        "name": "Qwen3 4B coding",
        "match": ("qwen3-4b", "qwen3_4b"),
        "settings": {
            "context_size": 16384,
            "max_model_output_tokens": 0,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.0,
        },
    },
    {
        "name": "Qwen3 1.7B",
        "match": ("qwen3-1.7b", "qwen3_1.7b"),
        "settings": {
            "context_size": 8192,
            "max_model_output_tokens": 0,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.0,
        },
    },
    {
        "name": "FunctionGemma experiment",
        "match": ("functiongemma",),
        "settings": {
            "context_size": 4096,
            "max_model_output_tokens": 0,
            "temperature": 0.1,
            "top_p": 0.95,
            "top_k": 40,
            "min_p": 0.05,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.0,
        },
    },
)


def model_profile(model_path: str | Path) -> dict[str, Any]:
    name = Path(model_path).name.lower()

    for profile in MODEL_PROFILES:
        if any(marker in name for marker in profile["match"]):
            return profile

    return FALLBACK_PROFILE


def apply_model_profile(
    config: dict[str, Any],
    model_path: str | Path,
) -> dict[str, Any]:
    profile = model_profile(model_path)
    config.update(profile["settings"])
    config["model_profile"] = profile["name"]
    return profile
