from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

try:
    from .protocol import ToolCall
    from .storage import DataPaths, EventStore, utc_now
except ImportError:  # Direct execution through main.py.
    from protocol import ToolCall
    from storage import DataPaths, EventStore, utc_now


TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".json", ".jsx", ".kt", ".md", ".php", ".properties",
    ".py", ".rb", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt",
    ".xml", ".yaml", ".yml",
}
SOURCE_EXTENSIONS = TEXT_EXTENSIONS - {".md", ".txt"}
EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".agent_data", "__pycache__", "node_modules",
    "models", "myllm_logs", "model_chat_logs", ".idea", ".vscode",
    ".ssh", ".aws", ".azure", ".gnupg",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "secrets.json",
}
SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".keystore", ".jks"}
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul", "clock$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
WINDOWS_INVALID_CHARS = set('<>:"|?*')
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOOL_OUTPUT = 64 * 1024
MAX_LIST_ENTRIES = 1000
MAX_SEARCH_MATCHES = 200
MAX_READ_LINES = 500


@dataclass(frozen=True)
class ToolSpec:
    required: frozenset[str]
    optional: frozenset[str]


TOOL_SPECS = {
    "list_files": ToolSpec(frozenset(), frozenset({"path", "depth"})),
    "search": ToolSpec(frozenset({"query"}), frozenset({"path"})),
    "read_file": ToolSpec(frozenset({"path"}), frozenset({"start_line", "end_line"})),
    "edit_file": ToolSpec(
        frozenset({"operation", "path"}),
        frozenset({"content", "old_text", "new_text"}),
    ),
    "run_check": ToolSpec(frozenset({"kind"}), frozenset()),
    "finish": ToolSpec(frozenset({"message"}), frozenset()),
}


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""


@dataclass
class ToolResult:
    status: str
    content: str
    changed_paths: list[str] = field(default_factory=list)
    checkpoint_id: str | None = None
    verification: str = "NOT_RUN"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckPlan:
    kind: str
    mode: str
    argv: tuple[str, ...] = ()
    description: str = ""
    safe_auto: bool = False


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bounded(text: str, maximum: int = MAX_TOOL_OUTPUT) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= maximum:
        return text
    half = maximum // 2
    return (
        encoded[:half].decode("utf-8", errors="replace")
        + "\n<tool output truncated>\n"
        + encoded[-half:].decode("utf-8", errors="replace")
    )


def sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in SENSITIVE_NAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in SENSITIVE_SUFFIXES
    )


class Workspace:
    def __init__(self, root: Path, protected_roots: tuple[Path, ...] = ()) -> None:
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"Workspace is not a directory: {self.root}")
        self.protected_roots = tuple(path.resolve() for path in protected_roots)

    def resolve(self, raw: str, *, file_required: bool = False, allow_missing: bool = False) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("A non-empty workspace-relative path is required.")
        supplied = Path(raw)
        if supplied.is_absolute():
            raise ValueError("Absolute paths are not allowed.")
        for part in supplied.parts:
            trimmed = part.rstrip(" .").casefold()
            base = trimmed.split(".", 1)[0]
            if (
                not trimmed
                or base in WINDOWS_RESERVED_NAMES
                or any(character in WINDOWS_INVALID_CHARS or ord(character) < 32 for character in part)
                or part != part.rstrip(" .")
            ):
                raise ValueError(f"Path contains an invalid or reserved name: {part!r}.")

        candidate = (self.root / supplied).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Path resolves outside the workspace.") from error

        for protected in self.protected_roots:
            try:
                candidate.relative_to(protected)
            except ValueError:
                continue
            raise ValueError("Agent-owned storage is not available to model tools.")

        relative_parts = candidate.relative_to(self.root).parts
        if any(part.casefold() in EXCLUDED_PARTS for part in relative_parts):
            raise ValueError("Path belongs to an excluded workspace area.")
        if sensitive_path(candidate):
            raise ValueError("Sensitive credential files are not available to model tools.")
        if file_required and candidate.exists() and not candidate.is_file():
            raise ValueError("Path must identify a file.")
        if not allow_missing and not candidate.exists():
            raise ValueError(f"Path does not exist: {self.relative(candidate)}")
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix()

    def visible(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=False)
            relative = resolved.relative_to(self.root)
        except ValueError:
            return False
        for protected in self.protected_roots:
            try:
                resolved.relative_to(protected)
                return False
            except ValueError:
                continue
        return not any(part.casefold() in EXCLUDED_PARTS for part in relative.parts)


