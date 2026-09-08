from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DUAL_MODEL_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DUAL_MODEL_DIRECTORY))

import llama_cpp  # noqa: E402
from myllm_dual_model_prompts import WORKER_SYSTEM_PROMPT  # noqa: E402
from myllm_dual_model_test import (  # noqa: E402
    GPU_LAYERS,
    WORKER_CONTEXT,
    WORKER_MODEL_PATH,
    load_model,
    parse_worker_action,
)
from temperature_benchmark_common import (  # noqa: E402
    DEFAULT_SEEDS,
    RESULTS_DIRECTORY,
    SAMPLING,
    create_completion,
    parse_numbers,
)


DEFAULT_TEMPERATURES = (0.25, 0.40, 0.60, 0.75)
GAME_SCENARIOS = {
    "snake": {
        "class_name": "SnakeGame",
        "file_name": "SnakeGame.java",
        "prompt": """
GOAL:
Create a complete playable Snake game as one Java 8 console application.

REQUIREMENTS:
- Use only the Java standard library and one source file named SnakeGame.java.
- Include a main method, a visible board, snake movement, keyboard or console
  direction controls, food placement, growth, collision/game-over handling,
  and a displayed score.
- The source must compile with Java 8.
- Do not use placeholders or omit methods.

NEXT STEP:
Create the complete SnakeGame.java file now.

WORKSPACE:
(empty)

LAST RESULT:
(none)

Return one write_file tool action containing the complete source.
""",
        "features": {
            "entry_point": (r"static\s+void\s+main\s*\(",),
            "game_loop": (r"\bwhile\s*\(", r"\bfor\s*\(", r"\bTimer\s*\("),
            "direction_input": (r"\bScanner\b", r"\bBufferedReader\b", r"\bKeyListener\b", r"\bKeyEvent\b"),
            "food": (r"(?i)\bfood\b",),
            "growth_or_score": (r"(?i)\bscore\b", r"(?i)\bgrow"),
            "collision_or_game_over": (r"(?i)collision", r"(?i)game\s*over", r"(?i)gameOver"),
            "board_output": (r"System\.out\.print", r"\bchar\s*\[", r"\bStringBuilder\b"),
        },
    },
    "tic-tac-toe": {
        "class_name": "TicTacToe",
        "file_name": "TicTacToe.java",
        "prompt": """
GOAL:
Create a complete playable two-player Tic-Tac-Toe game as one Java 8 console application.

REQUIREMENTS:
- Use only the Java standard library and one source file named TicTacToe.java.
- Include a main method, a visible 3x3 board, validated player input,
  alternating X and O turns, winner detection, and draw detection.
- The source must compile with Java 8.
- Do not use placeholders or omit methods.

NEXT STEP:
Create the complete TicTacToe.java file now.

WORKSPACE:
(empty)

LAST RESULT:
(none)

Return one write_file tool action containing the complete source.
""",
        "features": {
            "entry_point": (r"static\s+void\s+main\s*\(",),
            "three_by_three_board": (
                r"\[\s*3\s*\]\s*\[\s*3\s*\]",
                r"(?s)BOARD_SIZE\s*=\s*3.*\[\s*BOARD_SIZE\s*\]\s*\[\s*BOARD_SIZE\s*\]",
            ),
            "player_input": (r"\bScanner\b", r"\bBufferedReader\b"),
            "alternating_players": (r"(?i)currentPlayer", r"(?i)player\s*=", r"['\"]X['\"].*['\"]O['\"]"),
            "winner_detection": (r"(?i)checkWin", r"(?i)winner", r"(?i)hasWon"),
            "draw_detection": (r"(?i)\bdraw\b", r"(?i)boardFull", r"(?i)isFull"),
            "game_loop": (r"\bwhile\s*\(", r"\bfor\s*\("),
        },
    },
}


class DetailedLog:
    def __init__(self, run_directory: Path) -> None:
        self.started = time.perf_counter()
        self.sequence = 0
        self.jsonl_path = run_directory / "events.jsonl"
        self.text_path = run_directory / "run.log.txt"

    def event(self, name: str, **details: Any) -> dict[str, Any]:
        self.sequence += 1
        record = {
            "sequence": self.sequence,
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "elapsed_seconds": round(time.perf_counter() - self.started, 3),
            "event": name,
            **details,
        }
        encoded = json.dumps(record, ensure_ascii=False)

        with self.jsonl_path.open("a", encoding="utf-8") as file:
            file.write(encoded + "\n")

        with self.text_path.open("a", encoding="utf-8") as file:
            file.write(f"\n[{record['timestamp']}] #{self.sequence} {name}\n")
            file.write(json.dumps(details, ensure_ascii=False, indent=2) + "\n")

        return record


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_response(response: str) -> tuple[str, str]:
    match = re.search(r"<think>(.*?)</think>", response, flags=re.DOTALL | re.IGNORECASE)

    if not match:
        return "", response

    return match.group(1), response[match.end():].lstrip()


