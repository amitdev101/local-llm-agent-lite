from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


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
MAX_TOOL_OUTPUT_CHARS = 7_000
MAX_EDIT_CHARS = 30_000


# ============================================================
# HELPERS
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
        + "\n\n... [OUTPUT TRUNCATED] ...\n\n"
        + text[-half:]
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


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
            f"File is too large: {size:,} bytes"
        )

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


# ============================================================
# WORKSPACE
# ============================================================

class Workspace:
    """
    Restricts tools to one selected project directory.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, relative_path: str) -> Path:
        raw = Path(relative_path)

        if raw.is_absolute():
            candidate = raw.resolve()
        else:
            candidate = (
                self.root / raw
            ).resolve()

        try:
            candidate.relative_to(self.root)

        except ValueError:
            raise ValueError(
                "Access outside the selected "
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
# TOOLS
# ============================================================

class Tools:
    """
    Narrow tools exposed to the model.

    memory:
        ProjectMemory-compatible object or None.

    state:
        AgentState-compatible object.
    """

    def __init__(
        self,
        workspace: Workspace,
        memory: Any = None,
        state: Any = None,
    ):
        self.workspace = workspace
        self.memory = memory
        self.state = state

    # ========================================================
    # PROJECT INSPECTION
    # ========================================================

    def inspect_project(self) -> str:
        root = self.workspace.root

        top_level = []

        for item in sorted(
            root.iterdir(),
            key=lambda p: (
                not p.is_dir(),
                p.name.lower(),
            ),
        )[:100]:
            prefix = (
                "DIR "
                if item.is_dir()
                else "FILE"
            )

            top_level.append(
                f"{prefix} {item.name}"
            )

        detected = []

        if (root / "pyproject.toml").exists():
            detected.append("Python")

        if (root / "requirements.txt").exists():
            detected.append("Python")

        if (root / "package.json").exists():
            detected.append("Node.js")

        if (root / "Cargo.toml").exists():
            detected.append("Rust")

        if (root / "go.mod").exists():
            detected.append("Go")

        return (
            f"Workspace: {root}\n"
            f"Project types: "
            f"{', '.join(sorted(set(detected))) or 'unknown'}\n"
            f"Git repository: "
            f"{(root / '.git').exists()}\n\n"
            "Top-level entries:\n"
            + "\n".join(top_level)
        )

    # ========================================================
    # FILE DISCOVERY
    # ========================================================

    def list_files(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:
        base = self.workspace.resolve(path)

        if not base.exists():
            raise ValueError(
                f"Path does not exist: {path}"
            )

        if not base.is_dir():
            raise ValueError(
                f"Path is not a directory: {path}"
            )

        depth = max(
            0,
            min(int(depth), 5),
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
                directory
                for directory in dirs
                if directory not in IGNORE_DIRS
            ]

            if current_depth >= depth:
                dirs[:] = []

            for filename in sorted(files):
                file_path = (
                    current / filename
                )

                try:
                    relative = (
                        self.workspace.relative(
                            file_path
                        )
                    )

                except ValueError:
                    continue

                results.append(relative)

                if len(results) >= 300:
                    results.append(
                        "... result limit reached ..."
                    )

                    return "\n".join(results)

        return (
            "\n".join(results)
            or "(no files)"
        )

    def list_directories(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:
        base = self.workspace.resolve(path)

        if not base.exists():
            raise ValueError(
                f"Path does not exist: {path}"
            )

        if not base.is_dir():
            raise ValueError(
                f"Path is not a directory: {path}"
            )

        depth = max(
            0,
            min(int(depth), 5),
        )

        results = []
        base_parts = len(base.parts)

        for current_root, dirs, _ in os.walk(base):
            current = Path(current_root)

            current_depth = (
                len(current.parts)
                - base_parts
            )

            dirs[:] = [
                directory
                for directory in dirs
                if directory not in IGNORE_DIRS
            ]

            if current != base:
                results.append(
                    self.workspace.relative(
                        current
                    )
                )

            if current_depth >= depth:
                dirs[:] = []

            if len(results) >= 200:
                results.append(
                    "... result limit reached ..."
                )
                break

        return (
            "\n".join(results)
            or "(no directories)"
        )

    def find_file(
        self,
        name: str,
        path: str = ".",
    ) -> str:
        if not name:
            raise ValueError(
                "name cannot be empty"
            )

        base = self.workspace.resolve(path)

        lowered = name.lower()
        results = []

        for current_root, dirs, files in os.walk(base):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in IGNORE_DIRS
            ]

            for filename in files:
                if lowered in filename.lower():
                    file_path = (
                        Path(current_root)
                        / filename
                    )

                    results.append(
                        self.workspace.relative(
                            file_path
                        )
                    )

                    if len(results) >= 100:
                        return "\n".join(results)

        return (
            "\n".join(results)
            or "(no matching files)"
        )

    # ========================================================
    # SEARCH / READ
    # ========================================================

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

        results = []

        for current_root, dirs, files in os.walk(base):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in IGNORE_DIRS
            ]

            for filename in files:
                file_path = (
                    Path(current_root)
                    / filename
                )

                if (
                    file_path.suffix.lower()
                    not in TEXT_EXTENSIONS
                ):
                    continue

                try:
                    if (
                        file_path.stat().st_size
                        > MAX_FILE_BYTES
                    ):
                        continue

                    text = (
                        file_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
                    )

                except Exception:
                    continue

                for number, line in enumerate(
                    text.splitlines(),
                    start=1,
                ):
                    if (
                        query.lower()
                        in line.lower()
                    ):
                        results.append(
                            f"{self.workspace.relative(file_path)}:"
                            f"{number}: {line[:300]}"
                        )

                        if (
                            len(results)
                            >= max_results
                        ):
                            return "\n".join(results)

        return (
            "\n".join(results)
            or "(no matches)"
        )

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:
        file_path = (
            self.workspace.resolve(path)
        )

        text = read_text_file(
            file_path
        )

        lines = text.splitlines()

        if not lines:
            return (
                f"FILE: "
                f"{self.workspace.relative(file_path)}\n"
                "(empty file)"
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
            f"{self.workspace.relative(file_path)}\n"
            f"LINES: {start}-{end} "
            f"/ {len(lines)}\n\n"
            + "\n".join(selected)
        )

    # ========================================================
    # CREATE / EDIT / DELETE
    # ========================================================

    def create_file(
        self,
        path: str,
        content: str,
    ) -> str:
        file_path = (
            self.workspace.resolve(path)
        )

        if file_path.exists():
            raise ValueError(
                "File already exists. "
                "Use apply_patch to modify it."
            )

        if len(content) > MAX_EDIT_CHARS:
            raise ValueError(
                "Content is too large."
            )

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_path.write_text(
            content,
            encoding="utf-8",
        )

        self._mark_edited(
            self.workspace.relative(
                file_path
            )
        )

        return (
            "Created new file: "
            f"{self.workspace.relative(file_path)}"
        )

    def apply_patch(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> str:
        if not old_text:
            raise ValueError(
                "old_text cannot be empty. "
                "Use create_file for a new file."
            )

        if (
            len(old_text)
            + len(new_text)
            > MAX_EDIT_CHARS
        ):
            raise ValueError(
                "Patch is too large."
            )

        file_path = (
            self.workspace.resolve(path)
        )

        original = read_text_file(
            file_path
        )

        count = original.count(
            old_text
        )

        if count == 0:
            raise ValueError(
                "old_text was not found exactly."
            )

        if count > 1:
            raise ValueError(
                f"old_text occurs {count} times. "
                "Provide more surrounding text "
                "to make the patch unique."
            )

        updated = original.replace(
            old_text,
            new_text,
            1,
        )

        if updated == original:
            raise ValueError(
                "Patch would make no change."
            )

        if self.state is not None:
            backups = getattr(
                self.state,
                "edit_backups",
                None,
            )

            if backups is not None:
                backups.append(
                    (
                        file_path,
                        original,
                        True,
                    )
                )

        file_path.write_text(
            updated,
            encoding="utf-8",
        )

        self._mark_edited(
            self.workspace.relative(
                file_path
            )
        )

        return (
            "Patched existing file: "
            f"{self.workspace.relative(file_path)}"
        )

    def delete_file(
        self,
        path: str,
    ) -> str:
        file_path = (
            self.workspace.resolve(path)
        )

        if not file_path.exists():
            raise ValueError(
                "File does not exist."
            )

        if not file_path.is_file():
            raise ValueError(
                "Path is not a file."
            )

        original = read_text_file(
            file_path
        )

        if self.state is not None:
            backups = getattr(
                self.state,
                "edit_backups",
                None,
            )

            if backups is not None:
                backups.append(
                    (
                        file_path,
                        original,
                        False,
                    )
                )

        file_path.unlink()

        self._mark_edited(
            self.workspace.relative(
                file_path
            )
        )

        return (
            "Deleted file: "
            f"{self.workspace.relative(file_path)}"
        )

    def create_directory(
        self,
        path: str,
    ) -> str:
        directory = (
            self.workspace.resolve(path)
        )

        if directory.exists():
            raise ValueError(
                "Directory already exists."
            )

        directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        return (
            "Created directory: "
            f"{self.workspace.relative(directory)}"
        )

    def delete_empty_directory(
        self,
        path: str,
    ) -> str:
        directory = (
            self.workspace.resolve(path)
        )

        if not directory.exists():
            raise ValueError(
                "Directory does not exist."
            )

        if not directory.is_dir():
            raise ValueError(
                "Path is not a directory."
            )

        if any(directory.iterdir()):
            raise ValueError(
                "Directory is not empty."
            )

        directory.rmdir()

        return (
            "Deleted empty directory: "
            f"{self.workspace.relative(directory)}"
        )

    def undo_last_edit(self) -> str:
        if self.state is None:
            raise ValueError(
                "No agent state is available."
            )

        backups = getattr(
            self.state,
            "edit_backups",
            None,
        )

        if not backups:
            raise ValueError(
                "No edit is available to undo."
            )

        backup = backups.pop()

        if len(backup) == 2:
            file_path, original = backup
            existed_before = True

        else:
            (
                file_path,
                original,
                existed_before,
            ) = backup

        if existed_before:
            file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            file_path.write_text(
                original,
                encoding="utf-8",
            )

            result = (
                "Restored previous contents of "
                f"{self.workspace.relative(file_path)}"
            )

        else:
            file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            file_path.write_text(
                original,
                encoding="utf-8",
            )

            result = (
                "Restored deleted file "
                f"{self.workspace.relative(file_path)}"
            )

        self._invalidate_verification()

        return result

    # ========================================================
    # SYMBOLS
    # ========================================================

    def find_symbol(
        self,
        symbol: str,
        path: str = ".",
    ) -> str:
        if not symbol:
            raise ValueError(
                "symbol cannot be empty"
            )

        base = self.workspace.resolve(path)
        results = []

        for current_root, dirs, files in os.walk(base):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in IGNORE_DIRS
            ]

            for filename in files:
                if not filename.endswith(".py"):
                    continue

                file_path = (
                    Path(current_root)
                    / filename
                )

                try:
                    source = (
                        file_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )
                    )

                    tree = ast.parse(source)

                except Exception:
                    continue

                for node in ast.walk(tree):
                    if isinstance(
                        node,
                        (
                            ast.FunctionDef,
                            ast.AsyncFunctionDef,
                            ast.ClassDef,
                        ),
                    ):
                        if node.name == symbol:
                            results.append(
                                f"{self.workspace.relative(file_path)}:"
                                f"{node.lineno}: "
                                f"{type(node).__name__} "
                                f"{node.name}"
                            )

        return (
            "\n".join(results)
            or "(symbol not found)"
        )

    def find_references(
        self,
        symbol: str,
        path: str = ".",
        max_results: int = 100,
    ) -> str:
        return self.search_text(
            query=symbol,
            path=path,
            max_results=max_results,
        )

    # ========================================================
    # TESTS
    # ========================================================

    def run_test_case(
        self,
        target: str,
    ) -> str:
        if not target:
            raise ValueError(
                "target cannot be empty"
            )

        if target.startswith("-"):
            raise ValueError(
                "Invalid pytest target."
            )

        return self._run_pytest(
            [target]
        )

    def run_test_file(
        self,
        path: str,
    ) -> str:
        test_file = (
            self.workspace.resolve(path)
        )

        if not test_file.exists():
            raise ValueError(
                "Test file does not exist."
            )

        return self._run_pytest(
            [
                self.workspace.relative(
                    test_file
                )
            ]
        )

    def run_all_tests(self) -> str:
        return self._run_pytest([])

    def _run_pytest(
        self,
        targets: list[str],
    ) -> str:
        command = [
            sys.executable,
            "-m",
            "pytest",
        ]

        command.extend(targets)
        command.append("-q")

        output, code = (
            self._run_with_code(
                command,
                timeout=180,
            )
        )

        if self.state is not None:
            self.state.last_test_passed = (
                code == 0
            )

        return (
            f"EXIT_CODE: {code}\n\n"
            f"{output}"
        )

    # ========================================================
    # VALIDATION
    # ========================================================

    def validate_python(self) -> str:
        output, code = (
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

        if self.state is not None:
            self.state.last_validation_passed = (
                code == 0
            )

        return (
            f"EXIT_CODE: {code}\n\n"
            f"{output or 'Python compilation succeeded.'}"
        )

    def verify_file_exists(
        self,
        path: str,
    ) -> str:
        file_path = (
            self.workspace.resolve(path)
        )

        if not file_path.exists():
            raise ValueError(
                f"File does not exist: {path}"
            )

        if not file_path.is_file():
            raise ValueError(
                f"Path is not a file: {path}"
            )

        return (
            "Verified file exists: "
            f"{self.workspace.relative(file_path)}"
        )

    def verify_file_content(
        self,
        path: str,
        expected_text: str,
    ) -> str:
        file_path = (
            self.workspace.resolve(path)
        )

        text = read_text_file(
            file_path
        )

        if expected_text not in text:
            raise ValueError(
                "Expected text was not found."
            )

        return (
            "Verified expected content exists in "
            f"{self.workspace.relative(file_path)}"
        )

    def verify_line_count(
        self,
        path: str,
        expected: int,
        ignore_empty: bool = True,
    ) -> str:
        file_path = (
            self.workspace.resolve(path)
        )

        text = read_text_file(
            file_path
        )

        lines = text.splitlines()

        if ignore_empty:
            lines = [
                line
                for line in lines
                if line.strip()
            ]

        actual = len(lines)

        if actual != int(expected):
            raise ValueError(
                f"Expected {expected} lines, "
                f"but found {actual}."
            )

        return (
            f"Verified "
            f"{self.workspace.relative(file_path)} "
            f"contains {actual} "
            f"{'non-empty ' if ignore_empty else ''}lines."
        )

    def count_matches(
        self,
        path: str,
        text: str,
    ) -> str:
        if not text:
            raise ValueError(
                "text cannot be empty"
            )

        file_path = (
            self.workspace.resolve(path)
        )

        content = read_text_file(
            file_path
        )

        count = content.count(text)

        return (
            f"{self.workspace.relative(file_path)} "
            f"contains {count} occurrence(s) "
            f"of {text!r}."
        )

    # ========================================================
    # GIT
    # ========================================================

    def git_status(self) -> str:
        if not command_exists("git"):
            raise ValueError(
                "Git is not installed."
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
                "Git is not installed."
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

    def git_log_recent(
        self,
        count: int = 10,
    ) -> str:
        if not command_exists("git"):
            raise ValueError(
                "Git is not installed."
            )

        count = max(
            1,
            min(
                int(count),
                30,
            ),
        )

        return self._run(
            [
                "git",
                "log",
                f"-{count}",
                "--oneline",
            ],
            timeout=20,
        )

    # ========================================================
    # MEMORY
    # ========================================================

    def remember_fact(
        self,
        fact: str,
        evidence_id: str,
    ) -> str:
        if self.memory is None:
            raise ValueError(
                "Persistent memory is unavailable."
            )

        if self.state is None:
            raise ValueError(
                "Agent state is unavailable."
            )

        if len(fact) > 500:
            raise ValueError(
                "Fact is too long."
            )

        observation = next(
            (
                item
                for item
                in self.state.observations
                if item.id == evidence_id
            ),
            None,
        )

        if observation is None:
            raise ValueError(
                "Unknown evidence_id."
            )

        if not observation.success:
            raise ValueError(
                "Evidence must come from "
                "a successful tool call."
            )

        self.memory.add_fact(
            fact=fact,
            evidence=truncate_text(
                observation.text,
                1_000,
            ),
            evidence_id=evidence_id,
        )

        return (
            "Verified project fact saved."
        )

    # ========================================================
    # INTERNAL
    # ========================================================

    def _mark_edited(
        self,
        relative_path: str,
    ) -> None:
        if self.state is None:
            return

        edited_files = getattr(
            self.state,
            "edited_files",
            None,
        )

        if edited_files is not None:
            edited_files.add(
                relative_path
            )

        self._invalidate_verification()

    def _invalidate_verification(self) -> None:
        if self.state is None:
            return

        if hasattr(
            self.state,
            "last_test_passed",
        ):
            self.state.last_test_passed = False

        if hasattr(
            self.state,
            "last_validation_passed",
        ):
            self.state.last_validation_passed = False

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
# TOOL REGISTRY
# ============================================================

def build_tool_registry(
    tools: Tools,
) -> dict[str, Callable[..., str]]:
    return {
        "inspect_project":
            tools.inspect_project,

        "list_files":
            tools.list_files,

        "list_directories":
            tools.list_directories,

        "find_file":
            tools.find_file,

        "search_text":
            tools.search_text,

        "read_file":
            tools.read_file,

        "create_file":
            tools.create_file,

        "apply_patch":
            tools.apply_patch,

        "delete_file":
            tools.delete_file,

        "create_directory":
            tools.create_directory,

        "delete_empty_directory":
            tools.delete_empty_directory,

        "undo_last_edit":
            tools.undo_last_edit,

        "find_symbol":
            tools.find_symbol,

        "find_references":
            tools.find_references,

        "run_test_case":
            tools.run_test_case,

        "run_test_file":
            tools.run_test_file,

        "run_all_tests":
            tools.run_all_tests,

        "validate_python":
            tools.validate_python,

        "verify_file_exists":
            tools.verify_file_exists,

        "verify_file_content":
            tools.verify_file_content,

        "verify_line_count":
            tools.verify_line_count,

        "count_matches":
            tools.count_matches,

        "git_status":
            tools.git_status,

        "git_diff":
            tools.git_diff,

        "git_log_recent":
            tools.git_log_recent,

        "remember_fact":
            tools.remember_fact,
    }


# ============================================================
# TOOL DOCUMENTATION FOR MODEL
# ============================================================

TOOL_DOCS = """
AVAILABLE TOOLS

