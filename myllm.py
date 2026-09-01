from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lancedb
from llama_cpp import Llama

from myllm_tools import (
    ProjectProfile,
    Workspace,
    Tools,
    TOOL_DOCS,
    build_tool_registry,
    detect_project_profile,
    profile_to_prompt,
    truncate_text,
)


# ============================================================
# APPLICATION PATHS
# ============================================================

SCRIPT_DIR = (
    Path(__file__)
    .resolve()
    .parent
)

APP_DIR = (
    SCRIPT_DIR
    / ".myllm"
)

CONFIG_FILE = (
    APP_DIR
    / "config.json"
)

MEMORY_ROOT = (
    APP_DIR
    / "memory"
)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "model_path": "",
    "project_root": str(
        SCRIPT_DIR
    ),
    "context_size": 8192,
    "gpu_layers": 0,
    "max_steps": 30,
    "temperature": 0.1,
    "debug_level": 1,
    "max_no_progress_steps": 6,
    "recent_observations": 8,
}


MAX_IDENTICAL_ACTIONS = 2
MAX_MODEL_OUTPUT_TOKENS = 1100


# ============================================================
# CONFIG STORAGE
# ============================================================

def ensure_app_directory() -> None:

    APP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    MEMORY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )


def save_config(
    config: dict[str, Any],
) -> None:

    ensure_app_directory()

    CONFIG_FILE.write_text(
        json.dumps(
            config,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_config() -> dict[str, Any]:

    ensure_app_directory()

    if not CONFIG_FILE.exists():

        config = (
            DEFAULT_CONFIG.copy()
        )

        save_config(
            config
        )

        return config

    try:

        loaded = json.loads(
            CONFIG_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        loaded = {}

    config = (
        DEFAULT_CONFIG.copy()
    )

    config.update(
        loaded
    )

    save_config(
        config
    )

    return config


# ============================================================
# STATE
# ============================================================

@dataclass
class Observation:
    id: str
    tool: str
    args: dict[str, Any]
    text: str
    success: bool
    timestamp: float = field(
        default_factory=time.time
    )


@dataclass
class TaskConstraints:
    requested_languages: set[str] = field(
        default_factory=set
    )

    forbidden_languages: set[str] = field(
        default_factory=set
    )

    requested_technologies: set[str] = field(
        default_factory=set
    )

    forbidden_technologies: set[str] = field(
        default_factory=set
    )

    browser_app: bool = False
    frontend_required: bool = False


@dataclass
class AgentState:
    task: str

    step: int = 0

    observations: list[Observation] = field(
        default_factory=list
    )

    action_counts: dict[str, int] = field(
        default_factory=dict
    )

    edited_files: set[str] = field(
        default_factory=set
    )

    edit_backups: list[
        tuple[Path, str, bool]
    ] = field(
        default_factory=list
    )

    last_test_passed: bool = False
    last_validation_passed: bool = False

    unavailable_capabilities: set[str] = field(
        default_factory=set
    )

    no_progress_steps: int = 0

    created_files: set[str] = field(
        default_factory=set
    )

    modified_files: set[str] = field(
        default_factory=set
    )

    read_files: set[str] = field(
        default_factory=set
    )

    blockers: list[str] = field(
        default_factory=list
    )

    latest_result: str = ""


# ============================================================
# UI
# ============================================================

def clear_screen() -> None:

    os.system(
        "cls"
        if os.name == "nt"
        else "clear"
    )


def pause() -> None:

    input(
        "\nPress Enter to continue..."
    )


def print_header(
    title: str,
) -> None:

    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# PROJECT INFO
# ============================================================

def detect_environment(
    root: Path,
) -> dict[str, Any]:

    return {
        "os": (
            f"{platform.system()} "
            f"{platform.release()}"
        ),

        "python": (
            sys.version.split()[0]
        ),

        "workspace": (
            str(root)
        ),

        "git_repository": (
            root / ".git"
        ).exists(),
    }


# ============================================================
# TASK CONSTRAINTS
# ============================================================

def detect_task_constraints(
    task: str,
) -> TaskConstraints:

    text = (
        task.lower()
    )

    padded = (
        f" {text} "
    )

    constraints = (
        TaskConstraints()
    )

    # --------------------------------------------------------
    # LANGUAGES
    # --------------------------------------------------------

    if (
        "javascript" in text
        or re.search(
            r"\bjs\b",
            text,
        )
    ):
        constraints.requested_languages.add(
            "javascript"
        )

    if (
        "typescript" in text
        or re.search(
            r"\bts\b",
            text,
        )
    ):
        constraints.requested_languages.add(
            "typescript"
        )

    if (
        "python" in text
    ):
        constraints.requested_languages.add(
            "python"
        )

    if (
        re.search(
            r"\bjava\b",
            text,
        )
        and "javascript"
        not in text
    ):
        constraints.requested_languages.add(
            "java"
        )

    if (
        re.search(
            r"\brust\b",
            text,
        )
    ):
        constraints.requested_languages.add(
            "rust"
        )

    if (
        re.search(
            r"\bgo\b",
            text,
        )
        and (
            "golang" in text
            or "go app" in text
            or "go project" in text
        )
    ):
        constraints.requested_languages.add(
            "go"
        )

    # --------------------------------------------------------
    # TECHNOLOGIES
    # --------------------------------------------------------

    if "react" in text:

        constraints.requested_technologies.add(
            "react"
        )

        constraints.requested_languages.add(
            "javascript"
        )

        constraints.browser_app = True
        constraints.frontend_required = True

    if "next.js" in text or "nextjs" in text:

        constraints.requested_technologies.add(
            "next.js"
        )

        constraints.requested_languages.add(
            "javascript"
        )

        constraints.browser_app = True
        constraints.frontend_required = True

    if "vite" in text:

        constraints.requested_technologies.add(
            "vite"
        )

        constraints.browser_app = True

    if "pygame" in text:

        constraints.requested_technologies.add(
            "pygame"
        )

        constraints.requested_languages.add(
            "python"
        )

    # --------------------------------------------------------
    # FRONTEND / BROWSER
    # --------------------------------------------------------

    frontend_phrases = [
        "frontend",
        "front-end",
        "browser",
        "web app",
        "webapp",
        "website",
        "html",
    ]

    if any(
        phrase in text
        for phrase
        in frontend_phrases
    ):
        constraints.browser_app = True
        constraints.frontend_required = True

    # --------------------------------------------------------
    # NEGATIVE CONSTRAINTS
    # --------------------------------------------------------

    if (
        "not python" in text
        or "no python" in text
        or "without python" in text
    ):
        constraints.forbidden_languages.add(
            "python"
        )

    if (
        "not pygame" in text
        or "no pygame" in text
    ):
        constraints.forbidden_technologies.add(
            "pygame"
        )

    return constraints


def constraints_to_prompt(
    constraints: TaskConstraints,
) -> str:

    return (
        "REQUESTED LANGUAGES: "
        + (
            ", ".join(
                sorted(
                    constraints.requested_languages
                )
            )
            or "not explicitly specified"
        )
        + "\n"
        + "FORBIDDEN LANGUAGES: "
        + (
            ", ".join(
                sorted(
                    constraints.forbidden_languages
                )
            )
            or "none"
        )
        + "\n"
        + "REQUESTED TECHNOLOGIES: "
        + (
            ", ".join(
                sorted(
                    constraints.requested_technologies
                )
            )
            or "not explicitly specified"
        )
        + "\n"
        + "FORBIDDEN TECHNOLOGIES: "
        + (
            ", ".join(
                sorted(
                    constraints.forbidden_technologies
                )
            )
            or "none"
        )
        + "\n"
        + f"BROWSER APP REQUIRED: "
        + str(
            constraints.browser_app
        )
        + "\n"
        + f"FRONTEND REQUIRED: "
        + str(
            constraints.frontend_required
        )
    )


# ============================================================
# CONSTRAINT VALIDATION
# ============================================================

def validate_tool_against_constraints(
    tool_name: str,
    args: dict[str, Any],
    constraints: TaskConstraints,
) -> tuple[bool, str]:

    if tool_name not in {
        "create_file",
        "apply_patch",
    }:
        return (
            True,
            "",
        )

    path = str(
        args.get(
            "path",
            "",
        )
    ).lower()

    content = str(
        args.get(
            "content",
            args.get(
                "new_text",
                "",
            ),
        )
    ).lower()

    requested = (
        constraints.requested_languages
    )

    forbidden = (
        constraints.forbidden_languages
    )

    technologies = (
        constraints.requested_technologies
    )

    # --------------------------------------------------------
    # JAVASCRIPT / TYPESCRIPT LOCK
    # --------------------------------------------------------

    if (
        "javascript" in requested
        or "typescript" in requested
    ):

        if path.endswith(
            ".py"
        ):

            return (
                False,
                (
                    "The user explicitly requested "
                    "JavaScript/TypeScript. "
                    "Creating a Python implementation "
                    "violates the task."
                ),
            )

        if (
            "import pygame" in content
            or "pygame." in content
        ):

            return (
                False,
                (
                    "The user requested JavaScript/"
                    "TypeScript, but this content uses "
                    "Pygame/Python."
                ),
            )

    # --------------------------------------------------------
    # JAVA LOCK
    # --------------------------------------------------------

    if (
        "java" in requested
    ):

        if path.endswith(
            (
                ".py",
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
            )
        ):

            return (
                False,
                (
                    "The user explicitly requested Java. "
                    "Do not replace the implementation "
                    "with another language."
                ),
            )

    # --------------------------------------------------------
    # PYTHON LOCK
    # --------------------------------------------------------

    if (
        "python" in requested
        and not constraints.browser_app
    ):

        if path.endswith(
            (
                ".java",
                ".rs",
                ".go",
            )
        ):

            return (
                False,
                (
                    "The user explicitly requested Python."
                ),
            )

    # --------------------------------------------------------
    # FORBIDDEN PYTHON
    # --------------------------------------------------------

    if (
        "python" in forbidden
    ):

        if (
            path.endswith(
                ".py"
            )
            or "import pygame" in content
        ):

            return (
                False,
                "Python is explicitly forbidden.",
            )

    # --------------------------------------------------------
    # BROWSER FRONTEND
    # --------------------------------------------------------

    if (
        constraints.browser_app
        and "python"
        not in requested
    ):

        if path.endswith(
            ".py"
        ):

            return (
                False,
                (
                    "This task requires a browser/frontend "
                    "application. A Python desktop program "
                    "would violate that requirement."
                ),
            )

    # --------------------------------------------------------
    # REACT LOCK
    # --------------------------------------------------------

    if (
        "react" in technologies
    ):

        if path.endswith(
            ".py"
        ):

            return (
                False,
                (
                    "React was explicitly requested. "
                    "Do not create a Python implementation."
                ),
            )

    # --------------------------------------------------------
    # PYGAME FORBIDDEN
    # --------------------------------------------------------

    if (
        "pygame"
        in constraints.forbidden_technologies
    ):

        if (
            "pygame" in content
        ):

            return (
                False,
                "Pygame is explicitly forbidden.",
            )

    return (
        True,
        "",
    )


# ============================================================
# LANCEDB MEMORY
# ============================================================

class ProjectMemory:

    TABLE_NAME = (
        "project_facts"
    )

    def __init__(
        self,
        workspace: Workspace,
    ):

        self.project_id = (
            hashlib.sha256(
                str(
                    workspace.root
                ).encode("utf-8")
            ).hexdigest()[:16]
        )

        memory_dir = (
            MEMORY_ROOT
            / self.project_id
        )

        memory_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = lancedb.connect(
            str(memory_dir)
        )

    def _open(
        self,
    ):

        try:
            return (
                self.db.open_table(
                    self.TABLE_NAME
                )
            )

        except Exception:
            return None

    def add_fact(
        self,
        fact: str,
        evidence: str,
        evidence_id: str,
    ) -> None:

        row = {
            "project":
                self.project_id,

            "fact":
                fact.strip(),

            "evidence":
                evidence.strip(),

            "evidence_id":
                evidence_id,

            "created_at":
                int(time.time()),
        }

        table = (
            self._open()
        )

        if table is None:

            self.db.create_table(
                self.TABLE_NAME,
                data=[row],
            )

        else:

            table.add(
                [row]
            )

    def load_facts(
        self,
        limit: int = 30,
    ) -> list[dict[str, Any]]:

        table = (
            self._open()
        )

        if table is None:
            return []

        try:

            return (
                table.search()
                .where(
                    f"project = "
                    f"'{self.project_id}'"
                )
                .limit(limit)
                .to_list()
            )

        except Exception:
            return []


# ============================================================
# MODEL OUTPUT
# ============================================================

ACTION_SCHEMA = {
    "type": "object",

    "properties": {

        "type": {
            "type": "string",
            "enum": [
                "tool",
                "final",
            ],
        },

        "tool": {
            "type": "string",
        },

        "args": {
            "type": "object",
        },

        "message": {
            "type": "string",
        },
    },

    "required": [
        "type",
        "tool",
        "args",
        "message",
    ],
}


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = f"""
You are a local software engineering assistant connected to REAL
project tools through the surrounding Python controller.

You can:
- chat normally;
- inspect projects;
- create and modify files;
- verify work;
- run detected project tests/build/lint/typecheck commands.

The controller executes your tool requests.

IMPORTANT:
Never claim that you cannot access or create files when an appropriate
tool is available.

HARD REQUIREMENTS:
The user request may contain explicit language/framework/platform
requirements. These are mandatory.

Examples:
- If the user asks for JavaScript, do not silently create Python.
- If the user asks for React, do not replace React with Pygame.
- If the user asks for a browser frontend, do not replace it with a
  desktop Python application.

The controller may reject a tool request that violates hard requirements.

PROJECT CAPABILITY RULES:
Only use project commands reported as AVAILABLE in the capability card.
Never invent test commands or test cases.

Do not call run_project_tests if TEST COMMAND is unavailable.
Do not call run_project_build if BUILD COMMAND is unavailable.
Do not call run_project_lint if LINT COMMAND is unavailable.
Do not call run_project_typecheck if TYPECHECK COMMAND is unavailable.

For a newly created Python file with no tests:
use validate_python instead of inventing pytest tests.

TOOL CHOICE:
CREATE new file -> create_file
READ file -> read_file
MODIFY existing text -> apply_patch
DELETE file -> delete_file
FIND filename -> find_file
SEARCH text -> search_text

After modifying behavior:
- verify the result;
- prefer project build/test/typecheck when available;
- use narrow verification tools when appropriate.

Never use create_file on an existing file.
Never use apply_patch to create a new file.
Never repeatedly retry a capability already confirmed unavailable.

Take exactly ONE tool action per model turn.

If no tool is needed, return type="final".

Do not reveal private chain-of-thought.
The "message" field should contain only a short action description
or final user-facing answer.

AVAILABLE TOOLS:

{TOOL_DOCS}

Tool response example:

{{
  "type": "tool",
  "tool": "create_file",
  "args": {{
    "path": "src/game.js",
    "content": "..."
  }},
  "message": "Creating the JavaScript game implementation."
}}

Final response example:

{{
  "type": "final",
  "tool": "",
  "args": {{}},
  "message": "Created and verified the requested application."
}}
"""


# ============================================================
# AGENT
# ============================================================

class CodingAgent:

    def __init__(
        self,
        config: dict[str, Any],
    ):

        self.config = config

        model_path = (
            Path(
                config[
                    "model_path"
                ]
            ).resolve()
        )

        project_root = (
            Path(
                config[
                    "project_root"
                ]
            ).resolve()
        )

        if not model_path.exists():

            raise ValueError(
                "Configured model does not exist."
            )

        if not project_root.exists():

            raise ValueError(
                "Configured workspace does not exist."
            )

        self.workspace = (
            Workspace(
                project_root
            )
        )

        self.memory = (
            ProjectMemory(
                self.workspace
            )
        )

        threads = max(
            1,
            (os.cpu_count() or 4) - 2,
        )

        print()
        print(
            "🧠 Loading model..."
        )

        self.llm = Llama(
            model_path=str(
                model_path
            ),

            n_ctx=int(
                config[
                    "context_size"
                ]
            ),

            n_gpu_layers=int(
                config[
                    "gpu_layers"
                ]
            ),

            n_threads=threads,

            n_threads_batch=threads,

            use_mmap=True,

            verbose=False,
        )

        self.n_ctx = int(
            config[
                "context_size"
            ]
        )

        print(
            "✅ Model loaded."
        )

    # ========================================================
    # DEBUG
    # ========================================================

    def debug_level(
        self,
    ) -> int:

        return int(
            self.config.get(
                "debug_level",
                1,
            )
        )

    def debug_print(
        self,
        title: str,
        value: Any,
        minimum_level: int = 1,
    ) -> None:

        if (
            self.debug_level()
            < minimum_level
        ):
            return

        print()
        print(
            "─" * 70
        )

        print(
            f"🔎 {title}"
        )

        print(
            "─" * 70
        )

        if isinstance(
            value,
            str,
        ):

            print(
                value
            )

        else:

            print(
                json.dumps(
                    value,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        print(
            "─" * 70
        )

    def debug_messages(
        self,
        messages: list[dict[str, str]],
    ) -> None:

        if (
            self.debug_level()
            < 3
        ):
            return

        print()
        print(
            "=" * 70
        )

        print(
            "📨 FULL MODEL INPUT"
        )

        print(
            "=" * 70
        )

        for (
            index,
            message,
        ) in enumerate(
            messages,
            start=1,
        ):

            print()
            print(
                f"[{index}] "
                f"{message['role'].upper()}"
            )

            print(
                "-" * 70
            )

            print(
                message[
                    "content"
                ]
            )

    # ========================================================
    # TOKEN COUNT
    # ========================================================

    def token_count(
        self,
        messages: list[dict[str, str]],
    ) -> int:

        text = "\n".join(
            f"{message['role']}:\n"
            f"{message['content']}"

            for message
            in messages
        )

        try:

            return len(
                self.llm.tokenize(
                    text.encode(
                        "utf-8"
                    ),
                    add_bos=False,
                )
            )

        except Exception:

            return max(
                1,
                len(text) // 4,
            )

    # ========================================================
    # MODEL CALL
    # ========================================================

    def call_model(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:

        self.debug_messages(
            messages
        )

        stream = (
            self.llm.create_chat_completion(
                messages=messages,

                response_format={
                    "type":
                        "json_object",

                    "schema":
                        ACTION_SCHEMA,
                },

                temperature=float(
                    self.config[
                        "temperature"
                    ]
                ),

                top_p=0.9,

                max_tokens=(
                    MAX_MODEL_OUTPUT_TOKENS
                ),

                stream=True,
            )
        )

        full_content = ""

        if (
            self.debug_level()
            >= 1
        ):

            print()
            print(
                "─" * 70
            )

            print(
                "🧠 RAW LLM STREAM"
            )

            print(
                "─" * 70
            )

        for chunk in stream:

            choices = (
                chunk.get(
                    "choices",
                    [],
                )
            )

            if not choices:
                continue

            delta = (
                choices[0].get(
                    "delta",
                    {},
                )
            )

            text = (
                delta.get(
                    "content",
                    "",
                )
            )

            if not text:
                continue

            full_content += (
                text
            )

            if (
                self.debug_level()
                >= 1
            ):

                print(
                    text,
                    end="",
                    flush=True,
                )

        if (
            self.debug_level()
            >= 1
        ):

            print()
            print(
                "─" * 70
            )

        try:

            parsed = json.loads(
                full_content
            )

        except json.JSONDecodeError as error:

            self.debug_print(
                "JSON PARSE ERROR",
                {
                    "error":
                        str(error),

                    "raw":
                        full_content,
                },
                minimum_level=1,
            )

            return {
                "type":
                    "invalid",

                "tool":
                    "",

                "args":
                    {},

                "message":
                    full_content,
            }

        self.debug_print(
            "PARSED MODEL ACTION",
            parsed,
            minimum_level=2,
        )

        return parsed

    # ========================================================
    # MEMORY
    # ========================================================

    def memory_card(
        self,
    ) -> str:

        facts = (
            self.memory.load_facts(
                limit=30
            )
        )

        if not facts:

            return (
                "(no verified project memory)"
            )

        lines = []
        seen = set()

        for row in reversed(
            facts
        ):

            fact = str(
                row.get(
                    "fact",
                    "",
                )
            ).strip()

            if (
                not fact
                or fact in seen
            ):
                continue

            seen.add(
                fact
            )

            lines.append(
                f"- {fact}"
            )

        return (
            "\n".join(
                lines[:20]
            )
        )

    # ========================================================
    # WORKING STATE
    # ========================================================

    def state_card(
        self,
        state: AgentState,
    ) -> str:

        def items(
            values,
        ) -> str:

            if not values:
                return "(none)"

            return "\n".join(
                f"- {value}"
                for value
                in sorted(values)
            )

        blockers = (
            "\n".join(
                f"- {item}"
                for item
                in state.blockers[-5:]
            )
            or "(none)"
        )

        return (
            f"GOAL:\n"
            f"{state.task}\n\n"

            f"CREATED FILES:\n"
            f"{items(state.created_files)}\n\n"

            f"MODIFIED FILES:\n"
            f"{items(state.modified_files)}\n\n"

            f"READ FILES:\n"
            f"{items(state.read_files)}\n\n"

            f"KNOWN BLOCKERS:\n"
            f"{blockers}\n\n"

            f"LATEST RESULT:\n"
            f"{state.latest_result or '(none)'}"
        )

    def update_state(
        self,
        state: AgentState,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        output: str,
    ) -> bool:

        progress = False

        path = str(
            args.get(
                "path",
                "",
            )
        )

        if success:

            if (
                tool_name
                == "create_file"
                and path
            ):

                if (
                    path
                    not in state.created_files
                ):

                    progress = True

                state.created_files.add(
                    path
                )

            elif (
                tool_name
                == "apply_patch"
                and path
            ):

                state.modified_files.add(
                    path
                )

                progress = True

            elif (
                tool_name
                == "read_file"
                and path
            ):

                if (
                    path
                    not in state.read_files
                ):

                    progress = True

                state.read_files.add(
                    path
                )

            elif (
                tool_name.startswith(
                    "verify_"
                )
                or tool_name
                in {
                    "run_project_tests",
                    "run_project_build",
                    "run_project_lint",
                    "run_project_typecheck",
                    "validate_python",
                    "check_python_import",
                }
            ):

                progress = True

        else:

            lowered = (
                output.lower()
            )

            new_blocker = None

            if (
                "no module named pytest"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "project_tests"
                )

                new_blocker = (
                    "Project tests are unavailable "
                    "because pytest is not installed."
                )

            elif (
                "no project test command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "project_tests"
                )

                new_blocker = (
                    "No project test command is available."
                )

            elif (
                "no project build command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "project_build"
                )

                new_blocker = (
                    "No project build command is available."
                )

            elif (
                "no project lint command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "project_lint"
                )

                new_blocker = (
                    "No project lint command is available."
                )

            elif (
                "no project typecheck command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "project_typecheck"
                )

                new_blocker = (
                    "No project typecheck command is available."
                )

            if (
                new_blocker
                and new_blocker
                not in state.blockers
            ):

                state.blockers.append(
                    new_blocker
                )

                progress = True

        state.latest_result = (
            truncate_text(
                output,
                500,
            )
        )

        return progress

    # ========================================================
    # TRIM HISTORY
    # ========================================================

    def trim_history(
        self,
        base_messages: list[dict[str, str]],
        messages: list[dict[str, str]],
        state: AgentState,
        profile_card: str,
        constraints_card: str,
        memory_card: str,
    ) -> list[dict[str, str]]:

        keep_recent = int(
            self.config.get(
                "recent_observations",
                8,
            )
        )

        if (
            self.token_count(
                messages
            )
            < int(
                self.n_ctx
                * 0.78
            )
        ):
            return messages

        print(
            "🧹 Trimming old observations..."
        )

        recent = (
            messages[
                max(
                    2,
                    len(messages)
                    - keep_recent * 2
                ):
            ]
        )

        return [
            {
                "role":
                    "system",

                "content":
                    SYSTEM_PROMPT,
            },

            {
                "role":
                    "user",

                "content": (
                    f"USER REQUEST:\n"
                    f"{state.task}\n\n"

                    f"HARD TASK CONSTRAINTS:\n"
                    f"{constraints_card}\n\n"

                    f"PROJECT CAPABILITIES:\n"
                    f"{profile_card}\n\n"

                    f"PERSISTENT MEMORY:\n"
                    f"{memory_card}\n\n"

                    f"CURRENT WORKING STATE:\n"
                    f"{self.state_card(state)}"
                ),
            },

            *recent,
        ]

    # ========================================================
    # TOOL EXECUTION
    # ========================================================

    def execute_tool(
        self,
        tools: Tools,
        name: str,
        args: dict[str, Any],
    ) -> tuple[bool, str]:

        registry = (
            build_tool_registry(
                tools
            )
        )

        function = (
            registry.get(
                name
            )
        )

        if function is None:

            return (
                False,
                f"Unknown tool: {name}",
            )

        self.debug_print(
            "TOOL CALL",
            {
                "tool":
                    name,

                "args":
                    args,
            },
            minimum_level=2,
        )

        try:

            result = (
                function(
                    **args
                )
            )

            output = (
                truncate_text(
                    str(result)
                )
            )

            self.debug_print(
                "RAW TOOL RESULT",
                output,
                minimum_level=2,
            )

            return (
                True,
                output,
            )

        except Exception as error:

            output = (
                f"{type(error).__name__}: "
                f"{error}"
            )

            self.debug_print(
                "TOOL ERROR",
                output,
                minimum_level=2,
            )

            return (
                False,
                output,
            )

    # ========================================================
    # CAPABILITY BLOCKING
    # ========================================================

    def capability_block(
        self,
        state: AgentState,
        tool_name: str,
    ) -> str:

        mapping = {
            "run_project_tests":
                "project_tests",

            "run_project_build":
                "project_build",

            "run_project_lint":
                "project_lint",

            "run_project_typecheck":
                "project_typecheck",
        }

        capability = (
            mapping.get(
                tool_name
            )
        )

        if (
            capability
            and capability
            in state.unavailable_capabilities
        ):

            return (
                f"{tool_name} has already been "
                "confirmed unavailable for this task. "
                "Choose another verification method."
            )

        return ""

    # ========================================================
    # COMPLETION GATE
    # ========================================================

    def completion_allowed(
        self,
        state: AgentState,
    ) -> tuple[bool, str]:

        if not state.edited_files:
            return (
                True,
                "",
            )

        verification_tools = {
            "verify_file_exists",
            "verify_file_content",
            "verify_line_count",
            "run_project_tests",
            "run_project_build",
            "run_project_lint",
            "run_project_typecheck",
            "validate_python",
            "check_python_import",
        }

        verified = any(
            observation.success
            and observation.tool
            in verification_tools

            for observation
            in state.observations
        )

        if verified:
            return (
                True,
                "",
            )

        return (
            False,
            (
                "Files were created or modified, "
                "but no successful verification "
                "has occurred yet."
            ),
        )

    # ========================================================
    # RUN
    # ========================================================

    def run(
        self,
        task: str,
    ) -> str:

        state = (
            AgentState(
                task=task
            )
        )

        constraints = (
            detect_task_constraints(
                task
            )
        )

        profile = (
            detect_project_profile(
                self.workspace.root
            )
        )

        tools = Tools(
            workspace=self.workspace,
            memory=self.memory,
            state=state,
            profile=profile,
        )

        memory_card = (
            self.memory_card()
        )

        profile_card = (
            profile_to_prompt(
                profile
            )
        )

        constraints_card = (
            constraints_to_prompt(
                constraints
            )
        )

        environment = (
            detect_environment(
                self.workspace.root
            )
        )

        messages = [
            {
                "role":
                    "system",

                "content":
                    SYSTEM_PROMPT,
            },

            {
                "role":
                    "user",

                "content": (
                    f"USER REQUEST:\n"
                    f"{task}\n\n"

                    f"HARD TASK CONSTRAINTS:\n"
                    f"{constraints_card}\n\n"

                    f"PROJECT CAPABILITIES:\n"
                    f"{profile_card}\n\n"

                    f"ENVIRONMENT:\n"
                    f"{json.dumps(environment, indent=2)}\n\n"

                    f"PERSISTENT VERIFIED MEMORY:\n"
                    f"{memory_card}\n\n"

                    "Respect hard task constraints. "
                    "Do not silently change the requested "
                    "language/framework/platform."
                ),
            },
        ]

        max_steps = int(
            self.config[
                "max_steps"
            ]
        )

        max_no_progress = int(
            self.config.get(
                "max_no_progress_steps",
                6,
            )
        )

        for step in range(
            1,
            max_steps + 1,
        ):

            state.step = (
                step
            )

            messages = (
                self.trim_history(
                    messages[:2],
                    messages,
                    state,
                    profile_card,
                    constraints_card,
                    memory_card,
                )
            )

            used = (
                self.token_count(
                    messages
                )
            )

            print()
            print(
                f"🧠 Step "
                f"{step}/{max_steps}"
                f" | context ≈ "
                f"{used}/{self.n_ctx}"
            )

            try:

                action = (
                    self.call_model(
                        messages
                    )
                )

            except Exception as error:

                return (
                    "Model invocation failed: "
                    f"{type(error).__name__}: "
                    f"{error}"
                )

            action_type = (
                action.get(
                    "type"
                )
            )

            tool_name = (
                action.get(
                    "tool",
                    "",
                )
            )

            args = (
                action.get(
                    "args",
                    {},
                )
                or {}
            )

            model_message = (
                action.get(
                    "message",
                    "",
                )
            )

            # =================================================
            # FINAL
            # =================================================

            if (
                action_type
                == "final"
            ):

                allowed, reason = (
                    self.completion_allowed(
                        state
                    )
                )

                if allowed:
                    return (
                        model_message
                    )

                print(
                    f"🚫 Completion rejected: "
                    f"{reason}"
                )

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role":
                            "assistant",

                        "content":
                            json.dumps(
                                action,
                                ensure_ascii=False,
                            ),
                    }
                )

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "CONTROLLER:\n"
                            f"{reason}\n"
                            "Use an available verification tool."
                        ),
                    }
                )

                continue

            # =================================================
            # INVALID
            # =================================================

            if (
                action_type
                != "tool"
            ):

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "CONTROLLER ERROR:\n"
                            "Return either type='tool' "
                            "or type='final'."
                        ),
                    }
                )

                continue

            if not isinstance(
                args,
                dict,
            ):

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "CONTROLLER ERROR:\n"
                            "Tool args must be a JSON object."
                        ),
                    }
                )

                continue

            # =================================================
            # TASK CONSTRAINT VALIDATION
            # =================================================

            allowed, reason = (
                validate_tool_against_constraints(
                    tool_name,
                    args,
                    constraints,
                )
            )

            if not allowed:

                print()
                print(
                    f"🚫 CONTROLLER BLOCK: "
                    f"{reason}"
                )

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role":
                            "assistant",

                        "content":
                            json.dumps(
                                action,
                                ensure_ascii=False,
                            ),
                    }
                )

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "CONTROLLER BLOCK:\n"
                            f"{reason}\n\n"
                            "Respect the user's hard "
                            "language/framework requirements "
                            "and choose a compatible action."
                        ),
                    }
                )

                continue

            # =================================================
            # CAPABILITY VALIDATION
            # =================================================

            capability_reason = (
                self.capability_block(
                    state,
                    tool_name,
                )
            )

            if capability_reason:

                print()
                print(
                    f"🚫 CONTROLLER BLOCK: "
                    f"{capability_reason}"
                )

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role":
                            "assistant",

                        "content":
                            json.dumps(
                                action,
                                ensure_ascii=False,
                            ),
                    }
                )

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "CONTROLLER BLOCK:\n"
                            f"{capability_reason}"
                        ),
                    }
                )

                continue

            # =================================================
            # EXACT LOOP DETECTION
            # =================================================

            fingerprint = (
                hashlib.sha256(
                    json.dumps(
                        {
                            "tool":
                                tool_name,

                            "args":
                                args,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode(
                        "utf-8"
                    )
                ).hexdigest()
            )

            count = (
                state.action_counts.get(
                    fingerprint,
                    0,
                )
                + 1
            )

            state.action_counts[
                fingerprint
            ] = count

            if (
                count
                > MAX_IDENTICAL_ACTIONS
            ):

                warning = (
                    "The exact same tool call has "
                    "already been attempted multiple "
                    "times. Choose another approach."
                )

                print(
                    f"🔁 {warning}"
                )

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role":
                            "assistant",

                        "content":
                            json.dumps(
                                action,
                                ensure_ascii=False,
                            ),
                    }
                )

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "CONTROLLER:\n"
                            f"{warning}"
                        ),
                    }
                )

                continue

            # =================================================
            # EXECUTION
            # =================================================

            print()
            print(
                f"🛠️ {tool_name}"
            )

            if model_message:
                print(
                    model_message
                )

            (
                success,
                output,
            ) = self.execute_tool(
                tools,
                tool_name,
                args,
            )

            observation_id = (
                f"obs-{step:03d}"
            )

            observation = (
                Observation(
                    id=observation_id,
                    tool=tool_name,
                    args=args,
                    text=output,
                    success=success,
                )
            )

            state.observations.append(
                observation
            )

            if (
                len(
                    state.observations
                )
                > 100
            ):

                state.observations = (
                    state.observations[-100:]
                )

            progress = (
                self.update_state(
                    state,
                    tool_name,
                    args,
                    success,
                    output,
                )
            )

            if progress:

                state.no_progress_steps = 0

            else:

                state.no_progress_steps += 1

            icon = (
                "📦"
                if success
                else "💥"
            )

            print(
                f"{icon} "
                f"{truncate_text(output, 1600)}"
            )

            # -------------------------------------------------
            # AUTO CAPABILITY LEARNING
            # -------------------------------------------------

            lowered = (
                output.lower()
            )

            if (
                not success
                and (
                    "no module named pytest"
                    in lowered
                )
            ):

                state.unavailable_capabilities.add(
                    "project_tests"
                )

            messages.append(
                {
                    "role":
                        "assistant",

                    "content":
                        json.dumps(
                            action,
                            ensure_ascii=False,
                        ),
                }
            )

            observation_message = (
                f"TOOL OBSERVATION "
                f"{observation_id}\n"
                f"success={success}\n"
                f"tool={tool_name}\n\n"
                f"{output}\n\n"
                f"CURRENT WORKING STATE:\n"
                f"{self.state_card(state)}"
            )

            if (
                not success
                and tool_name
                == "apply_patch"
                and (
                    "file does not exist"
                    in lowered
                    or "old_text cannot be empty"
                    in lowered
                )
            ):

                observation_message += (
                    "\n\nCONTROLLER HINT:\n"
                    "apply_patch modifies an existing file. "
                    "Use create_file when the target does not exist."
                )

            if (
                not success
                and tool_name
                == "create_file"
                and "already exists"
                in lowered
            ):

                observation_message += (
                    "\n\nCONTROLLER HINT:\n"
                    "The file already exists. Read it, "
                    "then use apply_patch if modification is needed."
                )

            messages.append(
                {
                    "role":
                        "user",

                    "content":
                        observation_message,
                }
            )

            if (
                success
                and tool_name
                == "remember_fact"
            ):

                memory_card = (
                    self.memory_card()
                )

            # =================================================
            # NO-PROGRESS CIRCUIT BREAKER
            # =================================================

            if (
                state.no_progress_steps
                >= max_no_progress
            ):

                return (
                    "🛑 Agent stopped because it made "
                    f"no meaningful progress for "
                    f"{state.no_progress_steps} consecutive steps.\n\n"
                    f"Latest state:\n"
                    f"{self.state_card(state)}"
                )

        return (
            "🛑 Maximum number of "
            f"{max_steps} steps reached."
        )


