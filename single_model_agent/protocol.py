from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    source_format: str
    raw_summary: str


@dataclass(frozen=True)
class FinalMessage:
    content: str
    source_format: str = "text"


@dataclass(frozen=True)
class InvalidResponse:
    reason: str
    source_format: str
    raw_summary: str


ParsedResponse = ToolCall | FinalMessage | InvalidResponse

TOOL_ALIASES: dict[str, tuple[str, dict[str, Any]]] = {
    "create_file": ("edit_file", {"operation": "create"}),
    "write_file": ("edit_file", {"operation": "create"}),
    "replace_file": ("edit_file", {"operation": "replace"}),
    "patch_file": ("edit_file", {"operation": "patch"}),
    "run_tests": ("run_check", {"kind": "test"}),
    "run_build": ("run_check", {"kind": "build"}),
    "run_lint": ("run_check", {"kind": "lint"}),
}

TOOL_SHAPE = re.compile(
    r"(?im)^\s*(?:ACTION|TOOL)\s*:|<tool_call>|^\s*\{\s*[\"'](?:tool|name)[\"']\s*:"
)
NATURAL_MARKER = re.compile(r"(?im)^\s*(?:ACTION|TOOL)\s*:\s*([a-z][a-z0-9_-]*)\s*$")
FINAL_MARKER = re.compile(r"(?is)^\s*FINAL\s*:\s*(.+?)\s*$")


def _summary(text: str, limit: int = 500) -> str:
    clean = text.replace("\x00", "").strip()
    return clean if len(clean) <= limit else clean[:limit] + "…"


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _normalize_call(name: str, arguments: dict[str, Any], source: str, raw: str) -> ToolCall | InvalidResponse:
    normalized = _normalize_name(name)
    fixed: dict[str, Any] = {}

    if normalized in TOOL_ALIASES:
        normalized, fixed = TOOL_ALIASES[normalized]

    merged = dict(arguments)
    for key, value in fixed.items():
        if key in merged and merged[key] != value:
            return InvalidResponse(
                f"Alias conflicts with fixed {key}={value!r}.", source, _summary(raw)
            )
        merged[key] = value

    return ToolCall(normalized, merged, source, _summary(raw))


def _fence_mask(text: str) -> str:
    """Hide fenced content while preserving positions and line breaks."""
    chars = list(text)
    offset = 0
    in_fence = False

    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = stripped.startswith("```")
        if in_fence or marker:
            for index in range(offset, offset + len(line)):
                if chars[index] not in "\r\n":
                    chars[index] = " "
        if marker:
            in_fence = not in_fence
        offset += len(line)

    return "".join(chars)


def _visible_text(text: str, *, allow_orphan_think_close: bool = False) -> str:
    # Remove only complete thinking blocks outside Markdown fences. Replacing
    # non-newline characters preserves line/fence positions for later parsing.
    masked = _fence_mask(text)
    opens = list(re.finditer(r"<think>", masked, flags=re.I))
    closes = list(re.finditer(r"</think>", masked, flags=re.I))
    if allow_orphan_think_close and not opens and len(closes) == 1:
        # Some Qwen3.5 llama.cpp templates consume the opening token while the
        # generated text still contains the closing token. Treat the prefix as
        # reasoning and parse only the suffix. This remains deliberately narrow:
        # repeated closes and every other unbalanced shape are rejected.
        return text[closes[0].end():].strip()
    chars = list(text)
    for match in re.finditer(r"<think>.*?</think>", masked, flags=re.I | re.S):
        for index in range(match.start(), match.end()):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars).strip()


def _object_call(value: Any, source: str, raw: str) -> ParsedResponse:
    if not isinstance(value, dict):
        return InvalidResponse("Tool payload must be an object.", source, _summary(raw))

    name = value.get("tool", value.get("name"))
    arguments = value.get("args", value.get("arguments", {}))
    if not isinstance(name, str) or not name.strip():
        return InvalidResponse("Tool payload is missing a string tool/name.", source, _summary(raw))

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            return InvalidResponse(f"Tool arguments are invalid JSON: {error.msg}.", source, _summary(raw))

    if not isinstance(arguments, dict):
        return InvalidResponse("Tool arguments must be an object.", source, _summary(raw))

    return _normalize_call(name, arguments, source, raw)


