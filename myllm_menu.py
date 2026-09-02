from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from myllm_constants import (
    APP_DIR,
    CONFIG_FILE,
    LOG_ROOT,
    MEMORY_ROOT,
    PAYLOAD_ROOT,
    SCRIPT_DIR,
)
from myllm_logging import get_logger
from myllm_tools import (
    detect_project_profile,
    profile_to_prompt,
)

logger = get_logger()


# ============================================================
# BASIC UI
# ============================================================


def clear_screen() -> None:
    # Deliberately do not clear the terminal.
    #
    # Clearing makes console output disappear visually while the
    # same information remains in the log file. Keeping the terminal
    # append-only makes the console and log much easier to compare.
    pass


def pause() -> None:
    input("\nPress Enter to continue...")


def print_header(title: str) -> None:
    logger.info("")
    logger.info("=" * 70)
    logger.info(title)
    logger.info("=" * 70)


# ============================================================
# MODEL DISCOVERY
# ============================================================


def find_gguf_files(
    start: Path,
    max_results: int = 100,
) -> list[Path]:
    if not start.exists():
        return []

    found: list[Path] = []

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
# MODEL SELECTION
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

    models: list[Path] = []
    seen: set[str] = set()

    for location in search_locations:
        for model in find_gguf_files(location):
            key = str(model).lower()

            if key in seen:
                continue

            seen.add(key)

            models.append(model)

    if models:
        logger.info("")
        logger.info("Detected GGUF models:")
        logger.info("")

        for index, model in enumerate(
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
                size_gb = model.stat().st_size / (1024**3)

                size_text = f"{size_gb:.2f} GB"

            except Exception:
                size_text = "?"

            logger.info(
                "%s. %s [%s]%s",
                index,
                model.name,
                size_text,
                marker,
            )

        manual_index = len(models) + 1

        logger.info("")
        logger.info(
            "%s. Enter model path manually",
            manual_index,
        )
        logger.info("0. Back")

        selected = input("\nSelect model: ").strip()

        if selected == "0":
            return

        try:
            number = int(selected)

            if 1 <= number <= len(models):
                config["model_path"] = str(models[number - 1])

                save_config(config)

                logger.info("")
                logger.info("✅ Model selected.")

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
        logger.error("")
        logger.error("❌ Invalid GGUF file.")

        pause()
        return

    config["model_path"] = str(path)

    save_config(config)

    logger.info("")
    logger.info("✅ Model selected.")

    pause()


# ============================================================
# PROJECT SELECTION
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

    logger.info("")
    logger.info("Current:")
    logger.info(
        "%s",
        config["project_root"],
    )

    entered = input("\nNew project folder " "(Enter to cancel): ").strip().strip('"')

    if not entered:
        return

    path = Path(entered).expanduser().resolve()

    if not path.exists() or not path.is_dir():
        logger.error("")
        logger.error("❌ Invalid directory.")

        pause()
        return

    config["project_root"] = str(path)

    save_config(config)

    logger.info("")
    logger.info("✅ Project selected.")

    pause()


# ============================================================
# SETTINGS
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

        max_output = int(
            config.get(
                "max_model_output_tokens",
                0,
            )
        )

        max_output_text = "unlimited" if max_output <= 0 else str(max_output)

        logger.info("")
        logger.info(
            "1. Context size                : %s",
            config["context_size"],
        )
        logger.info(
            "2. GPU layers                  : %s",
            config["gpu_layers"],
        )
        logger.info(
            "3. Max agent steps             : %s",
            config["max_steps"],
        )
        logger.info(
            "4. Max no-progress steps       : %s",
            config["max_no_progress_steps"],
        )
        logger.info(
            "5. Temperature                 : %s",
            config["temperature"],
        )
        logger.info(
            "6. Debug level                 : %s",
            config["debug_level"],
        )
        logger.info(
            "7. Recent observations         : %s",
            config["recent_observations"],
        )
        logger.info(
            "8. Prompt cache enabled        : %s",
            config["prompt_cache_enabled"],
        )
        logger.info(
            "9. Prompt cache MB             : %s",
            config["prompt_cache_mb"],
        )
        logger.info(
            "10. Trim context ratio         : %s",
            config["trim_context_ratio"],
        )
        logger.info(
            "11. Payload threshold chars    : %s",
            config["payload_externalize_chars"],
        )
        logger.info(
            "12. Max payload files          : %s",
            config["payload_max_files"],
        )
        logger.info(
            "13. Max model output tokens    : %s",
            max_output_text,
        )
        logger.info(
            "14. Logging enabled            : %s",
            config.get(
                "logging_enabled",
                True,
            ),
        )
        logger.info("15. Reset defaults")
        logger.info("0. Back")

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
                value = int(input("Max agent steps: "))

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
                value = int(input("Max model output tokens " "(0 = unlimited): "))

                if value >= 0:
                    config["max_model_output_tokens"] = value

            elif choice == "14":
                config["logging_enabled"] = not bool(
                    config.get(
                        "logging_enabled",
                        True,
                    )
                )

                logger.info("")
                logger.info(
                    "ℹ️ Logging setting takes effect " "the next time myllm starts."
                )

            elif choice == "15":
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
            logger.warning("⚠️ Invalid setting value.")

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

        logger.info("")
        logger.info(
            "%s",
            profile_to_prompt(profile),
        )

    logger.info("")
    logger.info("Script directory:")
    logger.info(
        "%s",
        SCRIPT_DIR,
    )

    logger.info("")
    logger.info("Application directory:")
    logger.info(
        "%s",
        APP_DIR,
    )

    logger.info("")
    logger.info("Config file:")
    logger.info(
        "%s",
        CONFIG_FILE,
    )

    logger.info("")
    logger.info("Memory directory:")
    logger.info(
        "%s",
        MEMORY_ROOT,
    )

    logger.info("")
    logger.info("Payload directory:")
    logger.info(
        "%s",
        PAYLOAD_ROOT,
    )

    logger.info("")
    logger.info("Log directory:")
    logger.info(
        "%s",
        LOG_ROOT,
    )

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

    max_output = int(
        config.get(
            "max_model_output_tokens",
            0,
        )
    )

    max_output_text = "unlimited" if max_output <= 0 else str(max_output)

    logger.info("")
    logger.info(
        "🤖 Model   : %s",
        model_name,
    )
    logger.info(
        "📁 Project : %s",
        config["project_root"],
    )
    logger.info(
        "🧠 Context : %s",
        config["context_size"],
    )
    logger.info(
        "⚡ Cache   : %s",
        config["prompt_cache_enabled"],
    )
    logger.info(
        "📦 Payload : ≥ %s chars",
        config["payload_externalize_chars"],
    )
    logger.info(
        "♾️ Output  : %s",
        max_output_text,
    )
    logger.info(
        "📝 Logging : %s",
        config.get(
            "logging_enabled",
            True,
        ),
    )
    logger.info(
        "🔎 Debug   : %s",
        config["debug_level"],
    )


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
        logger.warning("")
        logger.warning("⚠️ Select a model first.")

        pause()
        return

    model = Path(model_path)

    project = Path(config["project_root"])

    if not model.exists():
        logger.error("")
        logger.error("❌ Model does not exist.")

        pause()
        return

    if not project.exists():
        logger.error("")
        logger.error("❌ Project does not exist.")

        pause()
        return

    clear_screen()
    print_header("🤖 LOCAL CODING AGENT")

    max_output = int(
        config.get(
            "max_model_output_tokens",
            0,
        )
    )

    max_output_text = "unlimited" if max_output <= 0 else str(max_output)

    logger.info("")
    logger.info(
        "Model   : %s",
        model.name,
    )
    logger.info(
        "Project : %s",
        project,
    )
    logger.info(
        "Context : %s",
        config["context_size"],
    )
    logger.info(
        "Debug   : %s",
        config["debug_level"],
    )
    logger.info(
        "Cache   : %s (%s MB)",
        config["prompt_cache_enabled"],
        config["prompt_cache_mb"],
    )
    logger.info(
        "Payload : externalize ≥ %s chars",
        config["payload_externalize_chars"],
    )
    logger.info(
        "Output  : %s",
        max_output_text,
    )
    logger.info(
        "Logging : %s",
        config.get(
            "logging_enabled",
            True,
        ),
    )

    logger.info("")
    logger.info("Loading...")

    try:
        agent = agent_factory(config)

    except Exception:
        logger.exception("❌ Failed to load agent.")

        pause()
        return

    logger.info("")
    logger.info("Commands:")
    logger.info("/back")
    logger.info("/session")
    logger.info("/reset")

    while True:
        logger.info("")
        logger.info("-" * 70)

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
            logger.info("")
            logger.info(
                "%s",
                agent.session_card(),
            )
            continue

        if normalized == "/reset":
            agent.reset_session()

            logger.info("✅ Session reset.")
            continue

        logger.info("")
        logger.info("👤 USER REQUEST")
        logger.info(
            "%s",
            task,
        )

        logger.info("")
        logger.info("🚀 Sending to model...")

        try:
            result = agent.run(task)
        except Exception:
            logger.exception("❌ Unhandled agent error.")
            continue

        logger.info("")
        logger.info("=" * 70)
        logger.info("🤖 RESPONSE")
        logger.info("=" * 70)
        logger.info(
            "%s",
            result,
        )


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

        logger.info("╔══════════════════════════════════════╗")
        logger.info("║          🤖 MY LOCAL LLM            ║")
        logger.info("╚══════════════════════════════════════╝")

        show_status(config)

        logger.info("")
        logger.info("1. 💬 Chat / Coding Agent")
        logger.info("2. ⚙️  Settings")
        logger.info("3. 🤖 Model Selection")
        logger.info("4. 📁 Project Selection")
        logger.info("5. 🔎 System Information")
        logger.info("0. 🚪 Exit")

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
            logger.info("")
            logger.info("👋 Goodbye.")
            return
