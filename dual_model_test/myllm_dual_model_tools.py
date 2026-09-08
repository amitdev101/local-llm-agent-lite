from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = (PROJECT_ROOT / "agent_test_workspace").resolve()

MAX_LIST_RESULTS = 200
MAX_SEARCH_RESULTS = 80
MAX_READ_LINES = 300
MAX_READ_CHARS = 40_000
MAX_EDIT_CHARS = 100_000

IGNORED_DIRECTORIES = {
    ".build",
    ".git",
    ".idea",
    ".myllm",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
}

PROTECTED_DIRECTORIES = {
    ".build",
    ".git",
    ".myllm",
}

TOOL_SPECS = {
    "list_files": {
        "required": set(),
        "optional": {"path", "depth"},
        "blocks": (),
    },
    "search": {
        "required": {"query"},
        "optional": {"path"},
        "blocks": (),
    },
    "read_file": {
        "required": {"path"},
        "optional": {"start_line", "end_line"},
        "blocks": (),
    },
    "write_file": {
        "required": {"path"},
        "optional": set(),
        "blocks": ("CONTENT",),
    },
    "patch_file": {
        "required": {"path"},
        "optional": set(),
        "blocks": ("OLD", "NEW"),
    },
    "move_path": {
        "required": {"source", "destination"},
        "optional": set(),
        "blocks": (),
    },
    "delete_path": {
        "required": {"path"},
        "optional": set(),
        "blocks": (),
    },
    "undo_last_edit": {
        "required": set(),
        "optional": set(),
        "blocks": (),
    },
    "run_check": {
        "required": set(),
        "optional": {"kind"},
        "blocks": (),
    },
}

TOOL_DOCS = """
Return one Markdown tool action using one of these forms:

## TOOL list_files
path: [path]
depth: [depth]

## TOOL search
query: <query>
path: [path]

## TOOL read_file
path: <path>
start_line: [start_line]
end_line: [end_line]

## TOOL write_file
path: <path>
```text
<complete content>
```

## TOOL patch_file
path: <path>
### OLD
```text
<exact old text>
```
### NEW
```text
<new text>
```

## TOOL move_path
source: <source>
destination: <destination>

## TOOL delete_path
path: <path>

## TOOL undo_last_edit

## TOOL run_check
kind: [auto|build|test|lint|typecheck]

Required values use <angle brackets>. Optional values use [square brackets].
Do not include brackets in real values. Use kind auto by default.

Example:
## TOOL write_file
path: SnakeGame.java
```java
public class SnakeGame {
}
```
""".strip()


# ============================================================
# SAFE WORKSPACE
# ============================================================


@dataclass
class UndoEntry:
    label: str
    before_files: dict[Path, bytes | None] = field(default_factory=dict)
    after_files: dict[Path, str | None] = field(default_factory=dict)
    directory_move: tuple[Path, Path] | None = None
    deleted_directory: Path | None = None


