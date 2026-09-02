from __future__ import annotations

import os

from pathlib import Path
from typing import Any, Callable

from myllm_constants import (
    APP_DIR,
    CONFIG_FILE,
    MEMORY_ROOT,
    PAYLOAD_ROOT,
    SCRIPT_DIR,
)

from myllm_tools import (
    detect_project_profile,
    profile_to_prompt,
)

# ============================================================
# BASIC UI
# ============================================================


def clear_screen() -> None:

    os.system("cls" if os.name == "nt" else "clear")


def pause() -> None:

    input("\nPress Enter to continue...")


def print_header(
    title: str,
) -> None:

    print()

    print("=" * 70)

    print(title)

    print("=" * 70)


# ============================================================
# MODEL DISCOVERY
# ============================================================


def find_gguf_files(
    start: Path,
    max_results: int = 100,
) -> list[Path]:

    if not start.exists():

        return []

    found = []

    try:

        for path in start.rglob("*.gguf"):

            if ".git" in path.parts:

                continue

            found.append(path.resolve())

            if len(found) >= max_results:

                break

    except Exception:

        pass

    return found


# ============================================================
# MODEL MENU
# ============================================================


def model_selection_menu(
    config: dict[str, Any],
    save_config: Callable[
        [dict[str, Any]],
        None,
    ],
) -> None:

    clear_screen()

    print_header("🤖 MODEL SELECTION")

    search_locations = [
        SCRIPT_DIR / "models",
        SCRIPT_DIR,
        Path.cwd() / "models",
    ]

    models = []
    seen = set()

    for location in search_locations:

        for model in find_gguf_files(location):

            key = str(model).lower()

            if key in seen:

                continue

            seen.add(key)

            models.append(model)

    if models:

        print()
        print("Detected GGUF models:\n")

        for (
            index,
            model,
        ) in enumerate(
            models,
            start=1,
        ):

            marker = ""

            current = config.get(
                "model_path",
                "",
            )

            if current:

                try:

                    if Path(current).resolve() == model:

                        marker = " ✅ current"

                except Exception:

                    pass

            try:

                size = model.stat().st_size / 1024**3

                size_text = f"{size:.2f} GB"

            except Exception:

                size_text = "?"

            print(f"{index}. " f"{model.name} " f"[{size_text}]" f"{marker}")

        manual_index = len(models) + 1

        print()

        print(f"{manual_index}. " "Enter model path manually")

        print("0. Back")

        selected = input("\nSelect model: ").strip()

        if selected == "0":

            return

        try:

            number = int(selected)

            if 1 <= number <= len(models):

                config["model_path"] = str(models[number - 1])

                save_config(config)

                print("\n✅ Model selected.")

                pause()

                return

            if number != manual_index:

                return

        except ValueError:

            pass

    entered = input("\nFull GGUF path: ").strip().strip('"')

    if not entered:

        return

    path = Path(entered).expanduser().resolve()

    if not path.exists() or path.suffix.lower() != ".gguf":

        print("\n❌ Invalid GGUF file.")

        pause()

        return

    config["model_path"] = str(path)

    save_config(config)

    print("\n✅ Model selected.")

    pause()


# ============================================================
# PROJECT MENU
# ============================================================


def project_selection_menu(
    config: dict[str, Any],
    save_config: Callable[
        [dict[str, Any]],
        None,
    ],
) -> None:

    clear_screen()

    print_header("📁 PROJECT SELECTION")

    print()

    print(f"Current:\n" f"{config['project_root']}")

    entered = input("\nNew project folder " "(Enter to cancel): ").strip().strip('"')

    if not entered:

        return

    path = Path(entered).expanduser().resolve()

    if not path.exists() or not path.is_dir():

        print("\n❌ Invalid directory.")

        pause()

        return

    config["project_root"] = str(path)

    save_config(config)

    print("\n✅ Project selected.")

    pause()


# ============================================================
# SETTINGS MENU
# ============================================================


