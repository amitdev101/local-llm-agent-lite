from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ============================================================
# FILE / TOOL LIMITS
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
    ".properties",
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

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".pdf",
    ".zip",
    ".jar",
    ".class",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".wav",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
}

MAX_FILE_BYTES = 1_500_000

MAX_TOOL_OUTPUT_CHARS = 6_000

MAX_EDIT_CHARS = 100_000

MAX_BATCH_FILES = 12


# ============================================================
# PAYLOAD STORE
# ============================================================


class PayloadStore:

    def __init__(
        self,
        root: Path,
        max_files: int = 250,
    ):

        self.root = root.resolve()

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.max_files = max(
            50,
            int(max_files),
        )

    def save(
        self,
        content: str,
    ) -> str:

        if not isinstance(
            content,
            str,
        ):

            raise ValueError("Payload content must be text.")

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

        payload_id = f"p-{digest}"

        path = self.root / f"{payload_id}.txt"

        if not path.exists():

            path.write_text(
                content,
                encoding="utf-8",
            )

        else:

            try:

                os.utime(
                    path,
                    None,
                )

            except Exception:

                pass

        return payload_id

    def load(
        self,
        payload_id: str,
    ) -> str:

        if not isinstance(
            payload_id,
            str,
        ):

            raise ValueError("Payload reference must be text.")

        if not (len(payload_id) == 18 and payload_id.startswith("p-")):

            raise ValueError("Invalid payload reference.")

        hex_part = payload_id[2:]

        if not all(character in "0123456789abcdef" for character in hex_part):

            raise ValueError("Invalid payload reference.")

        path = self.root / f"{payload_id}.txt"

        if not path.exists():

            raise ValueError(f"Payload does not exist: " f"{payload_id}")

        return path.read_text(
            encoding="utf-8",
        )

    def exists(
        self,
        payload_id: str,
    ) -> bool:

        try:

            self.load(payload_id)

            return True

        except Exception:

            return False

    def cleanup(
        self,
    ) -> None:

        try:

            files = sorted(
                self.root.glob("p-*.txt"),
                key=lambda path: (path.stat().st_mtime),
                reverse=True,
            )

        except Exception:

            return

        for path in files[self.max_files :]:

            try:

                path.unlink()

            except Exception:

                pass


# ============================================================
# PROJECT PROFILE
# ============================================================


@dataclass
class ProjectProfile:

    kind: str = "unknown"

    languages: list[str] = field(default_factory=list)

    frameworks: list[str] = field(default_factory=list)

    package_manager: str | None = None

    test_command: list[str] | None = None

    build_command: list[str] | None = None

    lint_command: list[str] | None = None

    typecheck_command: list[str] | None = None

    source_dirs: list[str] = field(default_factory=list)

    test_dirs: list[str] = field(default_factory=list)

    notes: list[str] = field(default_factory=list)

    root: str = "."


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

    return text[:half] + "\n\n... [OUTPUT TRUNCATED] ...\n\n" + text[-half:]


def command_exists(
    name: str,
) -> bool:

    return shutil.which(name) is not None


def resolved_command(
    name: str,
) -> str:

    return shutil.which(name) or name


def validate_text_write_path(
    path: Path,
) -> None:

    suffix = path.suffix.lower()

    if suffix in BINARY_EXTENSIONS:

        raise ValueError(
            f"Text tools cannot create or replace "
            f"binary file type {suffix}. "
            "Do not write fake strings such as "
            "'PNG image data here'."
        )


