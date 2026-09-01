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

from llama_cpp import (
    Llama,
    LlamaRAMCache,
)

from myllm_tools import (
    ProjectProfile,
    Workspace,
    Tools,
    TOOL_DOCS,
    TOOL_SCHEMAS,
    build_tool_registry,
    detect_project_profile,
    profile_to_prompt,
    truncate_text,
    validate_tool_arguments,
)


# ============================================================
# PATHS
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
# DEFAULTS
# ============================================================

DEFAULT_CONFIG = {
    "model_path": "",
    "project_root": str(
        SCRIPT_DIR
    ),

    "context_size": 8192,

    "gpu_layers": 0,

    "max_steps": 20,

    "max_no_progress_steps": 5,

    "temperature": 0.0,

    "debug_level": 1,

    "recent_observations": 6,

    # RAM prompt/state cache
    "prompt_cache_enabled": True,

    # 512 MB default
    "prompt_cache_mb": 512,

    # Trim dynamic agent history before it gets huge
    "trim_context_ratio": 0.72,
}


MAX_IDENTICAL_ACTIONS = 2

MAX_MODEL_OUTPUT_TOKENS = 700

MAX_SESSION_MESSAGES = 12


# ============================================================
# CONFIG
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
# DATA
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
class SessionContext:

    recent_messages: list[
        dict[str, str]
    ] = field(
        default_factory=list
    )

    active_target: str = ""

    active_language: str = ""

    active_technology: str = ""

    active_task_description: str = ""

    touched_files: set[str] = field(
        default_factory=set
    )

    last_constraints: TaskConstraints = field(
        default_factory=TaskConstraints
    )


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

    unavailable_capabilities: set[str] = field(
        default_factory=set
    )

    no_progress_steps: int = 0

    last_test_passed: bool = False

    last_validation_passed: bool = False

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
    print(
        "=" * 70
    )
    print(
        title
    )
    print(
        "=" * 70
    )


# ============================================================
# ENVIRONMENT
# ============================================================

def detect_environment(
    root: Path,
) -> dict[str, Any]:

    return {
        "os":
            (
                f"{platform.system()} "
                f"{platform.release()}"
            ),

        "python":
            sys.version.split()[0],

        "workspace":
            str(root),

        "git_repository":
            (
                root
                / ".git"
            ).exists(),
    }


# ============================================================
# CONSTRAINT DETECTION
# ============================================================

