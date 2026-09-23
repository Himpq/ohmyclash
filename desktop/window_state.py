from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable


logger = logging.getLogger("ohmyclash.desktop.window_state")


class WindowSizeMemory:
    """Persist the last normal window size without writing on every resize tick."""

    def __init__(
        self,
        path: Path,
        minimum_size: tuple[int, int],
        is_maximized: Callable[[object], bool],
        normalize_window_size: Callable[[object, int, int], tuple[int, int]],
        save_delay: float = 0.5,
    ):
        self.path = path
        self.minimum_size = minimum_size
        self.is_maximized = is_maximized
        self.normalize_window_size = normalize_window_size
        self.save_delay = save_delay
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._window = None
        self._maximized = False
        self._size: tuple[int, int] | None = None
        self._timer: threading.Timer | None = None
        self._generation = 0

    def load(self, default_size: tuple[int, int]) -> tuple[int, int]:
        self._size = default_size
        if not self.path.is_file():
            return default_size
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("saved window size could not be read; using the default size")
            return default_size
        size = self._validated_size(saved)
        if size is not None:
            if saved.get("unit") != "logical":
                logger.info("legacy saved window size has ambiguous DPI units; starting at the default size")
                size = None
        if size is None:
            logger.warning("saved window size is invalid or uses unsupported units; using the default size")
            return default_size
        self._size = size
        logger.info("restored window size width=%s height=%s", *size)
        return size

    def attach(self, window: object) -> None:
        self._window = window
        window.events.resized += self._on_resized
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored
        self.capture_current_size()

    def capture_current_size(self) -> None:
        if self._window is None or self._window_is_maximized():
            return
        try:
            self._remember_size(self._window.width, self._window.height)
        except Exception:
            logger.debug("current window size is not available yet", exc_info=True)

    def flush(self) -> None:
        with self._lock:
            timer, self._timer = self._timer, None
            generation, size = self._generation, self._size
        if timer is not None:
            timer.cancel()
        if size is not None:
            self._write_size(generation, size)

    def _on_resized(self, width: int, height: int) -> None:
        if self._window_is_maximized():
            return
        self._remember_size(width, height)

    def _on_maximized(self, *_args) -> None:
        with self._lock:
            self._maximized = True

    def _on_restored(self, *_args) -> None:
        with self._lock:
            self._maximized = False

    def _window_is_maximized(self) -> bool:
        with self._lock:
            window = self._window
            maximized = self._maximized
        if maximized:
            return True
        if window is None:
            return False
        try:
            return bool(self.is_maximized(window))
        except Exception:
            logger.debug("could not determine maximized state; skipping size save", exc_info=True)
            return True

    def _remember_size(self, width: int, height: int) -> None:
        try:
            logical_width, logical_height = self.normalize_window_size(self._window, width, height)
        except Exception:
            logger.warning("window size could not be converted to logical units; skipping save", exc_info=True)
            return
        size = self._validated_size({"width": logical_width, "height": logical_height})
        if size is None:
            return
        with self._lock:
            self._size = size
            self._generation += 1
            generation = self._generation
            timer, self._timer = self._timer, None
            if timer is not None:
                timer.cancel()
            timer = threading.Timer(self.save_delay, self._write_size, args=(generation, size))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _write_size(self, generation: int, size: tuple[int, int]) -> None:
        with self._write_lock:
            with self._lock:
                if generation != self._generation:
                    return
            temporary = self.path.with_name(f"{self.path.name}.pending")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(
                    json.dumps({"unit": "logical", "width": size[0], "height": size[1]}, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
                logger.info("saved window size width=%s height=%s", *size)
            except OSError:
                logger.exception("failed to save window size")

    def _validated_size(self, value: object) -> tuple[int, int] | None:
        if not isinstance(value, dict):
            return None
        width, height = value.get("width"), value.get("height")
        if type(width) is not int or type(height) is not int:
            return None
        min_width, min_height = self.minimum_size
        if not min_width <= width <= 4096 or not min_height <= height <= 2160:
            return None
        return width, height
