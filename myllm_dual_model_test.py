from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from builtins import print as builtin_print
from datetime import datetime
from llama_cpp import Llama

LOG_FILE = ".dualagent.log"


def print(*args, **kwargs):
    # Print normally to console
    builtin_print(*args, **kwargs)

    # Append to log file
    message = " ".join(str(arg) for arg in args)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {message}\n")


# ============================================================
# CONFIG
# ============================================================

KID_MODEL_PATH = Path(r"D:\Amit\Projects\local-llm-agent-lite\models\Qwen3-1.7B-Q8_0.gguf")

WORKER_MODEL_PATH = Path(r"D:\Amit\Projects\local-llm-agent-lite\models\Qwen3-4B-Q4_K_M.gguf")

WORKSPACE = Path("./agent_test_workspace").resolve()

KID_CONTEXT = 4096
WORKER_CONTEXT = 8192

KID_TEMPERATURE = 0.15
WORKER_TEMPERATURE = 0.25

GPU_LAYERS = 0
MAX_STEPS = 20

# ============================================================
# JSON SCHEMAS
# ============================================================

KID_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["continue", "done"],
        },
        "request": {
            "type": "string",
        },
    },
    "required": [
        "status",
        "request",
    ],
    "additionalProperties": False,
}

WORKER_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "enum": [
                "list_files",
                "read_file",
                "write_file",
                "compile_java",
            ],
        },
        "args": {
            "type": "object",
        },
        "message": {
            "type": "string",
        },
    },
    "required": [
        "tool",
        "args",
        "message",
    ],
    "additionalProperties": False,
}

# ============================================================
# PROMPTS
# ============================================================

KID_SYSTEM_PROMPT = """
You are the Kid in a two-model coding agent.

Your only job is to check whether the user's task is actually complete.

You NEVER modify files.
You NEVER execute tools.
You NEVER write implementation code.

You receive:
- the user's goal
- current workspace state
- recent controller observations

Return:

{
    "status": "done" or "continue",
    "request": "short instruction for the Worker"
}

Use status="done" only when the available evidence proves that the user's
request has been completed.

For source-code tasks, successful compilation or another appropriate
deterministic verification is strong completion evidence.

If work remains, use status="continue" and tell the Worker what needs attention.

Do not invent files, build results, compiler results, or other facts.

Keep request short.
"""

WORKER_SYSTEM_PROMPT = """
You are the Worker in a two-model coding agent.

You are the planner and implementer.

The Kid tells you what still needs attention.
The Controller executes your tools.

Choose exactly ONE tool action per response.

Available tools:

list_files()
read_file(path)
write_file(path, content)
compile_java()

Rules:

- Use only the tools listed above.
- Do not invent tool results.
- Inspect existing files when needed.
- write_file may create or completely replace a text file.
- For Java work, produce complete compilable source code.
- Do not leave placeholders, TODO implementations, or fake code.
- After Java changes, compile before assuming the task is complete.
- If compilation fails, use the compiler error to fix the source.
- Keep message short.
"""

# ============================================================
# MODEL
# ============================================================


def load_model(path: Path, context_size: int) -> Llama:
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")

    threads = max(1, (os.cpu_count() or 4) - 2)

    return Llama(
        model_path=str(path),
        n_ctx=context_size,
        n_gpu_layers=GPU_LAYERS,
        n_threads=threads,
        n_threads_batch=threads,
        use_mmap=True,
        verbose=False,
    )


def ask_json(
    model: Llama,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    temperature: float,
) -> dict[str, Any]:
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
        response_format={
            "type": "json_object",
            "schema": schema,
        },
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

        delta = choices[0].get("delta", {})
        text = delta.get("content", "")

        if not text:
            continue

        full_content += text

        print(
            text,
            end="",
            flush=True,
        )

    print()
    print("-" * 70)

    return json.loads(full_content)


# ============================================================
# SAFE WORKSPACE
# ============================================================


def resolve_workspace_path(relative_path: str) -> Path:
    candidate = (WORKSPACE / relative_path).resolve()

    try:
        candidate.relative_to(WORKSPACE)
    except ValueError as error:
        raise ValueError(f"Path escapes workspace: {relative_path}") from error

    return candidate


def list_files() -> str:
    files = [
        str(path.relative_to(WORKSPACE)) for path in WORKSPACE.rglob("*")
        if path.is_file() and ".build" not in path.parts
    ]

    if not files:
        return "Workspace contains no files."

    return "\n".join(sorted(files))


def read_file(path: str) -> str:
    target = resolve_workspace_path(path)

    if not target.exists():
        raise ValueError(f"File does not exist: {path}")

    if not target.is_file():
        raise ValueError(f"Not a file: {path}")

    return target.read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    target = resolve_workspace_path(path)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    target.write_text(
        content,
        encoding="utf-8",
    )

    return f"Wrote {len(content):,} characters to {path}"