def settings_menu(
    config: dict[str, Any],
    save_config: Callable[
        [dict[str, Any]],
        None,
    ],
    default_config: dict[str, Any],
) -> None:

    while True:

        clear_screen()

        print_header("⚙️ SETTINGS")

        print(f"\n1. Context size" f"                : " f"{config['context_size']}")

        print(f"2. GPU layers" f"                  : " f"{config['gpu_layers']}")

        print(f"3. Max steps" f"                   : " f"{config['max_steps']}")

        print(
            f"4. Max no-progress steps"
            f"       : "
            f"{config['max_no_progress_steps']}"
        )

        print(f"5. Temperature" f"                 : " f"{config['temperature']}")

        print(f"6. Debug level" f"                 : " f"{config['debug_level']}")

        print(
            f"7. Recent observations" f"         : " f"{config['recent_observations']}"
        )

        print(
            f"8. Prompt cache enabled" f"        : " f"{config['prompt_cache_enabled']}"
        )

        print(f"9. Prompt cache MB" f"             : " f"{config['prompt_cache_mb']}")

        print(
            f"10. Trim context ratio" f"         : " f"{config['trim_context_ratio']}"
        )

        print(
            f"11. Payload threshold chars"
            f"    : "
            f"{config['payload_externalize_chars']}"
        )

        print(f"12. Max payload files" f"          : " f"{config['payload_max_files']}")

        print("13. Reset defaults")

        print("0. Back")

        choice = input("\nSelect option: ").strip()

        if choice == "0":

            save_config(config)

            return

        try:

            if choice == "1":

                value = int(input("Context size: "))

                if value >= 1024:

                    config["context_size"] = value

            elif choice == "2":

                config["gpu_layers"] = int(input("GPU layers " "(0 CPU, -1 all): "))

            elif choice == "3":

                value = int(input("Max steps: "))

                if value > 0:

                    config["max_steps"] = value

            elif choice == "4":

                value = int(input("Max no-progress steps: "))

                if value > 0:

                    config["max_no_progress_steps"] = value

            elif choice == "5":

                value = float(input("Temperature: "))

                if 0 <= value <= 2:

                    config["temperature"] = value

            elif choice == "6":

                value = int(input("Debug level 0-3: "))

                if value in {
                    0,
                    1,
                    2,
                    3,
                }:

                    config["debug_level"] = value

            elif choice == "7":

                value = int(input("Recent observations: "))

                if value >= 2:

                    config["recent_observations"] = value

            elif choice == "8":

                config["prompt_cache_enabled"] = not bool(
                    config["prompt_cache_enabled"]
                )

            elif choice == "9":

                value = int(input("Prompt cache MB: "))

                if value >= 64:

                    config["prompt_cache_mb"] = value

            elif choice == "10":

                value = float(input("Trim context ratio: "))

                if 0.50 <= value <= 0.90:

                    config["trim_context_ratio"] = value

            elif choice == "11":

                value = int(input("Payload threshold chars: "))

                if value >= 200:

                    config["payload_externalize_chars"] = value

            elif choice == "12":

                value = int(input("Maximum payload files: "))

                if value >= 50:

                    config["payload_max_files"] = value

            elif choice == "13":

                model_path = config.get(
                    "model_path",
                    "",
                )

                project_root = config.get(
                    "project_root",
                    str(SCRIPT_DIR),
                )

                config.clear()

                config.update(default_config.copy())

                config["model_path"] = model_path

                config["project_root"] = project_root

        except ValueError:

            pass

        save_config(config)


# ============================================================
# SYSTEM INFORMATION
# ============================================================


def system_information_menu(
    config: dict[str, Any],
) -> None:

    clear_screen()

    print_header("🔎 SYSTEM INFORMATION")

    project = Path(config["project_root"])

    if project.exists():

        profile = detect_project_profile(project)

        print()

        print(profile_to_prompt(profile))

    print()

    print(f"Script directory:\n" f"{SCRIPT_DIR}")

    print()

    print(f"Application directory:\n" f"{APP_DIR}")

    print()

    print(f"Config file:\n" f"{CONFIG_FILE}")

    print()

    print(f"Memory directory:\n" f"{MEMORY_ROOT}")

    print()

    print(f"Payload directory:\n" f"{PAYLOAD_ROOT}")

    pause()


