from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "ohmyclash"


class _ExcludeCoreOutput(logging.Filter):
    """Keep raw Mihomo output in runtime/file logs without flooding the console."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(f"{LOGGER_NAME}.core.")


def configure_logging(project_root: Path, level: int = logging.INFO) -> Path:
    log_dir = project_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "ohmyclash.log"
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if getattr(logger, "_ohmyclash_configured", False):
        return log_path
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(_ExcludeCoreOutput())
    file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger._ohmyclash_configured = True  # type: ignore[attr-defined]
    logger.info("logging initialized: %s", log_path)
    return log_path


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{component}")