inspect_project
{}
Purpose:
Inspect the workspace type and top-level structure.


list_files
{
  "path": ".",
  "depth": 2
}
Purpose:
List files.


list_directories
{
  "path": ".",
  "depth": 2
}
Purpose:
List directories only.


find_file
{
  "name": "auth.py",
  "path": "."
}
Purpose:
Find files by filename.


search_text
{
  "query": "authenticate",
  "path": ".",
  "max_results": 50
}
Purpose:
Search literal text in project files.


read_file
{
  "path": "src/app.py",
  "start_line": 1,
  "end_line": 150
}
Purpose:
Read part of an existing file.


create_file
{
  "path": "fruits.txt",
  "content": "Apple\\nBanana\\nOrange"
}
Purpose:
CREATE a NEW file.

IMPORTANT:
Use create_file when the target file does not exist.
Never use apply_patch to create a new file.


apply_patch
{
  "path": "src/app.py",
  "old_text": "exact existing text",
  "new_text": "replacement text"
}
Purpose:
Modify an EXISTING file.

IMPORTANT:
old_text must exist exactly once.
apply_patch cannot create a new file.


delete_file
{
  "path": "obsolete.txt"
}
Purpose:
Delete one existing file.


create_directory
{
  "path": "src/new_module"
}
Purpose:
Create a directory.


