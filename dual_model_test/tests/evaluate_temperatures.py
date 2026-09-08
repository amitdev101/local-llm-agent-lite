from __future__ import annotations

import argparse
import gc
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DUAL_MODEL_DIRECTORY = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DUAL_MODEL_DIRECTORY.parent
sys.path.insert(0, str(DUAL_MODEL_DIRECTORY))

from myllm_dual_model_prompts import (  # noqa: E402
    KID_SYSTEM_PROMPT,
    WORKER_SYSTEM_PROMPT,
)
from myllm_dual_model_test import (  # noqa: E402
    KID_CONTEXT,
    KID_MODEL_PATH,
    WORKER_CONTEXT,
    WORKER_MODEL_PATH,
    load_model,
    parse_kid_decision,
    parse_worker_action,
)
from temperature_benchmark_common import (  # noqa: E402
    DEFAULT_SEEDS,
    RESULTS_DIRECTORY,
    SAMPLING,
    append_jsonl,
    create_completion,
    parse_numbers,
)


DEFAULT_TEMPERATURES = {
    "kid": (0.15, 0.30, 0.45, 0.60),
    "worker": (0.25, 0.40, 0.60, 0.75),
}


KID_SCENARIOS = (
    {
        "name": "reject_stale_verification",
        "critical": True,
        "expected": {"status": "continue", "request_contains": "check"},
        "prompt": """
GOAL:
Update Game.java and leave it compiling.

WORKER RESULT:
TOOL: patch_file
SUCCESS: True
RESULT:
Game.java was changed after its previous successful compilation.

WORKSPACE NOW:
FILE Game.java

EDIT REVISION: 7
VERIFIED REVISION: 6
Decide whether the goal is complete.
""",
    },
    {
        "name": "reject_failed_check",
        "critical": True,
        "expected": {"status": "continue"},
        "prompt": """
GOAL:
Fix App.java so it compiles.

WORKER RESULT:
TOOL: run_check
SUCCESS: False
RESULT:
App.java:18: error: cannot find symbol

WORKSPACE NOW:
FILE App.java

EDIT REVISION: 3
VERIFIED REVISION: 2
Decide whether the goal is complete.
""",
    },
    {
        "name": "accept_verified_change",
        "critical": False,
        "expected": {"status": "done"},
        "prompt": """
GOAL:
Create a valid Java 8 command-line program.

WORKER RESULT:
TOOL: run_check
SUCCESS: True
RESULT:
Java compilation succeeded with exit code 0.

WORKSPACE NOW:
FILE Main.java

CURRENT RUN EVIDENCE:
write_file created Main.java, then run_check compiled that exact revision.
EDIT REVISION: 1
VERIFIED REVISION: 1
Decide whether the goal is complete.
""",
    },
    {
        "name": "reject_inspection_for_change_goal",
        "critical": True,
        "expected": {"status": "continue"},
        "prompt": """
GOAL:
Add score tracking to Game.java.

WORKER RESULT:
TOOL: read_file
SUCCESS: True
RESULT:
Game.java was read successfully. No source was changed.

WORKSPACE NOW:
FILE Game.java

EDIT REVISION: 0
VERIFIED REVISION: 0
Decide whether the goal is complete.
""",
    },
)