def detect_task_constraints(
    task: str,
) -> TaskConstraints:

    text = (
        task.lower()
    )

    result = (
        TaskConstraints()
    )

    # ========================================================
    # LANGUAGE
    # ========================================================

    if (
        "javascript"
        in text
        or re.search(
            r"\bjs\b",
            text,
        )
    ):

        result.requested_languages.add(
            "javascript"
        )

    if (
        "typescript"
        in text
        or re.search(
            r"\bts\b",
            text,
        )
    ):

        result.requested_languages.add(
            "typescript"
        )

    if (
        "python"
        in text
    ):

        result.requested_languages.add(
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

        result.requested_languages.add(
            "java"
        )

    if re.search(
        r"\brust\b",
        text,
    ):

        result.requested_languages.add(
            "rust"
        )

    if (
        "golang"
        in text
        or "go project"
        in text
        or "go application"
        in text
    ):

        result.requested_languages.add(
            "go"
        )

    # ========================================================
    # TECHNOLOGY
    # ========================================================

    if "react" in text:

        result.requested_technologies.add(
            "react"
        )

        result.requested_languages.add(
            "javascript"
        )

        result.browser_app = True
        result.frontend_required = True

    if (
        "next.js"
        in text
        or "nextjs"
        in text
    ):

        result.requested_technologies.add(
            "next.js"
        )

        result.requested_languages.add(
            "javascript"
        )

        result.browser_app = True
        result.frontend_required = True

    if "vite" in text:

        result.requested_technologies.add(
            "vite"
        )

        result.browser_app = True
        result.frontend_required = True

    if "pygame" in text:

        result.requested_technologies.add(
            "pygame"
        )

        result.requested_languages.add(
            "python"
        )

    # ========================================================
    # FRONTEND
    # ========================================================

    if any(
        phrase in text
        for phrase
        in [
            "frontend",
            "front-end",
            "browser",
            "web app",
            "webapp",
            "website",
        ]
    ):

        result.browser_app = True
        result.frontend_required = True

    # ========================================================
    # NEGATIVE
    # ========================================================

    if any(
        phrase in text
        for phrase
        in [
            "no python",
            "not python",
            "without python",
        ]
    ):

        result.forbidden_languages.add(
            "python"
        )

    if any(
        phrase in text
        for phrase
        in [
            "no pygame",
            "not pygame",
            "without pygame",
        ]
    ):

        result.forbidden_technologies.add(
            "pygame"
        )

    return result


def inherit_constraints(
    current: TaskConstraints,
    previous: TaskConstraints,
) -> TaskConstraints:

    # Only inherit positive constraints when the
    # current message did not specify replacements.

    if not (
        current.requested_languages
    ):

        current.requested_languages = set(
            previous.requested_languages
        )

    if not (
        current.requested_technologies
    ):

        current.requested_technologies = set(
            previous.requested_technologies
        )

    if not (
        current.forbidden_languages
    ):

        current.forbidden_languages = set(
            previous.forbidden_languages
        )

    if not (
        current.forbidden_technologies
    ):

        current.forbidden_technologies = set(
            previous.forbidden_technologies
        )

    if not current.browser_app:

        current.browser_app = (
            previous.browser_app
        )

    if not current.frontend_required:

        current.frontend_required = (
            previous.frontend_required
        )

    return current


def constraints_to_prompt(
    constraints: TaskConstraints,
) -> str:

    def show(
        values: set[str],
        fallback: str,
    ) -> str:

        return (
            ", ".join(
                sorted(
                    values
                )
            )
            or fallback
        )

    return (
        f"REQUIRED LANGUAGES: "
        f"{show(constraints.requested_languages, 'not specified')}\n"

        f"FORBIDDEN LANGUAGES: "
        f"{show(constraints.forbidden_languages, 'none')}\n"

        f"REQUIRED TECHNOLOGIES: "
        f"{show(constraints.requested_technologies, 'not specified')}\n"

        f"FORBIDDEN TECHNOLOGIES: "
        f"{show(constraints.forbidden_technologies, 'none')}\n"

        f"BROWSER APP REQUIRED: "
        f"{constraints.browser_app}\n"

        f"FRONTEND REQUIRED: "
        f"{constraints.frontend_required}"
    )


# ============================================================
# CONTROLLER CONSTRAINT CHECK
# ============================================================

def validate_single_mutation(
    path: str,
    content: str,
    constraints: TaskConstraints,
) -> tuple[bool, str]:

    path_lower = (
        path.lower()
    )

    content_lower = (
        content.lower()
    )

    languages = (
        constraints.requested_languages
    )

    technologies = (
        constraints.requested_technologies
    )

    forbidden_languages = (
        constraints.forbidden_languages
    )

    forbidden_technologies = (
        constraints.forbidden_technologies
    )

    # JS / TS
    if (
        "javascript"
        in languages
        or "typescript"
        in languages
    ):

        if path_lower.endswith(
            ".py"
        ):

            return (
                False,
                (
                    "The active task requires "
                    "JavaScript/TypeScript. "
                    "A Python implementation "
                    "is not allowed."
                ),
            )

        if (
            "pygame"
            in content_lower
        ):

            return (
                False,
                (
                    "The active task requires "
                    "JavaScript/TypeScript. "
                    "Pygame is not compatible."
                ),
            )

    # Java
    if (
        "java"
        in languages
    ):

        if path_lower.endswith(
            (
                ".py",
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
                ".rs",
                ".go",
            )
        ):

            return (
                False,
                (
                    "The active task requires Java. "
                    "Do not replace it with another "
                    "implementation language."
                ),
            )

    # Forbidden Python
    if (
        "python"
        in forbidden_languages
    ):

        if (
            path_lower.endswith(
                ".py"
            )
            or "pygame"
            in content_lower
        ):

            return (
                False,
                "Python is explicitly forbidden.",
            )

    # Browser app
    if (
        constraints.browser_app
        and "python"
        not in languages
    ):

        if path_lower.endswith(
            ".py"
        ):

            return (
                False,
                (
                    "The task requires a browser/frontend "
                    "application. Do not replace it with "
                    "a Python desktop application."
                ),
            )

    if (
        "react"
        in technologies
    ):

        if (
            path_lower.endswith(
                ".py"
            )
            or "pygame"
            in content_lower
        ):

            return (
                False,
                (
                    "React is a hard requirement. "
                    "Python/Pygame is not allowed."
                ),
            )

    if (
        "pygame"
        in forbidden_technologies
        and "pygame"
        in content_lower
    ):

        return (
            False,
            "Pygame is explicitly forbidden.",
        )

    return (
        True,
        "",
    )


def validate_tool_against_constraints(
    tool_name: str,
    args: dict[str, Any],
    constraints: TaskConstraints,
) -> tuple[bool, str]:

    if (
        tool_name
        == "create_file"
    ):

        return (
            validate_single_mutation(
                str(
                    args.get(
                        "path",
                        ""
                    )
                ),
                str(
                    args.get(
                        "content",
                        ""
                    )
                ),
                constraints,
            )
        )

    if (
        tool_name
        == "apply_patch"
    ):

        return (
            validate_single_mutation(
                str(
                    args.get(
                        "path",
                        ""
                    )
                ),
                str(
                    args.get(
                        "new_text",
                        ""
                    )
                ),
                constraints,
            )
        )

    if (
        tool_name
        == "create_files"
    ):

        files = (
            args.get(
                "files",
                []
            )
            or []
        )

        for item in files:

            allowed, reason = (
                validate_single_mutation(
                    str(
                        item.get(
                            "path",
                            ""
                        )
                    ),
                    str(
                        item.get(
                            "content",
                            ""
                        )
                    ),
                    constraints,
                )
            )

            if not allowed:
                return (
                    False,
                    reason,
                )

    return (
        True,
        "",
    )


# ============================================================
# PROJECT MEMORY
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
                ).encode(
                    "utf-8"
                )
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

        self.db = (
            lancedb.connect(
                str(
                    memory_dir
                )
            )
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
                int(
                    time.time()
                ),
        }

        table = (
            self._open()
        )

        if table is None:

            self.db.create_table(
                self.TABLE_NAME,
                data=[
                    row
                ],
            )

        else:

            table.add(
                [
                    row
                ]
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
                .limit(
                    limit
                )
                .to_list()
            )

        except Exception:
            return []