delete_empty_directory
{
  "path": "src/old_empty_dir"
}
Purpose:
Delete an empty directory only.


undo_last_edit
{}
Purpose:
Undo the agent's most recent reversible file edit.


find_symbol
{
  "symbol": "authenticate_user",
  "path": "."
}
Purpose:
Find Python function/class definitions.


find_references
{
  "symbol": "authenticate_user",
  "path": ".",
  "max_results": 100
}
Purpose:
Find references/usages.


run_test_case
{
  "target": "tests/test_auth.py::test_expired_token"
}
Purpose:
Run one pytest case.


run_test_file
{
  "path": "tests/test_auth.py"
}
Purpose:
Run one pytest file.


run_all_tests
{}
Purpose:
Run all pytest tests.


validate_python
{}
Purpose:
Compile Python files and check syntax.


verify_file_exists
{
  "path": "fruits.txt"
}
Purpose:
Confirm a file exists.


verify_file_content
{
  "path": "fruits.txt",
  "expected_text": "Apple"
}
Purpose:
Confirm expected content exists.


verify_line_count
{
  "path": "fruits.txt",
  "expected": 20,
  "ignore_empty": true
}
Purpose:
Verify an exact line count.


count_matches
{
  "path": "src/app.py",
  "text": "TODO"
}
Purpose:
Count literal text occurrences.


git_status
{}
Purpose:
Show changed/untracked files.


git_diff
{}
Purpose:
Show the current Git diff.


git_log_recent
{
  "count": 10
}
Purpose:
Show recent commits.


remember_fact
{
  "fact": "Tests use pytest.",
  "evidence_id": "obs-003"
}
Purpose:
Persist a stable verified project fact.
Only use evidence from a successful tool observation.
"""