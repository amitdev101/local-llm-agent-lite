from __future__ import annotations

import copy
import hashlib
import json
import platform
import re
import time

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lancedb

from llama_cpp import (
    Llama,
    LlamaRAMCache,
)

from myllm_constants import (
    APP_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    KNOWN_FILE_EXTENSIONS,
    MAX_IDENTICAL_ACTIONS,
    MAX_MODEL_OUTPUT_TOKENS,
    MAX_SESSION_MESSAGES,
    MEMORY_ROOT,
    PAYLOAD_ROOT,
    SMALL_STUB_LINES,
)

from myllm_menu import (
    main_menu,
)

from myllm_tools import (
    PayloadStore,
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

    PAYLOAD_ROOT.mkdir(
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

        config = DEFAULT_CONFIG.copy()

        save_config(config)

        return config

    try:

        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    except Exception:

        loaded = {}

    config = DEFAULT_CONFIG.copy()

    config.update(loaded)

    save_config(config)

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

    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskConstraints:

    requested_languages: set[str] = field(default_factory=set)

    forbidden_languages: set[str] = field(default_factory=set)

    requested_technologies: set[str] = field(default_factory=set)

    forbidden_technologies: set[str] = field(default_factory=set)

    browser_app: bool = False

    frontend_required: bool = False


@dataclass
class SessionContext:

    recent_messages: list[dict[str, str]] = field(default_factory=list)

    active_root: str = ""

    active_file: str = ""

    active_language: str = ""

    active_technology: str = ""

    active_task_description: str = ""

    touched_files: set[str] = field(default_factory=set)

    payload_refs: set[str] = field(default_factory=set)

    last_constraints: TaskConstraints = field(default_factory=TaskConstraints)


@dataclass
class AgentState:

    task: str

    step: int = 0

    observations: list[Observation] = field(default_factory=list)

    action_counts: dict[str, int] = field(default_factory=dict)

    edited_files: set[str] = field(default_factory=set)

    edit_backups: list[
        tuple[
            Path,
            str,
            bool,
        ]
    ] = field(default_factory=list)

    created_files: set[str] = field(default_factory=set)

    modified_files: set[str] = field(default_factory=set)

    read_files: set[str] = field(default_factory=set)

    blockers: list[str] = field(default_factory=list)

    unavailable_capabilities: set[str] = field(default_factory=set)

    no_progress_steps: int = 0

    last_test_passed: bool = False

    last_validation_passed: bool = False

    latest_result: str = ""

    mutation_revision: int = 0

    verified_revision: int = -1

    implementation_required: bool = False

    implementation_target: str = ""


# ============================================================
# TASK CONSTRAINTS
# ============================================================


def detect_task_constraints(
    task: str,
) -> TaskConstraints:

    text = task.lower()

    result = TaskConstraints()

    if "javascript" in text or re.search(
        r"\bjs\b",
        text,
    ):

        result.requested_languages.add("javascript")

    if "typescript" in text or re.search(
        r"\bts\b",
        text,
    ):

        result.requested_languages.add("typescript")

    if "python" in text:

        result.requested_languages.add("python")

    if (
        re.search(
            r"\bjava\b",
            text,
        )
        and "javascript" not in text
    ):

        result.requested_languages.add("java")

    if re.search(
        r"\brust\b",
        text,
    ):

        result.requested_languages.add("rust")

    if "golang" in text or "go project" in text or "go application" in text:

        result.requested_languages.add("go")

    if "react" in text:

        result.requested_technologies.add("react")

        result.requested_languages.add("javascript")

        result.browser_app = True

        result.frontend_required = True

    if "next.js" in text or "nextjs" in text:

        result.requested_technologies.add("next.js")

        result.requested_languages.add("javascript")

        result.browser_app = True

        result.frontend_required = True

    if "vite" in text:

        result.requested_technologies.add("vite")

        result.browser_app = True

        result.frontend_required = True

    if "pygame" in text:

        result.requested_technologies.add("pygame")

        result.requested_languages.add("python")

    if any(
        phrase in text
        for phrase in [
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

    if any(
        phrase in text
        for phrase in [
            "no python",
            "not python",
            "without python",
        ]
    ):

        result.forbidden_languages.add("python")

    if any(
        phrase in text
        for phrase in [
            "no pygame",
            "not pygame",
            "without pygame",
        ]
    ):

        result.forbidden_technologies.add("pygame")

    return result


def inherit_constraints(
    current: TaskConstraints,
    previous: TaskConstraints,
) -> TaskConstraints:

    if not (current.requested_languages):

        current.requested_languages = set(previous.requested_languages)

    if not (current.requested_technologies):

        current.requested_technologies = set(previous.requested_technologies)

    if not (current.forbidden_languages):

        current.forbidden_languages = set(previous.forbidden_languages)

    if not (current.forbidden_technologies):

        current.forbidden_technologies = set(previous.forbidden_technologies)

    if not current.browser_app:

        current.browser_app = previous.browser_app

    if not (current.frontend_required):

        current.frontend_required = previous.frontend_required

    return current


def constraints_to_prompt(
    constraints: TaskConstraints,
) -> str:

    def show(
        values: set[str],
        fallback: str,
    ) -> str:

        return ", ".join(sorted(values)) or fallback

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
# FOLLOW-UP DETECTION
# ============================================================


def is_short_followup(
    task: str,
) -> bool:

    text = task.strip().lower()

    exact = {
        "continue",
        "continue it",
        "complete it",
        "finish it",
        "fix it",
        "do it",
        "yes",
        "go ahead",
        "proceed",
        "keep going",
    }

    if text in exact:

        return True

    if len(text.split()) <= 8 and any(
        phrase in text
        for phrase in [
            "complete it",
            "finish it",
            "continue",
            "fix that",
            "fix it",
            "the game",
            "that file",
        ]
    ):

        return True

    return False


# ============================================================
# EXPLICIT PATH DETECTION
# ============================================================


def extract_explicit_workspace_path(
    task: str,
    workspace: Workspace,
) -> str | None:

    extensions = "|".join(KNOWN_FILE_EXTENSIONS)

    pattern = rf"([A-Za-z]:\\" rf'[^\r\n<>|?*"]+?' rf"\.(?:{extensions}))"

    matches = re.findall(
        pattern,
        task,
        flags=re.IGNORECASE,
    )

    for raw in matches:

        raw = raw.strip().rstrip(".,;:")

        try:

            resolved = Path(raw).resolve()

            relative = resolved.relative_to(workspace.root)

            return str(relative)

        except Exception:

            continue

    return None


# ============================================================
# HARD CONSTRAINT CHECK
# ============================================================


def validate_single_mutation(
    path: str,
    content: str,
    constraints: TaskConstraints,
) -> tuple[
    bool,
    str,
]:

    path_lower = path.lower()

    content_lower = content.lower()

    languages = constraints.requested_languages

    technologies = constraints.requested_technologies

    if "javascript" in languages or "typescript" in languages:

        if path_lower.endswith(".py"):

            return (
                False,
                (
                    "The active task requires "
                    "JavaScript/TypeScript. "
                    "Python is not allowed."
                ),
            )

        if "pygame" in content_lower:

            return (
                False,
                (
                    "The active task requires "
                    "JavaScript/TypeScript. "
                    "Pygame is not allowed."
                ),
            )

    if "java" in languages:

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
                    "Do not substitute another language."
                ),
            )

    if "python" in constraints.forbidden_languages:

        if path_lower.endswith(".py") or "pygame" in content_lower:

            return (
                False,
                "Python is explicitly forbidden.",
            )

    if (
        constraints.browser_app
        and "python" not in languages
        and path_lower.endswith(".py")
    ):

        return (
            False,
            (
                "The task requires a browser application, "
                "not a Python desktop application."
            ),
        )

    if "react" in technologies and (
        path_lower.endswith(".py") or "pygame" in content_lower
    ):

        return (
            False,
            ("React is a hard requirement. " "Python/Pygame is not allowed."),
        )

    return (
        True,
        "",
    )


def validate_tool_against_constraints(
    tool_name: str,
    args: dict[str, Any],
    constraints: TaskConstraints,
    payload_store: PayloadStore,
) -> tuple[
    bool,
    str,
]:

    def get_text(
        inline_key: str,
        ref_key: str,
        source: dict[str, Any],
    ) -> str:

        inline = source.get(inline_key)

        if isinstance(
            inline,
            str,
        ):

            return inline

        ref = source.get(ref_key)

        if isinstance(
            ref,
            str,
        ):

            try:

                return payload_store.load(ref)

            except Exception:

                return ""

        return ""

    if tool_name in {
        "create_file",
        "replace_file",
    }:

        return validate_single_mutation(
            str(
                args.get(
                    "path",
                    "",
                )
            ),
            get_text(
                "content",
                "content_ref",
                args,
            ),
            constraints,
        )

    if tool_name == "apply_patch":

        return validate_single_mutation(
            str(
                args.get(
                    "path",
                    "",
                )
            ),
            get_text(
                "new_text",
                "new_text_ref",
                args,
            ),
            constraints,
        )

    if tool_name == "create_files":

        for item in (
            args.get(
                "files",
                [],
            )
            or []
        ):

            allowed, reason = validate_single_mutation(
                str(
                    item.get(
                        "path",
                        "",
                    )
                ),
                get_text(
                    "content",
                    "content_ref",
                    item,
                ),
                constraints,
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

    TABLE_NAME = "project_facts"

    def __init__(
        self,
        workspace: Workspace,
    ):

        self.project_id = hashlib.sha256(
            str(workspace.root).encode("utf-8")
        ).hexdigest()[:16]

        memory_dir = MEMORY_ROOT / self.project_id

        memory_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = lancedb.connect(str(memory_dir))

    def _open(
        self,
    ):

        try:

            return self.db.open_table(self.TABLE_NAME)

        except Exception:

            return None

    def add_fact(
        self,
        fact: str,
        evidence: str,
        evidence_id: str,
    ) -> None:

        row = {
            "project": self.project_id,
            "fact": fact.strip(),
            "evidence": evidence.strip(),
            "evidence_id": evidence_id,
            "created_at": int(time.time()),
        }

        table = self._open()

        if table is None:

            self.db.create_table(
                self.TABLE_NAME,
                data=[row],
            )

        else:

            table.add([row])

    def load_facts(
        self,
        limit: int = 30,
    ) -> list[dict[str, Any]]:

        table = self._open()

        if table is None:

            return []

        try:

            return (
                table.search()
                .where(f"project = " f"'{self.project_id}'")
                .limit(limit)
                .to_list()
            )

        except Exception:

            return []


# ============================================================
# ACTION SCHEMA
# ============================================================

TOOL_NAMES = sorted(TOOL_SCHEMAS.keys())


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
            "enum": [
                "",
                *TOOL_NAMES,
            ],
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
# SEMANTIC ACTION VALIDATION
# ============================================================


def validate_action_semantics(
    action: dict[str, Any],
) -> tuple[
    bool,
    str,
]:

    action_type = str(
        action.get(
            "type",
            "",
        )
    )

    tool_name = str(
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

    if not isinstance(
        args,
        dict,
    ):

        return (
            False,
            "args must be a JSON object.",
        )

    if action_type == "final":

        if tool_name:

            return (
                False,
                (
                    "type='final' cannot contain a tool. "
                    f"You supplied tool='{tool_name}'. "
                    "If you intend to execute the tool, "
                    "return type='tool'."
                ),
            )

        if args:

            return (
                False,
                (
                    "type='final' must use args={}. "
                    "Tool arguments are only valid "
                    "with type='tool'."
                ),
            )

        return (
            True,
            "",
        )

    if action_type == "tool":

        if not tool_name:

            return (
                False,
                ("type='tool' requires " "a non-empty tool name."),
            )

        return (
            True,
            "",
        )

    return (
        False,
        ("type must be either " "'tool' or 'final'."),
    )


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = f"""
You are a local software engineering agent connected to REAL tools.

The Python controller executes your tool actions.

For ordinary conversation, return type="final".
For real file/project work, use tools.

ACTION FORMAT IS STRICT.

A tool action MUST look like:

{{
  "type": "tool",
  "tool": "read_file",
  "args": {{"path": "src/file.java"}},
  "message": "Reading the target file."
}}

A final answer MUST look like:

{{
  "type": "final",
  "tool": "",
  "args": {{}},
  "message": "Final response."
}}

NEVER use type="final" when tool is non-empty.
NEVER use type="tool" with an empty tool name.

HARD CONSTRAINTS
The user's requested language, framework, platform and target path are
mandatory.

SESSION CONTINUITY
The controller gives ACTIVE ROOT, ACTIVE FILE and ACTIVE GOAL.

When the user says:
continue
complete it
finish it
fix it
the game
that file

use the active target instead of rediscovering the repository.

TARGETED EDIT RULE
When an explicit target file is known:
1. Read it.
2. If enough evidence exists, edit it immediately.
3. Do not repeatedly search or reread it.

FILE RULES

create_file:
new text file only.

create_files:
multiple new text files.

replace_file:
complete rewrite of an existing text file.
Use it when a tiny stub must become a real implementation.

apply_patch:
small localized exact-text change.

PAYLOAD REFERENCES
Large generated text may be stored outside conversation history.

Examples:

content_ref="p-1e6fbcc7d3141a42"
old_text_ref="p-..."
new_text_ref="p-..."

If a payload reference already exists, reuse it.
Do not regenerate the same large source code simply to retry an action.

BINARY RULE
Text tools cannot create real .png/.jpg/.jar/.class files.
Never write fake binary placeholder strings.

For Java Swing/AWT applications, draw graphics programmatically unless
the user explicitly asks for external image assets.

PROJECT CAPABILITIES
Only use project commands reported by the controller.

Simple Java source projects can use run_project_build, which may call
javac.

Do not invent tools.
Do not invent tests.
Do not invent commands.

VERIFICATION
After the latest mutation, prefer one strong verification:
1. run_project_build
2. run_project_tests
3. run_project_typecheck
4. run_project_lint
5. narrow file verification

Take exactly ONE tool action per model inference.

Do not reveal private chain-of-thought.
Keep message short.

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

        self.config = config

        model_path = Path(config["model_path"]).resolve()

        project_root = Path(config["project_root"]).resolve()

        if not model_path.exists():

            raise ValueError("Configured model does not exist.")

        if not project_root.exists():

            raise ValueError("Configured workspace does not exist.")

        self.workspace = Workspace(project_root)

        self.memory = ProjectMemory(self.workspace)

        self.payload_store = PayloadStore(
            PAYLOAD_ROOT,
            max_files=int(
                config.get(
                    "payload_max_files",
                    250,
                )
            ),
        )

        self.payload_store.cleanup()

        self.session = SessionContext()

        import os

        threads = max(
            1,
            (os.cpu_count() or 4) - 2,
        )

        print()

        print("🧠 Loading model...")

        self.llm = Llama(
            model_path=str(model_path),
            n_ctx=int(config["context_size"]),
            n_gpu_layers=int(config["gpu_layers"]),
            n_threads=threads,
            n_threads_batch=threads,
            use_mmap=True,
            verbose=False,
        )

        self.n_ctx = int(config["context_size"])

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
                        1024,
                    )
                ),
            )

            try:

                self.prompt_cache = LlamaRAMCache(
                    capacity_bytes=(cache_mb * 1024 * 1024)
                )

                self.llm.set_cache(self.prompt_cache)

                print(f"⚡ Prompt cache enabled: " f"{cache_mb} MB")

            except Exception as error:

                self.prompt_cache = None

                print("⚠️ Prompt cache could not " f"be enabled: {error}")

        print(f"📦 Payload store: " f"{PAYLOAD_ROOT}")

        print("✅ Model loaded.")

    # ========================================================
    # SESSION
    # ========================================================

    def reset_session(
        self,
    ) -> None:

        self.session = SessionContext()

    def append_session_message(
        self,
        role: str,
        content: str,
    ) -> None:

        self.session.recent_messages.append(
            {
                "role": role,
                "content": content,
            }
        )

        if len(self.session.recent_messages) > MAX_SESSION_MESSAGES:

            self.session.recent_messages = self.session.recent_messages[
                -MAX_SESSION_MESSAGES:
            ]

    def session_card(
        self,
    ) -> str:

        recent = []

        for message in self.session.recent_messages[-6:]:

            recent.append(f"{message['role'].upper()}: " f"{message['content']}")

        touched = (
            "\n".join(f"- {path}" for path in sorted(self.session.touched_files)[-10:])
            or "(none)"
        )

        payloads = (
            "\n".join(
                f"- {payload}" for payload in sorted(self.session.payload_refs)[-10:]
            )
            or "(none)"
        )

        return (
            f"ACTIVE ROOT: "
            f"{self.session.active_root or '(none)'}\n"
            f"ACTIVE FILE: "
            f"{self.session.active_file or '(none)'}\n"
            f"ACTIVE LANGUAGE: "
            f"{self.session.active_language or '(none)'}\n"
            f"ACTIVE TECHNOLOGY: "
            f"{self.session.active_technology or '(none)'}\n"
            f"ACTIVE GOAL: "
            f"{self.session.active_task_description or '(none)'}\n\n"
            f"TOUCHED FILES:\n"
            f"{touched}\n\n"
            f"AVAILABLE PAYLOAD REFS:\n"
            f"{payloads}\n\n"
            f"RECENT CONVERSATION:\n" + ("\n".join(recent) or "(none)")
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

        if self.debug_level() < minimum_level:

            return

        print()

        print("─" * 70)

        print(f"🔎 {title}")

        print("─" * 70)

        if isinstance(
            value,
            str,
        ):

            print(value)

        else:

            print(
                json.dumps(
                    value,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )

        print("─" * 70)

    # ========================================================
    # TOKENS
    # ========================================================

    def token_count(
        self,
        messages: list[dict[str, str]],
    ) -> int:

        text = "\n".join(
            (f"{message['role']}:\n" f"{message['content']}") for message in messages
        )

        try:

            return len(
                self.llm.tokenize(
                    text.encode("utf-8"),
                    add_bos=False,
                )
            )

        except Exception:

            return max(
                1,
                len(text) // 4,
            )

    # ========================================================
    # MODEL
    # ========================================================

    def call_model(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:

        if self.debug_level() >= 3:

            self.debug_print(
                "FULL MODEL INPUT",
                messages,
                minimum_level=3,
            )

        stream = self.llm.create_chat_completion(
            messages=messages,
            response_format={
                "type": "json_object",
                "schema": ACTION_SCHEMA,
            },
            temperature=float(self.config["temperature"]),
            top_p=0.9,
            max_tokens=(MAX_MODEL_OUTPUT_TOKENS),
            stream=True,
        )

        full_content = ""

        if self.debug_level() >= 1:

            print()

            print("─" * 70)

            print("🧠 RAW LLM STREAM")

            print("─" * 70)

        for chunk in stream:

            choices = chunk.get(
                "choices",
                [],
            )

            if not choices:

                continue

            delta = choices[0].get(
                "delta",
                {},
            )

            text = delta.get(
                "content",
                "",
            )

            if not text:

                continue

            full_content += text

            if self.debug_level() >= 1:

                print(
                    text,
                    end="",
                    flush=True,
                )

        if self.debug_level() >= 1:

            print()

            print("─" * 70)

        try:

            return json.loads(full_content)

        except json.JSONDecodeError as error:

            return {
                "type": "invalid",
                "tool": "",
                "args": {},
                "message": ("Invalid JSON returned by model: " f"{error}"),
            }

    # ========================================================
    # PAYLOAD EXTERNALIZATION
    # ========================================================

    def payload_threshold(
        self,
    ) -> int:

        return max(
            200,
            int(
                self.config.get(
                    "payload_externalize_chars",
                    700,
                )
            ),
        )

    def _externalize_string_field(
        self,
        args: dict[str, Any],
        inline_key: str,
        ref_key: str,
    ) -> tuple[
        str | None,
        int,
    ]:

        value = args.get(inline_key)

        if not isinstance(
            value,
            str,
        ):

            existing_ref = args.get(ref_key)

            if isinstance(
                existing_ref,
                str,
            ):

                self.session.payload_refs.add(existing_ref)

            return (
                None,
                0,
            )

        if len(value) < self.payload_threshold():

            return (
                None,
                len(value),
            )

        payload_id = self.payload_store.save(value)

        self.session.payload_refs.add(payload_id)

        args.pop(
            inline_key,
            None,
        )

        args[ref_key] = payload_id

        return (
            payload_id,
            len(value),
        )

    def externalize_action_for_history(
        self,
        action: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        list[str],
    ]:

        compact = copy.deepcopy(action)

        args = compact.get("args")

        if not isinstance(
            args,
            dict,
        ):

            return (
                compact,
                [],
            )

        tool_name = str(
            compact.get(
                "tool",
                "",
            )
        )

        notes = []

        if tool_name in {
            "create_file",
            "replace_file",
        }:

            payload_id, chars = self._externalize_string_field(
                args,
                "content",
                "content_ref",
            )

            if payload_id:

                notes.append(
                    f"content stored as " f"{payload_id} " f"({chars:,} chars)"
                )

        elif tool_name == "apply_patch":

            old_ref, old_chars = self._externalize_string_field(
                args,
                "old_text",
                "old_text_ref",
            )

            new_ref, new_chars = self._externalize_string_field(
                args,
                "new_text",
                "new_text_ref",
            )

            if old_ref:

                notes.append(
                    f"old_text stored as " f"{old_ref} " f"({old_chars:,} chars)"
                )

            if new_ref:

                notes.append(
                    f"new_text stored as " f"{new_ref} " f"({new_chars:,} chars)"
                )

        elif tool_name == "create_files":

            files = args.get("files")

            if isinstance(
                files,
                list,
            ):

                for item in files:

                    if not isinstance(
                        item,
                        dict,
                    ):

                        continue

                    payload_id, chars = self._externalize_string_field(
                        item,
                        "content",
                        "content_ref",
                    )

                    if payload_id:

                        notes.append(
                            f"{item.get('path', 'file')} "
                            f"stored as "
                            f"{payload_id} "
                            f"({chars:,} chars)"
                        )

        return (
            compact,
            notes,
        )

    # ========================================================
    # MEMORY
    # ========================================================

    def memory_card(
        self,
    ) -> str:

        facts = self.memory.load_facts(limit=20)

        if not facts:

            return "(none)"

        lines = []

        seen = set()

        for row in reversed(facts):

            fact = str(
                row.get(
                    "fact",
                    "",
                )
            ).strip()

            if not fact or fact in seen:

                continue

            seen.add(fact)

            lines.append(f"- {fact}")

        return "\n".join(lines[:15])

    # ========================================================
    # ACTIVE TARGET
    # ========================================================

    def apply_explicit_target(
        self,
        task: str,
    ) -> None:

        target = extract_explicit_workspace_path(
            task,
            self.workspace,
        )

        if not target:

            return

        self.session.active_file = target

        parent = str(Path(target).parent)

        if parent == ".":

            parent = ""

        self.session.active_root = parent

        self.session.touched_files.add(target)

    def derive_active_root(
        self,
    ) -> Path:

        if self.session.active_root:

            try:

                candidate = self.workspace.resolve(self.session.active_root)

                if candidate.exists():

                    return candidate

            except Exception:

                pass

        if self.session.active_file:

            try:

                return self.workspace.resolve(self.session.active_file).parent

            except Exception:

                pass

        return self.workspace.root

    def normalize_workspace_path(
        self,
        path: str,
    ) -> str:

        if not path:

            return ""

        try:

            return self.workspace.relative(self.workspace.resolve(path))

        except Exception:

            return path

    def record_tool_targets(
        self,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
    ) -> None:

        if not success:

            return

        if tool_name in {
            "create_file",
            "replace_file",
            "apply_patch",
            "read_file",
            "verify_file_exists",
        }:

            path = self.normalize_workspace_path(
                str(
                    args.get(
                        "path",
                        "",
                    )
                )
            )

            if path:

                self.session.touched_files.add(path)

                if not self.session.active_file and tool_name in {
                    "create_file",
                    "replace_file",
                    "apply_patch",
                }:

                    self.session.active_file = path

                    parent = str(Path(path).parent)

                    if parent != ".":

                        self.session.active_root = parent

        elif tool_name == "create_files":

            for item in (
                args.get(
                    "files",
                    [],
                )
                or []
            ):

                path = self.normalize_workspace_path(
                    str(
                        item.get(
                            "path",
                            "",
                        )
                    )
                )

                if path:

                    self.session.touched_files.add(path)

    # ========================================================
    # STATE
    # ========================================================

    def state_card(
        self,
        state: AgentState,
    ) -> str:

        def show(
            values,
        ) -> str:

            if not values:

                return "(none)"

            return "\n".join(f"- {value}" for value in sorted(values))

        blockers = (
            "\n".join(f"- {blocker}" for blocker in state.blockers[-5:]) or "(none)"
        )

        return (
            f"GOAL:\n"
            f"{state.task}\n\n"
            f"PRIMARY FILE:\n"
            f"{self.session.active_file or '(none)'}\n\n"
            f"PRIMARY ROOT:\n"
            f"{self.session.active_root or '(none)'}\n\n"
            f"CREATED:\n"
            f"{show(state.created_files)}\n\n"
            f"MODIFIED:\n"
            f"{show(state.modified_files)}\n\n"
            f"READ:\n"
            f"{show(state.read_files)}\n\n"
            f"BLOCKERS:\n"
            f"{blockers}\n\n"
            f"MUTATION REVISION: "
            f"{state.mutation_revision}\n"
            f"VERIFIED REVISION: "
            f"{state.verified_revision}\n\n"
            f"IMPLEMENTATION REQUIRED: "
            f"{state.implementation_required}\n"
            f"IMPLEMENTATION TARGET: "
            f"{state.implementation_target or '(none)'}\n\n"
            f"LATEST RESULT:\n"
            f"{state.latest_result or '(none)'}"
        )

    # ========================================================
    # IMPLEMENTATION PHASE
    # ========================================================

    def maybe_enter_implementation_phase(
        self,
        state: AgentState,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        output: str,
    ) -> str:

        if not success:

            return ""

        if tool_name != "read_file":

            return ""

        active_file = self.session.active_file

        if not active_file:

            return ""

        requested = self.normalize_workspace_path(
            str(
                args.get(
                    "path",
                    "",
                )
            )
        )

        if requested.lower() != active_file.lower():

            return ""

        task_text = (state.task + " " + self.session.active_task_description).lower()

        if not any(
            word in task_text
            for word in [
                "complete",
                "finish",
                "implement",
                "continue",
                "build",
                "make",
                "fix",
            ]
        ):

            return ""

        match = re.search(
            r"TOTAL_LINES:\s*(\d+)",
            output,
        )

        if not match:

            return ""

        line_count = int(match.group(1))

        if line_count > SMALL_STUB_LINES:

            return ""

        state.implementation_required = True

        state.implementation_target = active_file

        return (
            "The active target is only "
            f"{line_count} lines and appears "
            "to be a stub. Implement it now. "
            "Do not search, reread, or create "
            "unrelated assets first. "
            "Prefer replace_file for a full rewrite."
        )

    def implementation_phase_block(
        self,
        state: AgentState,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:

        if not (state.implementation_required):

            return ""

        target = state.implementation_target

        if tool_name not in {
            "replace_file",
            "apply_patch",
        }:

            return (
                "The active target has already been "
                "inspected and is an incomplete stub. "
                f"Implement {target} now. "
                "Do not search, reread, create directories, "
                "or create unrelated assets first."
            )

        requested = self.normalize_workspace_path(
            str(
                args.get(
                    "path",
                    "",
                )
            )
        )

        if requested.lower() != target.lower():

            return (
                "Implementation is currently required on "
                f"{target}. Edit that target."
            )

        return ""

    # ========================================================
    # STATE UPDATE
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

        path = self.normalize_workspace_path(
            str(
                args.get(
                    "path",
                    "",
                )
            )
        )

        if success:

            if tool_name == "create_file" and path:

                if path not in state.created_files:

                    progress = True

                state.created_files.add(path)

            elif tool_name == "create_files":

                for item in (
                    args.get(
                        "files",
                        [],
                    )
                    or []
                ):

                    item_path = self.normalize_workspace_path(
                        str(
                            item.get(
                                "path",
                                "",
                            )
                        )
                    )

                    if not item_path:

                        continue

                    if item_path not in state.created_files:

                        progress = True

                    state.created_files.add(item_path)

            elif (
                tool_name
                in {
                    "replace_file",
                    "apply_patch",
                }
                and path
            ):

                state.modified_files.add(path)

                progress = True

                if (
                    state.implementation_required
                    and path.lower() == state.implementation_target.lower()
                ):

                    state.implementation_required = False

            elif tool_name == "read_file" and path:

                if path not in state.read_files:

                    progress = True

                state.read_files.add(path)

            elif tool_name == "create_directory":

                progress = True

            elif tool_name.startswith("verify_") or tool_name in {
                "run_project_tests",
                "run_project_build",
                "run_project_lint",
                "run_project_typecheck",
                "validate_python",
                "check_python_import",
            }:

                progress = True

                state.verified_revision = state.mutation_revision

        else:

            lowered = output.lower()

            new_blocker = None

            if "no project test command" in lowered:

                state.unavailable_capabilities.add("tests")

                new_blocker = "Project tests unavailable."

            elif "no project build command" in lowered:

                state.unavailable_capabilities.add("build")

                new_blocker = "Project build unavailable."

            elif "no project lint command" in lowered:

                state.unavailable_capabilities.add("lint")

                new_blocker = "Project lint unavailable."

            elif "no project typecheck command" in lowered:

                state.unavailable_capabilities.add("typecheck")

                new_blocker = "Project typecheck unavailable."

            if new_blocker and new_blocker not in state.blockers:

                state.blockers.append(new_blocker)

                progress = True

        state.latest_result = truncate_text(
            output,
            450,
        )

        return progress

    # ========================================================
    # EXECUTION
    # ========================================================

    def execute_tool(
        self,
        tools: Tools,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[
        bool,
        str,
    ]:

        registry = build_tool_registry(tools)

        function = registry.get(tool_name)

        if function is None:

            return (
                False,
                f"Unknown tool: {tool_name}",
            )

        try:

            result = function(**args)

            return (
                True,
                truncate_text(str(result)),
            )

        except Exception as error:

            return (
                False,
                (f"{type(error).__name__}: " f"{error}"),
            )

    # ========================================================
    # HINTS / CAPABILITY
    # ========================================================

    def controller_hint(
        self,
        tool_name: str,
        output: str,
    ) -> str:

        lowered = output.lower()

        if tool_name == "create_directory" and "directory already exists" in lowered:

            return "The directory already exists. " "Do not recreate or verify it."

        if tool_name == "create_file" and "already exists" in lowered:

            return (
                "The file exists. Use replace_file "
                "for a full rewrite or apply_patch."
            )

        if tool_name == "apply_patch" and "old_text was not found" in lowered:

            return (
                "Use exact RAW_CONTENT or an existing "
                "old_text_ref. If most of the file changes, "
                "use replace_file."
            )

        if "binary" in lowered:

            return (
                "Do not create fake binary/image files. "
                "Use programmatic graphics instead."
            )

        if "payload does not exist" in lowered:

            return (
                "The payload reference is unavailable. "
                "Generate the required content again once."
            )

        return ""

    def capability_block(
        self,
        state: AgentState,
        tool_name: str,
    ) -> str:

        mapping = {
            "run_project_tests": "tests",
            "run_project_build": "build",
            "run_project_lint": "lint",
            "run_project_typecheck": "typecheck",
        }

        capability = mapping.get(tool_name)

        if capability and capability in state.unavailable_capabilities:

            return f"{tool_name} is already " "confirmed unavailable."

        return ""

    # ========================================================
    # COMPLETION
    # ========================================================

    def completion_allowed(
        self,
        state: AgentState,
    ) -> tuple[
        bool,
        str,
    ]:

        if state.implementation_required:

            return (
                False,
                ("The active stub has not yet " "been implemented."),
            )

        if state.mutation_revision == 0:

            return (
                True,
                "",
            )

        if state.verified_revision == state.mutation_revision:

            return (
                True,
                "",
            )

        return (
            False,
            ("The latest mutation has not " "been verified yet."),
        )

    # ========================================================
    # TRIMMING
    # ========================================================

    def trim_messages(
        self,
        messages: list[dict[str, str]],
        state: AgentState,
        task_context: str,
    ) -> list[dict[str, str]]:

        ratio = float(
            self.config.get(
                "trim_context_ratio",
                0.72,
            )
        )

        if self.token_count(messages) < int(self.n_ctx * ratio):

            return messages

        print("🧹 Trimming old observations...")

        keep_count = int(
            self.config.get(
                "recent_observations",
                6,
            )
        )

        recent = messages[2:][-(keep_count * 2) :]

        return [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    task_context
                    + "\n\nCURRENT WORKING STATE:\n"
                    + self.state_card(state)
                ),
            },
            *recent,
        ]

    # ========================================================
    # RUN
    # ========================================================

    def run(
        self,
        task: str,
    ) -> str:

        state = AgentState(task=task)

        self.apply_explicit_target(task)

        constraints = detect_task_constraints(task)

        constraints = inherit_constraints(
            constraints,
            self.session.last_constraints,
        )

        self.session.last_constraints = constraints

        if constraints.requested_languages:

            self.session.active_language = sorted(constraints.requested_languages)[0]

        if constraints.requested_technologies:

            self.session.active_technology = sorted(constraints.requested_technologies)[
                0
            ]

        if not (is_short_followup(task)):

            self.session.active_task_description = task

        elif not (self.session.active_task_description):

            self.session.active_task_description = task

        self.append_session_message(
            "user",
            task,
        )

        active_root = self.derive_active_root()

        profile = detect_project_profile(active_root)

        tools = Tools(
            workspace=self.workspace,
            payload_store=self.payload_store,
            memory=self.memory,
            state=state,
            profile=profile,
            command_root=active_root,
        )

        constraints_card = constraints_to_prompt(constraints)

        profile_card = profile_to_prompt(profile)

        environment = {
            "os": (f"{platform.system()} " f"{platform.release()}"),
            "workspace": str(self.workspace.root),
        }

        task_context = (
            f"CURRENT USER REQUEST:\n"
            f"{task}\n\n"
            f"SESSION CONTEXT:\n"
            f"{self.session_card()}\n\n"
            f"HARD TASK CONSTRAINTS:\n"
            f"{constraints_card}\n\n"
            f"ACTIVE PROJECT CAPABILITIES:\n"
            f"{profile_card}\n\n"
            f"ENVIRONMENT:\n"
            f"{json.dumps(environment, indent=2)}\n\n"
            f"VERIFIED MEMORY:\n"
            f"{self.memory_card()}\n\n"
            "If ACTIVE FILE is known, use it before "
            "searching the wider repository."
        )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": task_context,
            },
        ]

        max_steps = int(self.config["max_steps"])

        max_no_progress = int(self.config["max_no_progress_steps"])

        for step in range(
            1,
            max_steps + 1,
        ):

            state.step = step

            messages = self.trim_messages(
                messages,
                state,
                task_context,
            )

            used = self.token_count(messages)

            print()

            print(
                f"🧠 Step "
                f"{step}/{max_steps}"
                f" | context ≈ "
                f"{used}/{self.n_ctx}"
            )

            action = self.call_model(messages)

            # =================================================
            # CRITICAL SEMANTIC ACTION VALIDATION
            # =================================================

            semantic_ok, semantic_reason = validate_action_semantics(action)

            if not semantic_ok:

                print()

                print(f"🚫 ACTION FORMAT BLOCK: " f"{semantic_reason}")

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            action,
                            ensure_ascii=False,
                        ),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "CONTROLLER ACTION FORMAT ERROR:\n"
                            f"{semantic_reason}\n\n"
                            "Correct the action and try again."
                        ),
                    }
                )

                if state.no_progress_steps >= max_no_progress:

                    return (
                        "🛑 Agent stopped because "
                        "it repeatedly returned invalid "
                        "action formats."
                    )

                continue

            action_type = str(
                action.get(
                    "type",
                    "",
                )
            )

            tool_name = str(
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

            model_message = str(
                action.get(
                    "message",
                    "",
                )
            )

            # =================================================
            # FINAL
            # =================================================

            if action_type == "final":

                allowed, reason = self.completion_allowed(state)

                if allowed:

                    self.append_session_message(
                        "assistant",
                        model_message,
                    )

                    return model_message

                print()

                print(f"🚫 Completion rejected: " f"{reason}")

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            action,
                            ensure_ascii=False,
                        ),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": ("CONTROLLER:\n" f"{reason}"),
                    }
                )

                continue

            # =================================================
            # TOOL ARGUMENT VALIDATION
            # =================================================

            valid, reason = validate_tool_arguments(
                tool_name,
                args,
            )

            if not valid:

                print()

                print(f"🚫 TOOL SCHEMA BLOCK: " f"{reason}")

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            action,
                            ensure_ascii=False,
                        ),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": ("CONTROLLER TOOL SCHEMA ERROR:\n" f"{reason}"),
                    }
                )

                continue

            # =================================================
            # IMPLEMENTATION PHASE
            # =================================================

            implementation_reason = self.implementation_phase_block(
                state,
                tool_name,
                args,
            )

            if implementation_reason:

                print()

                print(f"🚫 IMPLEMENTATION BLOCK: " f"{implementation_reason}")

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            action,
                            ensure_ascii=False,
                        ),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": ("CONTROLLER:\n" f"{implementation_reason}"),
                    }
                )

                continue

            # =================================================
            # HARD REQUIREMENTS
            # =================================================

            allowed, reason = validate_tool_against_constraints(
                tool_name,
                args,
                constraints,
                self.payload_store,
            )

            if not allowed:

                print()

                print(f"🚫 CONSTRAINT BLOCK: " f"{reason}")

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            action,
                            ensure_ascii=False,
                        ),
                    }
                )

                messages.append(
                    {
                        "role": "user",
                        "content": ("CONTROLLER HARD-CONSTRAINT BLOCK:\n" f"{reason}"),
                    }
                )

                continue

            # =================================================
            # CAPABILITY
            # =================================================

            capability_reason = self.capability_block(
                state,
                tool_name,
            )

            if capability_reason:

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "user",
                        "content": ("CONTROLLER:\n" f"{capability_reason}"),
                    }
                )

                continue

            # =================================================
            # EXACT LOOP DETECTION
            # =================================================

            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "tool": tool_name,
                        "args": args,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()

            count = (
                state.action_counts.get(
                    fingerprint,
                    0,
                )
                + 1
            )

            state.action_counts[fingerprint] = count

            if count > MAX_IDENTICAL_ACTIONS:

                warning = (
                    "This exact action has already " "been attempted multiple times."
                )

                print()

                print(f"🔁 {warning}")

                state.no_progress_steps += 1

                messages.append(
                    {
                        "role": "user",
                        "content": ("CONTROLLER:\n" f"{warning}"),
                    }
                )

                continue

            # =================================================
            # EXTERNALIZE LARGE PAYLOAD FOR HISTORY
            # =================================================

            (
                compact_action,
                payload_notes,
            ) = self.externalize_action_for_history(action)

            if payload_notes:

                print()

                print("📦 PAYLOAD EXTERNALIZATION")

                for note in payload_notes:

                    print(f"   {note}")

            # =================================================
            # EXECUTE ORIGINAL ACTION
            # =================================================

            print()

            print(f"🛠️ {tool_name}")

            if model_message:

                print(model_message)

            success, output = self.execute_tool(
                tools,
                tool_name,
                args,
            )

            observation_id = f"obs-{step:03d}"

            observation = Observation(
                id=observation_id,
                tool=tool_name,
                args=(
                    compact_action.get(
                        "args",
                        {},
                    )
                ),
                text=output,
                success=success,
            )

            state.observations.append(observation)

            self.record_tool_targets(
                tool_name,
                args,
                success,
            )

            progress = self.update_state(
                state,
                tool_name,
                args,
                success,
                output,
            )

            phase_hint = self.maybe_enter_implementation_phase(
                state,
                tool_name,
                args,
                success,
                output,
            )

            if progress:

                state.no_progress_steps = 0

            else:

                state.no_progress_steps += 1

            icon = "📦" if success else "💥"

            print(f"{icon} " f"{truncate_text(output, 1800)}")

            # =================================================
            # COMPACT ACTION GOES INTO MODEL HISTORY
            # =================================================

            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        compact_action,
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
            )

            if payload_notes:

                observation_message += (
                    "PAYLOADS:\n"
                    + "\n".join(f"- {note}" for note in payload_notes)
                    + "\n\n"
                    "The real text is stored in the payload "
                    "store. Reuse the shown *_ref instead of "
                    "regenerating identical content.\n\n"
                )

            observation_message += "CURRENT WORKING STATE:\n" + self.state_card(state)

            if phase_hint:

                observation_message += "\n\nCONTROLLER GUIDANCE:\n" + phase_hint

                print()

                print(f"💡 {phase_hint}")

            if not success:

                hint = self.controller_hint(
                    tool_name,
                    output,
                )

                if hint:

                    observation_message += "\n\nCONTROLLER HINT:\n" + hint

                    print()

                    print(f"💡 {hint}")

            messages.append(
                {
                    "role": "user",
                    "content": observation_message,
                }
            )

            # =================================================
            # REFRESH ACTIVE PROJECT PROFILE
            # =================================================

            new_active_root = self.derive_active_root()

            if new_active_root.resolve() != active_root.resolve():

                active_root = new_active_root

                profile = detect_project_profile(active_root)

                tools.command_root = active_root

                tools.profile = profile

            elif success and tool_name in {
                "create_file",
                "create_files",
                "replace_file",
                "apply_patch",
            }:

                profile = detect_project_profile(active_root)

                tools.profile = profile

            # =================================================
            # CIRCUIT BREAKER
            # =================================================

            if state.no_progress_steps >= max_no_progress:

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

        result = "🛑 Maximum agent step limit " f"({max_steps}) reached."

        self.append_session_message(
            "assistant",
            result,
        )

        return result


# ============================================================
# FACTORY / ENTRY POINT
# ============================================================


def create_agent(
    config: dict[str, Any],
) -> CodingAgent:

    return CodingAgent(config)


def main() -> None:

    config = load_config()

    main_menu(
        config=config,
        save_config=save_config,
        agent_factory=create_agent,
        default_config=DEFAULT_CONFIG,
    )


if __name__ == "__main__":

    main()