def read_text_file(
    path: Path,
) -> str:

    if not path.exists():

        raise ValueError(f"File does not exist: " f"{path}")

    if not path.is_file():

        raise ValueError(f"Path is not a file: " f"{path}")

    suffix = path.suffix.lower()

    if suffix in BINARY_EXTENSIONS:

        raise ValueError(f"Binary file type {suffix} " "cannot be read as UTF-8 text.")

    size = path.stat().st_size

    if size > MAX_FILE_BYTES:

        raise ValueError(f"File is too large: " f"{size:,} bytes")

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def npm_script_command(
    manager: str,
    script: str,
) -> list[str]:

    executable = resolved_command(manager)

    if manager == "yarn":

        return [
            executable,
            script,
        ]

    return [
        executable,
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

    profile = ProjectProfile(root=str(root))

    if not root.exists():

        return profile

    # ========================================================
    # NODE
    # ========================================================

    package_json = root / "package.json"

    if package_json.exists():

        profile.kind = "node"

        profile.languages.append("JavaScript")

        try:

            package_data = json.loads(package_json.read_text(encoding="utf-8"))

        except Exception:

            package_data = {}

        dependencies: dict[
            str,
            Any,
        ] = {}

        dependencies.update(
            package_data.get(
                "dependencies",
                {},
            )
            or {}
        )

        dependencies.update(
            package_data.get(
                "devDependencies",
                {},
            )
            or {}
        )

        scripts = (
            package_data.get(
                "scripts",
                {},
            )
            or {}
        )

        if "typescript" in dependencies or (root / "tsconfig.json").exists():

            profile.languages.append("TypeScript")

        if "react" in dependencies:

            profile.frameworks.append("React")

        if "next" in dependencies:

            profile.frameworks.append("Next.js")

        if "vite" in dependencies:

            profile.frameworks.append("Vite")

        if "vue" in dependencies:

            profile.frameworks.append("Vue")

        if "svelte" in dependencies:

            profile.frameworks.append("Svelte")

        if (root / "pnpm-lock.yaml").exists():

            profile.package_manager = "pnpm"

        elif (root / "yarn.lock").exists():

            profile.package_manager = "yarn"

        else:

            profile.package_manager = "npm"

        manager = profile.package_manager

        if "test" in scripts:

            profile.test_command = npm_script_command(
                manager,
                "test",
            )

        if "build" in scripts:

            profile.build_command = npm_script_command(
                manager,
                "build",
            )

        if "lint" in scripts:

            profile.lint_command = npm_script_command(
                manager,
                "lint",
            )

        if "typecheck" in scripts:

            profile.typecheck_command = npm_script_command(
                manager,
                "typecheck",
            )

        else:

            # No npx fallback. Avoid implicit network/package installs.
            if os.name == "nt":

                local_tsc = root / "node_modules" / ".bin" / "tsc.cmd"

            else:

                local_tsc = root / "node_modules" / ".bin" / "tsc"

            if "TypeScript" in profile.languages and local_tsc.exists():

                profile.typecheck_command = [
                    str(local_tsc),
                    "--noEmit",
                ]

        return profile

    # ========================================================
    # MAVEN
    # ========================================================

    if (root / "pom.xml").exists():

        profile.kind = "java_maven"

        profile.languages = ["Java"]

        profile.package_manager = "Maven"

        if command_exists("mvn"):

            mvn = resolved_command("mvn")

            profile.test_command = [
                mvn,
                "test",
            ]

            profile.build_command = [
                mvn,
                "package",
                "-DskipTests",
            ]

        else:

            profile.notes.append("Maven project detected " "but mvn is unavailable.")

        return profile

    # ========================================================
    # GRADLE
    # ========================================================

    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():

        profile.kind = "java_gradle"

        profile.languages = ["Java"]

        profile.package_manager = "Gradle"

        if os.name == "nt":

            wrapper = root / "gradlew.bat"

        else:

            wrapper = root / "gradlew"

        runner: str | None = None

        if wrapper.exists():

            runner = str(wrapper)

        elif command_exists("gradle"):

            runner = resolved_command("gradle")

        if runner:

            profile.test_command = [
                runner,
                "test",
            ]

            profile.build_command = [
                runner,
                "build",
            ]

        else:

            profile.notes.append(
                "Gradle project detected " "but Gradle is unavailable."
            )

        return profile

    # ========================================================
    # SIMPLE JAVA
    # ========================================================

    try:

        java_files = list(root.glob("*.java"))

        if not java_files:

            java_files = list(root.rglob("*.java"))[:100]

    except Exception:

        java_files = []

    if java_files:

        profile.kind = "java_simple"

        profile.languages = ["Java"]

        profile.package_manager = "javac"

        if command_exists("javac"):

            javac = resolved_command("javac")

            relative_files = [str(file.relative_to(root)) for file in java_files]

            profile.build_command = [
                javac,
                *relative_files,
            ]

        else:

            profile.notes.append("Java source detected " "but javac is unavailable.")

        return profile

    # ========================================================
    # RUST
    # ========================================================

    if (root / "Cargo.toml").exists():

        profile.kind = "rust"

        profile.languages = ["Rust"]

        profile.package_manager = "Cargo"

        if command_exists("cargo"):

            cargo = resolved_command("cargo")

            profile.test_command = [
                cargo,
                "test",
            ]

            profile.build_command = [
                cargo,
                "check",
            ]

        return profile

    # ========================================================
    # GO
    # ========================================================

    if (root / "go.mod").exists():

        profile.kind = "go"

        profile.languages = ["Go"]

        profile.package_manager = "Go"

        if command_exists("go"):

            go = resolved_command("go")

            profile.test_command = [
                go,
                "test",
                "./...",
            ]

            profile.build_command = [
                go,
                "build",
                "./...",
            ]

        return profile

    # ========================================================
    # PYTHON
    # ========================================================

    python_markers = [
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "setup.py",
        root / "Pipfile",
    ]

    python_files = list(root.glob("*.py"))

    if any(marker.exists() for marker in python_markers) or python_files:

        profile.kind = "python"

        profile.languages = ["Python"]

        profile.package_manager = "Python"

        try:

            import pytest  # noqa: F401

            profile.test_command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
            ]

        except Exception:

            profile.notes.append("pytest is not installed.")

        return profile

    # ========================================================
    # STATIC WEB
    # ========================================================

    if list(root.glob("*.html")) or list(root.glob("*.js")):

        profile.kind = "static_web"

        profile.languages = [
            "JavaScript",
            "HTML",
            "CSS",
        ]

        profile.notes.append("Static browser application.")

        return profile

    return profile


