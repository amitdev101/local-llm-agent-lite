from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
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
    ".next",
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
    ".scss",
    ".sass",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".xml",
    ".gradle",
    ".rs",
    ".go",
    ".sql",
    ".sh",
    ".ps1",
}


MAX_FILE_BYTES = 1_500_000
MAX_TOOL_OUTPUT_CHARS = 7000
MAX_EDIT_CHARS = 50000


# ============================================================
# PROJECT PROFILE
# ============================================================

@dataclass
class ProjectProfile:
    kind: str = "unknown"

    languages: list[str] = field(
        default_factory=list
    )

    frameworks: list[str] = field(
        default_factory=list
    )

    package_manager: str | None = None

    test_command: list[str] | None = None
    build_command: list[str] | None = None
    lint_command: list[str] | None = None
    typecheck_command: list[str] | None = None

    source_dirs: list[str] = field(
        default_factory=list
    )

    test_dirs: list[str] = field(
        default_factory=list
    )

    notes: list[str] = field(
        default_factory=list
    )


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


def command_exists(
    name: str,
) -> bool:

    return shutil.which(name) is not None


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
            f"File is too large: {size:,} bytes"
        )

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def _npm_runner(
    package_manager: str,
    script: str,
) -> list[str]:

    if package_manager == "npm":
        return [
            "npm",
            "run",
            script,
        ]

    if package_manager == "pnpm":
        return [
            "pnpm",
            "run",
            script,
        ]

    if package_manager == "yarn":
        return [
            "yarn",
            script,
        ]

    return [
        package_manager,
        "run",
        script,
    ]


# ============================================================
# PROJECT DETECTION
# ============================================================

