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
# APPLICATION PATHS
# ============================================================

# Folder containing THIS Python script.
SCRIPT_DIR = Path(__file__).resolve().parent

# Everything belonging to this app is stored beside myllm.py.
APP_DIR = SCRIPT_DIR / ".myllm"

CONFIG_FILE = APP_DIR / "config.json"

MEMORY_ROOT = APP_DIR / "memory"


# ============================================================
# DEFAULT CONFIGURATION
# ============================================================

DEFAULT_CONFIG = {
    "model_path": "",
    "project_root": str(SCRIPT_DIR),
    "context_size": 8192,
    "gpu_layers": 0,
    "max_steps": 30,
    "temperature": 0.1,

    # 0 = no debug
    # 1 = raw model response
    # 2 = raw model response + tool details
    # 3 = full prompt + raw response + tools
    "debug_level": 1,
}


# ============================================================
# LIMITS
# ============================================================

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
# CONFIGURATION
# ============================================================

def ensure_app_folder() -> None:
    """
    Create .myllm beside this script if it does not exist.
    """

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

    ensure_app_folder()

    CONFIG_FILE.write_text(
        json.dumps(
            config,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_config() -> dict[str, Any]:
    """
    Load config.json.

    If it does not exist, automatically create it
    with default values.
    """

    ensure_app_folder()

    if not CONFIG_FILE.exists():

        config = DEFAULT_CONFIG.copy()

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

    # Merge existing config with defaults so newer config
    # properties appear automatically.
    config = DEFAULT_CONFIG.copy()

    config.update(loaded)

    # Persist merged config.
    save_config(config)

    return config


# ============================================================
# STATE TYPES
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

    edit_backups: list[
        tuple[Path, str]
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
# GENERAL HELPERS
# ============================================================

def truncate_text(
    text: str,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> str:

    if len(text) <= max_chars:
        return text

    half = max_chars // 2

    return (
        text[:half]
        + "\n\n"
        + "... [OUTPUT TRUNCATED] ..."
        + "\n\n"
        + text[-half:]
    )


def command_exists(
    name: str,
) -> bool:

    return shutil.which(
        name
    ) is not None


def module_available(
    name: str,
) -> bool:

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


def read_text_file(
    path: Path,
) -> str:

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
            f"File too large: "
            f"{size:,} bytes"
        )

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


# ============================================================
# WORKSPACE SAFETY
# ============================================================

class Workspace:

    def __init__(
        self,
        root: Path,
    ):

        self.root = root.resolve()

    def resolve(
        self,
        path: str,
    ) -> Path:

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
                "Access outside the selected "
                "project workspace is blocked."
            )

        return candidate

    def relative(
        self,
        path: Path,
    ) -> str:

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
            item.name
            for item in root.iterdir()
            if item.is_file()
        }

    except Exception:

        files = set()

    project_types = []

    if "pyproject.toml" in files:

        project_types.append(
            "Python"
        )

    if "requirements.txt" in files:

        project_types.append(
            "Python"
        )

    if "setup.py" in files:

        project_types.append(
            "Python"
        )

    if "package.json" in files:

        project_types.append(
            "Node.js"
        )

    if "Cargo.toml" in files:

        project_types.append(
            "Rust"
        )

    if "go.mod" in files:

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

        "working_directory": (
            str(root)
        ),

        "project_types": (
            sorted(
                set(project_types)
            )
            or ["unknown"]
        ),

        "git_available": (
            command_exists("git")
        ),

        "git_repository": (
            root / ".git"
        ).exists(),

        "pytest_available": (
            module_available("pytest")
        ),
    }


# ============================================================
# LANCEDB PROJECT MEMORY
# ============================================================