WORKER_SCENARIOS = (
    {
        "name": "check_after_patch",
        "critical": True,
        "expected": {"tool": "run_check", "kind_in": {"auto", "build"}},
        "prompt": """
GOAL:
Fix Main.java and leave it compiling.

NEXT STEP:
Compile and validate the source changed in the previous step.

WORKSPACE:
FILE Main.java

LAST RESULT:
TOOL: patch_file
SUCCESS: True
RESULT:
Updated Main.java.

Return one tool action.
""",
    },
    {
        "name": "search_before_unknown_edit",
        "critical": False,
        "expected": {"tool": "search", "query": "calculateScore"},
        "prompt": """
GOAL:
Update every use of calculateScore in the project.

NEXT STEP:
Find where calculateScore is used before editing anything.

WORKSPACE:
FILE src/Game.java
FILE src/Score.java

LAST RESULT:
(none)

Return one tool action.
""",
    },
    {
        "name": "patch_small_read_file",
        "critical": False,
        "expected": {"tool": "patch_file", "path": "config.py"},
        "prompt": """
GOAL:
Change TIMEOUT from 10 to 20 in config.py.

NEXT STEP:
Apply the exact small change now.

WORKSPACE:
FILE config.py

LAST RESULT:
TOOL: read_file
SUCCESS: True
RESULT:
FILE: config.py
LINES: 1-2 / 2

TIMEOUT = 10
RETRIES = 3

Return one tool action.
""",
    },
    {
        "name": "create_complete_python_file",
        "critical": False,
        "expected": {
            "tool": "write_file",
            "path": "hello.py",
            "content_contains": "print",
        },
        "prompt": """
GOAL:
Create hello.py that prints Hello.

NEXT STEP:
Create the complete file now.

WORKSPACE:
(empty)

LAST RESULT:
(none)

Return one tool action.
""",
    },
)


def evaluate(role: str, response: str, expected: dict[str, Any]) -> tuple[bool, dict, str]:
    parsed = parse_kid_decision(response) if role == "kid" else parse_worker_action(response)
    actual = {"status": parsed["status"]} if role == "kid" else {
        "tool": parsed["tool"],
        "args": parsed["args"],
    }
    failures = []

    if role == "kid":
        if parsed["status"] != expected["status"]:
            failures.append(f"expected status {expected['status']}")
        if expected.get("request_contains") not in (None, ""):
            if expected["request_contains"].lower() not in parsed["request"].lower():
                failures.append(f"request must mention {expected['request_contains']}")
    else:
        args = parsed["args"]
        if parsed["tool"] != expected["tool"]:
            failures.append(f"expected tool {expected['tool']}")
        if "path" in expected and args.get("path") != expected["path"]:
            failures.append(f"expected path {expected['path']}")
        if "query" in expected and expected["query"].lower() not in str(args.get("query", "")).lower():
            failures.append(f"query must contain {expected['query']}")
        if "kind_in" in expected and args.get("kind", "auto") not in expected["kind_in"]:
            failures.append(f"unexpected check kind {args.get('kind')}")
        if "content_contains" in expected:
            content = str(args.get("content", ""))
            if expected["content_contains"].lower() not in content.lower():
                failures.append(f"content must contain {expected['content_contains']}")

    return not failures, actual, "; ".join(failures)


def run_role(
    role: str,
    temperatures: tuple[float, ...],
    seeds: tuple[int, ...],
    output_path: Path,
) -> list[dict[str, Any]]:
    if role == "kid":
        model_path, context, prompt, scenarios = (
            KID_MODEL_PATH,
            KID_CONTEXT,
            KID_SYSTEM_PROMPT,
            KID_SCENARIOS,
        )
    else:
        model_path, context, prompt, scenarios = (
            WORKER_MODEL_PATH,
            WORKER_CONTEXT,
            WORKER_SYSTEM_PROMPT,
            WORKER_SCENARIOS,
        )

    print(f"\nLoading {role} model: {model_path.name}")
    model = load_model(model_path, context)
    results = []
    total = len(temperatures) * len(seeds) * len(scenarios)
    current = 0

    try:
        for temperature in temperatures:
            for seed in seeds:
                for scenario in scenarios:
                    current += 1
                    print(
                        f"[{role} {current}/{total}] temperature={temperature:.2f} "
                        f"seed={seed} scenario={scenario['name']}",
                        flush=True,
                    )
                    record = {
                        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "role": role,
                        "model": model_path.name,
                        "scenario": scenario["name"],
                        "temperature": temperature,
                        "seed": seed,
                        "sampling": SAMPLING,
                        "expected": {
                            key: sorted(value) if isinstance(value, set) else value
                            for key, value in scenario["expected"].items()
                        },
                        "critical": scenario["critical"],
                    }
                    response = ""
                    usage = {}
                    duration = None

                    try:
                        response, usage, duration, _ = create_completion(
                            model,
                            prompt,
                            scenario["prompt"],
                            temperature,
                            seed,
                        )
                        passed, actual, reason = evaluate(
                            role,
                            response,
                            scenario["expected"],
                        )
                        record.update({
                            "parse_success": True,
                            "passed": passed,
                            "critical_failure": scenario["critical"] and not passed,
                            "actual": actual,
                            "failure_reason": reason,
                            "duration_seconds": round(duration, 3),
                            "usage": usage,
                            "response": response,
                        })
                    except Exception as error:
                        record.update({
                            "parse_success": False,
                            "passed": False,
                            "critical_failure": scenario["critical"],
                            "actual": {},
                            "failure_reason": f"{type(error).__name__}: {error}",
                            "duration_seconds": round(duration, 3) if duration is not None else None,
                            "usage": usage,
                            "response": response,
                        })

                    append_jsonl(output_path, record)
                    results.append(record)
                    result_label = "PASS" if record["passed"] else "FAIL"
                    print(f"  {result_label}: {record['failure_reason'] or 'expected result'}")
    finally:
        model.close()
        del model
        gc.collect()

    return results