class ToolRegistry:
    def __init__(self, workspace: Workspace, data_paths: DataPaths, store: EventStore) -> None:
        self.workspace = workspace
        self.paths = data_paths
        self.store = store

    def validate(self, call: ToolCall) -> ValidationResult:
        spec = TOOL_SPECS.get(call.name)
        if spec is None:
            return ValidationResult(False, f"Unknown tool: {call.name}.")
        keys = set(call.arguments)
        missing = spec.required - keys
        extra = keys - spec.required - spec.optional
        if missing:
            return ValidationResult(False, f"Missing required argument(s): {', '.join(sorted(missing))}.")
        if extra:
            return ValidationResult(False, f"Unknown argument(s): {', '.join(sorted(extra))}.")

        if call.name == "list_files":
            if "path" in call.arguments and not isinstance(call.arguments["path"], str):
                return ValidationResult(False, "list_files path must be text.")
            if "depth" in call.arguments and (
                not isinstance(call.arguments["depth"], int) or isinstance(call.arguments["depth"], bool)
            ):
                return ValidationResult(False, "list_files depth must be an integer.")
        if call.name == "search":
            if not isinstance(call.arguments["query"], str):
                return ValidationResult(False, "search query must be text.")
            if "path" in call.arguments and not isinstance(call.arguments["path"], str):
                return ValidationResult(False, "search path must be text.")
        if call.name == "read_file":
            if not isinstance(call.arguments["path"], str):
                return ValidationResult(False, "read_file path must be text.")
            for line_field in ("start_line", "end_line"):
                if line_field in call.arguments and (
                    not isinstance(call.arguments[line_field], int)
                    or isinstance(call.arguments[line_field], bool)
                ):
                    return ValidationResult(False, f"{line_field} must be an integer.")

        if call.name == "edit_file":
            operation = call.arguments.get("operation")
            if not isinstance(operation, str):
                return ValidationResult(False, "edit_file operation must be text.")
            if not isinstance(call.arguments.get("path"), str):
                return ValidationResult(False, "edit_file path must be text.")
            allowed_fields = {
                "create": {"operation", "path", "content"},
                "replace": {"operation", "path", "content"},
                "patch": {"operation", "path", "old_text", "new_text"},
            }
            if operation not in allowed_fields:
                return ValidationResult(False, "edit_file operation must be create, replace, or patch.")
            expected = allowed_fields[operation]
            if keys != expected:
                missing = expected - keys
                forbidden = keys - expected
                details = []
                if missing:
                    details.append("missing " + ", ".join(sorted(missing)))
                if forbidden:
                    details.append("forbidden " + ", ".join(sorted(forbidden)))
                return ValidationResult(False, f"Invalid {operation} fields: {'; '.join(details)}.")
            for field_name in expected - {"operation", "path"}:
                if not isinstance(call.arguments[field_name], str):
                    return ValidationResult(False, f"{field_name} must be text.")

        if call.name == "run_check":
            if not isinstance(call.arguments["kind"], str):
                return ValidationResult(False, "run_check kind must be text.")
            if call.arguments["kind"] not in {"build", "test", "lint", "typecheck"}:
                return ValidationResult(False, "run_check kind must be build, test, lint, or typecheck.")
        if call.name == "finish" and not isinstance(call.arguments["message"], str):
            return ValidationResult(False, "finish message must be text.")
        return ValidationResult(True)

    def risk(self, call: ToolCall) -> str:
        if call.name in {"list_files", "search", "read_file", "finish"}:
            return "low"
        if call.name == "run_check":
            plan = self.check_plan(str(call.arguments["kind"]))
            return "low" if plan and plan.safe_auto else "medium"
        return "medium"

    def preview(self, call: ToolCall) -> str:
        if call.name == "edit_file":
            return self._edit_preview(call.arguments)
        if call.name == "run_check":
            plan = self.check_plan(str(call.arguments["kind"]))
            if not plan:
                return "No trusted built-in check is available."
            command = " ".join(plan.argv) if plan.argv else plan.description
            return f"Check: {plan.kind}\nMode: {plan.mode}\nCommand: {command}\nCWD: {self.workspace.root}"
        return json.dumps(call.arguments, ensure_ascii=False)

    def action_state(self, call: ToolCall) -> dict[str, Any]:
        """Capture the exact mutable state to which approval/execution is bound."""
        if call.name == "edit_file":
            path = self.workspace.resolve(str(call.arguments["path"]), file_required=True, allow_missing=True)
            current = sha256_file(path) if path.exists() else None
            return {"path": self.workspace.relative(path), "before_hash": current}
        if call.name == "run_check":
            manifest = self._source_manifest()
            encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            plan = self.check_plan(str(call.arguments["kind"]))
            return {
                "source_manifest_hash": sha256_bytes(encoded),
                "source_file_count": len(manifest),
                "check_argv": list(plan.argv) if plan else None,
                "check_mode": plan.mode if plan else None,
            }
        return {}

    def execute(
        self,
        call: ToolCall,
        cancel: Event,
        run_id: str,
        dry_run: bool = False,
        expected_state: dict[str, Any] | None = None,
        step: int = 0,
    ) -> ToolResult:
        handlers = {
            "list_files": self._list_files,
            "search": self._search,
            "read_file": self._read_file,
            "edit_file": self._edit_file,
            "run_check": self._run_check,
        }
        if call.name == "finish":
            return ToolResult("OK", str(call.arguments["message"]))
        if call.name not in handlers:
            return ToolResult("FAILED", f"Unknown tool: {call.name}")
        return handlers[call.name](call.arguments, cancel, run_id, dry_run, expected_state or {}, step)

    def _list_files(self, args: dict[str, Any], *_: Any) -> ToolResult:
        depth = args.get("depth", 2)
        if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 5:
            return ToolResult("REJECTED", "depth must be an integer from 1 to 5.")
        try:
            root = self.workspace.resolve(str(args.get("path", ".")))
        except ValueError as error:
            return ToolResult("REJECTED", str(error))
        if not root.is_dir():
            return ToolResult("REJECTED", "list_files path must be a directory.")

        entries: list[str] = []
        root_depth = len(root.parts)
        for current, directories, files in os.walk(root):
            current_path = Path(current)
            level = len(current_path.parts) - root_depth
            directories[:] = sorted(
                directory for directory in directories
                if directory.casefold() not in EXCLUDED_PARTS
                and self.workspace.visible(current_path / directory)
            )
            if level >= depth:
                directories[:] = []
            for name in sorted(directories):
                entries.append(self.workspace.relative(current_path / name) + "/")
            for name in sorted(files):
                path = current_path / name
                if self.workspace.visible(path) and not self._sensitive(path):
                    entries.append(self.workspace.relative(path))
                if len(entries) >= MAX_LIST_ENTRIES:
                    return ToolResult("OK", "\n".join(entries) + "\n<list truncated>")
        return ToolResult("OK", "\n".join(entries) if entries else "<no visible files>")

    def _search(self, args: dict[str, Any], *_: Any) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query:
            return ToolResult("REJECTED", "query must be non-empty text.")
        if len(query) > 1000:
            return ToolResult("REJECTED", "query must be at most 1,000 characters.")
        try:
            root = self.workspace.resolve(str(args.get("path", ".")))
        except ValueError as error:
            return ToolResult("REJECTED", str(error))
        candidates = [root] if root.is_file() else (path for path in root.rglob("*") if path.is_file())
        matches: list[str] = []
        scanned = 0
        for path in candidates:
            if not self.workspace.visible(path) or self._sensitive(path) or path.suffix.casefold() not in TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            scanned += 1
            if scanned > 10_000:
                break
            for number, line in enumerate(text.splitlines(), 1):
                if query in line:
                    snippet = line.strip()
                    if len(snippet) > 240:
                        snippet = snippet[:240] + "…"
                    matches.append(f"{self.workspace.relative(path)}:{number}: {snippet}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        return ToolResult("OK", "\n".join(matches) + "\n<search truncated>")
        return ToolResult("OK", "\n".join(matches) if matches else "<no matches>")

    def _read_file(self, args: dict[str, Any], *_: Any) -> ToolResult:
        try:
            path = self.workspace.resolve(str(args["path"]), file_required=True)
        except ValueError as error:
            return ToolResult("REJECTED", str(error))
        start = args.get("start_line", 1)
        end = args.get("end_line", 200)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (start, end)):
            return ToolResult("REJECTED", "start_line and end_line must be integers.")
        if start < 1 or end < start or end - start + 1 > MAX_READ_LINES:
            return ToolResult("REJECTED", f"Read at most {MAX_READ_LINES} lines with a valid positive range.")
        if path.stat().st_size > MAX_FILE_BYTES:
            return ToolResult("REJECTED", f"File exceeds {MAX_FILE_BYTES} bytes.")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeError:
            return ToolResult("REJECTED", "Only UTF-8 text files can be read.")
        selected = lines[start - 1:end]
        content = "\n".join(f"{start + index}: {line}" for index, line in enumerate(selected))
        return ToolResult("OK", bounded(content or "<empty range>"))

    def _edit_preview(self, args: dict[str, Any]) -> str:
        operation = str(args["operation"])
        try:
            path = self.workspace.resolve(str(args["path"]), file_required=True, allow_missing=True)
        except ValueError as error:
            return f"REJECTED: {error}"
        old = ""
        if path.exists():
            if path.stat().st_size > MAX_FILE_BYTES:
                return "REJECTED: existing file is too large for a safe edit."
            try:
                old = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return "REJECTED: existing file is not readable UTF-8 text."
        if operation == "create":
            if path.exists():
                return "REJECTED: create never overwrites an existing path."
            new = str(args["content"])
        elif operation == "replace":
            if not path.is_file():
                return "REJECTED: replace requires an existing file."
            new = str(args["content"])
        else:
            if not path.is_file():
                return "REJECTED: patch requires an existing file."
            search = str(args["old_text"])
            count = old.count(search)
            if not search or count != 1:
                return f"REJECTED: patch old_text must match exactly once; matches={count}."
            new = old.replace(search, str(args["new_text"]), 1)
        if len(new.encode("utf-8")) > MAX_FILE_BYTES:
            return "REJECTED: new content is too large for a single edit."
        diff = difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"a/{self.workspace.relative(path)}", tofile=f"b/{self.workspace.relative(path)}",
            lineterm="",
        )
        return bounded("\n".join(diff) or "<content unchanged>")

    def _edit_file(
        self,
        args: dict[str, Any],
        _cancel: Event,
        run_id: str,
        dry_run: bool,
        expected_state: dict[str, Any],
        step: int,
    ) -> ToolResult:
        preview = self._edit_preview(args)
        if preview.startswith("REJECTED:"):
            return ToolResult("REJECTED", preview.removeprefix("REJECTED: "))
        if dry_run:
            return ToolResult("DRY_RUN", preview, verification="NOT_RUN")

        operation = str(args["operation"])
        path = self.workspace.resolve(str(args["path"]), file_required=True, allow_missing=True)
        before = path.read_bytes() if path.exists() else b""
        current_hash = sha256_bytes(before) if path.exists() else None
        if current_hash != expected_state.get("before_hash"):
            return ToolResult("CONFLICT", "File changed after preview/approval; the edit was not applied.")
        if len(before) > MAX_FILE_BYTES:
            return ToolResult("REJECTED", "Existing file is too large for a safe edit.")
        old = before.decode("utf-8") if before else ""
        if operation in {"create", "replace"}:
            new = str(args["content"])
        else:
            new = old.replace(str(args["old_text"]), str(args["new_text"]), 1)
        after = new.encode("utf-8")
        if len(after) > MAX_FILE_BYTES:
            return ToolResult("REJECTED", "New content is too large for a single edit.")
        if before == after:
            return ToolResult("REJECTED", "Edit would not change the file.")

        checkpoint_id = f"cp-{uuid.uuid4().hex[:16]}"
        checkpoint_dir = self.paths.snapshots / run_id / checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        backup = checkpoint_dir / "before.bin"
        if path.exists():
            backup.write_bytes(before)
        manifest = {
            "checkpoint_id": checkpoint_id,
            "run_id": run_id,
            "created_at": utc_now(),
            "path": self.workspace.relative(path),
            "existed_before": path.exists(),
            "before_hash": sha256_bytes(before) if path.exists() else None,
            "intended_hash": sha256_bytes(after),
            "backup": backup.name if path.exists() else None,
            "write_started": False,
            "write_completed": False,
            "verification_status": "NOT_RUN",
        }
        manifest_path = checkpoint_dir / "manifest.json"
        self._write_manifest(manifest_path, manifest)
        self.store.append("CheckpointCreated", manifest, step)

        path.parent.mkdir(parents=True, exist_ok=True)
        manifest["write_started"] = True
        self._write_manifest(manifest_path, manifest)
        try:
            self._atomic_write(path, after, create=(operation == "create"), expected_before=expected_state.get("before_hash"))
        except Exception as error:
            return ToolResult("FAILED", f"Atomic write failed: {error}", checkpoint_id=checkpoint_id)

        actual = sha256_bytes(path.read_bytes())
        if actual != manifest["intended_hash"]:
            return ToolResult(
                "CONFLICT",
                "Post-write content changed before verification; automatic rollback was refused to protect concurrent work.",
                checkpoint_id=checkpoint_id,
            )

        manifest["write_completed"] = True
        manifest["verification_status"] = "CONTENT_VERIFIED"
        manifest["after_hash"] = actual
        self._write_manifest(manifest_path, manifest)
        relative = self.workspace.relative(path)
        self.store.append("EditApplied", {**manifest, "diff": preview}, step)
        return ToolResult(
            "OK", preview, changed_paths=[relative], checkpoint_id=checkpoint_id,
            verification="CONTENT_VERIFIED", metadata={"after_hash": actual},
        )

    def _atomic_write(self, path: Path, content: bytes, *, create: bool, expected_before: str | None) -> None:
        current_hash = sha256_file(path) if path.exists() else None
        if current_hash != expected_before:
            raise RuntimeError("file changed after preview; approval is stale")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        guard: Path | None = None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                shutil.copymode(path, temporary)
            latest_hash = sha256_file(path) if path.exists() else None
            if latest_hash != expected_before:
                raise RuntimeError("file changed while the atomic replacement was being prepared")
            if create:
                # Hard-link creation is atomic and fails when the destination exists.
                os.link(temporary, path)
                temporary.unlink()
                return

            # Claim the old pathname before installing the replacement. The
            # no-overwrite hard link below prevents a concurrent recreation from
            # being overwritten between the final hash and installation.
            guard = path.parent / f".{path.name}.myllm-guard-{uuid.uuid4().hex}"
            last_error: OSError | None = None
            for attempt in range(3):
                try:
                    os.rename(path, guard)
                    break
                except OSError as error:
                    last_error = error
                    time.sleep(0.05 * (attempt + 1))
            else:
                raise last_error or OSError("could not claim the original path")

            if sha256_file(guard) != expected_before:
                self._restore_guard_without_overwrite(path, guard)
                raise RuntimeError("file changed before it could be claimed; replacement aborted")
            try:
                os.link(temporary, path)
            except OSError as error:
                restored = self._restore_guard_without_overwrite(path, guard)
                guard_note = "" if restored else f" Original remains at {guard}."
                raise RuntimeError(f"replacement path was concurrently recreated.{guard_note}") from error
            temporary.unlink()
            guard.unlink()
            guard = None
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _restore_guard_without_overwrite(path: Path, guard: Path) -> bool:
        if path.exists():
            return False
        try:
            os.link(guard, path)
            guard.unlink()
            return True
        except OSError:
            return False

    @staticmethod
    def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def undo(self, run_id: str) -> ToolResult:
        root = self.paths.snapshots / run_id
        if not root.exists():
            return ToolResult("REJECTED", "No checkpoint exists for this run.")
        candidates = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
        for directory in candidates:
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("undone") or not manifest.get("write_completed") or not manifest.get("after_hash"):
                continue
            path = self.workspace.resolve(manifest["path"], file_required=True, allow_missing=True)
            current = sha256_bytes(path.read_bytes()) if path.exists() else None
            if current != manifest.get("after_hash"):
                return ToolResult("CONFLICT", "Current file differs from the agent checkpoint; undo refused.")
            if manifest["existed_before"]:
                backup = (directory / str(manifest["backup"])).read_bytes()
                self._atomic_write(path, backup, create=False, expected_before=current)
            elif path.exists():
                # Claim the exact path entry before deleting it. A process may create a
                # new file at the original name after the rename; that new file is never
                # unlinked by this undo operation.
                guard = path.with_name(f".{path.name}.myllm-undo-{uuid.uuid4().hex}.guard")
                last_error: OSError | None = None
                for attempt in range(3):
                    try:
                        os.rename(path, guard)
                        break
                    except OSError as error:
                        last_error = error
                        time.sleep(0.05 * (attempt + 1))
                else:
                    return ToolResult("CONFLICT", f"Could not claim the created file for undo: {last_error}")
                if sha256_bytes(guard.read_bytes()) != current:
                    self._restore_guard_without_overwrite(path, guard)
                    return ToolResult("CONFLICT", "File changed during undo; deletion was refused.")
                guard.unlink()
            manifest["undone"] = True
            manifest["undone_at"] = utc_now()
            self._write_manifest(manifest_path, manifest)
            self.store.append("EditRolledBack", {"checkpoint_id": manifest["checkpoint_id"], "reason": "user undo"})
            return ToolResult("OK", f"Restored {manifest['path']}.", changed_paths=[manifest["path"]])
        return ToolResult("REJECTED", "No active checkpoint is available to undo.")

    def check_plan(self, kind: str) -> CheckPlan | None:
        python_files = sorted(path for path in self.workspace.root.rglob("*.py") if self.workspace.visible(path))
        java_files = sorted(path for path in self.workspace.root.rglob("*.java") if self.workspace.visible(path))
        if kind == "build" and python_files and java_files:
            return None
        if kind in {"build", "lint"} and python_files:
            return CheckPlan(kind, "python_ast", description="Parse visible Python files with ast.parse", safe_auto=True)
        if kind == "test" and python_files and not java_files:
            return CheckPlan(kind, "subprocess", (sys.executable, "-m", "unittest", "discover"), "Python unittest discovery", False)
        if kind == "build" and java_files:
            java_home = os.environ.get("JAVA_HOME")
            javac = Path(java_home) / "bin" / ("javac.exe" if os.name == "nt" else "javac") if java_home else None
            if not javac or not javac.is_file():
                return None
            return CheckPlan(
                kind,
                "subprocess",
                (str(javac.resolve()), "-proc:none", "-d", "<agent-temp>", "@<agent-argfile>"),
                f"Java compile ({len(java_files)} source files)",
                False,
            )
        return None

    def _run_check(
        self,
        args: dict[str, Any],
        cancel: Event,
        _run_id: str,
        dry_run: bool,
        expected_state: dict[str, Any],
        _step: int,
    ) -> ToolResult:
        plan = self.check_plan(str(args["kind"]))
        if plan is None:
            return ToolResult("UNAVAILABLE", "No trusted built-in check profile is available.", verification="UNAVAILABLE")
        if list(plan.argv) != expected_state.get("check_argv") or plan.mode != expected_state.get("check_mode"):
            return ToolResult("CONFLICT", "The trusted check profile changed after preview/approval; the check was not started.")
        if dry_run:
            return ToolResult("DRY_RUN", self.preview(ToolCall("run_check", args, "internal", "")), verification="NOT_RUN")
        before = self._source_manifest()
        encoded = json.dumps(before, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if sha256_bytes(encoded) != expected_state.get("source_manifest_hash"):
            return ToolResult("CONFLICT", "Source files changed after check preview/approval; the check was not started.")
        if "<agent-temp>" in plan.argv:
            output = self.paths.temporary / f"javac-{uuid.uuid4().hex[:10]}"
            output.mkdir(parents=True, exist_ok=False)
            source_file = output / "sources.args"
            java_files = sorted(path for path in self.workspace.root.rglob("*.java") if self.workspace.visible(path))
            with source_file.open("x", encoding="utf-8", newline="\n") as handle:
                for path in java_files:
                    handle.write('"' + path.resolve().as_posix().replace('"', '\\"') + '"\n')
                handle.flush()
                os.fsync(handle.fileno())
            plan = CheckPlan(
                plan.kind,
                plan.mode,
                tuple(
                    str(output) if value == "<agent-temp>"
                    else "@" + str(source_file) if value == "@<agent-argfile>"
                    else value
                    for value in plan.argv
                ),
                plan.description,
                plan.safe_auto,
            )
        if plan.mode == "python_ast":
            failures: list[str] = []
            cancelled = False
            for path in self.workspace.root.rglob("*.py"):
                if cancel.is_set():
                    cancelled = True
                    break
                if not self.workspace.visible(path):
                    continue
                try:
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except (OSError, UnicodeError, SyntaxError) as error:
                    failures.append(f"{self.workspace.relative(path)}: {error}")
            after = self._source_manifest()
            changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
            if changed:
                return ToolResult(
                    "CONFLICT",
                    "Source files changed during Python syntax validation:\n" + "\n".join(changed),
                    changed_paths=changed,
                    verification="FAILED",
                )
            if cancelled:
                return ToolResult("CANCELLED", "Check cancelled.", verification="CANCELLED")
            status = "FAILED" if failures else "OK"
            verification = "FAILED" if failures else "PASSED"
            return ToolResult(status, bounded("\n".join(failures) or "Python syntax check passed."), verification=verification)

        try:
            result = self._run_subprocess(plan, cancel)
        finally:
            self._cleanup_check_output(plan)
        after = self._source_manifest()
        changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        if changed:
            return ToolResult(
                "CONFLICT",
                bounded(result.content + "\nUnexpected source changes:\n" + "\n".join(changed)),
                changed_paths=changed,
                verification="FAILED",
                metadata={"exit_code": result.metadata.get("exit_code")},
            )
        return result

    def _cleanup_check_output(self, plan: CheckPlan) -> None:
        if "-d" not in plan.argv:
            return
        index = plan.argv.index("-d") + 1
        if index >= len(plan.argv):
            return
        candidate = Path(plan.argv[index]).resolve(strict=False)
        temporary_root = self.paths.temporary.resolve()
        try:
            candidate.relative_to(temporary_root)
        except ValueError:
            return
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)

    def _run_subprocess(self, plan: CheckPlan, cancel: Event) -> ToolResult:
        environment = {
            key: value for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
        }
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            list(plan.argv), cwd=self.workspace.root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", shell=False,
            creationflags=flags,
        )
        started = time.monotonic()
        timeout = 120.0
        output = ""
        try:
            while True:
                if cancel.is_set():
                    self._terminate_tree(process)
                    return ToolResult("CANCELLED", bounded(output + "\nCheck cancelled."), verification="CANCELLED")
                if time.monotonic() - started > timeout:
                    self._terminate_tree(process)
                    return ToolResult("TIMED_OUT", bounded(output + "\nCheck timed out."), verification="TIMED_OUT")
                try:
                    chunk, _ = process.communicate(timeout=0.2)
                    output = chunk or output
                    break
                except subprocess.TimeoutExpired as pending:
                    if pending.output:
                        current = pending.output if isinstance(pending.output, str) else pending.output.decode("utf-8", errors="replace")
                        output = current
            code = process.returncode
            status = "OK" if code == 0 else "FAILED"
            verification = "PASSED" if code == 0 else "FAILED"
            return ToolResult(status, bounded(output or ("Check passed with no output." if code == 0 else "Check failed with no output.")), verification=verification, metadata={"exit_code": code})
        finally:
            if process.poll() is None:
                self._terminate_tree(process)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        try:
            import psutil

            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            _, alive = psutil.wait_procs(children, timeout=1.0)
            for child in alive:
                child.kill()
            parent.terminate()
            try:
                parent.wait(timeout=1.0)
            except psutil.TimeoutExpired:
                parent.kill()
        except Exception:
            process.kill()

    def _source_manifest(self) -> dict[str, str]:
        manifest: dict[str, str] = {}
        for path in self.workspace.root.rglob("*"):
            if len(manifest) >= 100_000:
                raise RuntimeError("Source manifest exceeds the 100,000-file safety limit.")
            if not path.is_file() or not self.workspace.visible(path) or path.suffix.casefold() not in SOURCE_EXTENSIONS:
                continue
            try:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                manifest[self.workspace.relative(path)] = digest.hexdigest()
            except OSError as error:
                raise RuntimeError(f"Cannot hash source file for check safety: {path}: {error}") from error
        return manifest

    @staticmethod
    def _sensitive(path: Path) -> bool:
        return sensitive_path(path)