@dataclass
class ToolState:
    known_versions: dict[Path, str] = field(default_factory=dict)
    undo_stack: list[UndoEntry] = field(default_factory=list)
    mutation_revision: int = 0
    verified_revision: int = 0
    last_action_signature: str = ""
    repeated_action_count: int = 0


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True

    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def resolve_workspace_path(relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Path cannot be empty.")

    raw = Path(relative_path)
    candidate = raw if raw.is_absolute() else WORKSPACE / raw
    lexical = Path(os.path.abspath(candidate))

    try:
        relative = lexical.relative_to(WORKSPACE)
    except ValueError as error:
        raise ValueError(f"Path escapes workspace: {relative_path}") from error

    current = WORKSPACE

    for part in relative.parts:
        current = current / part

        if current.exists() and _is_link(current):
            raise ValueError(f"Links and junctions are blocked: {relative_path}")

    resolved = lexical.resolve()

    try:
        resolved.relative_to(WORKSPACE)
    except ValueError as error:
        raise ValueError(f"Path escapes workspace: {relative_path}") from error

    return resolved


def _validate_mutation_path(target: Path) -> None:
    if target == WORKSPACE:
        raise ValueError("The workspace root cannot be changed.")

    relative = target.relative_to(WORKSPACE)

    if any(part.lower() in PROTECTED_DIRECTORIES for part in relative.parts):
        raise ValueError("Protected project metadata cannot be changed.")

    if os.name == "nt":
        reserved = {"CON", "PRN", "AUX", "NUL", *{
            f"{prefix}{number}"
            for prefix in ("COM", "LPT")
            for number in range(1, 10)
        }}

        if any(Path(part).stem.upper() in reserved for part in relative.parts):
            raise ValueError("The path contains a reserved Windows name.")


def _read_bytes(target: Path) -> bytes:
    if not target.exists():
        raise ValueError(f"File does not exist: {target.relative_to(WORKSPACE)}")

    if not target.is_file():
        raise ValueError(f"Not a file: {target.relative_to(WORKSPACE)}")

    return target.read_bytes()


def _decode_text(content: bytes, path: Path) -> str:
    if b"\x00" in content:
        raise ValueError(f"Binary file is not supported: {path.relative_to(WORKSPACE)}")

    try:
        return content.decode("utf-8-sig" if content.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"File is not UTF-8 text: {path.relative_to(WORKSPACE)}"
        ) from error


def _encode_text(content: str, original: bytes | None = None) -> bytes:
    if original:
        original_text = _decode_text(original, WORKSPACE / "existing-file")

        if "\r\n" in original_text and "\r\n" not in content:
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")

        encoded = content.encode("utf-8")

        if original.startswith(b"\xef\xbb\xbf"):
            encoded = b"\xef\xbb\xbf" + encoded

        return encoded

    return content.encode("utf-8")


def _atomic_write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".myllm-write-",
        dir=target.parent,
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _current_hash(target: Path) -> str | None:
    if not target.exists():
        return None

    if not target.is_file():
        raise ValueError(f"Not a file: {target.relative_to(WORKSPACE)}")

    return _hash_bytes(target.read_bytes())


def _require_current_version(target: Path, state: ToolState) -> None:
    known = state.known_versions.get(target)
    current = _current_hash(target)

    if known is None:
        raise ValueError("FILE_NOT_READ: Read the existing file before changing it.")

    if known != current:
        raise ValueError("FILE_CHANGED: Read the file again before changing it.")


def _apply_file_changes(
    label: str,
    changes: dict[Path, bytes | None],
    state: ToolState,
) -> None:
    before = {
        target: target.read_bytes() if target.exists() else None
        for target in changes
    }

    try:
        for target, content in changes.items():
            if content is None:
                target.unlink()
            else:
                _atomic_write(target, content)
    except Exception:
        for target, original in before.items():
            if original is None:
                if target.exists() and target.is_file():
                    target.unlink()
            else:
                _atomic_write(target, original)

        raise

    after = {target: _current_hash(target) for target in changes}
    state.undo_stack.append(UndoEntry(label, before, after))
    state.undo_stack[:] = state.undo_stack[-20:]
    state.mutation_revision += 1

    for target, digest in after.items():
        if digest is None:
            state.known_versions.pop(target, None)
        else:
            state.known_versions[target] = digest


def _approve(description: str) -> None:
    answer = input(f"\n⚠️ Approve {description}? [y/N]: ").strip().lower()

    if answer not in {"y", "yes"}:
        raise PermissionError("Action was rejected by the user.")


def list_files(path: str = ".", depth: int = 2) -> str:
    base = resolve_workspace_path(path)

    if not base.exists() or not base.is_dir():
        raise ValueError("list_files requires an existing directory.")

    requested_depth = int(depth)

    if requested_depth < 0 or requested_depth > 5:
        raise ValueError("depth must be between 0 and 5.")

    base_depth = len(base.parts)
    results: list[str] = []
    truncated = False

    for current_root, directories, files in os.walk(base):
        current = Path(current_root)
        current_depth = len(current.parts) - base_depth
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in IGNORED_DIRECTORIES
            and not _is_link(current / directory)
        )

        for directory in directories:
            results.append(f"DIR  {(current / directory).relative_to(WORKSPACE)}")

        for filename in sorted(files):
            if filename.endswith(".class"):
                continue

            results.append(f"FILE {(current / filename).relative_to(WORKSPACE)}")

        if len(results) >= MAX_LIST_RESULTS:
            results = results[:MAX_LIST_RESULTS]
            truncated = True
            break

        if current_depth >= requested_depth:
            directories[:] = []

    if truncated:
        results.append("TRUNCATED: Use a narrower path or smaller depth.")

    return f"Workspace: {WORKSPACE}\n" + ("\n".join(results) or "(empty)")


