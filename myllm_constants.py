from __future__ import annotations

from pathlib import Path

# ============================================================
# APPLICATION PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

APP_DIR = SCRIPT_DIR / ".myllm"

CONFIG_FILE = APP_DIR / "config.json"

MEMORY_ROOT = APP_DIR / "memory"

PAYLOAD_ROOT = APP_DIR / "payloads"

HISTORY_ROOT = APP_DIR / "history"

LOG_ROOT = SCRIPT_DIR / "myllm_logs"


# ============================================================
# DEFAULT CONFIGURATION
# ============================================================

DEFAULT_CONFIG = {
    "model_path": "",
    "project_root": str(SCRIPT_DIR),
    "context_size": 16384,
    "gpu_layers": 0,
    "max_steps": 30,
    "max_no_progress_steps": 5,
    "temperature": 0.15,
    "top_p": 0.95,
    "top_k": 40,
    "min_p": 0.05,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,
    "router_temperature": 0.1,
    "chat_temperature": 0.7,
    "debug_level": 1,
    "recent_observations": 6,
    "prompt_cache_enabled": True,
    "prompt_cache_mb": 1024,
    "trim_context_ratio": 0.72,
    # 0 = do not pass max_tokens to llama.cpp.
    "max_model_output_tokens": 0,
    "payload_externalize_chars": 700,
    "payload_max_files": 250,
    "logging_enabled": True,
    "model_profile": "Generic GGUF",
}


# ============================================================
# AGENT LIMITS
# ============================================================

MAX_IDENTICAL_ACTIONS = 2

MAX_SESSION_MESSAGES = 12

SMALL_STUB_LINES = 40


# ============================================================
# PATH DETECTION
# ============================================================

KNOWN_FILE_EXTENSIONS = (
    "py",
    "pyi",
    "java",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "ts",
    "tsx",
    "html",
    "css",
    "json",
    "md",
    "txt",
    "xml",
    "toml",
    "yaml",
    "yml",
    "ini",
    "cfg",
    "properties",
    "rs",
    "go",
    "gradle",
    "kts",
    "sql",
    "sh",
    "ps1",
)