class ProjectMemory:

    TABLE_NAME = "project_facts"

    def __init__(
        self,
        workspace: Workspace,
    ):

        project_hash = hashlib.sha256(
            str(
                workspace.root
            ).encode("utf-8")
        ).hexdigest()[:16]

        memory_dir = (
            MEMORY_ROOT
            / project_hash
        )

        memory_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = lancedb.connect(
            str(memory_dir)
        )

        self.project_id = project_hash

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

            "project": (
                self.project_id
            ),

            "fact": (
                fact.strip()
            ),

            "evidence": (
                evidence.strip()
            ),

            "evidence_id": (
                evidence_id
            ),

            "created_at": (
                int(time.time())
            ),
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

    # --------------------------------------------------------
    # inspect_project
    # --------------------------------------------------------

    def inspect_project(
        self,
    ) -> str:

        info = detect_project(
            self.workspace.root
        )

        items = []

        for item in sorted(
            self.workspace.root.iterdir(),
            key=lambda x: (
                not x.is_dir(),
                x.name.lower(),
            ),
        )[:80]:

            prefix = (
                "DIR "
                if item.is_dir()
                else "FILE"
            )

            items.append(
                f"{prefix} {item.name}"
            )

        return json.dumps(
            {
                "environment": info,
                "top_level": items,
            },
            indent=2,
        )

    # --------------------------------------------------------
    # list_files
    # --------------------------------------------------------

    def list_files(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:

        base = self.workspace.resolve(
            path
        )

        if not base.exists():

            raise ValueError(
                "Path does not exist."
            )

        depth = max(
            0,
            min(
                int(depth),
                4,
            ),
        )

        results = []

        base_parts = len(
            base.parts
        )

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            current = Path(
                current_root
            )

            current_depth = (
                len(current.parts)
                - base_parts
            )

            dirs[:] = [
                directory
                for directory in dirs
                if directory
                not in IGNORE_DIRS
            ]

            if current_depth >= depth:

                dirs[:] = []

            for filename in sorted(
                files
            ):

                path_obj = (
                    current
                    / filename
                )

                try:

                    relative = (
                        self.workspace.relative(
                            path_obj
                        )
                    )

                except Exception:

                    continue

                results.append(
                    relative
                )

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

    # --------------------------------------------------------
    # search_text
    # --------------------------------------------------------

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

        base = (
            self.workspace.resolve(
                path
            )
        )

        max_results = max(
            1,
            min(
                int(max_results),
                100,
            ),
        )

        matches = []

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            dirs[:] = [
                directory
                for directory in dirs
                if directory
                not in IGNORE_DIRS
            ]

            for filename in files:

                path_obj = (
                    Path(current_root)
                    / filename
                )

                if (
                    path_obj.suffix.lower()
                    not in TEXT_EXTENSIONS
                ):

                    continue

                try:

                    if (
                        path_obj.stat().st_size
                        > MAX_FILE_BYTES
                    ):

                        continue

                    text = (
                        path_obj.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
                    )

                except Exception:

                    continue

                for (
                    line_number,
                    line,
                ) in enumerate(
                    text.splitlines(),
                    start=1,
                ):

                    if (
                        query.lower()
                        in line.lower()
                    ):

                        matches.append(
                            f"{self.workspace.relative(path_obj)}:"
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

    # --------------------------------------------------------
    # read_file
    # --------------------------------------------------------

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:

        path_obj = (
            self.workspace.resolve(
                path
            )
        )

        text = read_text_file(
            path_obj
        )

        lines = (
            text.splitlines()
        )

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
            f"{self.workspace.relative(path_obj)}\n"
            f"LINES: "
            f"{start}-{end} / {len(lines)}\n\n"
            + "\n".join(selected)
        )

    # --------------------------------------------------------
    # apply_patch
    # --------------------------------------------------------

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
                "Edit is too large. "
                "Make a smaller targeted edit."
            )

        path_obj = (
            self.workspace.resolve(
                path
            )
        )

        original = read_text_file(
            path_obj
        )

        count = original.count(
            old_text
        )

        if count == 0:

            raise ValueError(
                "old_text was not found exactly. "
                "Read the relevant file again."
            )

        if count > 1:

            raise ValueError(
                f"old_text occurs {count} times. "
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
                path_obj,
                original,
            )
        )

        path_obj.write_text(
            updated,
            encoding="utf-8",
        )

        relative = (
            self.workspace.relative(
                path_obj
            )
        )

        self.state.edited_files.add(
            relative
        )

        # Any code edit invalidates old verification.
        self.state.last_test_passed = False

        self.state.last_validation_passed = False

        return (
            f"Edited {relative} successfully."
        )

    # --------------------------------------------------------
    # undo_last_edit
    # --------------------------------------------------------

    def undo_last_edit(
        self,
    ) -> str:

        if not self.state.edit_backups:

            raise ValueError(
                "No edit to undo."
            )

        (
            path_obj,
            original,
        ) = self.state.edit_backups.pop()

        path_obj.write_text(
            original,
            encoding="utf-8",
        )

        self.state.last_test_passed = False

        self.state.last_validation_passed = False

        return (
            f"Restored "
            f"{self.workspace.relative(path_obj)}"
        )

    # --------------------------------------------------------
    # git_status
    # --------------------------------------------------------

    def git_status(
        self,
    ) -> str:

        if not command_exists(
            "git"
        ):

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

    # --------------------------------------------------------
    # git_diff
    # --------------------------------------------------------

    def git_diff(
        self,
    ) -> str:

        if not command_exists(
            "git"
        ):

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

    # --------------------------------------------------------
    # run_tests
    # --------------------------------------------------------

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

            command.append(
                target
            )

        command.append(
            "-q"
        )

        (
            result,
            code,
        ) = self._run_with_code(
            command,
            timeout=180,
        )

        self.state.last_test_passed = (
            code == 0
        )

        return (
            f"EXIT_CODE: {code}\n\n"
            f"{result}"
        )

    # --------------------------------------------------------
    # validate_python
    # --------------------------------------------------------

    def validate_python(
        self,
    ) -> str:

        (
            result,
            code,
        ) = self._run_with_code(
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

        self.state.last_validation_passed = (
            code == 0
        )

        return (
            f"EXIT_CODE: {code}\n\n"
            f"{result or 'Python compilation succeeded.'}"
        )

    # --------------------------------------------------------
    # remember_fact
    # --------------------------------------------------------

    def remember_fact(
        self,
        fact: str,
        evidence_id: str,
    ) -> str:

        observation = next(
            (
                obs
                for obs
                in self.state.observations
                if obs.id == evidence_id
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
                "a successful tool observation."
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

        return (
            "Verified project fact saved."
        )

    # --------------------------------------------------------
    # RUN PROCESS
    # --------------------------------------------------------

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

            output += (
                process.stdout
            )

        if process.stderr:

            if output:

                output += "\n"

            output += (
                process.stderr
            )

        return (
            truncate_text(output),
            process.returncode,
        )


# ============================================================
# MODEL TOOL DOCUMENTATION
# ============================================================

TOOL_DOCS = """
AVAILABLE TOOLS

1. inspect_project
{}

Discover project type, OS, Python, Git and top-level files.


2. list_files
{
  "path": ".",
  "depth": 2
}


3. search_text
{
  "query": "authentication",
  "path": ".",
  "max_results": 50
}


4. read_file
{
  "path": "src/app.py",
  "start_line": 1,
  "end_line": 120
}


5. apply_patch
{
  "path": "src/app.py",
  "old_text": "exact existing text",
  "new_text": "replacement text"
}


6. undo_last_edit
{}


7. run_tests
{
  "target": ""
}


8. validate_python
{}


9. git_status
{}


10. git_diff
{}


11. remember_fact
{
  "fact": "stable verified project fact",
  "evidence_id": "obs-005"
}
"""


# ============================================================
# STRUCTURED MODEL OUTPUT
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
You are a local coding assistant.

The user may either:

1. Have a normal conversation.
2. Ask a general programming question.
3. Ask a project-specific question.
4. Ask you to inspect, debug, edit, test, or modify the project.

IMPORTANT:

Do NOT use tools merely because tools are available.

If the user's message is casual conversation, a greeting,
general knowledge, or something that can be answered directly,
return type="final" immediately.

Example:

User:
Hello how are you?

Correct response:

{{
  "type": "final",
  "tool": "",
  "args": {{}},
  "message": "I'm doing well. How can I help you?"
}}

Only use project tools when project-specific evidence or an actual
filesystem/code operation is needed.

When tools ARE needed, choose exactly ONE next action at a time.

{TOOL_DOCS}

TOOL RULES

- Inspect before editing.
- Search before reading large amounts of source code.
- Read small relevant file sections.
- Make the smallest reasonable edit.
- Never invent tool output.
- Never assume a tool succeeded.
- Tool errors are observations.
- Do not repeatedly request the same action.
- Prefer targeted tests before broad tests.
- Inspect Git diff after edits when Git is available.
- Stable facts may be persisted only when supported by evidence.
- Never claim an edit works until it has verification.
- Do not reveal private chain-of-thought.

For tool use return:

{{
  "type": "tool",
  "tool": "read_file",
  "args": {{
    "path": "src/example.py",
    "start_line": 1,
    "end_line": 100
  }},
  "message": "Inspecting the relevant code."
}}

For a direct answer or finished task return:

{{
  "type": "final",
  "tool": "",
  "args": {{}},
  "message": "Your answer here."
}}
"""


# ============================================================
# CODING AGENT
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

        project_root = Path(
            config["project_root"]
        ).resolve()

        if not model_path.exists():

            raise ValueError(
                "Configured model file "
                "does not exist."
            )

        if not project_root.exists():

            raise ValueError(
                "Configured project root "
                "does not exist."
            )

        self.workspace = Workspace(
            project_root
        )

        self.memory = ProjectMemory(
            self.workspace
        )

        cpu_count = (
            os.cpu_count()
            or 4
        )

        threads = max(
            1,
            cpu_count - 2,
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

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------

    def debug_level(
        self,
    ) -> int:

        return int(
            self.config.get(
                "debug_level",
                0,
            )
        )

    def debug_print(
        self,
        title: str,
        content: Any,
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
            content,
            str,
        ):

            print(content)

        else:

            try:

                print(
                    json.dumps(
                        content,
                        indent=2,
                        ensure_ascii=False,
                    )
                )

            except Exception:

                print(content)

        print(
            "─" * 70
        )

    def debug_messages(
        self,
        messages: list[dict[str, str]],
    ) -> None:

        if self.debug_level() < 3:

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
                message["content"]
            )

        print()
        print(
            "=" * 70
        )

    # --------------------------------------------------------
    # TOKEN COUNT
    # --------------------------------------------------------

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

            tokens = (
                self.llm.tokenize(
                    text.encode(
                        "utf-8"
                    ),
                    add_bos=False,
                )
            )

            return len(tokens)

        except Exception:

            # Rough fallback.
            return max(
                1,
                len(text) // 4,
            )

    # --------------------------------------------------------
    # MODEL CALL
    # --------------------------------------------------------

    def call_model(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:

        self.debug_messages(
            messages
        )

        response = (
            self.llm.create_chat_completion(

                messages=messages,

                response_format={
                    "type": (
                        "json_object"
                    ),

                    "schema": (
                        ACTION_SCHEMA
                    ),
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
            )
        )

        # Debug level 3 can inspect the entire llama.cpp response.
        self.debug_print(
            "FULL LLAMA.CPP RESPONSE OBJECT",
            response,
            minimum_level=3,
        )

        content = (
            response["choices"][0]
            ["message"]["content"]
        )

        # Level 1 and above sees exactly what the model produced.
        self.debug_print(
            "RAW LLM RESPONSE",
            content,
            minimum_level=1,
        )

        try:

            parsed = json.loads(
                content
            )

        except json.JSONDecodeError as error:

            self.debug_print(
                "JSON PARSE ERROR",
                str(error),
                minimum_level=1,
            )

            return {
                "type": "invalid",
                "tool": "",
                "args": {},
                "message": content,
            }

        self.debug_print(
            "PARSED MODEL ACTION",
            parsed,
            minimum_level=2,
        )

        return parsed

    # --------------------------------------------------------
    # MEMORY CARD
    # --------------------------------------------------------

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
                "(no persistent "
                "project memory yet)"
            )

        seen = set()

        lines = []

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

        return "\n".join(
            lines[:20]
        )

    # --------------------------------------------------------
    # TOOL EXECUTION
    # --------------------------------------------------------

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

            result = truncate_text(
                str(result)
            )

            self.debug_print(
                "FULL TOOL RESULT",
                result,
                minimum_level=2,
            )

            return (
                True,
                result,
            )

        except subprocess.TimeoutExpired:

            return (
                False,
                "Tool timed out.",
            )

        except TypeError as error:

            return (
                False,
                "Invalid tool arguments: "
                f"{error}",
            )

        except Exception as error:

            return (
                False,
                f"{type(error).__name__}: "
                f"{error}",
            )

    # --------------------------------------------------------
    # CONTEXT COMPACTION
    # --------------------------------------------------------

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

        prompt = f"""
Summarize the current coding-agent state.

Keep only facts required to continue correctly:

- original task
- confirmed discoveries
- relevant files/functions
- edits already made
- current errors
- test results
- unresolved work
- important constraints

Do not invent information.

TASK:
{state.task}

PERSISTENT MEMORY:
{memory_card}

HISTORY:
{truncate_text(transcript, 20000)}
"""

        summary_messages = [

            {
                "role": "system",
                "content": (
                    "Create a concise factual "
                    "working-state summary."
                ),
            },

            {
                "role": "user",
                "content": prompt,
            },
        ]

        if self.debug_level() >= 3:

            self.debug_messages(
                summary_messages
            )

        response = (
            self.llm.create_chat_completion(

                messages=summary_messages,

                temperature=0.0,

                max_tokens=600,
            )
        )

        summary = (
            response["choices"][0]
            ["message"]["content"]
        )

        self.debug_print(
            "COMPACTION RESPONSE",
            summary,
            minimum_level=2,
        )

        state.summary = (
            summary
        )

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

                    f"PERSISTENT PROJECT MEMORY:\n"
                    f"{memory_card}\n\n"

                    f"COMPACTED WORKING STATE:\n"
                    f"{summary}"
                ),
            },
        ]

    # --------------------------------------------------------
    # COMPLETION CHECK
    # --------------------------------------------------------

    def completion_allowed(
        self,
        state: AgentState,
    ) -> tuple[bool, str]:

        if not state.edited_files:

            return (
                True,
                "",
            )

        if state.last_test_passed:

            return (
                True,
                "",
            )

        if state.last_validation_passed:

            return (
                True,
                "",
            )

        return (
            False,
            (
                "Code was modified but no successful "
                "verification has occurred yet. "
                "Run tests or validate_python."
            ),
        )

    # --------------------------------------------------------
    # AGENT RUN
    # --------------------------------------------------------

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
                    f"USER REQUEST:\n"
                    f"{task}\n\n"

                    f"ENVIRONMENT:\n"
                    f"{json.dumps(environment, indent=2)}\n\n"

                    f"PERSISTENT VERIFIED PROJECT MEMORY:\n"
                    f"{memory_card}\n\n"

                    "If this can be answered directly, "
                    "do not use tools."
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

            state.step = (
                step
            )

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
                    "Model invocation failed:\n"
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

                    return (
                        model_message
                    )

                print(
                    f"🚫 Completion rejected: "
                    f"{reason}"
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            json.dumps(
                                action,
                                ensure_ascii=False,
                            )
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
                            "Your response was invalid. "
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
                            "Tool args must be "
                            "a JSON object."
                        ),
                    }
                )

                continue

            # =================================================
            # LOOP DETECTION
            # =================================================

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

                warning = (
                    "You have already requested "
                    "this exact action multiple times. "
                    "Choose another approach."
                )

                print(
                    f"🔁 {warning}"
                )

                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            json.dumps(
                                action,
                                ensure_ascii=False,
                            )
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
            # EXECUTE TOOL
            # =================================================

            print(
                f"🛠️ {tool_name}"
            )

            if model_message:

                print(
                    f"   {model_message}"
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

            icon = (
                "📦"
                if success
                else "💥"
            )

            print(
                f"{icon} "
                f"{truncate_text(output, 1000)}"
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        json.dumps(
                            action,
                            ensure_ascii=False,
                        )
                    ),
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"TOOL OBSERVATION "
                        f"{observation_id}\n"
                        f"success={success}\n"
                        f"tool={tool_name}\n\n"
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
            f"{max_steps} agent steps reached."
        )


# ============================================================
# MODEL DISCOVERY
# ============================================================

def find_gguf_files(
    start: Path,
    max_results: int = 100,
) -> list[Path]:

    found = []

    if not start.exists():

        return found

    try:

        for path in (
            start.rglob(
                "*.gguf"
            )
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
# MODEL SELECTION MENU
# ============================================================

def model_selection_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print_header(
        "🤖 MODEL SELECTION"
    )

    search_locations = [

        SCRIPT_DIR,

        SCRIPT_DIR / "models",

        Path.cwd(),

        Path.cwd() / "models",
    ]

    models = []

    seen = set()

    for location in (
        search_locations
    ):

        if not location.exists():

            continue

        for model in (
            find_gguf_files(
                location
            )
        ):

            key = str(
                model
            ).lower()

            if key in seen:

                continue

            seen.add(
                key
            )

            models.append(
                model
            )

    if models:

        print(
            "\nDetected GGUF models:\n"
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
                    model.resolve()
                    == configured
                ):

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
                f"{model.name}"
                f" [{size}]"
                f"{marker}"
            )

        manual_number = (
            len(models)
            + 1
        )

        print(
            f"\n{manual_number}. "
            f"Enter model path manually"
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

                selected_model = (
                    models[
                        number - 1
                    ]
                )

                config[
                    "model_path"
                ] = str(
                    selected_model
                )

                save_config(
                    config
                )

                print(
                    "\n✅ Model selected:"
                )

                print(
                    selected_model
                )

                pause()

                return

            if (
                number
                != manual_number
            ):

                return

        except ValueError:

            pass

    print()

    entered = input(
        "Enter full GGUF model path: "
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
            "\n❌ Selected file "
            "is not a .gguf model."
        )

        pause()

        return

    config[
        "model_path"
    ] = str(path)

    save_config(
        config
    )

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

    print_header(
        "📁 PROJECT SELECTION"
    )

    print(
        "\nCurrent project:"
    )

    print(
        config[
            "project_root"
        ]
    )

    print()
    print(
        "Enter another project folder."
    )

    print(
        "Press Enter to cancel."
    )

    entered = input(
        "\nProject folder: "
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
            "\n❌ Folder does not exist."
        )

        pause()

        return

    if not path.is_dir():

        print(
            "\n❌ Selected path "
            "is not a directory."
        )

        pause()

        return

    config[
        "project_root"
    ] = str(path)

    save_config(
        config
    )

    print(
        "\n✅ Project selected:"
    )

    print(path)

    pause()


# ============================================================
# DEBUG SETTINGS
# ============================================================

def debug_settings_menu(
    config: dict[str, Any],
) -> None:

    while True:

        clear_screen()

        print_header(
            "🔎 DEBUG MODE"
        )

        current = int(
            config.get(
                "debug_level",
                1,
            )
        )

        print(
            f"\nCurrent debug level: "
            f"{current}\n"
        )

        print(
            "0. Off"
        )

        print(
            "1. Raw LLM responses"
        )

        print(
            "2. Raw LLM responses "
            "+ tool calls/results"
        )

        print(
            "3. Full prompts + raw responses "
            "+ tools + llama response object"
        )

        print(
            "9. Back"
        )

        choice = input(
            "\nSelect debug level: "
        ).strip()

        if choice == "9":

            return

        if choice in {
            "0",
            "1",
            "2",
            "3",
        }:

            config[
                "debug_level"
            ] = int(
                choice
            )

            save_config(
                config
            )

            print(
                "\n✅ Debug level updated."
            )

            pause()

            return


# ============================================================
# SETTINGS MENU
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
            f"       : "
            f"{config['context_size']}"
        )

        print(
            f"2. GPU layers"
            f"         : "
            f"{config['gpu_layers']}"
        )

        print(
            f"3. Max agent steps"
            f"    : "
            f"{config['max_steps']}"
        )

        print(
            f"4. Temperature"
            f"        : "
            f"{config['temperature']}"
        )

        print(
            f"5. Debug level"
            f"        : "
            f"{config['debug_level']}"
        )

        print(
            "6. Reset settings to defaults"
        )

        print(
            "0. Back"
        )

        choice = input(
            "\nSelect option: "
        ).strip()

        # ----------------------------------------------------
        # BACK
        # ----------------------------------------------------

        if choice == "0":

            save_config(
                config
            )

            return

        # ----------------------------------------------------
        # CONTEXT
        # ----------------------------------------------------

        elif choice == "1":

            print()
            print(
                "1. 4096"
            )

            print(
                "2. 8192"
            )

            print(
                "3. 16384"
            )

            print(
                "4. 32768"
            )

            print(
                "5. Custom"
            )

            context_choice = input(
                "\nSelect: "
            ).strip()

            mapping = {
                "1": 4096,
                "2": 8192,
                "3": 16384,
                "4": 32768,
            }

            if (
                context_choice
                in mapping
            ):

                config[
                    "context_size"
                ] = (
                    mapping[
                        context_choice
                    ]
                )

            elif (
                context_choice
                == "5"
            ):

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

        # ----------------------------------------------------
        # GPU LAYERS
        # ----------------------------------------------------

        elif choice == "2":

            print()
            print(
                "0  = CPU only"
            )

            print(
                "-1 = attempt full "
                "GPU offload"
            )

            try:

                value = int(
                    input(
                        "\nGPU layers: "
                    )
                )

                config[
                    "gpu_layers"
                ] = value

            except ValueError:

                pass

        # ----------------------------------------------------
        # MAX STEPS
        # ----------------------------------------------------

        elif choice == "3":

            try:

                value = int(
                    input(
                        "\nMax agent steps: "
                    )
                )

                if value >= 1:

                    config[
                        "max_steps"
                    ] = value

            except ValueError:

                pass

        # ----------------------------------------------------
        # TEMPERATURE
        # ----------------------------------------------------

        elif choice == "4":

            try:

                value = float(
                    input(
                        "\nTemperature: "
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

        # ----------------------------------------------------
        # DEBUG
        # ----------------------------------------------------

        elif choice == "5":

            debug_settings_menu(
                config
            )

        # ----------------------------------------------------
        # RESET
        # ----------------------------------------------------

        elif choice == "6":

            current_model = (
                config.get(
                    "model_path",
                    "",
                )
            )

            current_project = (
                config.get(
                    "project_root",
                    str(SCRIPT_DIR),
                )
            )

            config.clear()

            config.update(
                DEFAULT_CONFIG.copy()
            )

            # Keep selections.
            config[
                "model_path"
            ] = current_model

            config[
                "project_root"
            ] = current_project

            print(
                "\n✅ Settings reset."
            )

            pause()

        save_config(
            config
        )


# ============================================================
# CHAT MENU
# ============================================================

def chat_menu(
    config: dict[str, Any],
) -> None:

    # --------------------------------------------------------
    # MODEL CHECK
    # --------------------------------------------------------

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

    model = Path(
        config[
            "model_path"
        ]
    )

    if not model.exists():

        print(
            "\n❌ Configured model "
            "was not found."
        )

        pause()

        model_selection_menu(
            config
        )

        return

    # --------------------------------------------------------
    # PROJECT CHECK
    # --------------------------------------------------------

    project = Path(
        config[
            "project_root"
        ]
    )

    if not project.exists():

        print(
            "\n❌ Configured project "
            "folder was not found."
        )

        pause()

        project_selection_menu(
            config
        )

        return

    # --------------------------------------------------------
    # START AGENT
    # --------------------------------------------------------

    clear_screen()

    print_header(
        "🤖 LOCAL CODING AGENT"
    )

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
        f"Debug   : "
        f"{config['debug_level']}"
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
            "\n❌ Could not load model:"
        )

        print(error)

        pause()

        return

    print()
    print(
        "Commands:"
    )

    print(
        "  /back   Return to main menu"
    )

    print(
        "  /help   Show commands"
    )

    # --------------------------------------------------------
    # INTERACTIVE LOOP
    # --------------------------------------------------------

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
                "/back  - return to main menu"
            )

            print(
                "/help  - show commands"
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
# SYSTEM INFORMATION
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

        info = detect_project(
            project
        )

        print()
        print(
            json.dumps(
                info,
                indent=2,
            )
        )

    print()
    print(
        "Application directory:"
    )

    print(
        SCRIPT_DIR
    )

    print()
    print(
        ".myllm directory:"
    )

    print(
        APP_DIR
    )

    print()
    print(
        "Configuration:"
    )

    print(
        CONFIG_FILE
    )

    print()
    print(
        "Persistent memory:"
    )

    print(
        MEMORY_ROOT
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

    # This immediately creates:
    #
    # .myllm/
    #     config.json
    #     memory/
    #
    # if they don't already exist.

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