def _parse_tagged(text: str) -> ParsedResponse | None:
    masked = _fence_mask(text)
    starts = list(re.finditer(r"<tool_call>", masked, flags=re.I))
    ends = list(re.finditer(r"</tool_call>", masked, flags=re.I))

    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].end():
        return InvalidResponse("Expected exactly one complete tool_call block.", "tagged", _summary(text))

    start = starts[0].end()
    end = ends[0].start()
    block = text[start:end].strip()
    trailing_mask = _fence_mask(text[ends[0].end():])
    if TOOL_SHAPE.search(trailing_mask):
        return InvalidResponse("Executable-looking content follows the tool call.", "tagged", _summary(text))

    try:
        value = json.loads(block)
    except json.JSONDecodeError:
        value = None
    if value is not None:
        return _object_call(value, "hermes-json", block)

    function = re.fullmatch(
        r"\s*<function=([a-zA-Z][a-zA-Z0-9_-]*)>(.*?)</function>\s*",
        block,
        flags=re.I | re.S,
    )
    if not function:
        return InvalidResponse("Tagged tool call is neither Hermes JSON nor Qwen XML.", "tagged", _summary(block))

    body = function.group(2)
    arguments: dict[str, Any] = {}
    parameter_pattern = re.compile(
        r"<parameter=([a-zA-Z][a-zA-Z0-9_-]*)>(.*?)</parameter>",
        flags=re.I | re.S,
    )
    consumed = []
    for match in parameter_pattern.finditer(body):
        key = _normalize_name(match.group(1))
        if key in arguments:
            return InvalidResponse(f"Duplicate parameter: {key}.", "qwen-xml", _summary(block))
        raw_value = match.group(2).strip()
        try:
            arguments[key] = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            arguments[key] = raw_value
        consumed.append(match.span())

    remainder = list(body)
    for left, right in consumed:
        remainder[left:right] = " " * (right - left)
    if "".join(remainder).strip():
        return InvalidResponse("Unexpected text in Qwen parameter block.", "qwen-xml", _summary(block))

    return _normalize_call(function.group(1), arguments, "qwen-xml", block)


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped.lower() in {"true", "false", "null"}:
        return json.loads(stripped.lower())
    if stripped[:1] in {'"', "[", "{"} or re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return stripped


def _read_fence(lines: list[str], start: int) -> tuple[str, int] | None:
    if not lines[start].lstrip().startswith("```"):
        return None
    values: list[str] = []
    index = start + 1
    while index < len(lines):
        if lines[index].strip() == "```":
            return "\n".join(values), index + 1
        values.append(lines[index])
        index += 1
    return None


def _parse_natural(text: str) -> ParsedResponse | None:
    masked = _fence_mask(text)
    markers = list(NATURAL_MARKER.finditer(masked))
    if not markers:
        return None
    if len(markers) != 1:
        return InvalidResponse("Expected exactly one ACTION block.", "natural", _summary(text))

    marker = markers[0]
    name = marker.group(1)
    body = text[marker.end():].strip()
    lines = body.splitlines()
    arguments: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        field = re.fullmatch(r"\s*([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(.*)", lines[index])
        if not field:
            return InvalidResponse(
                f"Unexpected action line: {lines[index].strip()[:80]}", "natural", _summary(text)
            )
        key = _normalize_name(field.group(1))
        if key in arguments:
            return InvalidResponse(f"Duplicate action field: {key}.", "natural", _summary(text))
        value = field.group(2)
        if not value.strip():
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and lines[next_index].lstrip().startswith("```"):
                fenced = _read_fence(lines, next_index)
                if fenced is None:
                    return InvalidResponse(f"Unclosed fence for {key}.", "natural", _summary(text))
                arguments[key], index = fenced
                continue
        arguments[key] = _parse_scalar(value)
        index += 1

    return _normalize_call(name, arguments, "natural", text)


def parse_response(
    response: str,
    protocol: str = "auto",
    native_tool_call: dict[str, Any] | None = None,
) -> ParsedResponse:
    if native_tool_call is not None:
        return _object_call(native_tool_call, "provider-native", json.dumps(native_tool_call, ensure_ascii=False))

    if not response or not response.strip():
        return InvalidResponse("Model returned an empty response.", "empty", "")

    if len(response.encode("utf-8", errors="replace")) > 16 * 1024 * 1024:
        return InvalidResponse("Model response exceeded the parser safety limit.", "oversized", _summary(response))

    lower = _fence_mask(response).lower()
    open_think = lower.count("<think>")
    close_think = lower.count("</think>")
    qwen35_orphan_close = protocol == "qwen35" and open_think == 0 and close_think == 1
    if open_think != close_think and not qwen35_orphan_close:
        return InvalidResponse("Response contains an unbalanced thinking block.", protocol, _summary(response))

    visible = _visible_text(response, allow_orphan_think_close=qwen35_orphan_close)
    if not visible:
        return InvalidResponse("Model returned reasoning without a visible answer or action.", protocol, _summary(response))
    tagged = _parse_tagged(visible)
    if tagged is not None:
        return tagged

    natural = _parse_natural(visible)
    if natural is not None:
        return natural

    final = FINAL_MARKER.fullmatch(visible)
    if final:
        return FinalMessage(final.group(1).strip(), "final-marker")

    # JSON is accepted only when the entire visible response is one object.
    if visible.startswith("{"):
        try:
            value = json.loads(visible)
        except json.JSONDecodeError as error:
            return InvalidResponse(f"Incomplete or invalid whole-response JSON: {error.msg}.", "json", _summary(visible))
        return _object_call(value, "whole-json", visible)

    masked = _fence_mask(visible)
    if TOOL_SHAPE.search(masked) or "<tool_call>" in masked.lower() or "</tool_call>" in masked.lower():
        return InvalidResponse("Response resembles a tool call but is incomplete or malformed.", protocol, _summary(response))

    return FinalMessage(visible, "text")