def search(query: str, path: str = ".") -> str:
    if not isinstance(query, str) or not query:
        raise ValueError("search requires a non-empty query.")

    base = resolve_workspace_path(path)

    if not base.exists():
        raise ValueError("Search path does not exist.")

    candidates = [base] if base.is_file() else [
        item for item in base.rglob("*")
        if item.is_file()
        and not any(part in IGNORED_DIRECTORIES for part in item.parts)
        and item.suffix != ".class"
    ]
    case_sensitive = any(character.isupper() for character in query)
    needle = query if case_sensitive else query.casefold()
    results: list[str] = []

    for candidate in sorted(candidates):
        relative = str(candidate.relative_to(WORKSPACE))
        compared_name = relative if case_sensitive else relative.casefold()

        if needle in compared_name:
            results.append(f"PATH {relative}")

        try:
            content = candidate.read_bytes()

            if len(content) > 1_000_000:
                continue

            text = _decode_text(content, candidate)
        except (OSError, ValueError):
            continue

        for line_number, line in enumerate(text.splitlines(), 1):
            compared_line = line if case_sensitive else line.casefold()

            if needle in compared_line:
                snippet = line.strip()
                results.append(f"TEXT {relative}:{line_number}: {snippet[:240]}")

                if len(results) >= MAX_SEARCH_RESULTS:
                    results.append("TRUNCATED: Use a narrower query or path.")
                    return "\n".join(results)

    return "\n".join(results) or "NO_MATCHES"


def read_file(
    path: str,
    state: ToolState,
    start_line: int = 1,
    end_line: int = 200,
) -> str:
    target = resolve_workspace_path(path)
    content = _read_bytes(target)
    text = _decode_text(content, target)
    lines = text.splitlines()
    start = int(start_line)
    end = int(end_line)
    state.known_versions[target] = _hash_bytes(content)

    if not lines:
        return f"FILE: {target.relative_to(WORKSPACE)}\nLINES: 0 / 0\n\n(empty)"

    if start < 1 or end < start:
        raise ValueError("Use 1-based lines with end_line greater than or equal to start_line.")

    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1

    if start > max(1, len(lines)):
        raise ValueError(f"start_line is beyond EOF; file has {len(lines)} lines.")

    selected = "\n".join(lines[start - 1:min(end, len(lines))])
    truncated_by_characters = len(selected) > MAX_READ_CHARS
    selected = selected[:MAX_READ_CHARS]
    actual_end = min(end, len(lines))
    suffix = "\nTRUNCATED: Read a smaller line range." if truncated_by_characters else ""

    return (
        f"FILE: {target.relative_to(WORKSPACE)}\n"
        f"LINES: {start}-{actual_end} / {len(lines)}\n\n"
        f"{selected}{suffix}"
    )


def write_file(path: str, content: str, state: ToolState) -> str:
    target = resolve_workspace_path(path)
    _validate_mutation_path(target)

    if not isinstance(content, str):
        raise ValueError("write_file content must be text.")

    if len(content) > MAX_EDIT_CHARS:
        raise ValueError("Content is too large for one edit.")

    if re.search(
        r"(?im)^\s*(?://|#).*?(?:existing content|rest of .*unchanged)",
        content,
    ):
        raise ValueError("Complete content is required; placeholders are forbidden.")

    original = target.read_bytes() if target.exists() and target.is_file() else None

    if target.exists():
        if not target.is_file():
            raise ValueError("write_file target is not a file.")

        _decode_text(original or b"", target)
        _require_current_version(target, state)

    encoded = _encode_text(content, original)

    if original == encoded:
        state.known_versions[target] = _hash_bytes(encoded)
        return "NO_CHANGE: File already has the requested content."

    _apply_file_changes("write_file", {target: encoded}, state)
    action = "Updated" if original is not None else "Created"
    return f"{action} {target.relative_to(WORKSPACE)} ({len(content):,} characters)"


def patch_file(
    path: str,
    old_text: str,
    new_text: str,
    state: ToolState,
) -> str:
    target = resolve_workspace_path(path)
    _validate_mutation_path(target)
    _require_current_version(target, state)
    original_bytes = _read_bytes(target)
    original = _decode_text(original_bytes, target)

    if not old_text:
        raise ValueError("old_text cannot be empty.")

    if len(old_text) + len(new_text) > MAX_EDIT_CHARS:
        raise ValueError("Patch is too large; use write_file for a complete rewrite.")

    count = original.count(old_text)

    if count != 1:
        raise ValueError(
            f"PATCH_MISMATCH: old_text matched {count} times. Read the file and provide one exact match."
        )

    updated = original.replace(old_text, new_text, 1)

    if updated == original:
        return "NO_CHANGE: Patch would not change the file."

    encoded = _encode_text(updated, original_bytes)
    _apply_file_changes("patch_file", {target: encoded}, state)
    return f"Patched {target.relative_to(WORKSPACE)}"


