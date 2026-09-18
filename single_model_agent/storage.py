from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CRITICAL_EVENTS = {
    "TaskRequirementsRecorded",
    "ChecksInvalidated",
    "ApprovalRequired",
    "ApprovalGranted",
    "ApprovalRejected",
    "CheckpointCreated",
    "EditApplied",
    "EditRolledBack",
    "RunInterrupted",
    "RunCompleted",
    "RunFailed",
}

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}\b"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def workspace_id(workspace: Path) -> str:
    canonical = str(workspace.resolve()).casefold().encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def workspace_identity(workspace: Path) -> str:
    try:
        stat = workspace.resolve(strict=True).stat()
    except OSError:
        return ""
    device = getattr(stat, "st_dev", 0)
    inode = getattr(stat, "st_ino", 0)
    fallback = getattr(stat, "st_ctime_ns", 0) if not inode else 0
    identity = f"{workspace.resolve()}|{device}|{inode}|{fallback}"
    return hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()


def redact(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value

    text = value
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}=<redacted>" if match.lastindex == 2 else "<redacted>", text)
    if len(text) > 1_000_000:
        text = text[:1_000_000] + "\n<truncated by event storage>"
    return text


@dataclass(frozen=True)
class DataPaths:
    root: Path
    logs: Path
    runs: Path
    sessions: Path
    snapshots: Path
    archives: Path
    temporary: Path

    @classmethod
    def create(cls, override: str | Path | None = None) -> "DataPaths":
        if override:
            root = Path(override).expanduser().resolve()
        elif os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            root = base / "MyLLM" / "single_model_agent"
        else:
            base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
            root = base / "myllm" / "single_model_agent"

        paths = cls(
            root=root,
            logs=root / "logs",
            runs=root / "runs",
            sessions=root / "sessions",
            snapshots=root / "snapshots",
            archives=root / "archives",
            temporary=root / "tmp",
        )
        for path in asdict(paths).values():
            Path(path).mkdir(parents=True, exist_ok=True)
        return paths


class EventStore:
    def __init__(
        self,
        paths: DataPaths,
        workspace: Path,
        model: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.paths = paths
        self.workspace = workspace.resolve()
        self.model = model
        self.session_id = session_id or new_id("session")
        self.run_id = run_id or new_id("run")
        self.sequence = 0
        self.parent_event_id: str | None = None
        self._lock = threading.Lock()

        session_dir = paths.sessions / workspace_id(self.workspace)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.run_file = paths.runs / f"{self.run_id}.jsonl"
        self.session_file = session_dir / f"{self.session_id}.jsonl"
        day = datetime.now().strftime("%Y-%m-%d")
        log_dir = paths.logs / day
        log_dir.mkdir(parents=True, exist_ok=True)
        self.text_log = log_dir / f"{self.run_id}.log.txt"

    def append(self, event_type: str, payload: dict[str, Any] | None = None, step: int = 0) -> dict[str, Any]:
        with self._lock:
            self.sequence += 1
            event_id = new_id("event")
            record = {
                "schema_version": 1,
                "event_id": event_id,
                "parent_event_id": self.parent_event_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "sequence": self.sequence,
                "timestamp": utc_now(),
                "type": event_type,
                "workspace": str(self.workspace),
                "model": self.model,
                "step": step,
                "payload": redact(payload or {}),
            }
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            critical = event_type in CRITICAL_EVENTS
            self._append_line(self.run_file, line, critical)
            self._append_line(self.session_file, line, critical)
            self._append_text(record, critical)
            self.parent_event_id = event_id
            return record

    @staticmethod
    def _append_line(path: Path, line: str, durable: bool) -> None:
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())

    def _append_text(self, record: dict[str, Any], durable: bool) -> None:
        payload = json.dumps(record["payload"], ensure_ascii=False)
        if len(payload) > 4000:
            payload = payload[:4000] + "…"
        line = f"{record['timestamp']} [{record['type']}] step={record['step']} {payload}\n"
        with self.text_log.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())

    def archive_messages(self, messages: list[dict[str, str]], reason: str) -> Path:
        directory = self.paths.archives / self.run_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"context-{self.sequence + 1:06d}.txt"
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(f"Run: {self.run_id}\nReason: {reason}\nArchived: {utc_now()}\n\n")
            for message in messages:
                handle.write(f"## {message.get('role', 'unknown')}\n\n")
                handle.write(message.get("content", ""))
                handle.write("\n\n")
            handle.flush()
            os.fsync(handle.fileno())
        return target

    @staticmethod
    def replay(path: Path) -> tuple[list[dict[str, Any]], bool]:
        events: list[dict[str, Any]] = []
        incomplete_tail = False
        if not path.exists():
            return events, incomplete_tail
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    incomplete_tail = True
                    break
                raise ValueError(f"Corrupt JSONL before final line: {path}:{index + 1}")
        return events, incomplete_tail


class TrustStore:
    def __init__(self, paths: DataPaths) -> None:
        self.path = paths.root / "workspace_trust.json"

    def capabilities(self, workspace: Path) -> set[str]:
        records = self._read()
        entry = records.get(workspace_id(workspace), {})
        if entry.get("canonical_path", "").casefold() != str(workspace.resolve()).casefold():
            return set()
        identity = workspace_identity(workspace)
        if not identity or entry.get("root_identity") != identity:
            return set()
        return set(entry.get("capabilities", []))

    def grant(self, workspace: Path, capabilities: set[str]) -> None:
        records = self._read()
        records[workspace_id(workspace)] = {
            "canonical_path": str(workspace.resolve()),
            "root_identity": workspace_identity(workspace),
            "granted_at": utc_now(),
            "granted_by": "local_user",
            "capabilities": sorted(capabilities),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
