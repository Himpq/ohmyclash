from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw
import pystray


class TrayController:
    def __init__(self, show_window: Callable[[], None], quit_app: Callable[[], None]):
        self._show_window = show_window
        self._quit_app = quit_app
        self._icon: pystray.Icon | None = None

    def start(self) -> None:
        if self._icon is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._show, default=True),
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon("OhMyClash", self._create_icon(), "OhMyClash", menu)
        self._icon.run_detached()

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            icon.stop()

    def _show(self, _icon: Any = None, _item: Any = None) -> None:
        self._show_window()

    def _quit(self, _icon: Any = None, _item: Any = None) -> None:
        self.stop()
        self._quit_app()

    @staticmethod
    def _create_icon() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (42, 41, 56, 255))
        draw = ImageDraw.Draw(image)
        draw.polygon(((12, 20), (22, 7), (30, 18), (42, 18), (51, 7), (53, 48), (43, 56), (21, 56), (11, 48)), fill=(24, 75, 137, 255))
        draw.ellipse((22, 29, 28, 35), fill="white")
        draw.ellipse((38, 29, 44, 35), fill="white")
        draw.arc((27, 34, 39, 45), 10, 170, fill="white", width=2)
        return image