def move_path(source: str, destination: str, state: ToolState) -> str:
    source_path = resolve_workspace_path(source)
    destination_path = resolve_workspace_path(destination)
    _validate_mutation_path(source_path)
    _validate_mutation_path(destination_path)

    if not source_path.exists():
        raise ValueError("Source path does not exist.")

    if destination_path.exists():
        raise ValueError("Destination already exists; move_path never overwrites.")

    if source_path.is_dir() and any(source_path.iterdir()):
        raise ValueError("Only empty directories may be moved safely in this experiment.")

    if not source_path.is_file() and not source_path.is_dir():
        raise ValueError("Unsupported source type.")

    _approve(f"move {source_path.relative_to(WORKSPACE)} to {destination_path.relative_to(WORKSPACE)}")

    if source_path.is_file():
        content = source_path.read_bytes()
        _apply_file_changes(
            "move_path",
            {destination_path: content, source_path: None},
            state,
        )
    else:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.rename(destination_path)
        state.undo_stack.append(UndoEntry(
            "move_path",
            directory_move=(source_path, destination_path),
        ))
        state.mutation_revision += 1

    return f"Moved {source_path.relative_to(WORKSPACE)} to {destination_path.relative_to(WORKSPACE)}"


def delete_path(path: str, state: ToolState) -> str:
    target = resolve_workspace_path(path)
    _validate_mutation_path(target)

    if not target.exists():
        return "NO_CHANGE: Path is already absent."

    _approve(f"delete {target.relative_to(WORKSPACE)}")

    if target.is_file():
        _apply_file_changes("delete_path", {target: None}, state)
    elif target.is_dir():
        if any(target.iterdir()):
            raise ValueError("Recursive deletion is blocked; the directory is not empty.")

        target.rmdir()
        state.undo_stack.append(UndoEntry("delete_path", deleted_directory=target))
        state.mutation_revision += 1
    else:
        raise ValueError("Unsupported path type.")

    return f"Deleted {target.relative_to(WORKSPACE)}"


def undo_last_edit(state: ToolState) -> str:
    if not state.undo_stack:
        raise ValueError("There is no edit to undo.")

    entry = state.undo_stack[-1]

    for target, expected in entry.after_files.items():
        if _current_hash(target) != expected:
            raise ValueError("UNDO_BLOCKED: A file changed after the agent edit.")

    if entry.directory_move:
        source, destination = entry.directory_move

        if source.exists() or not destination.is_dir() or any(destination.iterdir()):
            raise ValueError("UNDO_BLOCKED: The moved directory changed.")

        destination.rename(source)
    elif entry.deleted_directory:
        if entry.deleted_directory.exists():
            raise ValueError("UNDO_BLOCKED: The deleted directory path is now occupied.")

        entry.deleted_directory.mkdir(parents=True)
    else:
        for target, original in entry.before_files.items():
            if original is None:
                if target.exists():
                    target.unlink()
                state.known_versions.pop(target, None)
            else:
                _atomic_write(target, original)
                state.known_versions[target] = _hash_bytes(original)

    state.undo_stack.pop()
    state.mutation_revision += 1
    return f"Undid {entry.label}"


def _run_process(command: list[str], timeout: int = 120) -> tuple[str, int]:
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        creationflags=creation_flags,
    )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return "\n".join(part.strip() for part in (stdout, stderr) if part.strip()), 124
    except KeyboardInterrupt:
        process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

        raise

    output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    return output, process.returncode