def profile_to_prompt(
    profile: ProjectProfile,
) -> str:

    def show_command(
        command: list[str] | None,
    ) -> str:

        if not command:

            return "unavailable"

        return " ".join(command)

    return (
        f"ACTIVE PROJECT ROOT: "
        f"{profile.root}\n"
        f"PROJECT KIND: "
        f"{profile.kind}\n"
        f"LANGUAGES: "
        f"{', '.join(profile.languages) or 'unknown'}\n"
        f"FRAMEWORKS: "
        f"{', '.join(profile.frameworks) or 'none'}\n"
        f"PACKAGE MANAGER: "
        f"{profile.package_manager or 'none'}\n"
        f"TEST COMMAND: "
        f"{show_command(profile.test_command)}\n"
        f"BUILD COMMAND: "
        f"{show_command(profile.build_command)}\n"
        f"LINT COMMAND: "
        f"{show_command(profile.lint_command)}\n"
        f"TYPECHECK COMMAND: "
        f"{show_command(profile.typecheck_command)}\n"
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

        self.root = root.resolve()

    def resolve(
        self,
        path: str,
    ) -> Path:

        raw = Path(path)

        if raw.is_absolute():

            candidate = raw.resolve()

        else:

            candidate = (self.root / raw).resolve()

        try:

            candidate.relative_to(self.root)

        except ValueError:

            raise ValueError("Access outside the selected " "workspace is blocked.")

        return candidate

    def relative(
        self,
        path: Path,
    ) -> str:

        return str(path.resolve().relative_to(self.root))


# ============================================================
# TOOLS
# ============================================================


class Tools:

    def __init__(
        self,
        workspace: Workspace,
        payload_store: PayloadStore,
        memory: Any = None,
        state: Any = None,
        profile: ProjectProfile | None = None,
        command_root: Path | None = None,
    ):

        self.workspace = workspace

        self.payload_store = payload_store

        self.memory = memory

        self.state = state

        self.command_root = command_root.resolve() if command_root else workspace.root

        self.profile = profile or detect_project_profile(self.command_root)

    # ========================================================
    # PAYLOAD RESOLUTION
    # ========================================================

    def _resolve_text(
        self,
        inline: str | None,
        payload_ref: str | None,
        field_name: str,
    ) -> str:

        if inline is None and payload_ref is None:

            raise ValueError(f"Provide {field_name} " f"or {field_name}_ref.")

        if inline is not None and payload_ref is not None:

            raise ValueError(
                f"Provide only one of " f"{field_name} " f"or {field_name}_ref."
            )

        if payload_ref is not None:

            return self.payload_store.load(payload_ref)

        assert inline is not None

        return inline

    # ========================================================
    # PROJECT
    # ========================================================

    def inspect_project(
        self,
        path: str = ".",
    ) -> str:

        target = self.workspace.resolve(path)

        if target.is_file():

            target = target.parent

        profile = detect_project_profile(target)

        items = []

        for item in sorted(
            target.iterdir(),
            key=lambda path: (
                not path.is_dir(),
                path.name.lower(),
            ),
        )[:100]:

            prefix = "DIR " if item.is_dir() else "FILE"

            items.append(f"{prefix} " f"{item.name}")

        return profile_to_prompt(profile) + "\n\nTOP LEVEL:\n" + "\n".join(items)

    # ========================================================
    # DISCOVERY
    # ========================================================

    def list_files(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:

        base = self.workspace.resolve(path)

        if not base.exists():

            raise ValueError("Path does not exist.")

        if not base.is_dir():

            raise ValueError("Path is not a directory.")

        depth = max(
            0,
            min(
                int(depth),
                5,
            ),
        )

        results = []

        base_parts = len(base.parts)

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            current = Path(current_root)

            current_depth = len(current.parts) - base_parts

            dirs[:] = [directory for directory in dirs if directory not in IGNORE_DIRS]

            if current_depth >= depth:

                dirs[:] = []

            for filename in sorted(files):

                file_path = current / filename

                results.append(self.workspace.relative(file_path))

                if len(results) >= 300:

                    results.append("... result limit reached ...")

                    return "\n".join(results)

        return "\n".join(results) or "(no files)"

    def list_directories(
        self,
        path: str = ".",
        depth: int = 2,
    ) -> str:

        base = self.workspace.resolve(path)

        if not base.exists():

            raise ValueError("Path does not exist.")

        if not base.is_dir():

            raise ValueError("Path is not a directory.")

        depth = max(
            0,
            min(
                int(depth),
                5,
            ),
        )

        results = []

        base_parts = len(base.parts)

        for (
            current_root,
            dirs,
            _,
        ) in os.walk(base):

            current = Path(current_root)

            current_depth = len(current.parts) - base_parts

            dirs[:] = [directory for directory in dirs if directory not in IGNORE_DIRS]

            if current != base:

                results.append(self.workspace.relative(current))

            if current_depth >= depth:

                dirs[:] = []

            if len(results) >= 200:

                break

        return "\n".join(results) or "(no directories)"

    def find_file(
        self,
        name: str,
        path: str = ".",
    ) -> str:

        if not name:

            raise ValueError("name cannot be empty")

        base = self.workspace.resolve(path)

        query = name.lower()

        results = []

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            dirs[:] = [directory for directory in dirs if directory not in IGNORE_DIRS]

            for filename in files:

                if query in filename.lower():

                    file_path = Path(current_root) / filename

                    results.append(self.workspace.relative(file_path))

                    if len(results) >= 100:

                        return "\n".join(results)

        return "\n".join(results) or "(no matching files)"

    # ========================================================
    # SEARCH
    # ========================================================

    def search_text(
        self,
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:

        if not query:

            raise ValueError("query cannot be empty")

        base = self.workspace.resolve(path)

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

            dirs[:] = [directory for directory in dirs if directory not in IGNORE_DIRS]

            for filename in files:

                file_path = Path(current_root) / filename

                if file_path.suffix.lower() not in TEXT_EXTENSIONS:

                    continue

                try:

                    if file_path.stat().st_size > MAX_FILE_BYTES:

                        continue

                    text = file_path.read_text(
                        encoding="utf-8",
                        errors="replace",
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

                    if query.lower() in line.lower():

                        matches.append(
                            f"{self.workspace.relative(file_path)}:"
                            f"{line_number}: "
                            f"{line[:300]}"
                        )

                        if len(matches) >= max_results:

                            return "\n".join(matches)

        return "\n".join(matches) or "(no matches)"

    # ========================================================
    # READ
    # ========================================================

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:

        file_path = self.workspace.resolve(path)

        text = read_text_file(file_path)

        lines = text.splitlines()

        relative = self.workspace.relative(file_path)

        if not lines:

            return (
                f"FILE: {relative}\n"
                "TOTAL_LINES: 0\n\n"
                "RAW_CONTENT_BEGIN\n"
                "RAW_CONTENT_END"
            )

        start = max(
            1,
            int(start_line),
        )

        if start > len(lines):

            return (
                f"FILE: {relative}\n"
                f"TOTAL_LINES: "
                f"{len(lines)}\n"
                f"Requested start line "
                f"{start} is beyond EOF."
            )

        end = max(
            start,
            int(end_line),
        )

        end = min(
            end,
            len(lines),
            start + 299,
        )

        content = "\n".join(lines[start - 1 : end])

        return (
            f"FILE: {relative}\n"
            f"LINES: "
            f"{start}-{end} / "
            f"{len(lines)}\n"
            f"TOTAL_LINES: "
            f"{len(lines)}\n\n"
            "RAW_CONTENT_BEGIN\n"
            f"{content}\n"
            "RAW_CONTENT_END"
        )

    # ========================================================
    # CREATE FILE
    # ========================================================

    def create_file(
        self,
        path: str,
        content: str | None = None,
        content_ref: str | None = None,
    ) -> str:

        resolved_content = self._resolve_text(
            content,
            content_ref,
            "content",
        )

        file_path = self.workspace.resolve(path)

        validate_text_write_path(file_path)

        if file_path.exists():

            if file_path.is_file() and read_text_file(file_path) == resolved_content:

                return "File already has requested content: " + self.workspace.relative(
                    file_path
                )

            raise ValueError(
                "File already exists. "
                "Use replace_file for a complete rewrite "
                "or apply_patch for a localized edit."
            )

        if len(resolved_content) > MAX_EDIT_CHARS:

            raise ValueError("Content is too large.")

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_path.write_text(
            resolved_content,
            encoding="utf-8",
        )

        if self.state is not None:

            self.state.edit_backups.append(
                (
                    file_path,
                    "",
                    False,
                )
            )

        self._mark_edited(self.workspace.relative(file_path))

        return "Created new file: " f"{self.workspace.relative(file_path)}"

    # ========================================================
    # CREATE FILES
    # ========================================================

    def create_files(
        self,
        files: list[dict[str, Any]],
    ) -> str:

        if not files:

            raise ValueError("files cannot be empty")

        if len(files) > MAX_BATCH_FILES:

            raise ValueError(
                f"At most " f"{MAX_BATCH_FILES} files " "may be created in one call."
            )

        prepared: list[
            tuple[
                Path,
                str,
            ]
        ] = []

        unchanged: list[str] = []

        for item in files:

            path = str(
                item.get(
                    "path",
                    "",
                )
            )

            if not path:

                raise ValueError("Every file requires a path.")

            resolved_content = self._resolve_text(
                item.get("content"),
                item.get("content_ref"),
                "content",
            )

            target = self.workspace.resolve(path)

            validate_text_write_path(target)

            if target.exists():

                if target.is_file() and read_text_file(target) == resolved_content:

                    unchanged.append(self.workspace.relative(target))

                    continue

                raise ValueError(f"File already exists: " f"{path}")

            if len(resolved_content) > MAX_EDIT_CHARS:

                raise ValueError(f"Content too large: " f"{path}")

            prepared.append(
                (
                    target,
                    resolved_content,
                )
            )

        created = []

        for (
            target,
            resolved_content,
        ) in prepared:

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            target.write_text(
                resolved_content,
                encoding="utf-8",
            )

            if self.state is not None:

                self.state.edit_backups.append(
                    (
                        target,
                        "",
                        False,
                    )
                )

            relative = self.workspace.relative(target)

            self._mark_edited(relative)

            created.append(relative)

        lines = [*(f"Created: {item}" for item in created)]

        lines.extend(f"Already correct: {item}" for item in unchanged)

        return "Files ready:\n" + "\n".join(f"- {item}" for item in lines)

    # ========================================================
    # REPLACE FILE
    # ========================================================

    def replace_file(
        self,
        path: str,
        content: str | None = None,
        content_ref: str | None = None,
    ) -> str:

        resolved_content = self._resolve_text(
            content,
            content_ref,
            "content",
        )

        file_path = self.workspace.resolve(path)

        validate_text_write_path(file_path)

        if not file_path.exists():

            raise ValueError("File does not exist. " "Use create_file for a new file.")

        if not file_path.is_file():

            raise ValueError("Path is not a file.")

        if len(resolved_content) > MAX_EDIT_CHARS:

            raise ValueError("Content is too large.")

        original = read_text_file(file_path)

        if original == resolved_content:

            return "File already has requested content: " + self.workspace.relative(
                file_path
            )

        if self.state is not None:

            self.state.edit_backups.append(
                (
                    file_path,
                    original,
                    True,
                )
            )

        file_path.write_text(
            resolved_content,
            encoding="utf-8",
        )

        self._mark_edited(self.workspace.relative(file_path))

        return "Replaced entire contents of: " f"{self.workspace.relative(file_path)}"

    # ========================================================
    # PATCH
    # ========================================================

    def apply_patch(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str | None = None,
        old_text_ref: str | None = None,
        new_text_ref: str | None = None,
    ) -> str:

        resolved_old_text = self._resolve_text(
            old_text,
            old_text_ref,
            "old_text",
        )

        resolved_new_text = self._resolve_text(
            new_text,
            new_text_ref,
            "new_text",
        )

        if not resolved_old_text.strip():

            raise ValueError(
                "old_text cannot be empty. " "Use replace_file for a full rewrite."
            )

        if len(resolved_old_text) + len(resolved_new_text) > MAX_EDIT_CHARS:

            raise ValueError(
                "Patch is too large. "
                "Use replace_file when most "
                "of the file changes."
            )

        file_path = self.workspace.resolve(path)

        validate_text_write_path(file_path)

        original = read_text_file(file_path)

        count = original.count(resolved_old_text)

        match_kind = "exact"

        if count == 0:

            parts = re.split(r"\s+", resolved_old_text.strip())

            pattern = re.compile(r"\s+".join(re.escape(part) for part in parts))

            matches = list(pattern.finditer(original))

            if len(matches) == 1:

                match = matches[0]

                updated = (
                    original[: match.start()]
                    + resolved_new_text
                    + original[match.end() :]
                )

                match_kind = "whitespace-normalized"

            elif len(matches) > 1:

                raise ValueError(
                    "old_text has multiple whitespace-normalized matches. "
                    "Provide more surrounding text."
                )

            else:

                raise ValueError(
                    "old_text was not found exactly or with normalized whitespace. "
                    "Re-read the file or use replace_file."
                )

        elif count > 1:

            raise ValueError(
                f"old_text occurs " f"{count} times. " "Provide more surrounding text."
            )

        else:

            updated = original.replace(
                resolved_old_text,
                resolved_new_text,
                1,
            )

        if updated == original:

            raise ValueError("Patch would make no change.")

        if self.state is not None:

            self.state.edit_backups.append(
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

        self._mark_edited(self.workspace.relative(file_path))

        return (
            "Patched existing file "
            f"using {match_kind} matching: "
            f"{self.workspace.relative(file_path)}"
        )

    # ========================================================
    # DELETE / DIRECTORIES
    # ========================================================

    def delete_file(
        self,
        path: str,
    ) -> str:

        file_path = self.workspace.resolve(path)

        original = read_text_file(file_path)

        if self.state is not None:

            self.state.edit_backups.append(
                (
                    file_path,
                    original,
                    True,
                )
            )

        file_path.unlink()

        self._mark_edited(self.workspace.relative(file_path))

        return "Deleted file: " f"{self.workspace.relative(file_path)}"

    def create_directory(
        self,
        path: str,
    ) -> str:

        directory = self.workspace.resolve(path)

        if directory.exists():

            if directory.is_dir():

                return "Directory already exists: " + self.workspace.relative(directory)

            raise ValueError("A non-directory item " "already exists here.")

        directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        return "Created directory: " f"{self.workspace.relative(directory)}"

    def delete_empty_directory(
        self,
        path: str,
    ) -> str:

        directory = self.workspace.resolve(path)

        if not directory.exists():

            raise ValueError("Directory does not exist.")

        if not directory.is_dir():

            raise ValueError("Path is not a directory.")

        if any(directory.iterdir()):

            raise ValueError("Directory is not empty.")

        directory.rmdir()

        return "Deleted empty directory: " f"{self.workspace.relative(directory)}"

    # ========================================================
    # UNDO
    # ========================================================

    def undo_last_edit(
        self,
    ) -> str:

        if self.state is None:

            raise ValueError("Agent state unavailable.")

        if not (self.state.edit_backups):

            raise ValueError("No edit available to undo.")

        (
            file_path,
            old_content,
            existed_before,
        ) = self.state.edit_backups.pop()

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
                "Restored previous contents: " f"{self.workspace.relative(file_path)}"
            )

        else:

            if file_path.exists():

                file_path.unlink()

            result = (
                "Removed newly-created file: " f"{self.workspace.relative(file_path)}"
            )

        if self.state is not None:

            self.state.mutation_revision += 1

        self._invalidate_verification()

        return result

    # ========================================================
    # VERIFICATION
    # ========================================================

    def verify_file_exists(
        self,
        path: str,
    ) -> str:

        target = self.workspace.resolve(path)

        if not target.exists():

            raise ValueError("File does not exist.")

        if not target.is_file():

            raise ValueError("Path is not a file. " "Use verify_directory_exists.")

        return "Verified file exists: " f"{self.workspace.relative(target)}"

    def verify_files_exist(
        self,
        paths: list[str],
    ) -> str:

        if not paths:

            raise ValueError("paths cannot be empty")

        verified = []

        for path in paths:

            target = self.workspace.resolve(path)

            if not target.exists():

                raise ValueError(f"File does not exist: " f"{path}")

            if not target.is_file():

                raise ValueError(f"Not a file: " f"{path}")

            verified.append(self.workspace.relative(target))

        return "Verified files exist:\n" + "\n".join(f"- {item}" for item in verified)

    def verify_directory_exists(
        self,
        path: str,
    ) -> str:

        target = self.workspace.resolve(path)

        if not target.exists():

            raise ValueError("Directory does not exist.")

        if not target.is_dir():

            raise ValueError("Path is not a directory.")

        return "Verified directory exists: " f"{self.workspace.relative(target)}"

    def verify_file_content(
        self,
        path: str,
        expected_text: str,
    ) -> str:

        target = self.workspace.resolve(path)

        text = read_text_file(target)

        if expected_text not in text:

            raise ValueError("Expected text was not found.")

        return (
            "Verified expected content exists in " f"{self.workspace.relative(target)}"
        )

    def verify_line_count(
        self,
        path: str,
        expected: int,
        ignore_empty: bool = True,
    ) -> str:

        target = self.workspace.resolve(path)

        text = read_text_file(target)

        lines = text.splitlines()

        if ignore_empty:

            lines = [line for line in lines if line.strip()]

        actual = len(lines)

        if actual != int(expected):

            raise ValueError(f"Expected " f"{expected} lines, " f"found {actual}.")

        return (
            f"Verified "
            f"{self.workspace.relative(target)} "
            f"contains {actual} lines."
        )

    def count_matches(
        self,
        path: str,
        text: str,
    ) -> str:

        target = self.workspace.resolve(path)

        content = read_text_file(target)

        count = content.count(text)

        return (
            f"{self.workspace.relative(target)} "
            f"contains {count} occurrence(s) "
            f"of {text!r}."
        )

    # ========================================================
    # PROJECT COMMANDS
    # ========================================================

    def run_project_tests(
        self,
    ) -> str:

        self.profile = detect_project_profile(self.command_root)

        command = self.profile.test_command

        if not command:

            raise ValueError("No project test command was detected.")

        return self._run_project_command(
            command,
            "tests",
        )

    def run_project_build(
        self,
    ) -> str:

        self.profile = detect_project_profile(self.command_root)

        command = self.profile.build_command

        if not command:

            raise ValueError("No project build command was detected.")

        return self._run_project_command(
            command,
            "build",
        )

    def run_project_lint(
        self,
    ) -> str:

        self.profile = detect_project_profile(self.command_root)

        command = self.profile.lint_command

        if not command:

            raise ValueError("No project lint command was detected.")

        return self._run_project_command(
            command,
            "lint",
        )

    def run_project_typecheck(
        self,
    ) -> str:

        self.profile = detect_project_profile(self.command_root)

        command = self.profile.typecheck_command

        if not command:

            raise ValueError("No project typecheck command was detected.")

        return self._run_project_command(
            command,
            "typecheck",
        )

    # ========================================================
    # PYTHON
    # ========================================================

    def validate_python(self) -> str:
        output, code = self._run_with_code(
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                str(self.command_root),
            ],
            timeout=180,
        )

        formatted = (
            f"EXIT_CODE: {code}\n\n"
            f"{output or 'Python compilation succeeded.'}"
        )

        if self.state is not None:
            self.state.last_validation_passed = code == 0

        if code != 0:
            raise ValueError(formatted)

        return formatted
    def check_python_import(
        self,
        module: str,
    ) -> str:

        if not module:

            raise ValueError("module cannot be empty")

        if not all(
            part.replace(
                "_",
                "",
            ).isalnum()
            for part in module.split(".")
        ):

            raise ValueError("Invalid module name.")

        output, code = self._run_with_code(
            [
                sys.executable,
                "-c",
                (f"import {module}; " "print('IMPORT_OK')"),
            ],
            timeout=30,
        )

        if code != 0:

            raise ValueError(output)

        return f"Successfully imported " f"{module}."

    def check_command(
        self,
        name: str,
    ) -> str:

        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name):

            raise ValueError("Command name must not contain a path or arguments.")

        resolved = shutil.which(name)

        if not resolved:

            raise ValueError(f"Command is unavailable: {name}")

        return f"Command is available: {name}\nPATH: {resolved}"

    # ========================================================
    # SYMBOLS
    # ========================================================

    def find_symbol(
        self,
        symbol: str,
        path: str = ".",
    ) -> str:

        if not symbol:

            raise ValueError("symbol cannot be empty")

        base = self.workspace.resolve(path)

        results = []

        for (
            current_root,
            dirs,
            files,
        ) in os.walk(base):

            dirs[:] = [directory for directory in dirs if directory not in IGNORE_DIRS]

            for filename in files:

                if not filename.endswith(".py"):

                    continue

                file_path = Path(current_root) / filename

                try:

                    source = file_path.read_text(
                        encoding="utf-8",
                        errors="replace",
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

        return "\n".join(results) or "(symbol not found)"

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
    # GIT
    # ========================================================

    def git_status(
        self,
    ) -> str:

        if not command_exists("git"):

            raise ValueError("Git is not installed.")

        return self._run(
            [
                resolved_command("git"),
                "status",
                "--short",
            ],
            timeout=30,
        )

    def git_diff(
        self,
    ) -> str:

        if not command_exists("git"):

            raise ValueError("Git is not installed.")

        return self._run(
            [
                resolved_command("git"),
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

        if not command_exists("git"):

            raise ValueError("Git is not installed.")

        count = max(
            1,
            min(
                int(count),
                30,
            ),
        )

        return self._run(
            [
                resolved_command("git"),
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

            raise ValueError("Persistent memory unavailable.")

        if self.state is None:

            raise ValueError("Agent state unavailable.")

        observation = next(
            (item for item in self.state.observations if (item.id == evidence_id)),
            None,
        )

        if observation is None:

            raise ValueError("Unknown evidence_id.")

        if not (observation.success):

            raise ValueError("Evidence must be successful.")

        self.memory.add_fact(
            fact=fact,
            evidence=truncate_text(
                observation.text,
                1000,
            ),
            evidence_id=evidence_id,
        )

        return "Verified project fact saved."

    # ========================================================
    # INTERNAL
    # ========================================================

    def _run_project_command(
        self,
        command: list[str],
        purpose: str,
    ) -> str:
        output, code = self._run_with_code(
            command,
            timeout=300,
        )

        formatted = (
            f"COMMAND: {' '.join(command)}\n" f"EXIT_CODE: {code}\n\n" f"{output}"
        )

        if self.state is not None:
            if purpose == "tests":
                self.state.last_test_passed = code == 0
            elif purpose in {
                "build",
                "lint",
                "typecheck",
            }:
                self.state.last_validation_passed = code == 0

        if code != 0:
            raise ValueError(formatted)

        return formatted

    def _mark_edited(
        self,
        relative_path: str,
    ) -> None:

        if self.state is None:

            return

        self.state.edited_files.add(relative_path)

        self.state.mutation_revision += 1

        self._invalidate_verification()

    def _invalidate_verification(
        self,
    ) -> None:

        if self.state is None:

            return

        self.state.last_test_passed = False

        self.state.last_validation_passed = False

    def _run(
        self,
        command: list[str],
        timeout: int,
    ) -> str:

        output, _ = self._run_with_code(
            command,
            timeout,
        )

        return output

    def _run_with_code(
        self,
        command: list[str],
        timeout: int,
    ) -> tuple[str, int]:

        try:

            process = subprocess.run(
                command,
                cwd=self.command_root,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                shell=False,
            )

        except FileNotFoundError:

            return (
                f"Command not found: " f"{command[0]}",
                127,
            )

        except subprocess.TimeoutExpired:

            return (
                f"Command timed out after " f"{timeout} seconds.",
                124,
            )

        output = ""

        if process.stdout:

            output += process.stdout

        if process.stderr:

            if output:

                output += "\n"

            output += process.stderr

        return (
            output,
            process.returncode,
        )


# ============================================================
# TOOL REGISTRY
# ============================================================


def build_tool_registry(
    tools: Tools,
) -> dict[
    str,
    Callable[..., str],
]:

    return {
        "inspect_project": tools.inspect_project,
        "list_files": tools.list_files,
        "list_directories": tools.list_directories,
        "find_file": tools.find_file,
        "search_text": tools.search_text,
        "read_file": tools.read_file,
        "create_file": tools.create_file,
        "create_files": tools.create_files,
        "replace_file": tools.replace_file,
        "apply_patch": tools.apply_patch,
        "delete_file": tools.delete_file,
        "create_directory": tools.create_directory,
        "delete_empty_directory": tools.delete_empty_directory,
        "undo_last_edit": tools.undo_last_edit,
        "verify_file_exists": tools.verify_file_exists,
        "verify_files_exist": tools.verify_files_exist,
        "verify_directory_exists": tools.verify_directory_exists,
        "verify_file_content": tools.verify_file_content,
        "verify_line_count": tools.verify_line_count,
        "count_matches": tools.count_matches,
        "run_project_tests": tools.run_project_tests,
        "run_project_build": tools.run_project_build,
        "run_project_lint": tools.run_project_lint,
        "run_project_typecheck": tools.run_project_typecheck,
        "validate_python": tools.validate_python,
        "check_python_import": tools.check_python_import,
        "check_command": tools.check_command,
        "find_symbol": tools.find_symbol,
        "find_references": tools.find_references,
        "git_status": tools.git_status,
        "git_diff": tools.git_diff,
        "git_log_recent": tools.git_log_recent,
        "remember_fact": tools.remember_fact,
    }


# ============================================================
# TOOL ARGUMENT SCHEMAS
# ============================================================

TOOL_SCHEMAS = {
    "inspect_project": {
        "required": set(),
        "allowed": {
            "path",
        },
    },
    "list_files": {
        "required": set(),
        "allowed": {
            "path",
            "depth",
        },
    },
    "list_directories": {
        "required": set(),
        "allowed": {
            "path",
            "depth",
        },
    },
    "find_file": {
        "required": {
            "name",
        },
        "allowed": {
            "name",
            "path",
        },
    },
    "search_text": {
        "required": {
            "query",
        },
        "allowed": {
            "query",
            "path",
            "max_results",
        },
    },
    "read_file": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
            "start_line",
            "end_line",
        },
    },
    "create_file": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
            "content",
            "content_ref",
        },
    },
    "create_files": {
        "required": {
            "files",
        },
        "allowed": {
            "files",
        },
    },
    "replace_file": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
            "content",
            "content_ref",
        },
    },
    "apply_patch": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
            "old_text",
            "new_text",
            "old_text_ref",
            "new_text_ref",
        },
    },
    "delete_file": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
        },
    },
    "create_directory": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
        },
    },
    "delete_empty_directory": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
        },
    },
    "undo_last_edit": {
        "required": set(),
        "allowed": set(),
    },
    "verify_file_exists": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
        },
    },
    "verify_files_exist": {
        "required": {
            "paths",
        },
        "allowed": {
            "paths",
        },
    },
    "verify_directory_exists": {
        "required": {
            "path",
        },
        "allowed": {
            "path",
        },
    },
    "verify_file_content": {
        "required": {
            "path",
            "expected_text",
        },
        "allowed": {
            "path",
            "expected_text",
        },
    },
    "verify_line_count": {
        "required": {
            "path",
            "expected",
        },
        "allowed": {
            "path",
            "expected",
            "ignore_empty",
        },
    },
    "count_matches": {
        "required": {
            "path",
            "text",
        },
        "allowed": {
            "path",
            "text",
        },
    },
    "run_project_tests": {
        "required": set(),
        "allowed": set(),
    },
    "run_project_build": {
        "required": set(),
        "allowed": set(),
    },
    "run_project_lint": {
        "required": set(),
        "allowed": set(),
    },
    "run_project_typecheck": {
        "required": set(),
        "allowed": set(),
    },
    "validate_python": {
        "required": set(),
        "allowed": set(),
    },
    "check_python_import": {
        "required": {
            "module",
        },
        "allowed": {
            "module",
        },
    },
    "check_command": {
        "required": {
            "name",
        },
        "allowed": {
            "name",
        },
    },
    "find_symbol": {
        "required": {
            "symbol",
        },
        "allowed": {
            "symbol",
            "path",
        },
    },
    "find_references": {
        "required": {
            "symbol",
        },
        "allowed": {
            "symbol",
            "path",
            "max_results",
        },
    },
    "git_status": {
        "required": set(),
        "allowed": set(),
    },
    "git_diff": {
        "required": set(),
        "allowed": set(),
    },
    "git_log_recent": {
        "required": set(),
        "allowed": {
            "count",
        },
    },
    "remember_fact": {
        "required": {
            "fact",
            "evidence_id",
        },
        "allowed": {
            "fact",
            "evidence_id",
        },
    },
}