def detect_project_profile(
    root: Path,
) -> ProjectProfile:

    root = root.resolve()

    profile = ProjectProfile()

    # --------------------------------------------------------
    # NODE / REACT / JS / TS
    # --------------------------------------------------------

    package_json = root / "package.json"

    if package_json.exists():

        profile.kind = "node"

        try:
            data = json.loads(
                package_json.read_text(
                    encoding="utf-8"
                )
            )

        except Exception:
            data = {}

        dependencies: dict[str, Any] = {}

        dependencies.update(
            data.get(
                "dependencies",
                {}
            )
            or {}
        )

        dependencies.update(
            data.get(
                "devDependencies",
                {}
            )
            or {}
        )

        scripts = (
            data.get(
                "scripts",
                {}
            )
            or {}
        )

        profile.languages.append(
            "JavaScript"
        )

        if (
            "typescript" in dependencies
            or (root / "tsconfig.json").exists()
        ):
            profile.languages.append(
                "TypeScript"
            )

        if "react" in dependencies:
            profile.frameworks.append(
                "React"
            )

        if "next" in dependencies:
            profile.frameworks.append(
                "Next.js"
            )

        if "vite" in dependencies:
            profile.frameworks.append(
                "Vite"
            )

        if "vue" in dependencies:
            profile.frameworks.append(
                "Vue"
            )

        if (
            root / "pnpm-lock.yaml"
        ).exists():

            profile.package_manager = "pnpm"

        elif (
            root / "yarn.lock"
        ).exists():

            profile.package_manager = "yarn"

        else:
            profile.package_manager = "npm"

        pm = profile.package_manager

        if "test" in scripts:
            profile.test_command = (
                _npm_runner(
                    pm,
                    "test",
                )
            )

        if "build" in scripts:
            profile.build_command = (
                _npm_runner(
                    pm,
                    "build",
                )
            )

        if "lint" in scripts:
            profile.lint_command = (
                _npm_runner(
                    pm,
                    "lint",
                )
            )

        if "typecheck" in scripts:
            profile.typecheck_command = (
                _npm_runner(
                    pm,
                    "typecheck",
                )
            )

        elif (
            "TypeScript"
            in profile.languages
        ):
            if command_exists("npx"):
                profile.typecheck_command = [
                    "npx",
                    "tsc",
                    "--noEmit",
                ]

        profile.source_dirs = [
            directory
            for directory
            in [
                "src",
                "app",
                "pages",
                "components",
            ]
            if (
                root / directory
            ).exists()
        ]

        profile.test_dirs = [
            directory
            for directory
            in [
                "tests",
                "test",
                "__tests__",
            ]
            if (
                root / directory
            ).exists()
        ]

        return profile

    # --------------------------------------------------------
    # JAVA / MAVEN
    # --------------------------------------------------------

    if (
        root / "pom.xml"
    ).exists():

        profile.kind = "java_maven"

        profile.languages = [
            "Java"
        ]

        profile.frameworks = []

        profile.package_manager = (
            "Maven"
        )

        if command_exists("mvn"):
            profile.test_command = [
                "mvn",
                "test",
            ]

            profile.build_command = [
                "mvn",
                "package",
                "-DskipTests",
            ]

        profile.source_dirs = [
            "src/main/java"
        ]

        profile.test_dirs = [
            "src/test/java"
        ]

        return profile

    # --------------------------------------------------------
    # JAVA / GRADLE
    # --------------------------------------------------------

    if (
        (root / "build.gradle").exists()
        or
        (
            root
            / "build.gradle.kts"
        ).exists()
    ):

        profile.kind = "java_gradle"

        profile.languages = [
            "Java"
        ]

        profile.package_manager = (
            "Gradle"
        )

        if os.name == "nt":
            wrapper_path = (
                root / "gradlew.bat"
            )

            wrapper = (
                str(wrapper_path)
                if wrapper_path.exists()
                else "gradle"
            )

        else:
            wrapper_path = (
                root / "gradlew"
            )

            wrapper = (
                str(wrapper_path)
                if wrapper_path.exists()
                else "gradle"
            )

        if (
            Path(wrapper).exists()
            or command_exists(wrapper)
            or "gradlew" in wrapper.lower()
        ):
            profile.test_command = [
                wrapper,
                "test",
            ]

            profile.build_command = [
                wrapper,
                "build",
            ]

        profile.source_dirs = [
            "src/main/java"
        ]

        profile.test_dirs = [
            "src/test/java"
        ]

        return profile

    # --------------------------------------------------------
    # RUST
    # --------------------------------------------------------

    if (
        root / "Cargo.toml"
    ).exists():

        profile.kind = "rust"

        profile.languages = [
            "Rust"
        ]

        profile.package_manager = (
            "Cargo"
        )

        if command_exists("cargo"):
            profile.test_command = [
                "cargo",
                "test",
            ]

            profile.build_command = [
                "cargo",
                "check",
            ]

        profile.source_dirs = [
            "src"
        ]

        profile.test_dirs = [
            "tests"
        ]

        return profile

    # --------------------------------------------------------
    # GO
    # --------------------------------------------------------

    if (
        root / "go.mod"
    ).exists():

        profile.kind = "go"

        profile.languages = [
            "Go"
        ]

        profile.package_manager = "Go"

        if command_exists("go"):
            profile.test_command = [
                "go",
                "test",
                "./...",
            ]

            profile.build_command = [
                "go",
                "build",
                "./...",
            ]

        return profile

    # --------------------------------------------------------
    # PYTHON
    # --------------------------------------------------------

    python_markers = [
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "setup.py",
        root / "Pipfile",
    ]

    if any(
        marker.exists()
        for marker in python_markers
    ):

        profile.kind = "python"

        profile.languages = [
            "Python"
        ]

        profile.package_manager = (
            "Python"
        )

        try:
            import pytest  # noqa: F401

            profile.test_command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
            ]

        except Exception:
            profile.notes.append(
                "pytest is not installed"
            )

        profile.source_dirs = [
            directory
            for directory
            in [
                "src",
                "app",
            ]
            if (
                root / directory
            ).exists()
        ]

        profile.test_dirs = [
            directory
            for directory
            in [
                "tests",
                "test",
            ]
            if (
                root / directory
            ).exists()
        ]

        return profile

    return profile


