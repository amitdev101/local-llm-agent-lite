from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lancedb
from llama_cpp import Llama


# ============================================================
# PATHS / DEFAULTS
# ============================================================

APP_DIR = Path.home() / ".myllm"
print(f"Using app directory: {APP_DIR}")
CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "model_path": "",
    "project_root": str(Path.cwd()),
    "context_size": 8192,
    "gpu_layers": 0,
    "max_steps": 30,
    "temperature": 0.1,
}


IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".local_agent",
    ".myllm",
}


TEXT_EXTENSIONS = {
    ".py",
    ".pyi",
    ".txt",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sql",
    ".sh",
    ".ps1",
}


MAX_FILE_BYTES = 1_000_000
MAX_TOOL_OUTPUT_CHARS = 7000
MAX_EDIT_CHARS = 20_000

MAX_IDENTICAL_ACTIONS = 2
CONTEXT_COMPACT_RATIO = 0.70
MAX_MODEL_OUTPUT_TOKENS = 900


# ============================================================
# CONFIG
# ============================================================

def load_config() -> dict[str, Any]:
    APP_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()

    try:
        loaded = json.loads(
            CONFIG_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        loaded = {}

    config = DEFAULT_CONFIG.copy()
    config.update(loaded)

    return config


def save_config(config: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)

    CONFIG_FILE.write_text(
        json.dumps(
            config,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
    timestamp: float = field(default_factory=time.time)


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

    edit_backups: list[tuple[Path, str]] = field(
        default_factory=list
    )

    last_test_passed: bool = False
    last_validation_passed: bool = False


# ============================================================
# GENERAL HELPERS
# ============================================================

def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause() -> None:
    input("\nPress Enter to continue...")


def truncate_text(
    text: str,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> str:
    if len(text) <= max_chars:
        return text

    half = max_chars // 2

    return (
        text[:half]
        + "\n\n... [OUTPUT TRUNCATED] ...\n\n"
        + text[-half:]
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def action_fingerprint(
    name: str,
    args: dict[str, Any],
) -> str:
    blob = json.dumps(
        {
            "name": name,
            "args": args,
        },
        sort_keys=True,
        ensure_ascii=False,
    )

    return hashlib.sha256(
        blob.encode("utf-8")
    ).hexdigest()


def read_text_file(path: Path) -> str:
    if not path.exists():
        raise ValueError(
            f"File does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Not a file: {path}"
        )

    size = path.stat().st_size

    if size > MAX_FILE_BYTES:
        raise ValueError(
            f"File too large: {size:,} bytes"
        )

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


# ============================================================
# WORKSPACE SAFETY
# ============================================================

class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, path: str) -> Path:
        raw = Path(path)

        if raw.is_absolute():
            candidate = raw.resolve()
        else:
            candidate = (
                self.root / raw
            ).resolve()

        try:
            candidate.relative_to(
                self.root
            )
        except ValueError:
            raise ValueError(
                "Access outside the project "
                "workspace is blocked."
            )

        return candidate

    def relative(self, path: Path) -> str:
        return str(
            path.resolve().relative_to(
                self.root
            )
        )


# ============================================================
# PROJECT DISCOVERY
# ============================================================

def detect_project(
    root: Path,
) -> dict[str, Any]:

    try:
        files = {
            p.name
            for p in root.iterdir()
            if p.is_file()
        }
    except Exception:
        files = set()

    project_types = []

    if "pyproject.toml" in files:
        project_types.append("Python")

    if "requirements.txt" in files:
        project_types.append("Python")

    if "package.json" in files:
        project_types.append("Node.js")

    if "Cargo.toml" in files:
        project_types.append("Rust")

    if "go.mod" in files:
        project_types.append("Go")

    return {
        "os": (
            f"{platform.system()} "
            f"{platform.release()}"
        ),
        "python": sys.version.split()[0],
        "working_directory": str(root),
        "project_types": (
            list(set(project_types))
            or ["unknown"]
        ),
        "git_available": command_exists("git"),
        "git_repository": (
            root / ".git"
        ).exists(),
        "pytest_available": module_available(
            "pytest"
        ),
    }


# ============================================================
# LANCEDB MEMORY
# ============================================================

class ProjectMemory:

    TABLE_NAME = "project_facts"

    def __init__(
        self,
        workspace: Workspace,
    ):
        memory_dir = (
            workspace.root
            / ".local_agent"
            / "memory"
        )

        memory_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = lancedb.connect(
            str(memory_dir)
        )

        self.project_id = hashlib.sha256(
            str(
                workspace.root
            ).encode("utf-8")
        ).hexdigest()[:16]

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
# TOOLS
# ============================================================

class Tools:

    def __init__(
        self,
        workspace: Workspace,
        memory: ProjectMemory,
        state: AgentState,
    ):
        self.workspace = workspace
        self.memory = memory
        self.state = state

    def inspect_project(self) -> str:
        info = detect_project(
            self.workspace.root
        )

        items = []

        for p in sorted(
            self.workspace.root.iterdir(),
            key=lambda x: (
                not x.is_dir(),
                x.name.lower(),
            ),
        )[:80]:

            prefix = (
                "DIR "
                if p.is_dir()
                else "FILE"
            )

            items.append(
                f"{prefix} {p.name}"
            )

        return json.dumps(
            {
                "environment": info,
                "top_level": items,
            },
            indent=2,
        )

    def list_files(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:

        base = self.workspace.resolve(path)

        depth = max(
            0,
            min(int(depth), 4),
        )

        results = []

        base_parts = len(base.parts)

        for current_root, dirs, files in os.walk(base):

            current = Path(current_root)

            current_depth = (
                len(current.parts)
                - base_parts
            )

            dirs[:] = [
                d
                for d in dirs
                if d not in IGNORE_DIRS
            ]

            if current_depth >= depth:
                dirs[:] = []

            for filename in sorted(files):

                p = current / filename

                try:
                    relative = (
                        self.workspace.relative(p)
                    )
                except Exception:
                    continue

                results.append(relative)

                if len(results) >= 300:
                    results.append(
                        "... result limit reached ..."
                    )

                    return "\n".join(
                        results
                    )

        return (
            "\n".join(results)
            or "(no files)"
        )

    def search_text(
        self,
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:

        if not query:
            raise ValueError(
                "query cannot be empty"
            )

        base = self.workspace.resolve(path)

        max_results = max(
            1,
            min(
                int(max_results),
                100,
            ),
        )

        matches = []

        for current_root, dirs, files in os.walk(base):

            dirs[:] = [
                d
                for d in dirs
                if d not in IGNORE_DIRS
            ]

            for filename in files:

                p = Path(
                    current_root
                ) / filename

                if (
                    p.suffix.lower()
                    not in TEXT_EXTENSIONS
                ):
                    continue

                try:
                    if (
                        p.stat().st_size
                        > MAX_FILE_BYTES
                    ):
                        continue

                    text = p.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )

                except Exception:
                    continue

                for line_number, line in enumerate(
                    text.splitlines(),
                    start=1,
                ):

                    if (
                        query.lower()
                        in line.lower()
                    ):
                        matches.append(
                            f"{self.workspace.relative(p)}:"
                            f"{line_number}: "
                            f"{line[:300]}"
                        )

                        if (
                            len(matches)
                            >= max_results
                        ):
                            return "\n".join(
                                matches
                            )

        return (
            "\n".join(matches)
            or "(no matches)"
        )

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:

        p = self.workspace.resolve(path)

        text = read_text_file(p)

        lines = text.splitlines()

        start = max(
            1,
            int(start_line),
        )

        end = max(
            start,
            int(end_line),
        )

        if end - start > 300:
            end = start + 300

        end = min(
            end,
            len(lines),
        )

        selected = []

        for index in range(
            start - 1,
            end,
        ):
            selected.append(
                f"{index + 1:5d} | "
                f"{lines[index]}"
            )

        return (
            f"FILE: "
            f"{self.workspace.relative(p)}\n"
            f"LINES: {start}-{end} "
            f"/ {len(lines)}\n\n"
            + "\n".join(selected)
        )

    def apply_patch(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> str:

        if not old_text:
            raise ValueError(
                "old_text cannot be empty"
            )

        if (
            len(old_text)
            + len(new_text)
            > MAX_EDIT_CHARS
        ):
            raise ValueError(
                "Edit is too large."
            )

        p = self.workspace.resolve(path)

        original = read_text_file(p)

        count = original.count(old_text)

        if count == 0:
            raise ValueError(
                "old_text not found exactly."
            )

        if count > 1:
            raise ValueError(
                "old_text occurs multiple times. "
                "Provide more surrounding text."
            )

        updated = original.replace(
            old_text,
            new_text,
            1,
        )

        if updated == original:
            raise ValueError(
                "Edit would make no change."
            )

        self.state.edit_backups.append(
            (
                p,
                original,
            )
        )

        p.write_text(
            updated,
            encoding="utf-8",
        )

        relative = (
            self.workspace.relative(p)
        )

        self.state.edited_files.add(
            relative
        )

        self.state.last_test_passed = False
        self.state.last_validation_passed = False

        return (
            f"Edited {relative} successfully."
        )

    def undo_last_edit(self) -> str:

        if not self.state.edit_backups:
            raise ValueError(
                "No edit to undo."
            )

        path, old_contents = (
            self.state.edit_backups.pop()
        )

        path.write_text(
            old_contents,
            encoding="utf-8",
        )

        return (
            f"Restored "
            f"{self.workspace.relative(path)}"
        )

    def git_status(self) -> str:

        if not command_exists("git"):
            raise ValueError(
                "Git is unavailable."
            )

        return self._run(
            [
                "git",
                "status",
                "--short",
            ],
            timeout=20,
        )

    def git_diff(self) -> str:

        if not command_exists("git"):
            raise ValueError(
                "Git is unavailable."
            )

        return self._run(
            [
                "git",
                "diff",
                "--",
                ".",
            ],
            timeout=20,
        )

    def run_tests(
        self,
        target: str = "",
    ) -> str:

        command = [
            sys.executable,
            "-m",
            "pytest",
        ]

        if target:

            if target.startswith("-"):
                raise ValueError(
                    "Invalid pytest target."
                )

            command.append(target)

        command.append("-q")

        result, code = (
            self._run_with_code(
                command,
                timeout=180,
            )
        )

        self.state.last_test_passed = (
            code == 0
        )

        return (
            f"EXIT_CODE: {code}\n\n"
            f"{result}"
        )

    def validate_python(self) -> str:

        result, code = (
            self._run_with_code(
                [
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    str(
                        self.workspace.root
                    ),
                ],
                timeout=120,
            )
        )

        self.state.last_validation_passed = (
            code == 0
        )

        return (
            f"EXIT_CODE: {code}\n\n"
            f"{result or 'Compilation succeeded.'}"
        )

    def remember_fact(
        self,
        fact: str,
        evidence_id: str,
    ) -> str:

        observation = next(
            (
                observation
                for observation
                in self.state.observations
                if observation.id
                == evidence_id
            ),
            None,
        )

        if observation is None:
            raise ValueError(
                "Unknown evidence ID."
            )

        if not observation.success:
            raise ValueError(
                "Evidence must come from "
                "a successful observation."
            )

        if len(fact) > 500:
            raise ValueError(
                "Fact is too long."
            )

        self.memory.add_fact(
            fact=fact,
            evidence=truncate_text(
                observation.text,
                1000,
            ),
            evidence_id=evidence_id,
        )

        return "Project fact remembered."

    def _run(
        self,
        command: list[str],
        timeout: int,
    ) -> str:

        output, _ = (
            self._run_with_code(
                command,
                timeout,
            )
        )

        return output

    def _run_with_code(
        self,
        command: list[str],
        timeout: int,
    ) -> tuple[str, int]:

        process = subprocess.run(
            command,
            cwd=self.workspace.root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            shell=False,
        )

        output = ""

        if process.stdout:
            output += process.stdout

        if process.stderr:
            if output:
                output += "\n"

            output += process.stderr

        return (
            truncate_text(output),
            process.returncode,
        )


# ============================================================
# MODEL PROTOCOL
# ============================================================

TOOL_DOCS = """
AVAILABLE TOOLS

inspect_project
{}

list_files
{
  "path": ".",
  "depth": 2
}

search_text
{
  "query": "text",
  "path": ".",
  "max_results": 50
}

read_file
{
  "path": "src/app.py",
  "start_line": 1,
  "end_line": 150
}

apply_patch
{
  "path": "src/app.py",
  "old_text": "exact existing code",
  "new_text": "replacement code"
}

undo_last_edit
{}

run_tests
{
  "target": ""
}

validate_python
{}

git_status
{}

git_diff
{}

remember_fact
{
  "fact": "verified fact",
  "evidence_id": "obs-005"
}
"""


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


SYSTEM_PROMPT = f"""
You are a conservative autonomous local coding agent.

The user gives you a coding task.

Work toward it by choosing ONE next action at a time.

{TOOL_DOCS}

RULES

- Inspect before editing.
- Search before reading large files.
- Read small relevant regions.
- Prefer minimal edits.
- Never invent tool results.
- Never access paths outside the workspace.
- Do not repeatedly issue the same action.
- Test changes when practical.
- Inspect Git diff after editing when Git is available.
- Only store facts supported by real observations.
- Do not expose private chain-of-thought.
- Your message field should contain only a short action description.
- Return "final" when the task is complete.

For tool use:

{{
  "type": "tool",
  "tool": "read_file",
  "args": {{
    "path": "app.py",
    "start_line": 1,
    "end_line": 100
  }},
  "message": "Inspecting the implementation."
}}

For completion:

{{
  "type": "final",
  "tool": "",
  "args": {{}},
  "message": "Task completed."
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
        )

        root = Path(
            config["project_root"]
        ).resolve()

        if not model_path.exists():
            raise ValueError(
                "Configured model file "
                "does not exist."
            )

        if not root.exists():
            raise ValueError(
                "Configured project root "
                "does not exist."
            )

        self.workspace = Workspace(root)

        self.memory = ProjectMemory(
            self.workspace
        )

        threads = max(
            1,
            (os.cpu_count() or 4) - 2,
        )

        print(
            "\n🧠 Loading model..."
        )

        self.llm = Llama(
            model_path=str(model_path),
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
            "✅ Model loaded.\n"
        )

    def token_count(
        self,
        messages: list[dict[str, str]],
    ) -> int:

        text = "\n".join(
            f"{message['role']}:\n"
            f"{message['content']}"
            for message in messages
        )

        return len(
            self.llm.tokenize(
                text.encode("utf-8"),
                add_bos=False,
            )
        )

    def call_model(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:

        response = (
            self.llm.create_chat_completion(
                messages=messages,
                response_format={
                    "type": "json_object",
                    "schema": ACTION_SCHEMA,
                },
                temperature=float(
                    self.config["temperature"]
                ),
                top_p=0.9,
                max_tokens=(
                    MAX_MODEL_OUTPUT_TOKENS
                ),
            )
        )

        content = (
            response["choices"][0]
            ["message"]["content"]
        )

        return json.loads(content)

    def memory_card(self) -> str:

        facts = self.memory.load_facts(
            limit=30
        )

        if not facts:
            return (
                "(no persistent project "
                "memory yet)"
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

    def execute_tool(
        self,
        tools: Tools,
        name: str,
        args: dict[str, Any],
    ) -> tuple[bool, str]:

        available = {
            "inspect_project":
                tools.inspect_project,

            "list_files":
                tools.list_files,

            "search_text":
                tools.search_text,

            "read_file":
                tools.read_file,

            "apply_patch":
                tools.apply_patch,

            "undo_last_edit":
                tools.undo_last_edit,

            "run_tests":
                tools.run_tests,

            "validate_python":
                tools.validate_python,

            "git_status":
                tools.git_status,

            "git_diff":
                tools.git_diff,

            "remember_fact":
                tools.remember_fact,
        }

        function = available.get(
            name
        )

        if function is None:
            return (
                False,
                f"Unknown tool: {name}",
            )

        try:
            result = function(**args)

            return (
                True,
                truncate_text(
                    str(result)
                ),
            )

        except subprocess.TimeoutExpired:
            return (
                False,
                "Tool timed out.",
            )

        except Exception as error:
            return (
                False,
                f"{type(error).__name__}: "
                f"{error}",
            )

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
            f"{message['role']}:\n"
            f"{message['content']}"
            for message in messages[1:]
        )

        prompt = f"""
Summarize the current coding task state.

Keep:
- original goal
- confirmed facts
- important files
- edits made
- current errors
- test results
- unresolved work

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
                            "Create a concise factual "
                            "working-memory summary."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.0,
                max_tokens=600,
            )
        )

        summary = (
            response["choices"][0]
            ["message"]["content"]
        )

        state.summary = summary

        return [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"ORIGINAL TASK:\n"
                    f"{state.task}\n\n"
                    f"PROJECT MEMORY:\n"
                    f"{memory_card}\n\n"
                    f"CURRENT STATE:\n"
                    f"{summary}"
                ),
            },
        ]

    def run(
        self,
        task: str,
    ) -> str:

        state = AgentState(
            task=task
        )

        tools = Tools(
            self.workspace,
            self.memory,
            state,
        )

        memory_card = (
            self.memory_card()
        )

        environment = (
            detect_project(
                self.workspace.root
            )
        )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"TASK:\n{task}\n\n"
                    f"ENVIRONMENT:\n"
                    f"{json.dumps(environment, indent=2)}"
                    f"\n\nPROJECT MEMORY:\n"
                    f"{memory_card}"
                ),
            },
        ]

        max_steps = int(
            self.config["max_steps"]
        )

        for step in range(
            1,
            max_steps + 1,
        ):

            state.step = step

            used = self.token_count(
                messages
            )

            threshold = int(
                self.n_ctx
                * CONTEXT_COMPACT_RATIO
            )

            if used >= threshold:

                messages = self.compact(
                    state,
                    messages,
                    memory_card,
                )

                used = self.token_count(
                    messages
                )

            print(
                f"\n🧠 Step {step}/{max_steps}"
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
                    f"Model error: "
                    f"{error}"
                )

            action_type = (
                action.get("type")
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

            message = (
                action.get(
                    "message",
                    "",
                )
            )

            if action_type == "final":

                if (
                    state.edited_files
                    and not (
                        state.last_test_passed
                        or state.last_validation_passed
                    )
                ):
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "CONTROLLER: "
                                "Code was edited but "
                                "has not been successfully "
                                "verified yet."
                            ),
                        }
                    )

                    continue

                return message

            if action_type != "tool":

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "CONTROLLER: "
                            "Choose either "
                            "tool or final."
                        ),
                    }
                )

                continue

            fingerprint = (
                action_fingerprint(
                    tool_name,
                    args,
                )
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

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "CONTROLLER: "
                            "You have repeated "
                            "this exact action. "
                            "Choose another approach."
                        ),
                    }
                )

                continue

            print(
                f"🛠️ {tool_name}"
            )

            if message:
                print(
                    f"   {message}"
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

            state.observations.append(
                Observation(
                    id=observation_id,
                    tool=tool_name,
                    args=args,
                    text=output,
                    success=success,
                )
            )

            icon = (
                "📦"
                if success
                else "💥"
            )

            print(
                f"{icon} "
                f"{truncate_text(output, 700)}"
            )

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
                        f"TOOL OBSERVATION "
                        f"{observation_id}\n"
                        f"success={success}\n\n"
                        f"{output}"
                    ),
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
            "agent steps reached."
        )


