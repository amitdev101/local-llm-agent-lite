from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

try:
    from .prompts import observation_message, repair_message, system_prompt
    from .protocol import FinalMessage, InvalidResponse, ToolCall, parse_response
    from .provider import LlamaCppProvider, ModelProvider, ModelRequest
    from .storage import DataPaths, EventStore, TrustStore, new_id
    from .tools import ToolRegistry, ToolResult, Workspace
except ImportError:  # Direct execution through main.py.
    from prompts import observation_message, repair_message, system_prompt
    from protocol import FinalMessage, InvalidResponse, ToolCall, parse_response
    from provider import LlamaCppProvider, ModelProvider, ModelRequest
    from storage import DataPaths, EventStore, TrustStore, new_id
    from tools import ToolRegistry, ToolResult, Workspace


class RunState(str, Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    INTERRUPTING = "INTERRUPTING"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


@dataclass
class TaskRequirements:
    user_goal: str
    task_kind: str
    expected_paths: set[str] = field(default_factory=set)
    required_operations: set[str] = field(default_factory=set)
    required_checks: set[str] = field(default_factory=set)
    accepted_limitations: set[str] = field(default_factory=set)
    requires_workspace_evidence: bool = False

    @classmethod
    def from_message(cls, message: str) -> "TaskRequirements":
        lowered = message.casefold()
        mutation_words = {
            "add", "build", "change", "create", "edit", "fix", "implement", "make",
            "modify", "move", "patch", "refactor", "remove", "rename", "replace", "update",
            "write",
        }
        read_words = {"analyze", "check", "explain", "find", "inspect", "read", "review", "show"}
        tokens = set(re.findall(r"[a-z]+", lowered))
        if tokens & mutation_words:
            kind = "mutation"
        elif tokens & read_words:
            kind = "read_only"
        else:
            kind = "conversational"

        # Completion paths must be conservative. Natural phrases such as
        # "game-over/restart" and "wall/self collision" are not file paths.
        file_suffixes = {
            ".py", ".java", ".js", ".ts", ".md", ".json", ".yaml", ".yml",
            ".toml", ".html", ".css",
        }
        candidates = re.findall(r"`([^`\r\n]+)`", message)
        candidates.extend(re.findall(
            r"\b(?:[A-Za-z0-9_.-]+[/\\])*[A-Za-z0-9_.-]+\.(?:py|java|js|ts|md|json|ya?ml|toml|html|css)\b",
            message,
            flags=re.I,
        ))
        paths = set()
        for candidate in candidates:
            candidate_path = Path(candidate)
            if (
                candidate_path.suffix.casefold() in file_suffixes
                and not candidate_path.is_absolute()
                and ".." not in candidate_path.parts
            ):
                paths.add(candidate_path.as_posix())

        checks = {kind_name for kind_name in ("build", "test", "lint", "typecheck") if kind_name in tokens}
        operations = {"edit"} if kind == "mutation" else set()
        workspace_words = {"code", "file", "folder", "project", "repo", "repository", "workspace"}
        requires_evidence = kind == "read_only" and bool(paths or tokens & workspace_words)
        return cls(message, kind, paths, operations, checks, set(), requires_evidence)

    def serializable(self) -> dict[str, object]:
        value = asdict(self)
        for key in ("expected_paths", "required_operations", "required_checks", "accepted_limitations"):
            value[key] = sorted(value[key])
        return value

    @classmethod
    def from_record(cls, value: dict[str, object]) -> "TaskRequirements":
        return cls(
            user_goal=str(value["user_goal"]),
            task_kind=str(value["task_kind"]),
            expected_paths=set(value.get("expected_paths", [])),
            required_operations=set(value.get("required_operations", [])),
            required_checks=set(value.get("required_checks", [])),
            accepted_limitations=set(value.get("accepted_limitations", [])),
            requires_workspace_evidence=bool(value.get("requires_workspace_evidence", False)),
        )


@dataclass
class AgentConfig:
    model_path: Path
    workspace: Path
    data_dir: Path | None = None
    context_size: int = 32768
    temperature: float | None = None
    max_output_tokens: int | None = None
    gpu_layers: int = 0
    threads: int | None = None
    max_steps: int = 20
    mode: str = "ask"


@dataclass
class RunResult:
    state: RunState
    message: str
    run_id: str
    changed_paths: tuple[str, ...]
    log_file: Path


StreamCallback = Callable[[str, str], None]
ApprovalCallback = Callable[[ToolCall, str, str], bool]


class SingleModelAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        provider: ModelProvider | None = None,
        stream_callback: StreamCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        if config.mode not in {"ask", "auto", "dry-run"}:
            raise ValueError("mode must be ask, auto, or dry-run")
        self.config = config
        self.paths = DataPaths.create(config.data_dir)
        workspace_path = config.workspace.expanduser().resolve(strict=True)
        try:
            workspace_path.relative_to(self.paths.root.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("The workspace cannot be the agent data directory or one of its descendants.")
        self.workspace = Workspace(config.workspace, (self.paths.root,))
        self.provider = provider or LlamaCppProvider(
            config.model_path,
            context_size=config.context_size,
            gpu_layers=config.gpu_layers,
            threads=config.threads,
        )
        self.stream_callback = stream_callback or (lambda _kind, _text: None)
        self.approval_callback = approval_callback
        self.session_id = new_id("session")
        self.history: list[dict[str, str]] = []
        self.state = RunState.IDLE
        self.cancel_event = threading.Event()
        self.current_store: EventStore | None = None
        self.current_tools: ToolRegistry | None = None
        self.changed_paths: set[str] = set()
        self.checks: dict[str, str] = {}
        self.evidence_tools: set[str] = set()
        self.requirements: TaskRequirements | None = None

    def run(self, user_message: str, _resume: dict[str, object] | None = None) -> RunResult:
        if not user_message.strip():
            raise ValueError("User message cannot be empty.")
        if len(user_message) > 65_536:
            raise ValueError("User message exceeds the 65,536-character safety limit.")
        self.cancel_event.clear()
        self.changed_paths = set(_resume.get("changed_paths", [])) if _resume else set()
        self.checks = dict(_resume.get("checks", {})) if _resume else {}
        self.evidence_tools = set(_resume.get("evidence_tools", [])) if _resume else set()
        requirements_record = _resume.get("requirements") if _resume else None
        self.requirements = (
            TaskRequirements.from_record(requirements_record)
            if isinstance(requirements_record, dict)
            else TaskRequirements.from_message(user_message)
        )
        self.state = RunState.PLANNING

        store = EventStore(
            self.paths, self.workspace.root, self.config.model_path.name,
            session_id=self.session_id,
        )
        self.current_store = store
        tools = ToolRegistry(self.workspace, self.paths, store)
        self.current_tools = tools
        store.append("RunStarted", {
            "mode": self.config.mode,
            "context_size": self.config.context_size,
            "max_output_tokens": self.config.max_output_tokens,
            "resumed_from": _resume.get("run_id") if _resume else None,
        })
        store.append("UserMessage", {"content": user_message})
        store.append("TaskRequirementsRecorded", self.requirements.serializable())
        self.history.append({"role": "user", "content": user_message})
        self.state = RunState.RUNNING

        last_failure = ""
        repeated_failure = 0
        final_message = ""

        try:
            profile = self.provider.metadata()
            store.append("ModelProfileSelected", asdict(profile))
            for warning in profile.warnings:
                self.stream_callback("status", f"Warning: {warning}\n")

            temperature = self.config.temperature if self.config.temperature is not None else profile.temperature
            request = ModelRequest(temperature, self.config.max_output_tokens)

            if _resume:
                conflict = str(_resume.get("conflict", ""))
                if conflict:
                    return self._end(store, RunState.CONFLICT, conflict)
                pending = _resume.get("pending_approval")
                if isinstance(pending, dict):
                    call = ToolCall(
                        str(pending["tool"]),
                        dict(pending["arguments"]),
                        "resumed-approval",
                        "persisted normalized action",
                    )
                    expected_state = dict(pending.get("action_state", {}))
                    expected = str(pending["fingerprint"])
                    actual = self._fingerprint(call.name, call.arguments, str(self.workspace.root), expected_state)
                    if actual != expected:
                        return self._end(store, RunState.CONFLICT, "Persisted approval fingerprint does not match the normalized action.")
                    preview = tools.preview(call)
                    if not self._approve(store, call, preview, str(pending.get("risk", "medium")), expected_state, 0):
                        return self._end(store, RunState.FAILED, "User rejected the resumed action.")
                    result = self._execute(tools, call, store, expected_state, 0)
                    self._record_result(call, result, store)
                    self.history.append({"role": "user", "content": observation_message(call.name, result.status, self._active_text(result.content))})
                    if result.status == "CONFLICT":
                        return self._end(store, RunState.CONFLICT, result.content)
                    if result.status not in {"OK", "DRY_RUN"}:
                        self.history.append({"role": "user", "content": repair_message(f"Resumed action failed: {result.content}")})

            for step in range(1, self.config.max_steps + 1):
                if self.cancel_event.is_set():
                    raise InterruptedError("Run cancelled before the next step.")
                self._compact_if_needed(store, profile.context_size, step)
                messages = self._messages()
                estimated = self._estimate_tokens(messages)
                store.append("ContextPrepared", {"estimated_tokens": estimated, "message_count": len(messages)}, step)
                store.append("ModelStarted", {"temperature": temperature, "output_limit": self.config.max_output_tokens}, step)

                response_parts: list[str] = []
                reasoning_parts: list[str] = []
                started = time.monotonic()
                first_token_ms: int | None = None
                for chunk in self.provider.stream(messages, request):
                    if self.cancel_event.is_set():
                        self.provider.cancel_current()
                        raise InterruptedError("Run cancelled during model generation.")
                    if chunk.first and first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    if chunk.reasoning:
                        reasoning_parts.append(chunk.reasoning)
                        self.stream_callback("reasoning", chunk.reasoning)
                    if chunk.text:
                        response_parts.append(chunk.text)
                        self.stream_callback("response", chunk.text)
                response = "".join(response_parts)
                store.append("ModelResponse", {
                    "response": response,
                    "reasoning_chars": sum(map(len, reasoning_parts)),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "first_token_ms": first_token_ms,
                }, step)

                parsed = parse_response(response, profile.parser_name)
                if isinstance(parsed, InvalidResponse):
                    store.append("ParseFailed", asdict(parsed), step)
                    fingerprint = self._fingerprint("parse", parsed.reason)
                    repeated_failure = repeated_failure + 1 if fingerprint == last_failure else 1
                    last_failure = fingerprint
                    if repeated_failure >= 2:
                        return self._end(store, RunState.FAILED, f"Repeated invalid model response: {parsed.reason}")
                    self.history.extend([
                        {"role": "assistant", "content": parsed.raw_summary},
                        {"role": "user", "content": repair_message(parsed.reason)},
                    ])
                    continue

                store.append("ActionParsed", {
                    "kind": type(parsed).__name__,
                    "source_format": parsed.source_format,
                    "tool": parsed.name if isinstance(parsed, ToolCall) else None,
                    "argument_keys": sorted(parsed.arguments) if isinstance(parsed, ToolCall) else [],
                }, step)
                if isinstance(parsed, FinalMessage):
                    self.history.append({"role": "assistant", "content": self._active_text(parsed.content)})
                    accepted, reason = self._completion_allowed()
                    if accepted:
                        final_message = parsed.content
                        return self._end(store, RunState.COMPLETED, final_message)
                    store.append("VerificationResult", {"status": "REJECTED", "reason": reason}, step)
                    self.history.append({"role": "user", "content": repair_message(f"Premature final response: {reason}")})
                    continue

                validation = tools.validate(parsed)
                if not validation.valid:
                    store.append("SchemaRejected", {"tool": parsed.name, "reason": validation.reason}, step)
                    fingerprint = self._fingerprint(parsed.name, validation.reason, parsed.arguments)
                    repeated_failure = repeated_failure + 1 if fingerprint == last_failure else 1
                    last_failure = fingerprint
                    if repeated_failure >= 2:
                        return self._end(store, RunState.FAILED, f"Repeated invalid action: {validation.reason}")
                    self.history.append({"role": "user", "content": repair_message(validation.reason)})
                    continue

                if parsed.name == "finish":
                    accepted, reason = self._completion_allowed()
                    store.append("VerificationResult", {"status": "PASSED" if accepted else "REJECTED", "reason": reason}, step)
                    if accepted:
                        final_message = str(parsed.arguments["message"])
                        return self._end(store, RunState.COMPLETED, final_message)
                    self.history.append({"role": "user", "content": repair_message(f"finish rejected: {reason}")})
                    continue

                risk = tools.risk(parsed)
                try:
                    action_state = tools.action_state(parsed)
                    preview = tools.preview(parsed)
                except (OSError, RuntimeError, ValueError) as error:
                    store.append("PolicyDenied", {"tool": parsed.name, "reason": str(error)}, step)
                    result = ToolResult("REJECTED", str(error))
                    self._record_result(parsed, result, store, step)
                    self.history.append({"role": "user", "content": observation_message(parsed.name, result.status, self._active_text(result.content))})
                    continue
                if preview.startswith("REJECTED:"):
                    store.append("PolicyDenied", {"tool": parsed.name, "reason": preview}, step)
                    result = ToolResult("REJECTED", preview.removeprefix("REJECTED: "))
                elif self._needs_approval(parsed, risk, tools):
                    approved = self._approve(store, parsed, preview, risk, action_state, step)
                    if not approved:
                        return self._end(store, RunState.FAILED, "User rejected the requested action.")
                    result = self._execute(tools, parsed, store, action_state, step)
                else:
                    store.append("PolicyAllowed", {"tool": parsed.name, "risk": risk, "mode": self.config.mode}, step)
                    result = self._execute(tools, parsed, store, action_state, step)

                if result.status in {"FAILED", "REJECTED", "TIMED_OUT", "CONFLICT", "UNAVAILABLE"}:
                    fingerprint = self._fingerprint(parsed.name, result.status, parsed.arguments, result.content[:300])
                    repeated_failure = repeated_failure + 1 if fingerprint == last_failure else 1
                    last_failure = fingerprint
                else:
                    repeated_failure = 0
                    last_failure = ""
                if repeated_failure >= 2:
                    return self._end(store, RunState.FAILED, f"Repeated no-progress failure: {result.content}")

                self._record_result(parsed, result, store, step)
                self.history.append({"role": "user", "content": observation_message(parsed.name, result.status, self._active_text(result.content))})
                if result.status == "CONFLICT":
                    return self._end(store, RunState.CONFLICT, result.content)
                if result.status == "CANCELLED":
                    raise InterruptedError(result.content)

            return self._end(store, RunState.FAILED, f"Maximum step count ({self.config.max_steps}) reached.")
        except (KeyboardInterrupt, InterruptedError) as error:
            self.cancel_event.set()
            self.provider.cancel_current()
            return self._end(store, RunState.INTERRUPTED, str(error) or "Run interrupted.")
        except Exception as error:
            store.append("RunFailed", {"reason": str(error), "exception": type(error).__name__})
            self.state = RunState.FAILED
            return RunResult(self.state, str(error), store.run_id, tuple(sorted(self.changed_paths)), store.text_log)

    def _execute(
        self,
        tools: ToolRegistry,
        call: ToolCall,
        store: EventStore,
        action_state: dict[str, object],
        step: int,
    ) -> ToolResult:
        store.append("ToolStarted", {"tool": call.name, "arguments": call.arguments, "action_state": action_state}, step)
        result = tools.execute(
            call,
            self.cancel_event,
            store.run_id,
            dry_run=self.config.mode == "dry-run",
            expected_state=action_state,
            step=step,
        )
        event = "ToolOutput" if result.status in {"OK", "DRY_RUN"} else "ToolFailed"
        store.append(event, {"tool": call.name, "arguments": call.arguments, **asdict(result)}, step)
        return result

    def _approve(
        self,
        store: EventStore,
        call: ToolCall,
        preview: str,
        risk: str,
        action_state: dict[str, object],
        step: int,
    ) -> bool:
        fingerprint = self._fingerprint(call.name, call.arguments, str(self.workspace.root), action_state)
        self.state = RunState.WAITING_FOR_APPROVAL
        store.append("ApprovalRequired", {
            "tool": call.name,
            "arguments": call.arguments,
            "fingerprint": fingerprint,
            "preview": preview,
            "risk": risk,
            "action_state": action_state,
        }, step)
        approved = bool(self.approval_callback and self.approval_callback(call, preview, risk))
        store.append("ApprovalGranted" if approved else "ApprovalRejected", {
            "fingerprint": fingerprint,
            "tool": call.name,
        }, step)
        self.state = RunState.RUNNING if approved else RunState.FAILED
        return approved

    def _needs_approval(self, call: ToolCall, risk: str, tools: ToolRegistry) -> bool:
        if self.config.mode == "dry-run" or risk == "low":
            return False
        if call.name == "run_check":
            plan = tools.check_plan(str(call.arguments["kind"]))
            return not bool(plan and plan.safe_auto)
        if self.config.mode == "ask":
            return True
        capabilities = TrustStore(self.paths).capabilities(self.workspace.root)
        return call.name not in capabilities

    def _record_result(
        self,
        call: ToolCall,
        result: ToolResult,
        store: EventStore,
        step: int = 0,
    ) -> None:
        requirements_before = self.requirements.serializable() if self.requirements else None
        self.changed_paths.update(result.changed_paths)
        if self.requirements and call.name == "edit_file" and result.status == "OK":
            invalidated_checks = sorted(self.checks)
            for check in invalidated_checks:
                self.checks[check] = "STALE"
            if invalidated_checks:
                store.append("ChecksInvalidated", {
                    "checks": invalidated_checks,
                    "reason": "verified source edit applied after check",
                    "paths": result.changed_paths,
                }, step)
            self.requirements.expected_paths.update(result.changed_paths)
            for path in result.changed_paths:
                suffix = Path(path).suffix.casefold()
                if suffix == ".java":
                    self.requirements.required_checks.add("build")
                elif suffix == ".py":
                    self.requirements.required_checks.add("lint")
        if self.requirements and self.requirements.serializable() != requirements_before:
            # Requirements evolve when a successful edit reveals the artifact type.
            # Persist the new completion contract before another model step so resume
            # cannot finish against the stale, weaker initial contract.
            store.append("TaskRequirementsRecorded", self.requirements.serializable(), step)
        if call.name == "run_check":
            self.checks[str(call.arguments["kind"])] = result.verification
        if call.name in {"list_files", "search", "read_file"} and result.status == "OK":
            self.evidence_tools.add(call.name)

    def _completion_allowed(self) -> tuple[bool, str]:
        assert self.requirements is not None
        if self.requirements.task_kind == "mutation" and not self.changed_paths:
            return False, "the task requires a mutation but no verified edit was applied"
        if self.requirements.requires_workspace_evidence and not self.evidence_tools:
            return False, "the project read-only task has no workspace observation evidence"
        for relative in self.requirements.expected_paths:
            try:
                path = self.workspace.resolve(relative, file_required=True)
            except ValueError:
                return False, f"required artifact is missing or inaccessible: {relative}"
            if not path.is_file():
                return False, f"required artifact is not a file: {relative}"
        for check in self.requirements.required_checks:
            status = self.checks.get(check, "NOT_RUN")
            if status != "PASSED" and check not in self.requirements.accepted_limitations:
                return False, f"required {check} check is {status}"
        return True, "stored requirements and deterministic evidence are satisfied"

    def _messages(self) -> list[dict[str, str]]:
        assert self.requirements is not None
        state = {
            "user_goal": self.requirements.user_goal,
            "task_kind": self.requirements.task_kind,
            "changed_paths": sorted(self.changed_paths),
            "checks": [f"{key}={value}" for key, value in sorted(self.checks.items())],
        }
        return [{"role": "system", "content": system_prompt(str(self.workspace.root), state)}, *self.history]

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, str]]) -> int:
        return sum(max(1, len(message.get("content", "")) // 4) for message in messages)

    def _compact_if_needed(self, store: EventStore, context_size: int, step: int) -> None:
        messages = self._messages()
        used = self._estimate_tokens(messages)
        if used < int(context_size * 0.72):
            return
        archive = store.archive_messages(messages, f"estimated context {used}/{context_size}")
        store.append("ContextArchived", {"path": str(archive), "estimated_tokens": used}, step)
        # Compaction must actually shrink the prompt. Preserve only a bounded
        # repair window here; the complete history is already in the archive.
        recent = [
            {
                "role": message.get("role", "user"),
                "content": self._active_text(message.get("content", ""), limit=4096),
            }
            for message in self.history[-3:]
        ]
        summary = {
            "role": "user",
            "content": observation_message(
                "context_compaction",
                "OK",
                "Preserved controller state:\n"
                f"goal={self.requirements.user_goal if self.requirements else ''}\n"
                f"changed_paths={sorted(self.changed_paths)}\n"
                f"checks={self.checks}",
            ),
        }
        self.history = [summary, *recent]
        store.append("ContextCompacted", {
            "before_messages": len(messages),
            "after_messages": len(self.history) + 1,
            "before_estimated_tokens": used,
            "after_estimated_tokens": self._estimate_tokens(self._messages()),
            "method": "deterministic-extractive",
        }, step)

    def stop(self) -> None:
        self.state = RunState.INTERRUPTING
        self.cancel_event.set()
        self.provider.cancel_current()

    def undo_last_checkpoint(self, run_id: str | None = None) -> ToolResult:
        if not self.current_tools or not self.current_store:
            return ToolResult("REJECTED", "No active session checkpoint is available.")
        return self.current_tools.undo(run_id or self.current_store.run_id)

    def accept_limitation(self, check: str) -> None:
        if not self.requirements or check not in self.requirements.required_checks:
            raise ValueError(f"{check} is not a current required check.")
        self.requirements.accepted_limitations.add(check)
        if self.current_store:
            self.current_store.append("LimitationAccepted", {"check": check})

    def resume(self, run_id: str) -> RunResult:
        run_file = self.paths.runs / f"{run_id}.jsonl"
        events, incomplete = EventStore.replay(run_file)
        if not events:
            raise ValueError(f"Run not found: {run_id}")
        if any(event["type"] == "RunCompleted" for event in events):
            raise ValueError("Run is already completed.")
        user_events = [event for event in events if event["type"] == "UserMessage"]
        if not user_events:
            raise ValueError("Run has no recoverable user message.")
        requirement_events = [event for event in events if event["type"] == "TaskRequirementsRecorded"]
        if not requirement_events:
            raise ValueError("Run has no durable task requirements.")

        approval_requests = [event for event in events if event["type"] == "ApprovalRequired"]
        pending = None
        if approval_requests:
            last_request = approval_requests[-1]
            fingerprint = last_request["payload"].get("fingerprint")
            resolved_later = any(
                event["sequence"] > last_request["sequence"]
                and event["type"] in {"ApprovalGranted", "ApprovalRejected"}
                and event["payload"].get("fingerprint") == fingerprint
                for event in events
            )
            if not resolved_later:
                pending = last_request["payload"]
        changed = {
            path
            for event in events if event["type"] == "EditApplied"
            for path in [event["payload"].get("path")]
            if isinstance(path, str)
        }
        checks: dict[str, str] = {}
        for event in events:
            if (
                event["type"] in {"ToolOutput", "ToolFailed"}
                and event["payload"].get("tool") == "run_check"
            ):
                kind = str(event["payload"].get("arguments", {}).get("kind"))
                checks[kind] = str(event["payload"].get("verification"))
            elif event["type"] == "ChecksInvalidated":
                for check in event["payload"].get("checks", []):
                    checks[str(check)] = "STALE"
        evidence_tools = {
            str(event["payload"].get("tool"))
            for event in events
            if event["type"] == "ToolOutput"
            and event["payload"].get("tool") in {"list_files", "search", "read_file"}
        }
        recovery_changed, conflict = self._reconcile_snapshots(run_id)
        changed.update(recovery_changed)
        note = " Resume recovered after an incomplete JSONL tail." if incomplete else ""
        original = str(user_events[-1]["payload"]["content"])
        self.session_id = str(events[-1]["session_id"])
        requirements = dict(requirement_events[-1]["payload"])
        accepted = set(requirements.get("accepted_limitations", []))
        accepted.update(
            str(event["payload"].get("check"))
            for event in events if event["type"] == "LimitationAccepted"
        )
        requirements["accepted_limitations"] = sorted(value for value in accepted if value and value != "None")
        resume_data: dict[str, object] = {
            "run_id": run_id,
            "requirements": requirements,
            "pending_approval": pending,
            "changed_paths": sorted(changed),
            "checks": checks,
            "evidence_tools": sorted(evidence_tools),
            "conflict": conflict,
        }
        return self.run(original + "\nContinue from the last durable boundary." + note, _resume=resume_data)

    def _reconcile_snapshots(self, run_id: str) -> tuple[set[str], str]:
        root = self.paths.snapshots / run_id
        recovered: set[str] = set()
        if not root.exists():
            return recovered, ""
        latest_by_path: dict[str, tuple[Path, dict[str, object]]] = {}
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("undone"):
                continue
            relative = str(manifest["path"])
            existing = latest_by_path.get(relative)
            if existing is None or directory.stat().st_mtime_ns > existing[0].stat().st_mtime_ns:
                latest_by_path[relative] = (directory, manifest)
        for _directory, manifest in latest_by_path.values():
            relative = str(manifest["path"])
            try:
                path = self.workspace.resolve(relative, file_required=True, allow_missing=True)
            except ValueError as error:
                return recovered, f"Checkpoint path cannot be reconciled: {error}"
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            intended = manifest.get("intended_hash")
            original = manifest.get("before_hash")
            if current == intended:
                recovered.add(relative)
                continue
            if current == original or (current is None and original is None):
                continue
            return recovered, f"Checkpoint conflict for {relative}; current content matches neither original nor intended hash."
        return recovered, ""

    @staticmethod
    def _active_text(value: str, limit: int = 16_384) -> str:
        if len(value) <= limit:
            return value
        marker = "\n<active context truncated; full value is in the event log>\n"
        available = max(0, limit - len(marker))
        left = available // 2
        right = available - left
        return value[:left] + marker + (value[-right:] if right else "")

    def status(self) -> dict[str, object]:
        profile = self.provider.metadata()
        return {
            "state": self.state.value,
            "model": profile.name,
            "model_path": str(self.config.model_path),
            "workspace": str(self.workspace.root),
            "context_size": profile.context_size,
            "temperature": self.config.temperature if self.config.temperature is not None else profile.temperature,
            "max_output_tokens": self.config.max_output_tokens,
            "mode": self.config.mode,
            "session_id": self.session_id,
            "data_dir": str(self.paths.root),
        }

    def close(self) -> None:
        self.provider.close()

    def _end(self, store: EventStore, state: RunState, message: str) -> RunResult:
        self.state = state
        event = {
            RunState.COMPLETED: "RunCompleted",
            RunState.INTERRUPTED: "RunInterrupted",
            RunState.CONFLICT: "RunFailed",
        }.get(state, "RunFailed")
        store.append(event, {"state": state.value, "message": message, "changed_paths": sorted(self.changed_paths)})
        return RunResult(state, message, store.run_id, tuple(sorted(self.changed_paths)), store.text_log)

    @staticmethod
    def _fingerprint(*values: object) -> str:
        encoded = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