def _validate_inline_or_ref(
    args: dict[str, Any],
    inline_name: str,
    ref_name: str,
) -> tuple[
    bool,
    str,
]:

    has_inline = inline_name in args and args[inline_name] is not None

    has_ref = ref_name in args and args[ref_name] is not None

    if not has_inline and not has_ref:

        return (
            False,
            (f"Provide either " f"{inline_name} " f"or {ref_name}."),
        )

    if has_inline and has_ref:

        return (
            False,
            (f"Provide only one of " f"{inline_name} " f"or {ref_name}."),
        )

    return (
        True,
        "",
    )


def validate_tool_arguments(
    tool_name: str,
    args: dict[str, Any],
) -> tuple[
    bool,
    str,
]:

    schema = TOOL_SCHEMAS.get(tool_name)

    if schema is None:

        return (
            False,
            (f"Unknown tool: " f"{tool_name}. " "Do not invent tool names."),
        )

    provided = set(args.keys())

    missing = schema["required"] - provided

    extra = provided - schema["allowed"]

    if missing:

        return (
            False,
            (
                f"{tool_name} is missing "
                f"required argument(s): "
                f"{', '.join(sorted(missing))}"
            ),
        )

    if extra:

        return (
            False,
            (f"{tool_name} does not accept: " f"{', '.join(sorted(extra))}."),
        )

    if tool_name in {
        "create_file",
        "replace_file",
    }:

        return _validate_inline_or_ref(
            args,
            "content",
            "content_ref",
        )

    if tool_name == "apply_patch":

        valid, reason = _validate_inline_or_ref(
            args,
            "old_text",
            "old_text_ref",
        )

        if not valid:

            return (
                valid,
                reason,
            )

        return _validate_inline_or_ref(
            args,
            "new_text",
            "new_text_ref",
        )

    if tool_name == "create_files":

        files = args.get("files")

        if not isinstance(
            files,
            list,
        ):

            return (
                False,
                "files must be a list.",
            )

        for (
            index,
            item,
        ) in enumerate(files):

            if not isinstance(
                item,
                dict,
            ):

                return (
                    False,
                    (f"files[{index}] " "must be an object."),
                )

            if not item.get("path"):

                return (
                    False,
                    (f"files[{index}] " "requires path."),
                )

            extra_item_keys = set(item) - {
                "path",
                "content",
                "content_ref",
            }

            if extra_item_keys:

                return (
                    False,
                    (
                        f"files[{index}] does not accept: "
                        f"{', '.join(sorted(extra_item_keys))}. "
                        "path must be the complete file path, such as src/SnakeGame.java."
                    ),
                )

            valid, reason = _validate_inline_or_ref(
                item,
                "content",
                "content_ref",
            )

            if not valid:

                return (
                    False,
                    (f"files[{index}]: " f"{reason}"),
                )

    return (
        True,
        "",
    )