# ============================================================
# MODEL SELECTION
# ============================================================

def find_gguf_files(
    start: Path,
) -> list[Path]:

    found = []

    try:
        for path in start.rglob(
            "*.gguf"
        ):

            if (
                ".git"
                in path.parts
            ):
                continue

            found.append(
                path.resolve()
            )

            if len(found) >= 100:
                break

    except Exception:
        pass

    return found


def model_selection_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print("🤖 MODEL SELECTION")
    print("=" * 60)

    search_locations = [
        Path.cwd(),
        Path.cwd() / "models",
        Path.home() / "models",
    ]

    models = []
    seen = set()

    for location in search_locations:

        if not location.exists():
            continue

        for model in find_gguf_files(
            location
        ):

            key = str(model).lower()

            if key in seen:
                continue

            seen.add(key)
            models.append(model)

    if models:

        print(
            "\nDetected GGUF models:\n"
        )

        for index, model in enumerate(
            models,
            start=1,
        ):

            marker = ""

            if (
                str(model)
                == config["model_path"]
            ):
                marker = "  ✅ current"

            try:
                size_gb = (
                    model.stat().st_size
                    / 1024**3
                )

                size_text = (
                    f"{size_gb:.2f} GB"
                )

            except Exception:
                size_text = "?"

            print(
                f"{index}. "
                f"{model.name}"
                f" [{size_text}]"
                f"{marker}"
            )

        print(
            f"{len(models) + 1}. "
            "Enter model path manually"
        )

        print("0. Back")

        choice = input(
            "\nSelect model: "
        ).strip()

        if choice == "0":
            return

        try:
            selected = int(choice)

            if (
                1
                <= selected
                <= len(models)
            ):
                config["model_path"] = str(
                    models[
                        selected - 1
                    ]
                )

                save_config(config)

                print(
                    "\n✅ Model selected."
                )

                pause()
                return

            if (
                selected
                == len(models) + 1
            ):
                pass

            else:
                return

        except ValueError:
            pass

    model_path = input(
        "\nEnter full GGUF path: "
    ).strip().strip('"')

    path = Path(
        model_path
    ).expanduser()

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
            "\n❌ Selected file "
            "is not a GGUF file."
        )
        pause()
        return

    config["model_path"] = str(
        path.resolve()
    )

    save_config(config)

    print(
        "\n✅ Model selected."
    )

    pause()