def run_check(kind: str, state: ToolState, task: str) -> str:
    normalized_kind = kind.strip().lower() if kind else "auto"

    if normalized_kind not in {"auto", "build", "test", "lint", "typecheck"}:
        raise ValueError("kind must be auto, build, test, lint, or typecheck.")

    if normalized_kind == "test" and not re.search(r"\btests?\b", task, re.IGNORECASE):
        raise PermissionError("Tests were not explicitly requested by the user.")

    java_files = sorted(
        path for path in WORKSPACE.rglob("*.java")
        if not any(part in IGNORED_DIRECTORIES for part in path.parts)
    )
    python_files = sorted(
        path for path in WORKSPACE.rglob("*.py")
        if not any(part in IGNORED_DIRECTORIES for part in path.parts)
    )

    if normalized_kind in {"lint", "typecheck", "test"}:
        raise ValueError(f"CHECK_NOT_AVAILABLE: No {normalized_kind} command was detected.")

    if java_files:
        javac = shutil.which("javac")

        if javac is None:
            raise ValueError("CHECK_NOT_AVAILABLE: javac was not found on PATH.")

        build_directory = WORKSPACE / ".build"

        if build_directory.exists():
            shutil.rmtree(build_directory)

        build_directory.mkdir(parents=True, exist_ok=True)
        command = [
            javac,
            *(
                ["-source", "8", "-target", "8"]
                if re.search(r"\b(?:java|jdk)\s*8\b", task, re.IGNORECASE)
                else []
            ),
            "-d",
            str(build_directory),
            *[str(path) for path in java_files],
        ]
        output, code = _run_process(command)
        result = (
            f"COMMAND: {' '.join(command)}\n"
            f"EXIT_CODE: {code}\n\n"
            f"{output or 'Java compilation succeeded.'}"
        )
    elif python_files:
        failures: list[str] = []

        for path in python_files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except Exception as error:
                failures.append(f"{path.relative_to(WORKSPACE)}: {error}")

        code = 1 if failures else 0
        result = (
            f"CHECK: Python syntax\nEXIT_CODE: {code}\n\n"
            + ("\n".join(failures) if failures else "Python syntax check succeeded.")
        )
    else:
        raise ValueError("CHECK_NOT_AVAILABLE: No supported project was detected.")

    if code != 0:
        raise ValueError(result)

    state.verified_revision = state.mutation_revision
    return result


# ============================================================
# CONTROLLER
# ============================================================


def execute_worker_action(
    action: dict[str, Any],
    state: ToolState,
    task: str,
) -> tuple[bool, str]:
    tool = action.get("tool")
    args = action.get("args", {})

    if not isinstance(args, dict):
        return False, "args must be an object."

    try:
        if tool == "list_files":
            extra = set(args) - {"path", "depth"}

            if extra:
                raise ValueError(f"list_files does not accept: {', '.join(sorted(extra))}.")

            return True, list_files(
                args.get("path", "."),
                args.get("depth", 2),
            )

        if tool == "search":
            extra = set(args) - {"query", "path"}

            if extra:
                raise ValueError(f"search does not accept: {', '.join(sorted(extra))}.")

            return True, search(args.get("query"), args.get("path", "."))

        if tool == "read_file":
            path = args.get("path")

            if not isinstance(path, str) or not path:
                raise ValueError("read_file requires path.")

            extra = set(args) - {"path", "start_line", "end_line"}

            if extra:
                raise ValueError(f"read_file does not accept: {', '.join(sorted(extra))}.")

            return True, read_file(
                path,
                state,
                args.get("start_line", 1),
                args.get("end_line", 200),
            )

        if tool == "write_file":
            path = args.get("path")
            content = args.get("content")
            if not isinstance(path, str) or not path:
                raise ValueError("write_file requires path.")

            if not isinstance(content, str):
                raise ValueError("write_file content must be text.")

            extra = set(args) - {"path", "content"}

            if extra:
                raise ValueError(f"write_file does not accept: {', '.join(sorted(extra))}.")

            return True, write_file(path, content, state)

        if tool == "patch_file":
            extra = set(args) - {"path", "old_text", "new_text"}

            if extra:
                raise ValueError(f"patch_file does not accept: {', '.join(sorted(extra))}.")

            if not all(isinstance(args.get(name), str) for name in (
                "path",
                "old_text",
                "new_text",
            )):
                raise ValueError("patch_file requires path, old_text, and new_text.")

            return True, patch_file(
                args["path"],
                args["old_text"],
                args["new_text"],
                state,
            )

        if tool == "move_path":
            if set(args) != {"source", "destination"}:
                raise ValueError("move_path requires source and destination only.")

            return True, move_path(args["source"], args["destination"], state)

        if tool == "delete_path":
            if set(args) != {"path"}:
                raise ValueError("delete_path requires path only.")

            return True, delete_path(args["path"], state)

        if tool == "undo_last_edit":
            if args:
                raise ValueError("undo_last_edit takes no arguments.")

            return True, undo_last_edit(state)

        if tool == "run_check":
            extra = set(args) - {"kind"}

            if extra:
                raise ValueError(f"run_check does not accept: {', '.join(sorted(extra))}.")

            return True, run_check(str(args.get("kind", "auto")), state, task)

        return False, f"Unknown tool: {tool}"

    except Exception as error:
        return (
            False,
            f"{type(error).__name__}: {error}",
        )