def profile_to_prompt(
    profile: ProjectProfile,
) -> str:

    def command_text(
        command: list[str] | None,
    ) -> str:

        if not command:
            return "unavailable"

        return " ".join(
            command
        )

    return (
        f"PROJECT KIND: {profile.kind}\n"
        f"LANGUAGES: "
        f"{', '.join(profile.languages) or 'unknown'}\n"
        f"FRAMEWORKS: "
        f"{', '.join(profile.frameworks) or 'none detected'}\n"
        f"PACKAGE MANAGER: "
        f"{profile.package_manager or 'unknown'}\n"
        f"TEST COMMAND: "
        f"{command_text(profile.test_command)}\n"
        f"BUILD COMMAND: "
        f"{command_text(profile.build_command)}\n"
        f"LINT COMMAND: "
        f"{command_text(profile.lint_command)}\n"
        f"TYPECHECK COMMAND: "
        f"{command_text(profile.typecheck_command)}\n"
        f"SOURCE DIRECTORIES: "
        f"{', '.join(profile.source_dirs) or 'unknown'}\n"
        f"TEST DIRECTORIES: "
        f"{', '.join(profile.test_dirs) or 'unknown'}\n"
        f"NOTES: "
        f"{'; '.join(profile.notes) or 'none'}"
    )


# ============================================================
# WORKSPACE
# ============================================================