# ============================================================
# PROJECT SELECTION
# ============================================================

def project_selection_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print("📁 PROJECT SELECTION")
    print("=" * 60)

    print(
        "\nCurrent project:\n"
        f"{config['project_root']}"
    )

    print(
        "\nEnter a folder path "
        "or press Enter to cancel."
    )

    entered = input(
        "\nProject folder: "
    ).strip().strip('"')

    if not entered:
        return

    path = Path(
        entered
    ).expanduser().resolve()

    if not path.exists():
        print(
            "\n❌ Folder does not exist."
        )
        pause()
        return

    if not path.is_dir():
        print(
            "\n❌ That is not a folder."
        )
        pause()
        return

    config["project_root"] = str(
        path
    )

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

        print("⚙️ SETTINGS")
        print("=" * 60)

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
            "\n5. Reset defaults"
        )

        print(
            "0. Back"
        )

        choice = input(
            "\nSelect option: "
        ).strip()

        if choice == "0":
            return

        elif choice == "1":

            print(
                "\nCommon values:"
            )
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
                config["context_size"] = (
                    mapping[selected]
                )

            elif selected == "5":

                try:
                    config["context_size"] = int(
                        input(
                            "Context size: "
                        )
                    )
                except ValueError:
                    pass

        elif choice == "2":

            print(
                "\nGPU layer options:"
            )

            print(
                "0 = CPU only"
            )

            print(
                "-1 = attempt full "
                "GPU offload"
            )

            try:
                config["gpu_layers"] = int(
                    input(
                        "\nGPU layers: "
                    )
                )
            except ValueError:
                pass

        elif choice == "3":

            try:
                config["max_steps"] = int(
                    input(
                        "\nMax steps: "
                    )
                )
            except ValueError:
                pass

        elif choice == "4":

            try:
                config["temperature"] = float(
                    input(
                        "\nTemperature: "
                    )
                )
            except ValueError:
                pass

        elif choice == "5":

            current_model = (
                config["model_path"]
            )

            current_project = (
                config["project_root"]
            )

            config.clear()
            config.update(
                DEFAULT_CONFIG.copy()
            )

            config["model_path"] = (
                current_model
            )

            config["project_root"] = (
                current_project
            )

        save_config(config)


