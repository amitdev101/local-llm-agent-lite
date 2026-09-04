from __future__ import annotations

import logging
import sys

from datetime import datetime
from pathlib import Path

LOGGER_NAME = "myllm"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure_logging(
    log_root: Path,
    enabled: bool = True,
) -> tuple[logging.Logger, Path | None]:
    logger = get_logger()

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Avoid duplicate handlers if main() gets invoked again.
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)

    console_handler.setLevel(logging.DEBUG)

    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    log_path: Path | None = None

    if enabled:
        now = datetime.now()

        day_directory = log_root / now.strftime("%Y-%m-%d")

        day_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        filename = (
            f"myllm-{now:%Y-%m-%d-%H%M%S}-" f"{now.microsecond // 1000:03d}.log.txt"
        )

        log_path = day_directory / filename

        file_handler = logging.FileHandler(
            log_path,
            encoding="utf-8",
        )

        file_handler.setLevel(logging.DEBUG)

        file_handler.setFormatter(formatter)

        logger.addHandler(file_handler)

    return logger, log_path