class Workspace:

    def __init__(
        self,
        root: Path,
    ):

        self.root = (
            root.resolve()
        )

    def resolve(
        self,
        path: str,
    ) -> Path:

        raw = Path(path)

        if raw.is_absolute():
            candidate = (
                raw.resolve()
            )

        else:
            candidate = (
                self.root
                / raw
            ).resolve()

        try:
            candidate.relative_to(
                self.root
            )

        except ValueError:
            raise ValueError(
                "Access outside the selected "
                "workspace is blocked."
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
# TOOLS
# ============================================================

class Tools:

    def __init__(
        self,
        workspace: Workspace,
        memory: Any = None,
        state: Any = None,
        profile: ProjectProfile | None = None,
    ):

        self.workspace = workspace
        self.memory = memory
        self.state = state

        self.profile = (
            profile
            or detect_project_profile(
                workspace.root
            )
        )

    # ========================================================
    # PROJECT
    # ========================================================

    def inspect_project(
        self,
    ) -> str:

        items = []

        for item in sorted(
            self.workspace.root.iterdir(),
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

            items.append(
                f"{prefix} {item.name}"
            )

        return (
            profile_to_prompt(
                self.profile
            )
            + "\n\nTOP LEVEL:\n"
            + "\n".join(items)
        )

    # ========================================================
    # FILE DISCOVERY
    # ========================================================

    def list_files(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:

        base = (
            self.workspace.resolve(
                path
            )
        )

        if not base.exists():
            raise ValueError(
                "Path does not exist."
            )

        if not base.is_dir():
            raise ValueError(
                "Path is not a directory."
            )

        depth = max(
            0,
            min(
                int(depth),
                5,
            ),
        )

        results = []

        base_parts = (
            len(base.parts)
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
                for directory
                in dirs
                if directory
                not in IGNORE_DIRS
            ]

            if current_depth >= depth:
                dirs[:] = []

            for filename in sorted(
                files
            ):

                file_path = (
                    current
                    / filename
                )

                try:
                    relative = (
                        self.workspace.relative(
                            file_path
                        )
                    )

                except ValueError:
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

    def list_directories(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:

        base = (
            self.workspace.resolve(
                path
            )
        )

        depth = max(
            0,
            min(
                int(depth),
                5,
            ),
        )

        results = []

        base_parts = (
            len(base.parts)
        )

        for (
            current_root,
            dirs,
            _,
        ) in os.walk(base):

            current = (
                Path(current_root)
            )

            current_depth = (
                len(current.parts)
                - base_parts
            )

            dirs[:] = [
                directory
                for directory
                in dirs
                if directory
                not in IGNORE_DIRS
            ]

            if current != base:
                results.append(
                    self.workspace.relative(
                        current
                    )
                )

            if current_depth >= depth:
                dirs[:] = []

        return (
            "\n".join(results[:200])
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

        base = (
            self.workspace.resolve(
                path
            )
        )

        query = (
            name.lower()
        )

        results = []

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            dirs[:] = [
                directory
                for directory
                in dirs
                if directory
                not in IGNORE_DIRS
            ]

            for filename in files:

                if (
                    query
                    in filename.lower()
                ):

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
                        return "\n".join(
                            results
                        )

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
                for directory
                in dirs
                if directory
                not in IGNORE_DIRS
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

                for (
                    number,
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
                            f"{self.workspace.relative(file_path)}:"
                            f"{number}: {line[:300]}"
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

        file_path = (
            self.workspace.resolve(
                path
            )
        )

        text = (
            read_text_file(
                file_path
            )
        )

        lines = (
            text.splitlines()
        )

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

        if (
            end - start
            > 300
        ):
            end = (
                start + 300
            )

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
    # FILE MUTATION
    # ========================================================

    def create_file(
        self,
        path: str,
        content: str,
    ) -> str:

        file_path = (
            self.workspace.resolve(
                path
            )
        )

        if file_path.exists():
            raise ValueError(
                "File already exists. "
                "Use apply_patch to modify it."
            )

        if (
            len(content)
            > MAX_EDIT_CHARS
        ):
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
                        "",
                        False,
                    )
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
                "Use create_file for new files."
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
            self.workspace.resolve(
                path
            )
        )

        original = (
            read_text_file(
                file_path
            )
        )

        count = (
            original.count(
                old_text
            )
        )

        if count == 0:
            raise ValueError(
                "old_text was not found exactly."
            )

        if count > 1:
            raise ValueError(
                f"old_text occurs {count} times. "
                "Provide more surrounding text."
            )

        updated = (
            original.replace(
                old_text,
                new_text,
                1,
            )
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
            self.workspace.resolve(
                path
            )
        )

        original = (
            read_text_file(
                file_path
            )
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
            self.workspace.resolve(
                path
            )
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
            self.workspace.resolve(
                path
            )
        )

        if not directory.exists():
            raise ValueError(
                "Directory does not exist."
            )

        if not directory.is_dir():
            raise ValueError(
                "Path is not a directory."
            )

        if any(
            directory.iterdir()
        ):
            raise ValueError(
                "Directory is not empty."
            )

        directory.rmdir()

        return (
            "Deleted empty directory: "
            f"{self.workspace.relative(directory)}"
        )

    def undo_last_edit(
        self,
    ) -> str:

        if self.state is None:
            raise ValueError(
                "Agent state unavailable."
            )

        backups = getattr(
            self.state,
            "edit_backups",
            None,
        )

        if not backups:
            raise ValueError(
                "No edit available to undo."
            )

        (
            file_path,
            old_content,
            existed_before,
        ) = backups.pop()

        if existed_before:

            file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            file_path.write_text(
                old_content,
                encoding="utf-8",
            )

            result = (
                "Restored previous contents: "
                f"{self.workspace.relative(file_path)}"
            )

        else:

            if file_path.exists():
                file_path.unlink()

            result = (
                "Removed newly created file: "
                f"{self.workspace.relative(file_path)}"
            )

        self._invalidate_verification()

        return result

    # ========================================================
    # SYMBOL TOOLS
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

        base = (
            self.workspace.resolve(
                path
            )
        )

        results = []

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            dirs[:] = [
                directory
                for directory
                in dirs
                if directory
                not in IGNORE_DIRS
            ]

            for filename in files:

                if not filename.endswith(
                    ".py"
                ):
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

                    tree = (
                        ast.parse(
                            source
                        )
                    )

                except Exception:
                    continue

                for node in ast.walk(
                    tree
                ):

                    if isinstance(
                        node,
                        (
                            ast.FunctionDef,
                            ast.AsyncFunctionDef,
                            ast.ClassDef,
                        ),
                    ):

                        if (
                            node.name
                            == symbol
                        ):

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
    # GENERIC PROJECT VERIFICATION
    # ========================================================

    def run_project_tests(
        self,
    ) -> str:

        command = (
            self.profile.test_command
        )

        if not command:
            raise ValueError(
                "No project test command was detected."
            )

        return self._run_project_command(
            command,
            "tests",
        )

    def run_project_build(
        self,
    ) -> str:

        command = (
            self.profile.build_command
        )

        if not command:
            raise ValueError(
                "No project build command was detected."
            )

        return self._run_project_command(
            command,
            "build",
        )

    def run_project_lint(
        self,
    ) -> str:

        command = (
            self.profile.lint_command
        )

        if not command:
            raise ValueError(
                "No project lint command was detected."
            )

        return self._run_project_command(
            command,
            "lint",
        )

    def run_project_typecheck(
        self,
    ) -> str:

        command = (
            self.profile.typecheck_command
        )

        if not command:
            raise ValueError(
                "No project typecheck command was detected."
            )

        return self._run_project_command(
            command,
            "typecheck",
        )

    def validate_python(
        self,
    ) -> str:

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
                timeout=180,
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

    def check_python_import(
        self,
        module: str,
    ) -> str:

        if not module:
            raise ValueError(
                "module cannot be empty"
            )

        if not all(
            part.replace(
                "_",
                "",
            ).isalnum()
            for part
            in module.split(".")
        ):
            raise ValueError(
                "Invalid Python module name."
            )

        command = [
            sys.executable,
            "-c",
            (
                f"import {module}; "
                f"print('IMPORT_OK')"
            ),
        ]

        output, code = (
            self._run_with_code(
                command,
                timeout=30,
            )
        )

        if code != 0:
            raise ValueError(
                output
                or (
                    f"Could not import "
                    f"{module}"
                )
            )

        return (
            f"Successfully imported "
            f"{module}."
        )

    # ========================================================
    # VERIFICATION
    # ========================================================

    def verify_file_exists(
        self,
        path: str,
    ) -> str:

        file_path = (
            self.workspace.resolve(
                path
            )
        )

        if not file_path.exists():
            raise ValueError(
                "File does not exist."
            )

        if not file_path.is_file():
            raise ValueError(
                "Path is not a file."
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
            self.workspace.resolve(
                path
            )
        )

        text = (
            read_text_file(
                file_path
            )
        )

        if (
            expected_text
            not in text
        ):
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
            self.workspace.resolve(
                path
            )
        )

        text = (
            read_text_file(
                file_path
            )
        )

        lines = (
            text.splitlines()
        )

        if ignore_empty:
            lines = [
                line
                for line
                in lines
                if line.strip()
            ]

        actual = (
            len(lines)
        )

        if actual != int(
            expected
        ):
            raise ValueError(
                f"Expected {expected} lines, "
                f"but found {actual}."
            )

        return (
            f"Verified "
            f"{self.workspace.relative(file_path)} "
            f"contains {actual} "
            f"{'non-empty ' if ignore_empty else ''}"
            f"lines."
        )

    def count_matches(
        self,
        path: str,
        text: str,
    ) -> str:

        file_path = (
            self.workspace.resolve(
                path
            )
        )

        content = (
            read_text_file(
                file_path
            )
        )

        count = (
            content.count(
                text
            )
        )

        return (
            f"{self.workspace.relative(file_path)} "
            f"contains {count} occurrence(s) "
            f"of {text!r}."
        )

    # ========================================================
    # GIT
    # ========================================================

    def git_status(
        self,
    ) -> str:

        if not command_exists(
            "git"
        ):
            raise ValueError(
                "Git is not installed."
            )

        return self._run(
            [
                "git",
                "status",
                "--short",
            ],
            timeout=30,
        )

    def git_diff(
        self,
    ) -> str:

        if not command_exists(
            "git"
        ):
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
            timeout=30,
        )

    def git_log_recent(
        self,
        count: int = 10,
    ) -> str:

        if not command_exists(
            "git"
        ):
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
            timeout=30,
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
                "Persistent memory unavailable."
            )

        if self.state is None:
            raise ValueError(
                "Agent state unavailable."
            )

        observation = next(
            (
                item
                for item
                in self.state.observations
                if (
                    item.id
                    == evidence_id
                )
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

        return (
            "Verified project fact saved."
        )

    # ========================================================
    # INTERNAL
    # ========================================================

    def _run_project_command(
        self,
        command: list[str],
        purpose: str,
    ) -> str:

        output, code = (
            self._run_with_code(
                command,
                timeout=300,
            )
        )

        if (
            self.state is not None
            and purpose == "tests"
        ):
            self.state.last_test_passed = (
                code == 0
            )

        if (
            self.state is not None
            and purpose
            in {
                "build",
                "lint",
                "typecheck",
            }
        ):
            if code == 0:
                self.state.last_validation_passed = True

        return (
            f"COMMAND: "
            f"{' '.join(command)}\n"
            f"EXIT_CODE: {code}\n\n"
            f"{output}"
        )

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

    def _invalidate_verification(
        self,
    ) -> None:

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
            truncate_text(
                output
            ),
            process.returncode,
        )


# ============================================================
# REGISTRY
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

        "run_project_tests":
            tools.run_project_tests,

        "run_project_build":
            tools.run_project_build,

        "run_project_lint":
            tools.run_project_lint,

        "run_project_typecheck":
            tools.run_project_typecheck,

        "validate_python":
            tools.validate_python,

        "check_python_import":
            tools.check_python_import,

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
# TOOL DOCUMENTATION
# ============================================================

TOOL_DOCS = """
AVAILABLE TOOLS

inspect_project {}
Inspect detected project capabilities and top-level structure.

list_files {"path": ".", "depth": 2}
List files.

list_directories {"path": ".", "depth": 2}
List directories only.

find_file {"name": "App.tsx", "path": "."}
Find a file by name.

search_text {"query": "login", "path": ".", "max_results": 50}
Search literal text.

read_file {"path": "src/app.js", "start_line": 1, "end_line": 150}
Read an existing file.

create_file {"path": "src/game.js", "content": "..."}
Create a NEW file only.

apply_patch {
  "path": "src/game.js",
  "old_text": "exact existing text",
  "new_text": "replacement text"
}
Modify an EXISTING file only.

delete_file {"path": "old.txt"}
Delete an existing file.

create_directory {"path": "src/game"}
Create a new directory.

delete_empty_directory {"path": "old"}
Delete an empty directory only.

undo_last_edit {}
Undo the most recent reversible file edit.

find_symbol {"symbol": "authenticate_user", "path": "."}
Find Python function/class definitions.

find_references {"symbol": "authenticate_user", "path": ".", "max_results": 100}
Find symbol references as text.

run_project_tests {}
Run the project's detected test command.
Do not use if the project capability card says tests are unavailable.

run_project_build {}
Run the project's detected build command.
Do not use if unavailable.

run_project_lint {}
Run detected lint command.
Do not use if unavailable.

run_project_typecheck {}
Run detected typecheck command.
Do not use if unavailable.

validate_python {}
Python-only syntax compilation check.

check_python_import {"module": "pygame"}
Python-only module import check.

verify_file_exists {"path": "src/game.js"}
Verify that a file exists.

verify_file_content {"path": "src/game.js", "expected_text": "function startGame"}
Verify expected text exists.

verify_line_count {"path": "fruits.txt", "expected": 20, "ignore_empty": true}
Verify exact line count.

count_matches {"path": "src/app.js", "text": "TODO"}
Count literal matches.

git_status {}
Show Git changes.

git_diff {}
Show Git diff.

git_log_recent {"count": 10}
Show recent Git commits.

remember_fact {"fact": "Build uses Vite.", "evidence_id": "obs-003"}
Persist a stable fact backed by successful tool evidence.
"""