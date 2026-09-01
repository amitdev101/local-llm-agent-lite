from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lancedb
from llama_cpp import Llama

from myllm_tools import (
    Workspace,
    Tools,
    TOOL_DOCS,
    build_tool_registry,
    truncate_text,
)


# ============================================================
# APPLICATION PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

APP_DIR = SCRIPT_DIR / ".myllm"

CONFIG_FILE = APP_DIR / "config.json"

MEMORY_ROOT = APP_DIR / "memory"


# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "model_path": "",
    "project_root": str(SCRIPT_DIR),
    "context_size": 8192,
    "gpu_layers": 0,
    "max_steps": 30,
    "temperature": 0.1,

    # 0 = minimal
    # 1 = raw streamed LLM output
    # 2 = level 1 + parsed actions + tool args/results
    # 3 = level 2 + entire prompt/messages
    "debug_level": 1,
}


MAX_IDENTICAL_ACTIONS = 2

CONTEXT_COMPACT_RATIO = 0.70

MAX_MODEL_OUTPUT_TOKENS = 1000


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

        save_config(config)

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
    timestamp: float = field(
        default_factory=time.time
    )


@dataclass
class AgentState:
    task: str

    step: int = 0

    summary: str = ""

    observations: list[Observation] = field(
        default_factory=list
    )

    action_counts: dict[str, int] = field(
        default_factory=dict
    )

    edited_files: set[str] = field(
        default_factory=set
    )

    # Path, original content, existed_before
    edit_backups: list[
        tuple[Path, str, bool]
    ] = field(
        default_factory=list
    )

    last_test_passed: bool = False

    last_validation_passed: bool = False


# ============================================================
# UI HELPERS
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
# PROJECT DISCOVERY
# ============================================================