def safe_label(temperature: float, seed: int) -> str:
    return f"temperature-{temperature:.2f}-seed-{seed}".replace(".", "_")


def inspect_features(source: str, patterns: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    checks = {}

    for name, alternatives in patterns.items():
        matched = [pattern for pattern in alternatives if re.search(pattern, source, re.DOTALL)]
        checks[name] = {
            "passed": bool(matched),
            "matched_patterns": matched,
            "patterns_checked": list(alternatives),
        }

    passed = sum(check["passed"] for check in checks.values())
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "coverage": passed / len(checks) if checks else 0.0,
    }


def compiler_details() -> dict[str, Any]:
    executable = shutil.which("javac")

    if not executable:
        return {"available": False, "executable": None, "version": ""}

    completed = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    return {
        "available": True,
        "executable": executable,
        "version": (completed.stdout + completed.stderr).strip(),
        "version_exit_code": completed.returncode,
    }


def compile_java(source_path: Path, javac: dict[str, Any], log: DetailedLog) -> dict[str, Any]:
    if not javac["available"]:
        result = {
            "attempted": False,
            "succeeded": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "javac was not found on PATH.",
            "duration_seconds": 0.0,
            "command": [],
        }
        log.event("compilation_skipped", source_path=str(source_path), **result)
        return result

    build_directory = source_path.parent / ".build"
    build_directory.mkdir(exist_ok=True)
    command = [
        javac["executable"],
        "-encoding",
        "UTF-8",
        "-source",
        "8",
        "-target",
        "8",
        "-proc:none",
        "-d",
        str(build_directory),
        str(source_path),
    ]
    log.event(
        "compilation_started",
        source_path=str(source_path),
        build_directory=str(build_directory),
        command=command,
        timeout_seconds=60,
        generated_program_will_run=False,
    )
    started = time.perf_counter()

    try:
        completed = subprocess.run(
            command,
            cwd=source_path.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        result = {
            "attempted": True,
            "succeeded": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": command,
        }
    except subprocess.TimeoutExpired as error:
        result = {
            "attempted": True,
            "succeeded": False,
            "exit_code": None,
            "stdout": error.stdout or "",
            "stderr": error.stderr or "",
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": command,
            "error": "Compilation timed out after 60 seconds.",
        }

    log.event("compilation_completed", source_path=str(source_path), **result)
    return result


def score_result(
    parse_success: bool,
    action_correct: bool,
    compilation: dict[str, Any],
    features: dict[str, Any],
) -> float:
    return round(
        (10 if parse_success else 0)
        + (10 if action_correct else 0)
        + (40 if compilation["succeeded"] else 0)
        + (40 * features["coverage"]),
        2,
    )


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = defaultdict(list)

    for result in results:
        grouped[(result["game"], result["temperature"])].append(result)

    rows = []

    for (game, temperature), cases in sorted(grouped.items()):
        rows.append({
            "game": game,
            "temperature": temperature,
            "runs": len(cases),
            "parsed": sum(case["parse_success"] for case in cases),
            "correct_actions": sum(case["action_correct"] for case in cases),
            "compiled": sum(case["compilation"]["succeeded"] for case in cases),
            "average_feature_coverage": round(
                sum(case["features"]["coverage"] for case in cases) / len(cases),
                3,
            ),
            "average_score": round(sum(case["score"] for case in cases) / len(cases), 2),
            "average_duration_seconds": round(
                sum(case["generation_duration_seconds"] for case in cases) / len(cases),
                3,
            ),
            "average_completion_tokens": round(
                sum(case["usage"].get("completion_tokens", 0) for case in cases) / len(cases),
                1,
            ),
        })

    best_by_game = {}

    for game in sorted({row["game"] for row in rows}):
        candidates = [row for row in rows if row["game"] == game]
        best = min(
            candidates,
            key=lambda row: (
                -row["compiled"],
                -row["average_score"],
                row["average_duration_seconds"],
                row["average_completion_tokens"],
            ),
        )
        best_by_game[game] = best["temperature"]

    return {"rows": rows, "best_observed_temperature_by_game": best_by_game}


def print_summary(summary: dict[str, Any]) -> None:
    print("\nSUMMARY")
    print("game         temp  compiled  action  features  score   avg sec  avg tokens")
    print("-" * 82)

    for row in summary["rows"]:
        print(
            f"{row['game']:<12} {row['temperature']:>4.2f}  "
            f"{row['compiled']:>2}/{row['runs']:<2}      "
            f"{row['correct_actions']:>2}/{row['runs']:<2}   "
            f"{row['average_feature_coverage']:>7.1%}  "
            f"{row['average_score']:>5.1f}  "
            f"{row['average_duration_seconds']:>7.1f}  "
            f"{row['average_completion_tokens']:>10.1f}"
        )

    for game, temperature in summary["best_observed_temperature_by_game"].items():
        print(f"Best observed {game} temperature: {temperature:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Java 8 games with the Worker model at several temperatures.",
    )
    parser.add_argument("--game", choices=("snake", "tic-tac-toe", "both"), default="both")
    parser.add_argument(
        "--temperatures",
        type=lambda value: parse_numbers(value, float),
        default=DEFAULT_TEMPERATURES,
    )
    parser.add_argument(
        "--seeds",
        type=lambda value: parse_numbers(value, int),
        default=DEFAULT_SEEDS,
    )
    args = parser.parse_args()

    run_id = datetime.now().strftime("game-generation-%Y%m%d-%H%M%S-%f")
    run_directory = RESULTS_DIRECTORY / run_id
    generated_directory = run_directory / "generated"
    generated_directory.mkdir(parents=True, exist_ok=False)
    log = DetailedLog(run_directory)
    games = tuple(GAME_SCENARIOS) if args.game == "both" else (args.game,)
    model_stat = WORKER_MODEL_PATH.stat() if WORKER_MODEL_PATH.exists() else None
    javac = compiler_details()
    model = None
    results = []
    interrupted = False

    log.event(
        "benchmark_started",
        run_id=run_id,
        command_line=sys.argv,
        working_directory=os.getcwd(),
        python_version=sys.version,
        platform=platform.platform(),
        processor=platform.processor(),
        llama_cpp_version=llama_cpp.__version__,
        model={
            "path": str(WORKER_MODEL_PATH),
            "exists": WORKER_MODEL_PATH.exists(),
            "size_bytes": model_stat.st_size if model_stat else None,
            "modified_ns": model_stat.st_mtime_ns if model_stat else None,
            "context": WORKER_CONTEXT,
            "gpu_layers": GPU_LAYERS,
        },
        games=list(games),
        temperatures=list(args.temperatures),
        seeds=list(args.seeds),
        sampling=SAMPLING,
        explicit_output_token_limit=None,
        compiler=javac,
        safety={
            "generated_programs_executed": False,
            "compiler_annotation_processing": False,
            "isolated_output_directory": str(generated_directory),
        },
    )

    try:
        load_started = time.perf_counter()
        log.event("model_load_started")
        model = load_model(WORKER_MODEL_PATH, WORKER_CONTEXT)
        log.event(
            "model_load_completed",
            duration_seconds=round(time.perf_counter() - load_started, 3),
            metadata=dict(model.metadata or {}),
        )

        total = len(games) * len(args.temperatures) * len(args.seeds)
        current = 0

        for game in games:
            scenario = GAME_SCENARIOS[game]

            for temperature in args.temperatures:
                for seed in args.seeds:
                    current += 1
                    case_id = f"{game}-{safe_label(temperature, seed)}"
                    case_directory = generated_directory / case_id
                    case_directory.mkdir(parents=True, exist_ok=False)
                    print(
                        f"[{current}/{total}] game={game} temperature={temperature:.2f} seed={seed}",
                        flush=True,
                    )
                    log.event(
                        "case_started",
                        case_id=case_id,
                        game=game,
                        temperature=temperature,
                        seed=seed,
                        sampling=SAMPLING,
                        system_prompt=WORKER_SYSTEM_PROMPT,
                        system_prompt_sha256=sha256_text(WORKER_SYSTEM_PROMPT),
                        user_prompt=scenario["prompt"],
                        user_prompt_sha256=sha256_text(scenario["prompt"]),
                    )
                    response = ""
                    usage = {}
                    generation_duration = 0.0
                    parse_success = False
                    action_correct = False
                    action = {}
                    source = ""
                    source_path = case_directory / scenario["file_name"]
                    compilation = {
                        "attempted": False,
                        "succeeded": False,
                        "exit_code": None,
                        "stdout": "",
                        "stderr": "Generation did not produce parseable source.",
                        "duration_seconds": 0.0,
                        "command": [],
                    }
                    features = inspect_features("", scenario["features"])
                    failure = ""

                    try:
                        response, usage, generation_duration, api_response = create_completion(
                            model,
                            WORKER_SYSTEM_PROMPT,
                            scenario["prompt"],
                            temperature,
                            seed,
                        )
                        thinking_trace, final_response = split_response(response)
                        log.event(
                            "generation_completed",
                            case_id=case_id,
                            duration_seconds=round(generation_duration, 3),
                            usage=usage,
                            finish_reason=api_response.get("choices", [{}])[0].get("finish_reason"),
                            api_response=api_response,
                            raw_response=response,
                            raw_response_characters=len(response),
                            raw_response_sha256=sha256_text(response),
                            thinking_trace=thinking_trace,
                            thinking_characters=len(thinking_trace),
                            final_response=final_response,
                            final_response_characters=len(final_response),
                        )
                        action = parse_worker_action(response)
                        parse_success = True
                        source = str(action.get("args", {}).get("content", ""))
                        returned_path = str(action.get("args", {}).get("path", ""))
                        normalized_returned_path = returned_path.replace("\\", "/")
                        action_correct = (
                            action.get("tool") == "write_file"
                            and normalized_returned_path.lower()
                            in {
                                scenario["file_name"].lower(),
                                f"./{scenario['file_name']}".lower(),
                            }
                            and bool(source.strip())
                        )
                        log.event(
                            "action_parsed",
                            case_id=case_id,
                            action=action,
                            action_correct=action_correct,
                            expected_tool="write_file",
                            expected_path=scenario["file_name"],
                        )
                        source_path.write_text(source, encoding="utf-8")
                        log.event(
                            "source_saved",
                            case_id=case_id,
                            source_path=str(source_path),
                            source=source,
                            characters=len(source),
                            lines=len(source.splitlines()),
                            sha256=sha256_text(source),
                        )
                        compilation = compile_java(source_path, javac, log)
                        features = inspect_features(source, scenario["features"])
                        log.event("feature_analysis_completed", case_id=case_id, **features)
                    except Exception as error:
                        failure = f"{type(error).__name__}: {error}"
                        log.event(
                            "case_error",
                            case_id=case_id,
                            error=failure,
                            traceback=traceback.format_exc(),
                            raw_response=response,
                            usage=usage,
                            generation_duration_seconds=round(generation_duration, 3),
                        )

                    result = {
                        "case_id": case_id,
                        "game": game,
                        "temperature": temperature,
                        "seed": seed,
                        "parse_success": parse_success,
                        "action_correct": action_correct,
                        "compilation": compilation,
                        "features": features,
                        "score": score_result(
                            parse_success,
                            action_correct,
                            compilation,
                            features,
                        ),
                        "generation_duration_seconds": round(generation_duration, 3),
                        "usage": usage,
                        "source_path": str(source_path) if source else None,
                        "source_sha256": sha256_text(source) if source else None,
                        "failure": failure,
                    }
                    results.append(result)
                    log.event("case_completed", **result)
                    print(
                        f"  score={result['score']:.1f} "
                        f"compiled={compilation['succeeded']} "
                        f"features={features['passed']}/{features['total']}",
                    )
    except KeyboardInterrupt:
        interrupted = True
        log.event("benchmark_interrupted", completed_cases=len(results))
        print("\nInterrupted. All completed details were preserved.")
    except Exception as error:
        log.event(
            "benchmark_error",
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(),
        )
        raise
    finally:
        if model is not None:
            model.close()
            del model
            gc.collect()
            log.event("model_closed")

    summary = summarize(results)
    summary.update({
        "run_id": run_id,
        "interrupted": interrupted,
        "completed_cases": len(results),
        "requested_cases": len(games) * len(args.temperatures) * len(args.seeds),
    })
    summary_path = run_directory / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.event("benchmark_completed", summary=summary, summary_path=str(summary_path))
    print_summary(summary)
    print(f"\nDetailed JSONL: {log.jsonl_path}")
    print(f"Readable log:    {log.text_path}")
    print(f"Summary:         {summary_path}")
    print(f"Generated files: {generated_directory}")


if __name__ == "__main__":
    main()
