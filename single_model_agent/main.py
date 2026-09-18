from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from pathlib import Path

try:
    from .agent import AgentConfig, SingleModelAgent
    from .protocol import ToolCall
    from .storage import TrustStore
except ImportError:  # `python single_model_agent/main.py`
    from agent import AgentConfig, SingleModelAgent
    from protocol import ToolCall
    from storage import TrustStore


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent


def discover_models(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        (path.resolve() for path in folder.rglob("*.gguf") if path.is_file()),
        key=lambda path: path.name.casefold(),
    )


def choose_model(models: list[Path]) -> Path:
    if not models:
        raise SystemExit(
            f"No GGUF models were found in {REPOSITORY / 'models'}. "
            "Use --model <path> or add a .gguf file to models/."
        )
    if len(models) == 1:
        print(f"Using model: {models[0].name}")
        return models[0]
    print("\nAvailable local models:")
    for index, model in enumerate(models, 1):
        gib = model.stat().st_size / (1024 ** 3)
        print(f"  {index}. {model.name} ({gib:.2f} GiB)")
    while True:
        value = input("Select model number: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(models):
            return models[int(value) - 1]
        print("Enter one of the listed numbers.")


def stream_output(kind: str, text: str) -> None:
    if kind == "reasoning":
        # Keep thinking visibly separate without claiming it is reliable evidence.
        prefix = "\n[thinking] " if not getattr(stream_output, "thinking", False) else ""
        stream_output.thinking = True
        print(prefix + text, end="", flush=True)
        return
    if getattr(stream_output, "thinking", False):
        print("\n[response] ", end="", flush=True)
        stream_output.thinking = False
    print(text, end="", flush=True)


def approval_prompt(call: ToolCall, preview: str, risk: str) -> bool:
    print("\n\nApproval required")
    print(f"Tool: {call.name} | Risk: {risk}")
    print(preview)
    while True:
        answer = input("Approve this exact action? [y/N]: ").strip().casefold()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the MyLLM single-model local coding agent.")
    parser.add_argument("--model", type=Path, help="Path to one GGUF model.")
    parser.add_argument("--models-dir", type=Path, default=REPOSITORY / "models")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-output-tokens", type=int, help="Omit for no explicit model-output cap.")
    parser.add_argument("--gpu-layers", type=int, default=0)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--mode", choices=("ask", "auto", "dry-run"), default="ask")
    return parser


def make_agent(args: argparse.Namespace, model: Path) -> SingleModelAgent:
    config = AgentConfig(
        model_path=model,
        workspace=args.workspace,
        data_dir=args.data_dir,
        context_size=args.context,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        gpu_layers=args.gpu_layers,
        threads=args.threads,
        max_steps=args.max_steps,
        mode=args.mode,
    )
    return SingleModelAgent(
        config,
        stream_callback=stream_output,
        approval_callback=approval_prompt,
    )


def print_header(agent: SingleModelAgent) -> None:
    print("\nMyLLM Single-Model Agent")
    print(f"Model:     {agent.config.model_path.name}")
    print(f"Workspace: {agent.workspace.root}")
    print(f"Mode:      {agent.config.mode}")
    print("Output:    unlimited by default" if agent.config.max_output_tokens is None else f"Output:    {agent.config.max_output_tokens} tokens")
    print("Type /help for commands. The model loads on the first request.\n")


def print_help() -> None:
    print("""Commands:
  /status                 Show model, workspace, context, mode, and session.
  /undo                   Undo the last unchanged agent edit from the latest run.
  /resume <run-id>        Continue an interrupted run from its durable boundary.
  /accept <check>         Accept a known unavailable/failed check as an explicit limitation.
  /trust-edit             Allow workspace edits without prompts in auto mode.
  /new                    Start a new conversation session with the same settings.
  /stop or Ctrl+C         Interrupt active generation/check work.
  /exit                   Close the model worker and exit.
""")


def interactive(args: argparse.Namespace, model: Path) -> int:
    agent = make_agent(args, model)
    print_header(agent)
    try:
        while True:
            try:
                message = input("You> ").strip()
            except KeyboardInterrupt:
                agent.stop()
                print("\nStopped. Press Ctrl+C again at the prompt or use /exit to leave.")
                continue
            except EOFError:
                message = "/exit"
            if not message:
                continue
            command, _, value = message.partition(" ")
            command = command.casefold()
            if command == "/exit":
                return 0
            if command == "/help":
                print_help()
                continue
            if command == "/status":
                print(json.dumps(agent.status(), indent=2, default=str))
                continue
            if command == "/stop":
                agent.stop()
                print("Stop requested.")
                continue
            if command == "/undo":
                result = agent.undo_last_checkpoint()
                print(f"{result.status}: {result.content}")
                continue
            if command == "/accept":
                try:
                    agent.accept_limitation(value.strip())
                    print(f"Accepted limitation for {value.strip()}.")
                except ValueError as error:
                    print(error)
                continue
            if command == "/resume":
                if not value.strip():
                    print("Usage: /resume <run-id>")
                    continue
                try:
                    result = agent.resume(value.strip())
                    print(f"\n{result.state.value}: {result.message}")
                except ValueError as error:
                    print(error)
                continue
            if command == "/trust-edit":
                TrustStore(agent.paths).grant(agent.workspace.root, {"edit_file"})
                print("Edit capability granted to this exact workspace. Executable checks still ask.")
                continue
            if command == "/new":
                agent.close()
                agent = make_agent(args, model)
                print_header(agent)
                continue
            if command.startswith("/"):
                print("Unknown command. Type /help.")
                continue

            print("Agent> ", end="", flush=True)
            result = agent.run(message)
            print(f"\n\n[{result.state.value}] {result.message}")
            print(f"Run: {result.run_id}")
            print(f"Log: {result.log_file}\n")
    finally:
        agent.close()


def main() -> int:
    multiprocessing.freeze_support()
    args = build_parser().parse_args()
    workspace = args.workspace.expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise SystemExit(f"Workspace must be a directory: {workspace}")
    args.workspace = workspace
    model = args.model.expanduser().resolve(strict=True) if args.model else choose_model(discover_models(args.models_dir))
    if model.suffix.casefold() != ".gguf":
        raise SystemExit(f"Model must be a GGUF file: {model}")
    return interactive(args, model)


if __name__ == "__main__":
    raise SystemExit(main())
