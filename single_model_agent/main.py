from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import threading
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
MODE_DESCRIPTIONS = {
    "ask": "Approve file edits and executable checks.",
    "auto": "Trusted edits can run automatically; executable checks still ask.",
    "dry-run": "Preview and log actions without edits or subprocess checks.",
}


class CliSpinner:
    FRAMES = ("|", "/", "-", "\\")

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty()
        self.label = ""
        self.width = 0
        self.frame = 0
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self, label: str) -> None:
        if not self.enabled:
            print(label, flush=True)
            return
        with self.lock:
            self.label = label
            self._draw()
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="myllm-spinner", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(0.12):
            with self.lock:
                self._draw()

    def _draw(self) -> None:
        line = f"{self.FRAMES[self.frame]} {self.label}"
        self.frame = (self.frame + 1) % len(self.FRAMES)
        self.width = max(self.width, len(line) + 2)
        print("\r" + line.ljust(self.width), end="", flush=True)

    def stop(self) -> None:
        if not self.enabled or not self.thread:
            return
        self.stop_event.set()
        self.thread.join(timeout=0.5)
        with self.lock:
            print("\r" + (" " * self.width) + "\r", end="", flush=True)
        self.thread = None
        self.width = 0
        self.frame = 0


CLI_SPINNER = CliSpinner()


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


def choose_mode(current: str) -> str:
    modes = tuple(MODE_DESCRIPTIONS)
    print("\nAvailable modes:")
    for index, mode in enumerate(modes, 1):
        marker = " (current)" if mode == current else ""
        print(f"  {index}. {mode}{marker} - {MODE_DESCRIPTIONS[mode]}")
    print("  0. Back")
    while True:
        value = input("Select mode: ").strip()
        if value == "0":
            return current
        if value.isdigit() and 1 <= int(value) <= len(modes):
            return modes[int(value) - 1]
        print("Enter one of the listed numbers.")


def choose_workspace(current: Path) -> Path:
    value = input(f"Workspace [{current}]: ").strip().strip('"')
    if not value:
        return current
    try:
        workspace = Path(value).expanduser().resolve(strict=True)
    except OSError as error:
        print(f"Workspace not found: {error}")
        return current
    if not workspace.is_dir():
        print("Workspace must be a directory.")
        return current
    return workspace


def display_value(value: object, fallback: str) -> str:
    return fallback if value is None else str(value)


def advanced_settings_menu(args: argparse.Namespace) -> None:
    while True:
        output = display_value(args.max_output_tokens, "unlimited")
        temperature = display_value(args.temperature, "model default")
        threads = display_value(args.threads, "automatic")
        data_dir = args.data_dir or Path.cwd() / "single_model_agent_data"
        print("\n" + "=" * 70)
        print("⚙️  ADVANCED SETTINGS — changes apply to this launch")
        print("=" * 70)
        print(f"  1. Context size       : {args.context} tokens (higher uses more memory)")
        print(f"  2. Temperature        : {temperature} (higher is more varied)")
        print(f"  3. GPU layers         : {args.gpu_layers} (0 = CPU, -1 = all possible)")
        print(f"  4. CPU threads        : {threads} (automatic is recommended)")
        print(f"  5. Maximum steps      : {args.max_steps} (agent actions per request)")
        print(f"  6. Model output limit : {output} (0 = unlimited)")
        print(f"  7. Runtime data folder: {data_dir}")
        print("  8. Reset advanced settings")
        print("  0. Back")
        choice = input("\nSelect option: ").strip()
        try:
            if choice == "0":
                return
            if choice == "1":
                value = int(input("Context tokens (minimum 1024): ").strip())
                if value < 1024:
                    raise ValueError("Context size must be at least 1024.")
                args.context = value
            elif choice == "2":
                value = input("Temperature (0-2, or 'default'): ").strip().casefold()
                if value in {"", "default"}:
                    args.temperature = None
                else:
                    temperature_value = float(value)
                    if not 0 <= temperature_value <= 2:
                        raise ValueError("Temperature must be between 0 and 2.")
                    args.temperature = temperature_value
            elif choice == "3":
                value = int(input("GPU layers (0 = CPU, -1 = all): ").strip())
                if value < -1:
                    raise ValueError("GPU layers must be -1 or greater.")
                args.gpu_layers = value
            elif choice == "4":
                value = int(input("CPU threads (0 = automatic): ").strip())
                if value < 0:
                    raise ValueError("CPU threads cannot be negative.")
                args.threads = value or None
            elif choice == "5":
                value = int(input("Maximum agent steps: ").strip())
                if value < 1:
                    raise ValueError("Maximum steps must be at least 1.")
                args.max_steps = value
            elif choice == "6":
                value = int(input("Maximum output tokens (0 = unlimited): ").strip())
                if value < 0:
                    raise ValueError("Output tokens cannot be negative.")
                args.max_output_tokens = value or None
            elif choice == "7":
                value = input("Runtime data folder (Enter = local default): ").strip().strip('"')
                args.data_dir = Path(value).expanduser() if value else None
            elif choice == "8":
                defaults = build_parser().parse_args([])
                for name in (
                    "context", "temperature", "gpu_layers", "threads",
                    "max_steps", "max_output_tokens", "data_dir",
                ):
                    setattr(args, name, getattr(defaults, name))
                print("Advanced settings reset.")
            else:
                print("Enter one of the listed numbers.")
        except ValueError as error:
            print(f"Invalid value: {error}")