# ============================================================
# CHAT
# ============================================================

def chat_menu(
    config: dict[str, Any],
) -> None:

    if not config["model_path"]:

        print(
            "\n⚠️ No model selected."
        )

        print(
            "Opening model selection..."
        )

        pause()

        model_selection_menu(
            config
        )

        if not config["model_path"]:
            return

    model = Path(
        config["model_path"]
    )

    if not model.exists():

        print(
            "\n❌ Configured model "
            "could not be found."
        )

        pause()

        model_selection_menu(
            config
        )

        return

    project = Path(
        config["project_root"]
    )

    if not project.exists():

        print(
            "\n❌ Project folder "
            "could not be found."
        )

        pause()

        project_selection_menu(
            config
        )

        return

    clear_screen()

    print("🤖 LOCAL CODING AGENT")
    print("=" * 60)

    print(
        f"\nModel   : "
        f"{model.name}"
    )

    print(
        f"Project : "
        f"{project}"
    )

    print(
        f"Context : "
        f"{config['context_size']}"
    )

    print(
        "\nLoading..."
    )

    try:
        agent = CodingAgent(
            config
        )

    except Exception as error:

        print(
            f"\n❌ Could not "
            f"start model:\n{error}"
        )

        pause()
        return

    while True:

        print()
        print("-" * 60)

        task = input(
            "\n👤 Task > "
        ).strip()

        if not task:
            continue

        if task.lower() in {
            "/exit",
            "/quit",
            "/back",
            "exit",
        }:
            return

        if task.lower() == "/help":

            print(
                "\nCommands:"
            )

            print(
                "/back  - return to menu"
            )

            print(
                "/exit  - return to menu"
            )

            print(
                "/help  - show commands"
            )

            continue

        print(
            "\n🚀 Starting agent..."
        )

        result = agent.run(
            task
        )

        print()
        print("=" * 60)
        print("🤖 RESULT")
        print("=" * 60)
        print(result)