def compile_java() -> str:
    java_files = sorted(path for path in WORKSPACE.rglob("*.java") if ".build" not in path.parts)

    if not java_files:
        raise ValueError("No Java source files found.")

    javac = shutil.which("javac")

    if javac is None:
        raise ValueError("javac was not found on PATH.")

    build_directory = WORKSPACE / ".build"

    if build_directory.exists():
        shutil.rmtree(build_directory)

    build_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        javac,
        "-d",
        str(build_directory),
        *[str(path) for path in java_files],
    ]

    process = subprocess.run(
        command,
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        shell=False,
        timeout=120,
    )

    output = "\n".join(part for part in (
        process.stdout.strip(),
        process.stderr.strip(),
    ) if part)

    result = (f"COMMAND: {' '.join(command)}\n"
              f"EXIT_CODE: {process.returncode}\n\n"
              f"{output or 'Compilation succeeded.'}")

    if process.returncode != 0:
        raise ValueError(result)

    return result


# ============================================================
# CONTROLLER
# ============================================================


def execute_worker_action(action: dict[str, Any], ) -> tuple[bool, str]:
    tool = action.get("tool")
    args = action.get("args", {})

    if not isinstance(args, dict):
        return False, "args must be an object."

    try:
        if tool == "list_files":
            if args:
                raise ValueError("list_files takes no arguments.")

            return True, list_files()

        if tool == "read_file":
            path = args.get("path")

            if not isinstance(path, str) or not path:
                raise ValueError("read_file requires path.")

            return True, read_file(path)

        if tool == "write_file":
            path = args.get("path")
            content = args.get("content")

            if not isinstance(path, str) or not path:
                raise ValueError("write_file requires path.")

            if not isinstance(content, str):
                raise ValueError("write_file requires content.")

            return True, write_file(
                path,
                content,
            )

        if tool == "compile_java":
            if args:
                raise ValueError("compile_java takes no arguments.")

            return True, compile_java()

        return False, f"Unknown tool: {tool}"

    except Exception as error:
        return (
            False,
            f"{type(error).__name__}: {error}",
        )


# ============================================================
# STATE
# ============================================================


def workspace_state() -> str:
    return list_files()


def recent_observations_text(observations: list[str], ) -> str:
    if not observations:
        return "(none)"

    return "\n\n".join(observations[-6:])


# ============================================================
# AGENT LOOP
# ============================================================


def run_agent(
    task: str,
    kid: Llama,
    worker: Llama,
) -> None:
    observations: list[str] = []

    for step in range(
            1,
            MAX_STEPS + 1,
    ):
        print()
        print("=" * 70)
        print(f"STEP {step}/{MAX_STEPS}")
        print("=" * 70)

        kid_prompt = f"""
USER GOAL:
{task}

WORKSPACE:
{workspace_state()}

RECENT CONTROLLER OBSERVATIONS:
{recent_observations_text(observations)}

Determine whether the user's goal is complete.
"""

        print()
        print("👶 KID")

        kid_result = ask_json(
            model=kid,
            system_prompt=KID_SYSTEM_PROMPT,
            user_prompt=kid_prompt,
            schema=KID_SCHEMA,
            temperature=KID_TEMPERATURE,
        )

        status = str(kid_result.get(
            "status",
            "",
        ))

        request = str(kid_result.get(
            "request",
            "",
        ))

        print()
        print(f"Kid status  : {status}")
        print(f"Kid request : {request}")

        if status == "done":
            print()
            print("✅ KID ACCEPTED COMPLETION")
            return

        worker_prompt = f"""
USER GOAL:
{task}

KID REQUEST:
{request}

CURRENT WORKSPACE:
{workspace_state()}

RECENT CONTROLLER OBSERVATIONS:
{recent_observations_text(observations)}

Choose the single best next tool action.
"""

        print()
        print("👷 WORKER")

        worker_action = ask_json(
            model=worker,
            system_prompt=WORKER_SYSTEM_PROMPT,
            user_prompt=worker_prompt,
            schema=WORKER_SCHEMA,
            temperature=WORKER_TEMPERATURE,
        )

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

        success, output = execute_worker_action(worker_action)

        observation = f"TOOL: {tool}\n" f"SUCCESS: {success}\n" f"RESULT:\n{output}"

        observations.append(observation)

        print()
        print("⚙️ CONTROLLER")
        print(observation)

    print()
    print(f"🛑 Controller stopped after "
          f"{MAX_STEPS} steps.")


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

    while True:
        task = input("\n👤 Task (/exit to quit): ").strip()

        if task.lower() in {
                "/exit",
                "exit",
                "quit",
        }:
            break

        if not task:
            continue

        try:
            run_agent(
                task,
                kid,
                worker,
            )

        except KeyboardInterrupt:
            print()
            print("🛑 Agent loop manually stopped.")


if __name__ == "__main__":
    main()