def show_system_information(args: argparse.Namespace, model: Path | None) -> None:
    data_dir = (args.data_dir or Path.cwd() / "single_model_agent_data").expanduser().resolve()
    print("\n" + "=" * 70)
    print("🔎 SYSTEM INFORMATION")
    print("=" * 70)
    print(f"Model file      : {model or 'Not selected'}")
    if model and model.exists():
        print(f"Model size      : {model.stat().st_size / (1024 ** 3):.2f} GiB")
    print(f"Model search    : {args.models_dir}")
    print(f"Workspace       : {args.workspace}")
    print(f"Mode            : {args.mode} — {MODE_DESCRIPTIONS[args.mode]}")
    print(f"Context         : {args.context} tokens")
    print(f"Temperature     : {display_value(args.temperature, 'model default')}")
    print(f"GPU layers      : {args.gpu_layers}")
    print(f"CPU threads     : {display_value(args.threads, 'automatic')}")
    print(f"Maximum steps   : {args.max_steps}")
    print(f"Output limit    : {display_value(args.max_output_tokens, 'unlimited')}")
    print(f"Runtime data    : {data_dir}")
    input("\nPress Enter to return...")


def startup_menu(args: argparse.Namespace) -> Path | None:
    models = discover_models(args.models_dir)
    selected_model = models[0] if len(models) == 1 else None
    while True:
        print("\n╔══════════════════════════════════════╗")
        print("║      🤖 MYLLM CODING AGENT          ║")
        print("╚══════════════════════════════════════╝")
        print(f"\n🤖 Model     : {selected_model.name if selected_model else 'Not selected'}")
        print(f"📁 Workspace : {args.workspace}")
        print(f"🛡️  Mode      : {args.mode}")
        print(f"🧠 Context   : {args.context} tokens")
        print(f"🌡️  Temp      : {display_value(args.temperature, 'model default')}")
        print(f"♾️  Output    : {display_value(args.max_output_tokens, 'unlimited')}")
        print("\n  1. 💬 Start coding agent")
        print("  2. ⚙️  Advanced settings")
        print("  3. 🤖 Model selection")
        print("  4. 📁 Workspace selection")
        print("  5. 🛡️  Agent mode")
        print("  6. 🔎 System information")
        print("  0. Exit")
        choice = input("\nSelect option: ").strip()
        if choice == "1":
            return selected_model or choose_model(models)
        if choice == "2":
            advanced_settings_menu(args)
        elif choice == "3":
            selected_model = choose_model(models)
        elif choice == "4":
            args.workspace = choose_workspace(args.workspace)
        elif choice == "5":
            args.mode = choose_mode(args.mode)
        elif choice == "6":
            show_system_information(args, selected_model)
        elif choice == "0":
            print("\n👋 Goodbye.")
            return None
        else:
            print("Enter one of the listed numbers.")


def stream_output(kind: str, text: str) -> None:
    if kind == "waiting":
        if getattr(stream_output, "thinking", False) or getattr(stream_output, "response_started", False):
            print()
        stream_output.thinking = False
        stream_output.response_started = False
        CLI_SPINNER.start(text)
        return
    CLI_SPINNER.stop()
    if kind == "status":
        print(text, end="", flush=True)
        return
    if kind == "reasoning":
        # Keep thinking visibly separate without claiming it is reliable evidence.
        prefix = "🧠 Agent reasoning:\n" if not getattr(stream_output, "thinking", False) else ""
        stream_output.thinking = True
        print(prefix + text, end="", flush=True)
        return
    if getattr(stream_output, "thinking", False):
        print("\n🤖 Agent response:\n", end="", flush=True)
        stream_output.thinking = False
        stream_output.response_started = True
    elif not getattr(stream_output, "response_started", False):
        print("🤖 Agent response:\n", end="", flush=True)
        stream_output.response_started = True
    print(text, end="", flush=True)


def approval_prompt(call: ToolCall, preview: str, risk: str) -> bool:
    print("\n\n🛡️  APPROVAL REQUIRED")
    print(f"🔧 Tool: {call.name}")
    print(f"⚠️  Risk: {risk}")
    print("📄 Proposed action:")
    print(preview)
    while True:
        answer = input("👤 Your decision — approve this exact action? [y/N]: ").strip().casefold()
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
                message = input("👤 You › ").strip()
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

            print("\n🤖 Agent")
            try:
                result = agent.run(message)
            finally:
                CLI_SPINNER.stop()
            result_icon = "✅" if result.state.value == "COMPLETED" else "⚠️"
            print(f"\n\n{result_icon} [{result.state.value}] {result.message}")
            print(f"🆔 Run: {result.run_id}")
            print(f"📝 Log: {result.log_file}\n")
    finally:
        agent.close()


def main() -> int:
    multiprocessing.freeze_support()
    args = build_parser().parse_args()
    menu_model = startup_menu(args) if len(sys.argv) == 1 else None
    if len(sys.argv) == 1 and menu_model is None:
        return 0
    workspace = args.workspace.expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise SystemExit(f"Workspace must be a directory: {workspace}")
    args.workspace = workspace
    model = (
        args.model.expanduser().resolve(strict=True)
        if args.model
        else menu_model or choose_model(discover_models(args.models_dir))
    )
    if model.suffix.casefold() != ".gguf":
        raise SystemExit(f"Model must be a GGUF file: {model}")
    return interactive(args, model)


if __name__ == "__main__":
    raise SystemExit(main())
