from __future__ import annotations

import json
import re

from typing import Any


TOOL_ALIASES = {
    "create_file": "create_files",
    "write_file": "create_files",
    "edit_file": "replace_file",
    "patch_file": "apply_patch",
    "run_tests": "run_project_tests",
    "run_build": "run_project_build",
    "run_lint": "run_project_lint",
    "run_typecheck": "run_project_typecheck",
}

FIELD_ALIASES = {
    "file": "path",
    "filename": "path",
}

CONTENT_TOOLS = {
    "create_files",
    "replace_file",
}

ACTION_MARKER = re.compile(
    r"(?im)^\s*(?:ACTION|TOOL)\s*:\s*([a-z][a-z0-9_-]*)\s*$"
    r"|^\s*##\s+TOOL\s+([a-z][a-z0-9_-]*)\s*$",
)


def visible_content(response: str) -> str:
    visible = re.sub(
        r"<think>.*?</think>",
        "",
        response,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    if "</think>" in visible.lower():
        parts = re.split(
            r"</think>",
            visible,
            flags=re.IGNORECASE,
        )
        visible = parts[-1].strip()

    return visible


def normalize_tool_name(name: str) -> str:
    normalized = name.strip().lower().replace("-", "_")
    return TOOL_ALIASES.get(normalized, normalized)


def normalize_action(
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    tool_name = normalize_tool_name(tool_name)
    normalized_args = {
        FIELD_ALIASES.get(str(key).lower(), str(key).lower()): value
        for key, value in args.items()
    }

    if tool_name == "create_files" and "files" not in normalized_args:
        path = normalized_args.pop("path", "")
        content = normalized_args.pop("content", None)

        if path and content is not None:
            normalized_args["files"] = [{
                "path": path,
                "content": content,
            }]

    return {
        "type": "tool",
        "tool": tool_name,
        "args": normalized_args,
        "message": "",
    }


def parse_scalar(value: str) -> Any:
    value = value.strip()

    if not value:
        return ""

    if value.lower() in {"true", "false"}:
        return value.lower() == "true"

    if value.lower() == "null":
        return None

    if value[0] in "[{\"" or re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

    return value


def read_fence(
    lines: list[str],
    start: int,
) -> tuple[str, int]:
    opening = lines[start].strip()

    if not opening.startswith("```"):
        raise ValueError("Expected a fenced multiline value.")

    content: list[str] = []
    index = start + 1

    while index < len(lines):
        if lines[index].strip() == "```":
            return "\n".join(content), index + 1

        content.append(lines[index])
        index += 1

    raise ValueError("Unclosed fenced multiline value.")


def parse_natural_action(response: str) -> dict[str, Any] | None:
    matches = list(ACTION_MARKER.finditer(response))

    if not matches:
        return None

    marker = matches[-1]
    tool_name = normalize_tool_name(marker.group(1) or marker.group(2) or "")
    action_body = response[marker.end():].strip()

    if tool_name == "create_files" and action_body.startswith("["):
        try:
            files, end = json.JSONDecoder().raw_decode(action_body)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid create_files array: {error.msg}.") from error

        if action_body[end:].strip():
            raise ValueError("Unexpected text after the create_files array.")

        if not isinstance(files, list):
            raise ValueError("create_files requires a JSON array.")

        return normalize_action(tool_name, {"files": files})

    lines = action_body.splitlines()
    args: dict[str, Any] = {}
    index = 0

    while index < len(lines):
        line = lines[index]

        if not line.strip():
            index += 1
            continue

        field_match = re.fullmatch(
            r"\s*([a-z][a-z0-9_-]*)\s*:\s*(.*)",
            line,
            flags=re.IGNORECASE,
        )

        if field_match:
            field = FIELD_ALIASES.get(
                field_match.group(1).lower().replace("-", "_"),
                field_match.group(1).lower().replace("-", "_"),
            )
            value = field_match.group(2)

            if field in args:
                raise ValueError(f"Duplicate action field: {field}.")

            next_index = index + 1

            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1

            if not value.strip() and next_index < len(lines):
                if lines[next_index].strip().startswith("```"):
                    args[field], index = read_fence(lines, next_index)
                    continue

            args[field] = parse_scalar(value)
            index += 1
            continue

        if (line.strip().startswith("```")
                and tool_name in CONTENT_TOOLS
                and "content" not in args):
            args["content"], index = read_fence(lines, index)
            continue

        raise ValueError(f"Unexpected action line: {line.strip()[:80]}")

    return normalize_action(tool_name, args)


def action_from_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    tool_name = value.get("tool", value.get("name"))

    if not isinstance(tool_name, str) or not tool_name.strip():
        return None

    args = value.get("args", value.get("arguments", {}))

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None

    if not isinstance(args, dict):
        return None

    return normalize_action(tool_name, args)


def parse_json_action(response: str) -> tuple[dict[str, Any] | None, int]:
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int, dict[str, Any]]] = []

    for match in re.finditer(r"\{", response):
        try:
            value, end = decoder.raw_decode(response, match.start())
        except json.JSONDecodeError:
            continue

        action = action_from_object(value)

        if action is not None:
            candidates.append((end, match.start(), action))

    if not candidates:
        return None, -1

    _, start, action = max(candidates, key=lambda candidate: candidate[0])
    return action, start


def parse_tagged_action(response: str) -> dict[str, Any] | None:
    blocks = re.findall(
        r"<tool_call>(.*?)</tool_call>",
        response,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if not blocks:
        return None

    block = blocks[-1]
    action, _ = parse_json_action(block)

    if action is not None:
        return action

    function_match = re.search(
        r"<function=([a-z][a-z0-9_-]*)>(.*?)</function>",
        block,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if not function_match:
        return None

    args = {
        name.lower().replace("-", "_"): value.strip()
        for name, value in re.findall(
            r"<parameter=([a-z][a-z0-9_-]*)>(.*?)</parameter>",
            function_match.group(2),
            flags=re.DOTALL | re.IGNORECASE,
        )
    }
    return normalize_action(function_match.group(1), args)


def parse_model_response(response: str) -> tuple[dict[str, Any], str]:
    visible = visible_content(response)

    try:
        natural = parse_natural_action(visible)
    except ValueError as error:
        return ({
            "type": "invalid",
            "tool": "",
            "args": {},
            "message": f"Invalid natural tool action: {error}",
        }, "invalid-natural")

    if natural is not None:
        return natural, "natural"

    tagged = parse_tagged_action(response)

    if tagged is not None:
        return tagged, "tagged"

    json_action, offset = parse_json_action(response)

    if json_action is not None:
        return json_action, "mixed-json" if offset > 0 else "json"

    tool_shaped = (
        re.search(r'"(?:tool|name)"\s*:', response)
        or re.search(r"<tool_call>", response, flags=re.IGNORECASE)
        or ACTION_MARKER.search(response)
    )

    if tool_shaped or visible.startswith("{"):
        return ({
            "type": "invalid",
            "tool": "",
            "args": {},
            "message": "Tool-shaped output was present but incomplete or invalid.",
        }, "invalid")

    return ({
        "type": "final",
        "tool": "",
        "args": {},
        "message": visible,
    }, "final")