def detect_project(
    root: Path,
) -> dict[str, Any]:
    try:
        names = {
            item.name
            for item in root.iterdir()
        }

    except Exception:
        names = set()

    project_types = []

    if (
        "pyproject.toml" in names
        or "requirements.txt" in names
        or "setup.py" in names
    ):
        project_types.append(
            "Python"
        )

    if "package.json" in names:
        project_types.append(
            "Node.js"
        )

    if "Cargo.toml" in names:
        project_types.append(
            "Rust"
        )

    if "go.mod" in names:
        project_types.append(
            "Go"
        )

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

        "project_types": (
            project_types
            or ["unknown"]
        ),

        "git_repository": (
            root / ".git"
        ).exists(),
    }


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

    def _open(self):
        try:
            return self.db.open_table(
                self.TABLE_NAME
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

        table = self._open()

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
        table = self._open()

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
# ACTION SCHEMA
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
You are a local assistant connected to REAL project tools through
the surrounding Python application.

You can have normal conversations and you can autonomously inspect
and modify files inside the selected workspace.

IMPORTANT CAPABILITY RULE:

When an appropriate tool exists, DO NOT tell the user that you cannot
access files, create files, inspect the project, edit code, or run tests.

The surrounding application executes your tool calls for you.

For example:

User:
Create fruits.txt containing exactly 20 fruit names.

Correct behavior:
1. Call create_file.
2. Verify the result, for example with verify_line_count or read_file.
3. Return final only after verification.

Incorrect behavior:
"I cannot create files."

TOOL SELECTION RULES:

CREATE new file
-> create_file

READ file
-> read_file

MODIFY existing exact text
-> apply_patch

DELETE file
-> delete_file

FIND filename
-> find_file

SEARCH contents
-> search_text

RUN one pytest case
-> run_test_case

RUN one test file
-> run_test_file

RUN all tests
-> run_all_tests

VERIFY exact line count
-> verify_line_count

NEVER use apply_patch to create a new file.
NEVER use create_file to overwrite an existing file.

If the user's request is casual conversation or can be answered directly,
return type="final" without using tools.

When project work is required:
- choose exactly ONE tool action at a time;
- inspect only what is necessary;
- prefer narrow reads;
- make small changes;
- observe tool results;
- adapt after failures;
- verify mutations before claiming success;
- do not reveal private chain-of-thought.

AVAILABLE TOOLS:

{TOOL_DOCS}

Return tool actions as:

{{
  "type": "tool",
  "tool": "create_file",
  "args": {{
    "path": "fruits.txt",
    "content": "Apple\\nBanana"
  }},
  "message": "Creating the requested file."
}}

Return direct/final answers as:

{{
  "type": "final",
  "tool": "",
  "args": {{}},
  "message": "Your answer here."
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

        model_path = Path(
            config["model_path"]
        ).resolve()

        project_root = Path(
            config["project_root"]
        ).resolve()

        if not model_path.exists():
            raise ValueError(
                "Configured model does not exist."
            )

        if not project_root.exists():
            raise ValueError(
                "Configured workspace does not exist."
            )

        self.workspace = Workspace(
            project_root
        )

        self.memory = ProjectMemory(
            self.workspace
        )

        threads = max(
            1,
            (os.cpu_count() or 4) - 2,
        )

        print()
        print("🧠 Loading model...")

        self.llm = Llama(
            model_path=str(
                model_path
            ),

            n_ctx=int(
                config["context_size"]
            ),

            n_gpu_layers=int(
                config["gpu_layers"]
            ),

            n_threads=threads,

            n_threads_batch=threads,

            use_mmap=True,

            verbose=False,
        )

        self.n_ctx = int(
            config["context_size"]
        )

        print(
            "✅ Model loaded."
        )

    # ========================================================
    # DEBUG
    # ========================================================

    def debug_level(self) -> int:
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
        print("─" * 70)
        print(f"🔎 {title}")
        print("─" * 70)

        if isinstance(value, str):
            print(value)

        else:
            try:
                print(
                    json.dumps(
                        value,
                        indent=2,
                        ensure_ascii=False,
                    )
                )

            except Exception:
                print(value)

        print("─" * 70)

    def debug_messages(
        self,
        messages: list[dict[str, str]],
    ) -> None:
        if self.debug_level() < 3:
            return

        print()
        print("=" * 70)
        print("📨 FULL MODEL INPUT")
        print("=" * 70)

        for index, message in enumerate(
            messages,
            start=1,
        ):
            print()
            print(
                f"[{index}] "
                f"{message['role'].upper()}"
            )

            print("-" * 70)

            print(
                message["content"]
            )

        print("=" * 70)

    # ========================================================
    # TOKEN ESTIMATE
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
    # STREAMED MODEL CALL
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
            parsed = json.loads(
                full_content
            )

        except json.JSONDecodeError as error:
            self.debug_print(
                "JSON PARSE ERROR",
                {
                    "error": str(error),
                    "raw": full_content,
                },
                minimum_level=1,
            )

            return {
                "type": "invalid",
                "tool": "",
                "args": {},
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

    def memory_card(self) -> str:
        facts = (
            self.memory.load_facts(
                limit=30
            )
        )

        if not facts:
            return (
                "(no verified project memory yet)"
            )

        lines = []
        seen = set()

        for row in reversed(facts):
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

            seen.add(fact)

            lines.append(
                f"- {fact}"
            )

        return "\n".join(
            lines[:20]
        )

    # ========================================================
    # FAILURE HINT
    # ========================================================

    def tool_failure_hint(
        self,
        tool_name: str,
        output: str,
    ) -> str:
        text = output.lower()

        if (
            tool_name == "apply_patch"
            and (
                "file does not exist"
                in text
            )
        ):
            return (
                "CONTROLLER HINT: "
                "apply_patch only modifies "
                "an existing file. "
                "Use create_file when creating "
                "a new file."
            )

        if (
            tool_name == "apply_patch"
            and (
                "old_text cannot be empty"
                in text
            )
        ):
            return (
                "CONTROLLER HINT: "
                "apply_patch requires existing "
                "non-empty old_text. "
                "Use create_file for a new file."
            )

        if (
            tool_name == "create_file"
            and "already exists" in text
        ):
            return (
                "CONTROLLER HINT: "
                "The file already exists. "
                "Read it first, then use "
                "apply_patch if modification "
                "is required."
            )

        return ""

    # ========================================================
    # EXECUTE TOOL
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

        function = registry.get(
            name
        )

        if function is None:
            return (
                False,
                f"Unknown tool: {name}",
            )

        self.debug_print(
            "TOOL CALL",
            {
                "tool": name,
                "args": args,
            },
            minimum_level=2,
        )

        try:
            result = function(
                **args
            )

            output = truncate_text(
                str(result)
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
    # COMPACTION
    # ========================================================

    def compact(
        self,
        state: AgentState,
        messages: list[dict[str, str]],
        memory_card: str,
    ) -> list[dict[str, str]]:
        print(
            "🧹 Compacting context..."
        )

        transcript = "\n\n".join(
            f"{message['role'].upper()}:\n"
            f"{message['content']}"

            for message
            in messages[1:]
        )

        compact_prompt = f"""
Summarize the current agent state.

Preserve:
- original task
- verified discoveries
- relevant files
- edits already made
- current errors
- test results
- tool failures that matter
- unresolved work
- constraints

Do not invent anything.

TASK:
{state.task}

MEMORY:
{memory_card}

HISTORY:
{truncate_text(transcript, 20000)}
"""

        response = (
            self.llm.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Produce a concise factual "
                            "working-state summary."
                        ),
                    },

                    {
                        "role": "user",
                        "content":
                            compact_prompt,
                    },
                ],

                temperature=0.0,

                max_tokens=700,
            )
        )

        summary = (
            response["choices"][0]
            ["message"]["content"]
        )

        state.summary = summary

        self.debug_print(
            "COMPACTED STATE",
            summary,
            minimum_level=2,
        )

        return [
            {
                "role": "system",
                "content":
                    SYSTEM_PROMPT,
            },

            {
                "role": "user",
                "content": (
                    f"ORIGINAL TASK:\n"
                    f"{state.task}\n\n"

                    f"PROJECT MEMORY:\n"
                    f"{memory_card}\n\n"

                    f"CURRENT WORKING STATE:\n"
                    f"{summary}"
                ),
            },
        ]

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

        # This is deliberately permissive enough for
        # non-Python file creation tasks.
        #
        # Verification tools do not currently set one global
        # verified flag, so we also inspect whether a successful
        # verification observation exists.

        verification_tools = {
            "verify_file_exists",
            "verify_file_content",
            "verify_line_count",
            "run_test_case",
            "run_test_file",
            "run_all_tests",
            "validate_python",
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
                "A file was modified or created, "
                "but the result has not yet been "
                "verified. Use an appropriate "
                "verification tool before finishing."
            ),
        )

    # ========================================================
    # AGENT LOOP
    # ========================================================

    def run(
        self,
        task: str,
    ) -> str:
        state = AgentState(
            task=task
        )

        tools = Tools(
            workspace=self.workspace,
            memory=self.memory,
            state=state,
        )

        memory_card = (
            self.memory_card()
        )

        environment = detect_project(
            self.workspace.root
        )

        messages = [
            {
                "role": "system",
                "content":
                    SYSTEM_PROMPT,
            },

            {
                "role": "user",
                "content": (
                    f"USER REQUEST:\n"
                    f"{task}\n\n"

                    f"ENVIRONMENT:\n"
                    f"{json.dumps(environment, indent=2)}\n\n"

                    f"VERIFIED PROJECT MEMORY:\n"
                    f"{memory_card}\n\n"

                    "If this request can be answered "
                    "without tools, answer directly. "
                    "If the request asks you to perform "
                    "a project/file action, use the "
                    "appropriate real tool."
                ),
            },
        ]

        max_steps = int(
            self.config[
                "max_steps"
            ]
        )

        for step in range(
            1,
            max_steps + 1,
        ):
            state.step = step

            used = (
                self.token_count(
                    messages
                )
            )

            threshold = int(
                self.n_ctx
                * CONTEXT_COMPACT_RATIO
            )

            if used >= threshold:
                messages = (
                    self.compact(
                        state,
                        messages,
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

            if action_type == "final":
                allowed, reason = (
                    self.completion_allowed(
                        state
                    )
                )

                if allowed:
                    return model_message

                print(
                    f"🚫 Completion rejected: "
                    f"{reason}"
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

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "CONTROLLER:\n"
                            f"{reason}"
                        ),
                    }
                )

                continue

            # =================================================
            # INVALID
            # =================================================

            if action_type != "tool":
                messages.append(
                    {
                        "role": "user",
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
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "CONTROLLER ERROR:\n"
                            "args must be a JSON object."
                        ),
                    }
                )

                continue

            # =================================================
            # LOOP DETECTION
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
                    ).encode("utf-8")
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
                    "The same exact tool call "
                    "has already been attempted "
                    "multiple times. Choose a "
                    "different approach."
                )

                print(
                    f"🔁 {warning}"
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

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "CONTROLLER:\n"
                            f"{warning}"
                        ),
                    }
                )

                continue

            # =================================================
            # TOOL
            # =================================================

            print()
            print(
                f"🛠️ {tool_name}"
            )

            if model_message:
                print(
                    model_message
                )

            success, output = (
                self.execute_tool(
                    tools,
                    tool_name,
                    args,
                )
            )

            observation_id = (
                f"obs-{step:03d}"
            )

            observation = Observation(
                id=observation_id,
                tool=tool_name,
                args=args,
                text=output,
                success=success,
            )

            state.observations.append(
                observation
            )

            if len(
                state.observations
            ) > 100:
                state.observations = (
                    state.observations[-100:]
                )

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
                f"{output}"
            )

            if not success:
                hint = (
                    self.tool_failure_hint(
                        tool_name,
                        output,
                    )
                )

                if hint:
                    observation_message += (
                        f"\n\n{hint}"
                    )

                    print()
                    print(
                        f"💡 {hint}"
                    )

            messages.append(
                {
                    "role": "user",
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
        for path in start.rglob(
            "*.gguf"
        ):
            if ".git" in path.parts:
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

    for location in search_locations:
        for model in find_gguf_files(
            location
        ):
            key = str(
                model
            ).lower()

            if key in seen:
                continue

            seen.add(key)

            models.append(
                model
            )

    if models:
        print()
        print(
            "Detected GGUF models:\n"
        )

        for index, model in enumerate(
            models,
            start=1,
        ):
            marker = ""

            try:
                current = Path(
                    config[
                        "model_path"
                    ]
                ).resolve()

                if current == model:
                    marker = (
                        "  ✅ current"
                    )

            except Exception:
                pass

            try:
                size_gb = (
                    model.stat().st_size
                    / 1024**3
                )

                size = (
                    f"{size_gb:.2f} GB"
                )

            except Exception:
                size = "?"

            print(
                f"{index}. "
                f"{model.name} "
                f"[{size}]"
                f"{marker}"
            )

        manual_index = (
            len(models)
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

        choice = input(
            "\nSelect model: "
        ).strip()

        if choice == "0":
            return

        try:
            number = int(choice)

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
    ).strip().strip('"')

    if not entered:
        return

    path = (
        Path(entered)
        .expanduser()
        .resolve()
    )

    if not path.exists():
        print(
            "\n❌ File does not exist."
        )

        pause()

        return

    if (
        path.suffix.lower()
        != ".gguf"
    ):
        print(
            "\n❌ File must be .gguf"
        )

        pause()

        return

    config[
        "model_path"
    ] = str(path)

    save_config(config)

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
        "Current:"
    )

    print(
        config[
            "project_root"
        ]
    )

    entered = input(
        "\nNew project folder "
        "(Enter to cancel): "
    ).strip().strip('"')

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
    ] = str(path)

    save_config(config)

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
            f"      : "
            f"{config['context_size']}"
        )

        print(
            f"2. GPU layers"
            f"        : "
            f"{config['gpu_layers']}"
        )

        print(
            f"3. Max agent steps"
            f"   : "
            f"{config['max_steps']}"
        )

        print(
            f"4. Temperature"
            f"       : "
            f"{config['temperature']}"
        )

        print(
            f"5. Debug level"
            f"       : "
            f"{config['debug_level']}"
        )

        print(
            "6. Reset defaults"
        )

        print(
            "0. Back"
        )

        choice = input(
            "\nSelect option: "
        ).strip()

        if choice == "0":
            save_config(config)
            return

        if choice == "1":
            print()
            print("1. 4096")
            print("2. 8192")
            print("3. 16384")
            print("4. 32768")
            print("5. Custom")

            selected = input(
                "\nSelect: "
            ).strip()

            mapping = {
                "1": 4096,
                "2": 8192,
                "3": 16384,
                "4": 32768,
            }

            if selected in mapping:
                config[
                    "context_size"
                ] = (
                    mapping[selected]
                )

            elif selected == "5":
                try:
                    value = int(
                        input(
                            "Context: "
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
                value = float(
                    input(
                        "Temperature: "
                    )
                )

                if 0 <= value <= 2:
                    config[
                        "temperature"
                    ] = value

            except ValueError:
                pass

        elif choice == "5":
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
                "3. Everything including prompts"
            )

            selected = input(
                "\nDebug level: "
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

        elif choice == "6":
            model_path = (
                config.get(
                    "model_path",
                    "",
                )
            )

            project_root = (
                config.get(
                    "project_root",
                    str(SCRIPT_DIR),
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

        save_config(config)


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
            "\n⚠️ Select a model first."
        )

        pause()

        model_selection_menu(
            config
        )

        if not config.get(
            "model_path"
        ):
            return

    model_path = Path(
        config["model_path"]
    )

    project_path = Path(
        config["project_root"]
    )

    if not model_path.exists():
        print(
            "\n❌ Configured model "
            "does not exist."
        )

        pause()
        return

    if not project_path.exists():
        print(
            "\n❌ Configured project "
            "does not exist."
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
    print("Loading...")

    try:
        agent = CodingAgent(
            config
        )

    except Exception as error:
        print(
            "\n❌ Model failed to load:"
        )

        print(error)

        pause()
        return

    print()
    print("Commands:")
    print(
        "/back   Return to main menu"
    )
    print(
        "/help   Show commands"
    )

    while True:
        print()
        print("-" * 70)

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

        result = agent.run(
            task
        )

        print()
        print("=" * 70)
        print("🤖 RESPONSE")
        print("=" * 70)
        print(result)


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
        print()
        print(
            json.dumps(
                detect_project(
                    project
                ),
                indent=2,
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

    pause()


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
            Path(model_path).name
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
    config = load_config()

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

        show_status(config)

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
            chat_menu(config)

        elif choice == "2":
            settings_menu(config)

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
            print("👋 Goodbye.")
            break


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main_menu()