# ============================================================
# MAIN MENU
# ============================================================

def show_status(
    config: dict[str, Any],
) -> None:

    model_path = (
        config["model_path"]
    )

    if model_path:
        model_name = Path(
            model_path
        ).name
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


def main_menu() -> None:

    config = load_config()

    while True:

        clear_screen()

        print(
            "╔══════════════════════════════════╗"
        )

        print(
            "║        🤖 MY LOCAL LLM          ║"
        )

        print(
            "╚══════════════════════════════════╝"
        )

        show_status(config)

        print()
        print("1. 💬 Chat / Coding Agent")
        print("2. ⚙️  Settings")
        print("3. 🤖 Model Selection")
        print("4. 📁 Project Selection")
        print("5. 🔎 System Information")
        print("0. 🚪 Exit")

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

            clear_screen()

            print(
                "🔎 SYSTEM INFORMATION"
            )

            print(
                "=" * 60
            )

            info = detect_project(
                Path(
                    config[
                        "project_root"
                    ]
                )
            )

            print(
                json.dumps(
                    info,
                    indent=2,
                )
            )

            print(
                "\nConfiguration file:"
            )

            print(
                CONFIG_FILE
            )

            pause()

        elif choice == "0":

            print(
                "\n👋 Goodbye."
            )

            break


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main_menu()