# ============================================================
# COMPACT TOOL DOCUMENTATION
# ============================================================

TOOL_DOCS = """
inspect_project(path=".")
list_files(path=".",depth=2)
list_directories(path=".",depth=2)
find_file(name,path=".")
search_text(query,path=".",max_results=50)

read_file(path,start_line=1,end_line=200)
-> returns RAW_CONTENT

create_file(path,content)
create_file(path,content_ref)
-> NEW text file only

create_files(files=[{"path":"src/File.java", "content":...}])
create_files(files=[{"path":"src/File.java", "content_ref":...}])
-> path is the complete file path, not a directory

replace_file(path,content)
replace_file(path,content_ref)
-> completely rewrite EXISTING text file

apply_patch(path,old_text,new_text)
apply_patch(path,old_text_ref,new_text_ref)
-> localized exact edit

Payload reference example:
p-1e6fbcc7d3141a42

Reuse existing *_ref instead of regenerating identical large content.

delete_file(path)
create_directory(path)
delete_empty_directory(path)
undo_last_edit()

verify_file_exists(path)
verify_files_exist(paths)
verify_directory_exists(path)
verify_file_content(path,expected_text)
verify_line_count(path,expected,ignore_empty=true)
count_matches(path,text)

run_project_tests()
run_project_build()
run_project_lint()
run_project_typecheck()

validate_python()
check_python_import(module)
check_command(name)

find_symbol(symbol,path=".")
find_references(symbol,path=".",max_results=100)

git_status()
git_diff()
git_log_recent(count=10)

remember_fact(fact,evidence_id)

Text tools cannot create real binary files such as .png/.jpg/.jar/.class.
"""