def print_summary(results: list[dict[str, Any]]) -> None:
    grouped = defaultdict(list)

    for result in results:
        grouped[(result["role"], result["temperature"])].append(result)

    print("\nSUMMARY")
    print("role    temp   passed   parsed   critical   avg sec   avg tokens")
    print("-" * 70)

    by_role = defaultdict(list)

    for (role, temperature), rows in sorted(grouped.items()):
        passed = sum(row["passed"] for row in rows)
        parsed = sum(row["parse_success"] for row in rows)
        critical = sum(row["critical_failure"] for row in rows)
        durations = [row["duration_seconds"] for row in rows if row["duration_seconds"] is not None]
        tokens = [
            row["usage"].get("completion_tokens")
            for row in rows
            if isinstance(row["usage"].get("completion_tokens"), int)
        ]
        average_duration = statistics.mean(durations) if durations else 0
        average_tokens = statistics.mean(tokens) if tokens else 0
        pass_rate = passed / len(rows)
        by_role[role].append((critical, -pass_rate, average_duration, average_tokens, temperature))
        print(
            f"{role:<7} {temperature:>4.2f}   {passed:>2}/{len(rows):<2}    "
            f"{parsed:>2}/{len(rows):<2}    {critical:^8}   "
            f"{average_duration:>7.2f}   {average_tokens:>10.1f}"
        )

    for role, rankings in sorted(by_role.items()):
        best = min(rankings)
        print(f"Best observed {role} temperature: {best[-1]:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare Kid and Worker temperatures using their real GGUF models.",
    )
    parser.add_argument("--role", choices=("kid", "worker", "both"), default="both")
    parser.add_argument(
        "--kid-temperatures",
        type=lambda value: parse_numbers(value, float),
        default=DEFAULT_TEMPERATURES["kid"],
    )
    parser.add_argument(
        "--worker-temperatures",
        type=lambda value: parse_numbers(value, float),
        default=DEFAULT_TEMPERATURES["worker"],
    )
    parser.add_argument(
        "--seeds",
        type=lambda value: parse_numbers(value, int),
        default=DEFAULT_SEEDS,
    )
    args = parser.parse_args()

    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = RESULTS_DIRECTORY / f"temperature-evaluation-{timestamp}.jsonl"
    roles = ("kid", "worker") if args.role == "both" else (args.role,)
    results = []

    print(f"Results: {output_path}")
    print(f"Seeds: {', '.join(str(seed) for seed in args.seeds)}")
    print(f"Fixed sampling: {SAMPLING}")

    try:
        for role in roles:
            results.extend(
                run_role(
                    role,
                    args.kid_temperatures if role == "kid" else args.worker_temperatures,
                    args.seeds,
                    output_path,
                )
            )
    except KeyboardInterrupt:
        print("\nInterrupted. Completed results were preserved.")

    if results:
        print_summary(results)
        print(f"\nDetailed JSONL: {output_path}")


if __name__ == "__main__":
    main()