# ============================================================
# STATUS
# ============================================================


def show_status(
    config: dict[str, Any],
) -> None:

    model_path = config.get(
        "model_path",
        "",
    )

    model_name = Path(model_path).name if model_path else "Not selected"

    print(f"\n🤖 Model   : " f"{model_name}")

    print(f"📁 Project : " f"{config['project_root']}")

    print(f"🧠 Context : " f"{config['context_size']}")

    print(f"⚡ Cache   : " f"{config['prompt_cache_enabled']}")

    print(f"📦 Payload : ≥ " f"{config['payload_externalize_chars']} chars")

    print(f"🔎 Debug   : " f"{config['debug_level']}")


# ============================================================
# CHAT
# ============================================================


def chat_menu(
    config: dict[str, Any],
    agent_factory: Callable[
        [dict[str, Any]],
        Any,
    ],
) -> None:

    model_path = config.get(
        "model_path",
        "",
    )

    if not model_path:

        print("\n⚠️ Select a model first.")

        pause()

        return

    model = Path(model_path)

    project = Path(config["project_root"])

    if not model.exists():

        print("\n❌ Model does not exist.")

        pause()

        return

    if not project.exists():

        print("\n❌ Project does not exist.")

        pause()

        return

    clear_screen()

    print_header("🤖 LOCAL CODING AGENT")

    print(f"\nModel   : " f"{model.name}")

    print(f"Project : " f"{project}")

    print(f"Context : " f"{config['context_size']}")

    print(f"Debug   : " f"{config['debug_level']}")

    print(
        f"Cache   : "
        f"{config['prompt_cache_enabled']} "
        f"({config['prompt_cache_mb']} MB)"
    )

    print(f"Payload : externalize ≥ " f"{config['payload_externalize_chars']} chars")

    print()

    print("Loading...")

    try:

        agent = agent_factory(config)

    except Exception as error:

        print()

        print(f"❌ Failed to load agent: " f"{error}")

        pause()

        return

    print()

    print("Commands:")

    print("/back")

    print("/session")

    print("/reset")

    while True:

        print()

        print("-" * 70)

        task = input("\n👤 > ").strip()

        if not task:

            continue

        normalized = task.lower()

        if normalized in {
            "/back",
            "/exit",
            "/quit",
            "exit",
        }:

            return

        if normalized == "/session":

            print()

            print(agent.session_card())

            continue

        if normalized == "/reset":

            agent.reset_session()

            print("✅ Session reset.")

            continue

        print()

        print("🚀 Sending to model...")

        result = agent.run(task)

        print()

        print("=" * 70)

        print("🤖 RESPONSE")

        print("=" * 70)

        print(result)


# ============================================================
# MAIN MENU
# ============================================================


def main_menu(
    config: dict[str, Any],
    save_config: Callable[
        [dict[str, Any]],
        None,
    ],
    agent_factory: Callable[
        [dict[str, Any]],
        Any,
    ],
    default_config: dict[str, Any],
) -> None:

    while True:

        clear_screen()

        print("╔══════════════════════════════════════╗")

        print("║          🤖 MY LOCAL LLM            ║")

        print("╚══════════════════════════════════════╝")

        show_status(config)

        print()

        print("1. 💬 Chat / Coding Agent")

        print("2. ⚙️  Settings")

        print("3. 🤖 Model Selection")

        print("4. 📁 Project Selection")

        print("5. 🔎 System Information")

        print("0. 🚪 Exit")

        choice = input("\nSelect option: ").strip()

        if choice == "1":

            chat_menu(
                config,
                agent_factory,
            )

        elif choice == "2":

            settings_menu(
                config,
                save_config,
                default_config,
            )

        elif choice == "3":

            model_selection_menu(
                config,
                save_config,
            )

        elif choice == "4":

            project_selection_menu(
                config,
                save_config,
            )

        elif choice == "5":

            system_information_menu(config)

        elif choice == "0":

            print()

            print("👋 Goodbye.")

            return
