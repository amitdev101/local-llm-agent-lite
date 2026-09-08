from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any
from builtins import print as builtin_print
from datetime import datetime
from llama_cpp import Llama

from myllm_dual_model_prompts import (
    CHAT_SYSTEM_PROMPT,
    KID_SYSTEM_PROMPT,
    ROUTER_PROMPT,
    WORKER_SYSTEM_PROMPT,
)
from myllm_dual_model_tools import (
    TOOL_SPECS,
    WORKSPACE,
    ToolState,
    execute_worker_action,
    list_files,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / ".dualagent.log"
ROUTER_FEEDBACK_FILE = PROJECT_ROOT / ".myllm/router_feedback.jsonl"
MAX_ROUTER_RECORDS = 200
MAX_ROUTER_EXAMPLES = 3

for console_stream in (sys.stdout, sys.stderr):
    if hasattr(console_stream, "reconfigure"):
        console_stream.reconfigure(encoding="utf-8", errors="replace")


def print(*args, **kwargs):
    message = kwargs.get("sep", " ").join(str(arg) for arg in args)

    with open(LOG_FILE, "a+", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = "" if kwargs.get("end") == "" else f"[{timestamp}] "
        f.write(prefix + message + kwargs.get("end", "\n"))

    # Persist first so console failures cannot erase diagnostic evidence.
    builtin_print(*args, **kwargs)


# ============================================================
# CONFIG
# ============================================================

KID_MODEL_PATH = Path(r"D:\Amit\Projects\local-llm-agent-lite\models\Qwen3-1.7B-Q8_0.gguf")

WORKER_MODEL_PATH = Path(r"D:\Amit\Projects\local-llm-agent-lite\models\Qwen3-4B-Q4_K_M.gguf")

KID_CONTEXT = 4096
WORKER_CONTEXT = 8192

KID_TEMPERATURE = 0.15
ROUTER_TEMPERATURE = 0.1
WORKER_TEMPERATURE = 0.25

GPU_LAYERS = 0
MAX_STEPS = 20

# ============================================================
# MODEL
# ============================================================


def load_model(path: Path, context_size: int) -> Llama:
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")

    threads = min(8, max(1, (os.cpu_count() or 4) // 2))

    return Llama(
        model_path=str(path),
        n_ctx=context_size,
        n_gpu_layers=GPU_LAYERS,
        n_threads=threads,
        n_threads_batch=threads,
        use_mmap=True,
        verbose=False,
    )


def stream_model_response(
    model: Llama,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
) -> str:
    stream = model.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=temperature,
        top_p=0.9,
        stream=True,
    )
    full_content = ""

    print()
    print("RAW MODEL RESPONSE:")
    print("-" * 70)

    for chunk in stream:
        choices = chunk.get("choices", [])

        if not choices:
            continue

        text = choices[0].get("delta", {}).get("content", "")

        if text:
            full_content += text
            print(text, end="", flush=True)

    print()
    print("-" * 70)

    return full_content


def content_after_thinking(response: str) -> str:
    lowered = response.lower()

    if "<think>" in lowered and "</think>" not in lowered:
        raise ValueError("Thinking output was not closed.")

    if "</think>" in lowered:
        return re.split(r"</think>", response, flags=re.IGNORECASE)[-1]

    return response


def parse_markdown_fields(
    body: str,
) -> tuple[dict[str, str], dict[str, str]]:
    lines = body.splitlines(keepends=True)
    headers: dict[str, str] = {}
    blocks: dict[str, str] = {}
    pending_label = ""
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        label_match = re.fullmatch(r"###\s+(CONTENT|OLD|NEW)\s*", stripped)

        if label_match:
            if pending_label:
                raise ValueError(f"{pending_label} requires a fenced block.")

            pending_label = label_match.group(1)
            index += 1
            continue

        fence_match = re.fullmatch(
            r"[ \t]*(?P<fence>`{3,}|~{3,})[^\r\n]*",
            line,
        )

        if fence_match:
            fence = fence_match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            content_lines: list[str] = []
            index += 1

            while index < len(lines):
                candidate = lines[index].rstrip("\r\n")
                closing_pattern = (
                    rf"[ \t]*{re.escape(fence_character)}"
                    rf"{{{fence_length},}}[ \t]*"
                )

                if re.fullmatch(closing_pattern, candidate):
                    break

                content_lines.append(lines[index])
                index += 1

            if index >= len(lines):
                raise ValueError("Fenced content was not closed.")

            label = pending_label or "CONTENT"

            if label in blocks:
                raise ValueError(f"Duplicate {label} block.")

            blocks[label] = "".join(content_lines)
            pending_label = ""
            index += 1
            continue

        if pending_label:
            raise ValueError(f"{pending_label} must be followed by a fenced block.")

        header_match = re.fullmatch(r"([a-z_]+):[ \t]*(.*)", stripped)

        if not header_match:
            raise ValueError(f"Unexpected action line: {stripped[:80]}")

        name = header_match.group(1)
        value = header_match.group(2).strip()

        if name in headers:
            raise ValueError(f"Duplicate field: {name}")

        headers[name] = value
        index += 1

    if pending_label:
        raise ValueError(f"{pending_label} requires a fenced block.")

    return headers, blocks


def parse_worker_action(response: str) -> dict[str, Any]:
    visible = content_after_thinking(response)
    tool_match = re.search(
        r"(?m)^[ \t.]*##[ \t]+TOOL[ \t]+([a-z_]+)[ \t]*$",
        visible,
    )

    if not tool_match:
        raise ValueError("Missing '## TOOL <name>' action footer.")

    tool = tool_match.group(1)
    spec = TOOL_SPECS.get(tool)

    if spec is None:
        raise ValueError(f"Unknown tool: {tool}")

    headers, blocks = parse_markdown_fields(visible[tool_match.end():])
    required = set(spec["required"])
    optional = set(spec["optional"])
    expected_blocks = set(spec["blocks"])
    headers = {
        name: value
        for name, value in headers.items()
        if value or name in required
    }
    missing = required - set(headers)
    unexpected = set(headers) - required - optional

    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(sorted(missing))}.")

    if unexpected:
        raise ValueError(f"Unexpected field(s): {', '.join(sorted(unexpected))}.")

    if set(blocks) != expected_blocks:
        missing_blocks = expected_blocks - set(blocks)
        extra_blocks = set(blocks) - expected_blocks
        details = []

        if missing_blocks:
            details.append(f"missing {', '.join(sorted(missing_blocks))}")

        if extra_blocks:
            details.append(f"unexpected {', '.join(sorted(extra_blocks))}")

        raise ValueError("Invalid fenced blocks: " + "; ".join(details) + ".")

    args: dict[str, Any] = dict(headers)

    for integer_field in ("depth", "start_line", "end_line"):
        if integer_field in args:
            try:
                args[integer_field] = int(args[integer_field])
            except ValueError as error:
                raise ValueError(f"{integer_field} must be an integer.") from error

    if tool == "write_file":
        args["content"] = blocks["CONTENT"]
    elif tool == "patch_file":
        args["old_text"] = blocks["OLD"]
        args["new_text"] = blocks["NEW"]

    return {
        "tool": tool,
        "args": args,
        "message": f"Run {tool}.",
    }


def parse_kid_decision(response: str) -> dict[str, str]:
    visible = content_after_thinking(response)
    decision_match = re.search(
        r"(?m)^[ \t.]*##[ \t]+DECISION[ \t]+(continue|done)[ \t]*$",
        visible,
        flags=re.IGNORECASE,
    )

    if not decision_match:
        raise ValueError("Missing '## DECISION continue|done' footer.")

    fields, blocks = parse_markdown_fields(visible[decision_match.end():])

    if blocks:
        raise ValueError("Kid decisions cannot contain fenced blocks.")

    if set(fields) != {"request"}:
        raise ValueError("Kid decision requires exactly one request field.")

    status = decision_match.group(1).lower()
    request = fields["request"]

    if status == "continue" and not request:
        raise ValueError("A continue decision requires a request.")

    return {
        "status": status,
        "request": request,
    }


def log_exception(context: str) -> None:
    print()
    print(f"❌ {context}")
    print(traceback.format_exc().rstrip())


def ask_route(
    model: Llama,
    user_prompt: str,
) -> str:
    response = model.create_chat_completion(
        messages=[
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=ROUTER_TEMPERATURE,
        stream=False,
    )

    raw = str(response.get("choices", [{}])[0].get("message", {}).get("content", ""))
    labels = re.findall(r"(?m)^\s*(CHAT|TOOL)\s*$", raw.upper())
    route = labels[-1] if labels else "CHAT"

    print(f"🧭 Router: {route} | raw={raw!r}")

    return route


def stream_chat(
    model: Llama,
    task: str,
    history: list[dict[str, str]],
) -> str:
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.extend(history[-8:])
    messages.append({"role": "user", "content": task})

    stream = model.create_chat_completion(
        messages=messages,
        temperature=0.7,
        top_p=0.9,
        stream=True,
    )

    response = ""

    print()
    print("💬 CHAT")

    for chunk in stream:
        text = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")

        if text:
            print(text, end="", flush=True)
            response += text

    print()

    return response


# ============================================================
# ROUTER MEMORY
# ============================================================


def normalize_router_message(message: str) -> str:
    return " ".join(message.strip().split())[:500]


def router_tokens(message: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", message.lower()))


def load_router_feedback() -> list[dict[str, Any]]:
    if not ROUTER_FEEDBACK_FILE.exists():
        return []

    records: list[dict[str, Any]] = []

    for line in ROUTER_FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if (record.get("confirmed") is True and record.get("route") in {"CHAT", "TOOL"}
                and isinstance(record.get("message"), str)):
            records.append(record)

    return records[-MAX_ROUTER_RECORDS:]


def save_router_feedback(
    message: str,
    route: str,
    source: str,
) -> None:
    normalized = normalize_router_message(message)

    if not normalized or route not in {"CHAT", "TOOL"}:
        return

    records = load_router_feedback()

    if any(record["message"] == normalized and record["route"] == route for record in records):
        return

    ROUTER_FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "message": normalized,
        "route": route,
        "source": source,
        "confirmed": True,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    with ROUTER_FEEDBACK_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"🧠 Learned confirmed route: {route} ({source})")


def select_router_examples(message: str) -> list[dict[str, Any]]:
    query_tokens = router_tokens(message)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    newest_by_message: dict[str, tuple[int, dict[str, Any]]] = {}

    for index, record in enumerate(load_router_feedback()):
        newest_by_message[record["message"]] = (index, record)

    for index, record in newest_by_message.values():
        candidate_tokens = router_tokens(record["message"])
        union = query_tokens | candidate_tokens
        score = len(query_tokens & candidate_tokens) / len(union) if union else 0.0

        if score > 0:
            scored.append((score, index, record))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    return [item[2] for item in scored[:MAX_ROUTER_EXAMPLES]]


def build_router_input(
    task: str,
    route_history: list[dict[str, str]],
) -> str:
    recent = "\n".join(
        f'- Message: "{item["message"]}" -> {item["route"]}'
        for item in route_history[-4:]
    ) or "(none)"

    learned = []

    for record in select_router_examples(task):
        safe_message = record["message"].replace("<", "[").replace(">", "]")
        learned.append(f'- <example message="{safe_message}" route="{record["route"]}" />')

    return ("RECENT CONVERSATION ROUTES:\n"
            f"{recent}\n\n"
            "CONFIRMED LEARNED EXAMPLES:\n"
            f"{chr(10).join(learned) or '(none)'}\n\n"
            "CURRENT USER MESSAGE:\n"
            f"<current_message>{normalize_router_message(task)}</current_message>")


# ============================================================
# STATE
# ============================================================


def workspace_state() -> str:
    return list_files()


# ============================================================
# AGENT LOOP
# ============================================================


def run_agent(
    task: str,
    kid: Llama,
    worker: Llama,
) -> bool:
    observations: list[str] = []
    state = ToolState()
    tool_succeeded = False
    kid_request = "Start the task."

    for step in range(
            1,
            MAX_STEPS + 1,
    ):
        print()
        print("=" * 70)
        print(f"STEP {step}/{MAX_STEPS}")
        print("=" * 70)

        worker_prompt = f"""
GOAL:
{task}

NEXT STEP:
{kid_request}

WORKSPACE:
{workspace_state()}

LAST RESULT:
{observations[-1] if observations else "(none)"}

Return one tool action.
"""

        print()
        print("👷 WORKER")

        worker_response = stream_model_response(
            model=worker,
            system_prompt=WORKER_SYSTEM_PROMPT,
            user_prompt=worker_prompt,
            temperature=WORKER_TEMPERATURE,
        )

        try:
            worker_action = parse_worker_action(worker_response)
        except ValueError as error:
            print(f"⚠️ Invalid Worker action: {error}")
            worker_action = {
                "tool": "format_error",
                "args": {},
                "message": f"FORMAT_ERROR: {error}",
            }

        tool = str(worker_action.get(
            "tool",
            "",
        ))

        message = str(worker_action.get(
            "message",
            "",
        ))

        print()
        print(f"Worker tool    : {tool}")
        print(f"Worker message : {message}")

        signature = json.dumps(
            {
                "tool": tool,
                "args": worker_action.get("args", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        if signature == state.last_action_signature:
            state.repeated_action_count += 1
        else:
            state.last_action_signature = signature
            state.repeated_action_count = 1

        if tool == "format_error":
            success = False
            output = message
        elif state.repeated_action_count >= 3:
            success = False
            output = "REPEATED_ACTION: Choose a different action or read current evidence."
        else:
            success, output = execute_worker_action(worker_action, state, task)

        tool_succeeded = tool_succeeded or success

        observation = f"TOOL: {tool}\n" f"SUCCESS: {success}\n" f"RESULT:\n{output}"

        observations.append(observation)

        print()
        print("⚙️ CONTROLLER")
        print(observation)

        kid_prompt = f"""
GOAL:
{task}

WORKER RESULT:
{observation}

WORKSPACE NOW:
{workspace_state()}

EDIT REVISION: {state.mutation_revision}
VERIFIED REVISION: {state.verified_revision}

Decide whether the goal is complete.
"""

        print()
        print("👶 KID")

        kid_response = stream_model_response(
            model=kid,
            system_prompt=KID_SYSTEM_PROMPT,
            user_prompt=kid_prompt,
            temperature=KID_TEMPERATURE,
        )

        try:
            kid_result = parse_kid_decision(kid_response)
        except ValueError as error:
            print(f"⚠️ Invalid Kid decision: {error}")
            kid_result = {
                "status": "continue",
                "request": "Continue from the last controller result.",
            }

        status = str(kid_result.get("status", ""))
        kid_request = str(kid_result.get("request", ""))

        print()
        print(f"Kid status  : {status}")
        print(f"Kid request : {kid_request}")

        if status == "done":
            if state.mutation_revision > state.verified_revision:
                kid_request = "Run run_check with kind auto."
                print()
                print("🚫 CONTROLLER: Completion requires a successful check after edits.")
                continue

            print()
            print("✅ KID ACCEPTED COMPLETION")
            return tool_succeeded

    print()
    print(f"🛑 Controller stopped after "
          f"{MAX_STEPS} steps.")

    return False


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    WORKSPACE.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("Loading Kid model...")

    kid = load_model(
        KID_MODEL_PATH,
        KID_CONTEXT,
    )

    print("Loading Worker model...")

    worker = load_model(
        WORKER_MODEL_PATH,
        WORKER_CONTEXT,
    )

    print()
    print("✅ Models loaded.")

    chat_history: list[dict[str, str]] = []
    route_history: list[dict[str, str]] = []
    last_task = ""

    while True:
        try:
            task = input("\n👤 Task (/exit, /route chat, /route tool): ").strip()
        except EOFError:
            print()
            print("🛑 Input stream closed.")
            break

        if task.lower() in {
                "/exit",
                "exit",
                "quit",
        }:
            break

        if not task:
            continue

        forced_route = ""
        correction = re.fullmatch(r"/route\s+(chat|tool)", task, re.IGNORECASE)

        if correction:
            if not last_task:
                print("⚠️ There is no previous request to reroute.")
                continue

            forced_route = correction.group(1).upper()
            save_router_feedback(last_task, forced_route, "user")
            task = last_task

        else:
            last_task = task

        try:
            route = forced_route or ask_route(
                kid,
                build_router_input(task, route_history),
            )

            print(f"➡️ Route selected: {route}")

            if route == "CHAT":
                response = stream_chat(worker, task, chat_history)
                chat_history.extend([
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": response},
                ])

            else:
                run_agent(task, kid, worker)

            route_history.append({
                "message": normalize_router_message(task),
                "route": route,
            })

        except KeyboardInterrupt:
            print()
            print("🛑 Agent loop manually stopped.")

        except Exception:
            log_exception("Task failed. See the traceback below.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_exception("Application failed during startup or shutdown.")
        raise