# ============================================================
# ACTION FORMAT
# ============================================================

ACTION_SCHEMA = {

    "type":
        "object",

    "properties": {

        "type": {
            "type":
                "string",

            "enum": [
                "tool",
                "final",
            ],
        },

        "tool": {
            "type":
                "string",
        },

        "args": {
            "type":
                "object",
        },

        "message": {
            "type":
                "string",
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
# STATIC SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = f"""
You are a local software engineering agent.

You are connected to REAL tools executed by a Python controller.

Use tools only when filesystem/project work is required.
For ordinary conversation, answer directly.

IMPORTANT:
The user may give explicit language, framework, directory, or platform
requirements. These are HARD constraints.

Examples:
- JavaScript must not silently become Python.
- Java must not silently become JavaScript.
- React must not become Pygame.
- Browser frontend must not become a Python desktop application.

The controller may block violations.

SESSION CONTINUITY:
The controller may provide an ACTIVE TARGET and RECENT CONVERSATION.

If the user says:
- "complete it"
- "fix it"
- "change that"
- "the game"
- "that file"

use ACTIVE TARGET and RECENT CONVERSATION before searching the entire repo.

Do not rediscover the whole repository if a specific active target is known.

PROJECT CAPABILITIES:
Only run a project build/test/lint/typecheck if the capability card says
that command is available.

Do not invent tool names.
Do not invent pytest test names.
Do not invent build commands.

For simple Java source projects:
run_project_build may use javac.

For Python scripts with no tests:
validate_python is appropriate.

CREATE:
create_file or create_files

EDIT EXISTING:
read_file, then apply_patch

VERIFY DIRECTORY:
verify_directory_exists

VERIFY FILE:
verify_file_exists or verify_files_exist

Each model turn must choose exactly ONE tool action or return final.

When multiple new files are needed, prefer create_files over several
create_file calls.

After modifications, prefer ONE strong verification rather than many
redundant checks:
1. project build
2. project tests
3. typecheck
4. lint
5. narrow file verification

Do not reveal private chain-of-thought.

Keep the message field short.

TOOLS:

{TOOL_DOCS}
"""


# ============================================================
# AGENT
# ============================================================

class CodingAgent:

    def __init__(
        self,
        config: dict[str, Any],
    ):

        self.config = (
            config
        )

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

        self.session = (
            SessionContext()
        )

        threads = max(
            1,
            (
                os.cpu_count()
                or 4
            )
            - 2,
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

        # ====================================================
        # PROMPT / STATE CACHE
        # ====================================================

        self.prompt_cache = None

        if bool(
            config.get(
                "prompt_cache_enabled",
                True,
            )
        ):

            cache_mb = max(
                64,
                int(
                    config.get(
                        "prompt_cache_mb",
                        512,
                    )
                ),
            )

            try:

                self.prompt_cache = (
                    LlamaRAMCache(
                        capacity_bytes=(
                            cache_mb
                            * 1024
                            * 1024
                        )
                    )
                )

                self.llm.set_cache(
                    self.prompt_cache
                )

                print(
                    f"⚡ Prompt cache enabled: "
                    f"{cache_mb} MB"
                )

            except Exception as error:

                self.prompt_cache = None

                print(
                    "⚠️ Prompt cache could not "
                    f"be enabled: {error}"
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
        messages: list[
            dict[str, str]
        ],
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
        messages: list[
            dict[str, str]
        ],
    ) -> int:

        text = "\n".join(
            (
                f"{message['role']}:\n"
                f"{message['content']}"
            )
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
                len(
                    text
                )
                // 4,
            )

    # ========================================================
    # MODEL CALL
    # ========================================================

    def call_model(
        self,
        messages: list[
            dict[str, str]
        ],
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

            parsed = (
                json.loads(
                    full_content
                )
            )

        except json.JSONDecodeError as error:

            self.debug_print(
                "JSON PARSE ERROR",
                {
                    "error":
                        str(
                            error
                        ),

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
    # MEMORY CARD
    # ========================================================

    def memory_card(
        self,
    ) -> str:

        facts = (
            self.memory.load_facts(
                limit=20
            )
        )

        if not facts:

            return (
                "(none)"
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
                or fact
                in seen
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
                lines[:15]
            )
        )

    # ========================================================
    # SESSION CARD
    # ========================================================

    def session_card(
        self,
    ) -> str:

        recent = []

        for message in (
            self.session.recent_messages[
                -6:
            ]
        ):

            recent.append(
                f"{message['role'].upper()}: "
                f"{message['content']}"
            )

        return (
            f"ACTIVE TARGET: "
            f"{self.session.active_target or '(none)'}\n"

            f"ACTIVE LANGUAGE: "
            f"{self.session.active_language or '(none)'}\n"

            f"ACTIVE TECHNOLOGY: "
            f"{self.session.active_technology or '(none)'}\n"

            f"ACTIVE TASK: "
            f"{self.session.active_task_description or '(none)'}\n"

            f"RECENT TOUCHED FILES:\n"
            + (
                "\n".join(
                    f"- {path}"
                    for path
                    in sorted(
                        self.session.touched_files
                    )[-10:]
                )
                or "(none)"
            )
            + "\n\nRECENT CONVERSATION:\n"
            + (
                "\n".join(
                    recent
                )
                or "(none)"
            )
        )

    # ========================================================
    # WORKING STATE
    # ========================================================

    def state_card(
        self,
        state: AgentState,
    ) -> str:

        def format_items(
            values,
        ) -> str:

            if not values:
                return "(none)"

            return "\n".join(
                f"- {value}"
                for value
                in sorted(
                    values
                )
            )

        blockers = (
            "\n".join(
                f"- {item}"
                for item
                in state.blockers[
                    -5:
                ]
            )
            or "(none)"
        )

        return (
            f"GOAL:\n"
            f"{state.task}\n\n"

            f"CREATED:\n"
            f"{format_items(state.created_files)}\n\n"

            f"MODIFIED:\n"
            f"{format_items(state.modified_files)}\n\n"

            f"READ:\n"
            f"{format_items(state.read_files)}\n\n"

            f"BLOCKERS:\n"
            f"{blockers}\n\n"

            f"LATEST RESULT:\n"
            f"{state.latest_result or '(none)'}"
        )

    # ========================================================
    # ACTIVE TARGET
    # ========================================================

    def derive_active_root(
        self,
    ) -> Path:

        target = (
            self.session.active_target
        )

        if not target:

            return (
                self.workspace.root
            )

        try:

            resolved = (
                self.workspace.resolve(
                    target
                )
            )

        except Exception:

            return (
                self.workspace.root
            )

        if resolved.is_file():

            return (
                resolved.parent
            )

        if resolved.exists():

            return (
                resolved
            )

        # If target does not exist yet,
        # use its parent when possible.

        parent = (
            resolved.parent
        )

        if parent.exists():

            return (
                parent
            )

        return (
            self.workspace.root
        )

    # ========================================================
    # DETECT TARGET FROM TOOL
    # ========================================================

    def update_session_target_from_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
    ) -> None:

        if not success:
            return

        path = ""

        if tool_name in {
            "create_directory",
            "verify_directory_exists",
        }:

            path = str(
                args.get(
                    "path",
                    ""
                )
            )

            if path:

                self.session.active_target = (
                    path
                )

        elif tool_name in {
            "create_file",
            "apply_patch",
            "read_file",
            "verify_file_exists",
        }:

            path = str(
                args.get(
                    "path",
                    ""
                )
            )

            if path:

                self.session.touched_files.add(
                    path
                )

                parent = str(
                    Path(
                        path
                    ).parent
                )

                if (
                    parent
                    and parent
                    != "."
                ):

                    self.session.active_target = (
                        parent
                    )

        elif (
            tool_name
            == "create_files"
        ):

            files = (
                args.get(
                    "files",
                    []
                )
                or []
            )

            parents = []

            for item in files:

                path = str(
                    item.get(
                        "path",
                        ""
                    )
                )

                if not path:
                    continue

                self.session.touched_files.add(
                    path
                )

                parent = str(
                    Path(
                        path
                    ).parent
                )

                if parent:
                    parents.append(
                        parent
                    )

            if parents:

                common = os.path.commonpath(
                    parents
                )

                self.session.active_target = (
                    common
                )

    # ========================================================
    # UPDATE STATE
    # ========================================================

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
                ""
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
                == "create_files"
            ):

                for item in (
                    args.get(
                        "files",
                        []
                    )
                    or []
                ):

                    item_path = str(
                        item.get(
                            "path",
                            ""
                        )
                    )

                    if not item_path:
                        continue

                    if (
                        item_path
                        not in state.created_files
                    ):
                        progress = True

                    state.created_files.add(
                        item_path
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
                or tool_name in {
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
                "no project test command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "tests"
                )

                new_blocker = (
                    "Project tests unavailable."
                )

            elif (
                "no project build command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "build"
                )

                new_blocker = (
                    "Project build unavailable."
                )

            elif (
                "no project lint command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "lint"
                )

                new_blocker = (
                    "Project lint unavailable."
                )

            elif (
                "no project typecheck command"
                in lowered
            ):

                state.unavailable_capabilities.add(
                    "typecheck"
                )

                new_blocker = (
                    "Project typecheck unavailable."
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
                450,
            )
        )

        return progress

    # ========================================================
    # EXECUTE
    # ========================================================

    def execute_tool(
        self,
        tools: Tools,
        name: str,
        args: dict[str, Any],
    ) -> tuple[bool, str]:

        valid, reason = (
            validate_tool_arguments(
                name,
                args,
            )
        )

        if not valid:

            return (
                False,
                (
                    "CONTROLLER TOOL SCHEMA ERROR:\n"
                    f"{reason}"
                ),
            )

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
                (
                    f"Unknown tool: {name}\n"
                    "Do not invent tool names."
                ),
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
                    str(
                        result
                    )
                )
            )

            self.debug_print(
                "TOOL RESULT",
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
    # ERROR HINT
    # ========================================================

    def controller_hint(
        self,
        tool_name: str,
        output: str,
    ) -> str:

        lowered = (
            output.lower()
        )

        if (
            tool_name
            == "create_directory"
            and "directory already exists"
            in lowered
        ):

            return (
                "The directory already exists. "
                "Do not recreate or verify it. "
                "Continue with the requested child "
                "directory or files."
            )

        if (
            tool_name
            == "verify_file_exists"
            and "not a file"
            in lowered
        ):

            return (
                "This path is a directory. "
                "Use verify_directory_exists only "
                "if verification is actually needed."
            )

        if (
            tool_name
            == "create_file"
            and "already exists"
            in lowered
        ):

            return (
                "The file already exists. "
                "Read it and use apply_patch."
            )

        if (
            tool_name
            == "apply_patch"
            and (
                "file does not exist"
                in lowered
                or
                "old_text cannot be empty"
                in lowered
            )
        ):

            return (
                "apply_patch only modifies an "
                "existing file. Use create_file "
                "for a new file."
            )

        if (
            "tool schema error"
            in lowered
        ):

            return (
                "Use the exact documented tool "
                "signature. Do not add arguments "
                "that the tool does not accept."
            )

        if (
            "unknown tool"
            in lowered
        ):

            return (
                "Choose one of the documented tools. "
                "Do not invent a replacement tool name."
            )

        return ""

    # ========================================================
    # CAPABILITY BLOCK
    # ========================================================

    def capability_block(
        self,
        state: AgentState,
        tool_name: str,
    ) -> str:

        mapping = {
            "run_project_tests":
                "tests",

            "run_project_build":
                "build",

            "run_project_lint":
                "lint",

            "run_project_typecheck":
                "typecheck",
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
                f"{tool_name} is already confirmed "
                "unavailable. Choose another "
                "verification method."
            )

        return ""

    # ========================================================
    # COMPLETION GATE
    # ========================================================

    def completion_allowed(
        self,
        state: AgentState,
    ) -> tuple[bool, str]:

        if not (
            state.edited_files
        ):

            return (
                True,
                "",
            )

        verification_tools = {
            "verify_file_exists",
            "verify_files_exist",
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
            (
                observation.success
                and observation.tool
                in verification_tools
            )
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
                "Files were created or modified "
                "but no successful verification "
                "has occurred."
            ),
        )

    # ========================================================
    # DETERMINISTIC TRIMMING
    # ========================================================

    def trim_messages(
        self,
        messages: list[
            dict[str, str]
        ],
        state: AgentState,
        task_context: str,
    ) -> list[
        dict[str, str]
    ]:

        ratio = float(
            self.config.get(
                "trim_context_ratio",
                0.72,
            )
        )

        if (
            self.token_count(
                messages
            )
            < int(
                self.n_ctx
                * ratio
            )
        ):

            return messages

        print(
            "🧹 Trimming old observations..."
        )

        keep_count = int(
            self.config.get(
                "recent_observations",
                6,
            )
        )

        dynamic_messages = (
            messages[2:]
        )

        recent = (
            dynamic_messages[
                -(keep_count * 2):
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
                    task_context
                    + "\n\nCURRENT WORKING STATE:\n"
                    + self.state_card(
                        state
                    )
                ),
            },

            *recent,
        ]

    # ========================================================
    # SESSION STORAGE
    # ========================================================

    def append_session_message(
        self,
        role: str,
        content: str,
    ) -> None:

        self.session.recent_messages.append(
            {
                "role":
                    role,

                "content":
                    content,
            }
        )

        if (
            len(
                self.session.recent_messages
            )
            > MAX_SESSION_MESSAGES
        ):

            self.session.recent_messages = (
                self.session.recent_messages[
                    -MAX_SESSION_MESSAGES:
                ]
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

        # ====================================================
        # CONSTRAINTS + INHERITANCE
        # ====================================================

        constraints = (
            detect_task_constraints(
                task
            )
        )

        constraints = (
            inherit_constraints(
                constraints,
                self.session.last_constraints,
            )
        )

        self.session.last_constraints = (
            constraints
        )

        if (
            constraints.requested_languages
        ):

            self.session.active_language = (
                sorted(
                    constraints.requested_languages
                )[0]
            )

        if (
            constraints.requested_technologies
        ):

            self.session.active_technology = (
                sorted(
                    constraints.requested_technologies
                )[0]
            )

        self.session.active_task_description = (
            task
        )

        self.append_session_message(
            "user",
            task,
        )

        # ====================================================
        # ACTIVE ROOT
        # ====================================================

        active_root = (
            self.derive_active_root()
        )

        profile = (
            detect_project_profile(
                active_root
            )
        )

        tools = (
            Tools(
                workspace=self.workspace,
                memory=self.memory,
                state=state,
                profile=profile,
                command_root=active_root,
            )
        )

        profile_card = (
            profile_to_prompt(
                profile
            )
        )

        constraint_card = (
            constraints_to_prompt(
                constraints
            )
        )

        memory_card = (
            self.memory_card()
        )

        environment = (
            detect_environment(
                self.workspace.root
            )
        )

        task_context = (
            f"CURRENT USER REQUEST:\n"
            f"{task}\n\n"

            f"SESSION CONTEXT:\n"
            f"{self.session_card()}\n\n"

            f"HARD TASK CONSTRAINTS:\n"
            f"{constraint_card}\n\n"

            f"ACTIVE PROJECT CAPABILITIES:\n"
            f"{profile_card}\n\n"

            f"WORKSPACE ENVIRONMENT:\n"
            f"{json.dumps(environment, indent=2)}\n\n"

            f"VERIFIED PROJECT MEMORY:\n"
            f"{memory_card}\n\n"

            "Use ACTIVE TARGET first when the user's "
            "message refers to an existing thing such as "
            "'it', 'that', 'the game', or 'the file'."
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

                "content":
                    task_context,
            },
        ]

        max_steps = int(
            self.config[
                "max_steps"
            ]
        )

        max_no_progress = int(
            self.config[
                "max_no_progress_steps"
            ]
        )

        for step in range(
            1,
            max_steps + 1,
        ):

            state.step = (
                step
            )

            messages = (
                self.trim_messages(
                    messages,
                    state,
                    task_context,
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

            tool_name = str(
                action.get(
                    "tool",
                    ""
                )
            )

            args = (
                action.get(
                    "args",
                    {}
                )
                or {}
            )

            model_message = str(
                action.get(
                    "message",
                    ""
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

                    self.append_session_message(
                        "assistant",
                        model_message,
                    )

                    for path in (
                        state.edited_files
                    ):

                        self.session.touched_files.add(
                            path
                        )

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
                            "Perform one appropriate "
                            "verification."
                        ),
                    }
                )

                continue

            # =================================================
            # INVALID ACTION TYPE
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
                            "Return type='tool' or "
                            "type='final'."
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
                            "args must be a JSON object."
                        ),
                    }
                )

                continue

            # =================================================
            # TOOL SCHEMA CHECK
            # =================================================

            valid, schema_reason = (
                validate_tool_arguments(
                    tool_name,
                    args,
                )
            )

            if not valid:

                print()
                print(
                    f"🚫 TOOL SCHEMA BLOCK: "
                    f"{schema_reason}"
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
                            "CONTROLLER TOOL SCHEMA ERROR:\n"
                            f"{schema_reason}\n\n"
                            "Use an existing tool with "
                            "its exact documented arguments."
                        ),
                    }
                )

                continue

            # =================================================
            # LANGUAGE / FRAMEWORK CONSTRAINTS
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
                    f"🚫 CONSTRAINT BLOCK: "
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
                            "CONTROLLER HARD-CONSTRAINT BLOCK:\n"
                            f"{reason}\n"
                            "Keep the requested language/"
                            "technology."
                        ),
                    }
                )

                continue

            # =================================================
            # CAPABILITY CHECK
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
                    f"🚫 CAPABILITY BLOCK: "
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
                            "CONTROLLER:\n"
                            f"{capability_reason}"
                        ),
                    }
                )

                continue

            # =================================================
            # IDENTICAL LOOP CHECK
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
                    "This exact tool call has already "
                    "been attempted multiple times. "
                    "Choose another approach."
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
            # TOOL EXECUTION
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
            ) = (
                self.execute_tool(
                    tools,
                    tool_name,
                    args,
                )
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
                    state.observations[
                        -100:
                    ]
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

            self.update_session_target_from_tool(
                tool_name,
                args,
                success,
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
                f"{truncate_text(output, 1500)}"
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

                f"CURRENT ACTIVE TARGET:\n"
                f"{self.session.active_target or '(none)'}\n\n"

                f"CURRENT WORKING STATE:\n"
                f"{self.state_card(state)}"
            )

            if not success:

                hint = (
                    self.controller_hint(
                        tool_name,
                        output,
                    )
                )

                if hint:

                    observation_message += (
                        "\n\nCONTROLLER HINT:\n"
                        + hint
                    )

                    print()
                    print(
                        f"💡 {hint}"
                    )

            messages.append(
                {
                    "role":
                        "user",

                    "content":
                        observation_message,
                }
            )

            # =================================================
            # RE-DETECT ACTIVE PROJECT WHEN TARGET CHANGES
            # =================================================

            new_active_root = (
                self.derive_active_root()
            )

            if (
                new_active_root.resolve()
                != active_root.resolve()
            ):

                active_root = (
                    new_active_root
                )

                profile = (
                    detect_project_profile(
                        active_root
                    )
                )

                tools.command_root = (
                    active_root
                )

                tools.profile = (
                    profile
                )

                profile_card = (
                    profile_to_prompt(
                        profile
                    )
                )

            # =================================================
            # NO-PROGRESS BREAKER
            # =================================================

            if (
                state.no_progress_steps
                >= max_no_progress
            ):

                result = (
                    "🛑 Agent stopped because "
                    f"no meaningful progress was made "
                    f"for {state.no_progress_steps} "
                    f"consecutive steps.\n\n"
                    f"{self.state_card(state)}"
                )

                self.append_session_message(
                    "assistant",
                    result,
                )

                return result

        result = (
            "🛑 Maximum agent step limit "
            f"({max_steps}) reached."
        )

        self.append_session_message(
            "assistant",
            result,
        )

        return result


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
                len(
                    found
                )
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
        SCRIPT_DIR
        / "models",

        SCRIPT_DIR,

        Path.cwd()
        / "models",
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
                str(
                    model
                ).lower()
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
            len(
                models
            )
            + 1
        )

        print()
        print(
            f"{manual_index}. "
            "Enter model path manually"
        )

        print(
            "0. Back"
        )

        selected = (
            input(
                "\nSelect model: "
            ).strip()
        )

        if (
            selected
            == "0"
        ):

            return

        try:

            number = int(
                selected
            )

            if (
                1
                <= number
                <= len(
                    models
                )
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

    entered = (
        input(
            "\nFull GGUF path: "
        )
        .strip()
        .strip(
            '"'
        )
    )

    if not entered:

        return

    path = (
        Path(
            entered
        )
        .expanduser()
        .resolve()
    )

    if (
        not path.exists()
        or path.suffix.lower()
        != ".gguf"
    ):

        print(
            "\n❌ Invalid GGUF file."
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
        f"Current:\n"
        f"{config['project_root']}"
    )

    entered = (
        input(
            "\nNew project folder "
            "(Enter to cancel): "
        )
        .strip()
        .strip(
            '"'
        )
    )

    if not entered:

        return

    path = (
        Path(
            entered
        )
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
            f"             : "
            f"{config['context_size']}"
        )

        print(
            f"2. GPU layers"
            f"               : "
            f"{config['gpu_layers']}"
        )

        print(
            f"3. Max agent steps"
            f"          : "
            f"{config['max_steps']}"
        )

        print(
            f"4. Max no-progress steps"
            f"    : "
            f"{config['max_no_progress_steps']}"
        )

        print(
            f"5. Temperature"
            f"              : "
            f"{config['temperature']}"
        )

        print(
            f"6. Debug level"
            f"              : "
            f"{config['debug_level']}"
        )

        print(
            f"7. Recent observations"
            f"      : "
            f"{config['recent_observations']}"
        )

        print(
            f"8. Prompt cache enabled"
            f"     : "
            f"{config['prompt_cache_enabled']}"
        )

        print(
            f"9. Prompt cache MB"
            f"          : "
            f"{config['prompt_cache_mb']}"
        )

        print(
            f"10. Trim context ratio"
            f"      : "
            f"{config['trim_context_ratio']}"
        )

        print(
            "11. Reset defaults"
        )

        print(
            "0. Back"
        )

        choice = (
            input(
                "\nSelect option: "
            ).strip()
        )

        if choice == "0":

            save_config(
                config
            )

            return

        if choice == "1":

            try:

                value = int(
                    input(
                        "Context size: "
                    )
                )

                if (
                    value
                    >= 1024
                ):

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
                "1. Raw stream"
            )
            print(
                "2. Raw + parsed + tools"
            )
            print(
                "3. Full prompts"
            )

            selected = (
                input(
                    "Debug level: "
                ).strip()
            )

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
                        "Recent observations: "
                    )
                )

                if value >= 2:

                    config[
                        "recent_observations"
                    ] = value

            except ValueError:
                pass

        elif choice == "8":

            current = bool(
                config[
                    "prompt_cache_enabled"
                ]
            )

            config[
                "prompt_cache_enabled"
            ] = not current

        elif choice == "9":

            try:

                value = int(
                    input(
                        "Prompt cache MB: "
                    )
                )

                if value >= 64:

                    config[
                        "prompt_cache_mb"
                    ] = value

            except ValueError:
                pass

        elif choice == "10":

            try:

                value = float(
                    input(
                        "Trim ratio "
                        "(example 0.72): "
                    )
                )

                if (
                    0.50
                    <= value
                    <= 0.90
                ):

                    config[
                        "trim_context_ratio"
                    ] = value

            except ValueError:
                pass

        elif choice == "11":

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
# SYSTEM INFORMATION
# ============================================================

def system_information_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print_header(
        "🔎 SYSTEM INFORMATION"
    )

    project = (
        Path(
            config[
                "project_root"
            ]
        )
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
        f"Script directory:\n"
        f"{SCRIPT_DIR}"
    )

    print()
    print(
        f"Application directory:\n"
        f"{APP_DIR}"
    )

    print()
    print(
        f"Config:\n"
        f"{CONFIG_FILE}"
    )

    print()
    print(
        f"Memory:\n"
        f"{MEMORY_ROOT}"
    )

    print()
    print(
        f"Prompt cache enabled: "
        f"{config['prompt_cache_enabled']}"
    )

    print(
        f"Prompt cache MB: "
        f"{config['prompt_cache_mb']}"
    )

    pause()


# ============================================================
# CHAT
# ============================================================

def chat_menu(
    config: dict[str, Any],
) -> None:

    if not (
        config.get(
            "model_path"
        )
    ):

        print(
            "\n⚠️ Select a model first."
        )

        pause()

        model_selection_menu(
            config
        )

        if not (
            config.get(
                "model_path"
            )
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

    print(
        f"Cache   : "
        f"{config['prompt_cache_enabled']} "
        f"({config['prompt_cache_mb']} MB)"
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

        print()
        print(
            "❌ Model failed to load:"
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

    print(
        "/session Show current session context"
    )

    print(
        "/reset   Reset conversation/session target"
    )

    while True:

        print()
        print(
            "-" * 70
        )

        task = (
            input(
                "\n👤 > "
            ).strip()
        )

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
                "/back    Return to menu"
            )

            print(
                "/session Show active conversation target"
            )

            print(
                "/reset   Forget active conversation target"
            )

            continue

        if normalized == "/session":

            print()
            print(
                agent.session_card()
            )

            continue

        if normalized == "/reset":

            agent.session = (
                SessionContext()
            )

            print(
                "✅ Session context reset."
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

    model_name = (
        Path(
            model_path
        ).name
        if model_path
        else "Not selected"
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
        f"⚡ Cache   : "
        f"{config['prompt_cache_enabled']}"
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

        choice = (
            input(
                "\nSelect option: "
            ).strip()
        )

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