# ============================================================
# MODEL DISCOVERY
# ============================================================

def find_gguf_files(
    start: Path,
    max_results: int = 100,
) -> list[Path]:

    if not start.exists():
        return []

    found = []

    try:

        for path in (
            start.rglob(
                "*.gguf"
            )
        ):

            if (
                ".git"
                in path.parts
            ):
                continue

            found.append(
                path.resolve()
            )

            if (
                len(found)
                >= max_results
            ):
                break

    except Exception:
        pass

    return found


# ============================================================
# MODEL MENU
# ============================================================

def model_selection_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print_header(
        "🤖 MODEL SELECTION"
    )

    search_locations = [
        SCRIPT_DIR / "models",
        SCRIPT_DIR,
        Path.cwd() / "models",
    ]

    models = []
    seen = set()

    for location in (
        search_locations
    ):

        for model in (
            find_gguf_files(
                location
            )
        ):

            key = (
                str(model)
                .lower()
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            models.append(
                model
            )

    if models:

        print()
        print(
            "Detected GGUF models:\n"
        )

        for (
            index,
            model,
        ) in enumerate(
            models,
            start=1,
        ):

            marker = ""

            try:

                configured = (
                    Path(
                        config[
                            "model_path"
                        ]
                    ).resolve()
                )

                if (
                    configured
                    == model
                ):
                    marker = (
                        " ✅ current"
                    )

            except Exception:
                pass

            try:

                size = (
                    model.stat().st_size
                    / 1024**3
                )

                size_text = (
                    f"{size:.2f} GB"
                )

            except Exception:

                size_text = "?"

            print(
                f"{index}. "
                f"{model.name} "
                f"[{size_text}]"
                f"{marker}"
            )

        manual_index = (
            len(models)
            + 1
        )

        print()
        print(
            f"{manual_index}. "
            "Enter path manually"
        )

        print(
            "0. Back"
        )

        selected = input(
            "\nSelect model: "
        ).strip()

        if selected == "0":
            return

        try:

            number = int(
                selected
            )

            if (
                1
                <= number
                <= len(models)
            ):

                config[
                    "model_path"
                ] = str(
                    models[
                        number - 1
                    ]
                )

                save_config(
                    config
                )

                print(
                    "\n✅ Model selected."
                )

                pause()

                return

            if (
                number
                != manual_index
            ):
                return

        except ValueError:
            pass

    entered = input(
        "\nFull GGUF path: "
    ).strip().strip(
        '"'
    )

    if not entered:
        return

    path = (
        Path(entered)
        .expanduser()
        .resolve()
    )

    if (
        not path.exists()
        or path.suffix.lower()
        != ".gguf"
    ):

        print(
            "\n❌ Invalid GGUF path."
        )

        pause()

        return

    config[
        "model_path"
    ] = str(
        path
    )

    save_config(
        config
    )

    print(
        "\n✅ Model selected."
    )

    pause()


# ============================================================
# PROJECT MENU
# ============================================================

def project_selection_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print_header(
        "📁 PROJECT SELECTION"
    )

    print()
    print(
        "Current project:"
    )

    print(
        config[
            "project_root"
        ]
    )

    entered = input(
        "\nNew project folder "
        "(Enter to cancel): "
    ).strip().strip(
        '"'
    )

    if not entered:
        return

    path = (
        Path(entered)
        .expanduser()
        .resolve()
    )

    if (
        not path.exists()
        or not path.is_dir()
    ):

        print(
            "\n❌ Invalid directory."
        )

        pause()

        return

    config[
        "project_root"
    ] = str(
        path
    )

    save_config(
        config
    )

    print(
        "\n✅ Project selected."
    )

    pause()


# ============================================================
# SETTINGS
# ============================================================

def settings_menu(
    config: dict[str, Any],
) -> None:

    while True:

        clear_screen()

        print_header(
            "⚙️ SETTINGS"
        )

        print(
            f"\n1. Context size"
            f"           : "
            f"{config['context_size']}"
        )

        print(
            f"2. GPU layers"
            f"             : "
            f"{config['gpu_layers']}"
        )

        print(
            f"3. Max agent steps"
            f"        : "
            f"{config['max_steps']}"
        )

        print(
            f"4. Max no-progress steps"
            f"  : "
            f"{config['max_no_progress_steps']}"
        )

        print(
            f"5. Temperature"
            f"            : "
            f"{config['temperature']}"
        )

        print(
            f"6. Debug level"
            f"            : "
            f"{config['debug_level']}"
        )

        print(
            f"7. Recent observations"
            f"    : "
            f"{config['recent_observations']}"
        )

        print(
            "8. Reset defaults"
        )

        print(
            "0. Back"
        )

        choice = input(
            "\nSelect option: "
        ).strip()

        if choice == "0":

            save_config(
                config
            )

            return

        elif choice == "1":

            try:

                value = int(
                    input(
                        "Context size: "
                    )
                )

                if value >= 1024:
                    config[
                        "context_size"
                    ] = value

            except ValueError:
                pass

        elif choice == "2":

            try:

                config[
                    "gpu_layers"
                ] = int(
                    input(
                        "GPU layers "
                        "(0 CPU, -1 all): "
                    )
                )

            except ValueError:
                pass

        elif choice == "3":

            try:

                value = int(
                    input(
                        "Max steps: "
                    )
                )

                if value > 0:
                    config[
                        "max_steps"
                    ] = value

            except ValueError:
                pass

        elif choice == "4":

            try:

                value = int(
                    input(
                        "Max no-progress steps: "
                    )
                )

                if value > 0:
                    config[
                        "max_no_progress_steps"
                    ] = value

            except ValueError:
                pass

        elif choice == "5":

            try:

                value = float(
                    input(
                        "Temperature: "
                    )
                )

                if (
                    0
                    <= value
                    <= 2
                ):
                    config[
                        "temperature"
                    ] = value

            except ValueError:
                pass

        elif choice == "6":

            print()
            print(
                "0. Minimal"
            )

            print(
                "1. Raw LLM stream"
            )

            print(
                "2. Raw + parsed + tools"
            )

            print(
                "3. Full prompts"
            )

            selected = input(
                "Debug level: "
            ).strip()

            if selected in {
                "0",
                "1",
                "2",
                "3",
            }:

                config[
                    "debug_level"
                ] = int(
                    selected
                )

        elif choice == "7":

            try:

                value = int(
                    input(
                        "Recent observations "
                        "to retain: "
                    )
                )

                if value >= 2:
                    config[
                        "recent_observations"
                    ] = value

            except ValueError:
                pass

        elif choice == "8":

            model_path = (
                config.get(
                    "model_path",
                    "",
                )
            )

            project_root = (
                config.get(
                    "project_root",
                    str(
                        SCRIPT_DIR
                    ),
                )
            )

            config.clear()

            config.update(
                DEFAULT_CONFIG.copy()
            )

            config[
                "model_path"
            ] = model_path

            config[
                "project_root"
            ] = project_root

        save_config(
            config
        )


# ============================================================
# SYSTEM INFO
# ============================================================

def system_information_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print_header(
        "🔎 SYSTEM INFORMATION"
    )

    project = Path(
        config[
            "project_root"
        ]
    )

    if project.exists():

        profile = (
            detect_project_profile(
                project
            )
        )

        print()
        print(
            profile_to_prompt(
                profile
            )
        )

    print()
    print(
        f"Application directory:\n"
        f"{APP_DIR}"
    )

    print()
    print(
        f"Config file:\n"
        f"{CONFIG_FILE}"
    )

    print()
    print(
        f"Memory directory:\n"
        f"{MEMORY_ROOT}"
    )

    pause()


# ============================================================
# CHAT
# ============================================================

def chat_menu(
    config: dict[str, Any],
) -> None:

    if not config.get(
        "model_path"
    ):

        print(
            "\n⚠️ No model selected."
        )

        pause()

        model_selection_menu(
            config
        )

        if not config.get(
            "model_path"
        ):
            return

    model_path = (
        Path(
            config[
                "model_path"
            ]
        )
    )

    project_path = (
        Path(
            config[
                "project_root"
            ]
        )
    )

    if not model_path.exists():

        print(
            "\n❌ Model does not exist."
        )

        pause()

        return

    if not project_path.exists():

        print(
            "\n❌ Project does not exist."
        )

        pause()

        return

    clear_screen()

    print_header(
        "🤖 LOCAL CODING AGENT"
    )

    print(
        f"\nModel   : "
        f"{model_path.name}"
    )

    print(
        f"Project : "
        f"{project_path}"
    )

    print(
        f"Context : "
        f"{config['context_size']}"
    )

    print(
        f"Debug   : "
        f"{config['debug_level']}"
    )

    print()
    print(
        "Loading..."
    )

    try:

        agent = (
            CodingAgent(
                config
            )
        )

    except Exception as error:

        print(
            "\n❌ Model failed to load:"
        )

        print(
            error
        )

        pause()

        return

    print()
    print(
        "Commands:"
    )

    print(
        "/back   Return to main menu"
    )

    print(
        "/help   Show commands"
    )

    while True:

        print()
        print(
            "-" * 70
        )

        task = input(
            "\n👤 > "
        ).strip()

        if not task:
            continue

        normalized = (
            task.lower()
        )

        if normalized in {
            "/back",
            "/exit",
            "/quit",
            "exit",
        }:
            return

        if normalized == "/help":

            print()
            print(
                "/back - return to menu"
            )

            print(
                "/help - show commands"
            )

            continue

        print()
        print(
            "🚀 Sending to model..."
        )

        result = (
            agent.run(
                task
            )
        )

        print()
        print(
            "=" * 70
        )

        print(
            "🤖 RESPONSE"
        )

        print(
            "=" * 70
        )

        print(
            result
        )


# ============================================================
# STATUS
# ============================================================

def show_status(
    config: dict[str, Any],
) -> None:

    model_path = (
        config.get(
            "model_path",
            "",
        )
    )

    if model_path:

        model_name = (
            Path(
                model_path
            ).name
        )

    else:

        model_name = (
            "Not selected"
        )

    print(
        f"\n🤖 Model   : "
        f"{model_name}"
    )

    print(
        f"📁 Project : "
        f"{config['project_root']}"
    )

    print(
        f"🧠 Context : "
        f"{config['context_size']}"
    )

    print(
        f"🔎 Debug   : "
        f"{config['debug_level']}"
    )


# ============================================================
# MAIN MENU
# ============================================================

def main_menu() -> None:

    config = (
        load_config()
    )

    while True:

        clear_screen()

        print(
            "╔══════════════════════════════════════╗"
        )

        print(
            "║          🤖 MY LOCAL LLM            ║"
        )

        print(
            "╚══════════════════════════════════════╝"
        )

        show_status(
            config
        )

        print()
        print(
            "1. 💬 Chat / Coding Agent"
        )

        print(
            "2. ⚙️  Settings"
        )

        print(
            "3. 🤖 Model Selection"
        )

        print(
            "4. 📁 Project Selection"
        )

        print(
            "5. 🔎 System Information"
        )

        print(
            "0. 🚪 Exit"
        )

        choice = input(
            "\nSelect option: "
        ).strip()

        if choice == "1":

            chat_menu(
                config
            )

        elif choice == "2":

            settings_menu(
                config
            )

        elif choice == "3":

            model_selection_menu(
                config
            )

        elif choice == "4":

            project_selection_menu(
                config
            )

        elif choice == "5":

            system_information_menu(
                config
            )

        elif choice == "0":

            print()
            print(
                "👋 Goodbye."
            )

            break


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main_menu()