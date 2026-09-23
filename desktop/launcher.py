from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from backend.api_server import BackendServer
from backend.app_runtime import ManagedRuntime, create_managed_runtime
from backend.mihomo_manager import MihomoManager
from backend.logging_config import configure_logging, get_logger
from desktop.tray import TrayController
from backend.windows_proxy import set_system_proxy, system_proxy_status


PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
WEBVIEWUI_ROOT = PROJECT_ROOT / "WebViewUI"
FRONTEND_URL = "http://127.0.0.1:5173/"
BACKEND_URL = "http://127.0.0.1:17890"
logger = get_logger("desktop")


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _logical_size_for_physical_pixels(width: int, height: int) -> tuple[int, int]:
    """Convert a measured Windows window size to DPI-aware webview units."""
    if os.name != "nt":
        return width, height
    dpi = int(ctypes.windll.user32.GetDpiForSystem())
    if dpi <= 0:
        raise RuntimeError(f"invalid Windows system DPI: {dpi}")
    scale = dpi / 96
    return round(width / scale), round(height / scale)


def _is_alive(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.8) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def _wait_for(url: str, process: subprocess.Popen[str] | None, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_alive(url):
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"process exited before becoming ready: {url}")
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for {url}")


def _start_backend() -> tuple[MihomoManager, BackendServer, threading.Thread]:
    runtime = create_managed_runtime(PROJECT_ROOT)
    _disable_owned_system_proxy(runtime)
    manager = runtime.build_manager()
    try:
        logger.info("starting managed Mihomo instances")
        manager.start_autostart()
        manager.wait_for_ready()
        server = BackendServer(
            ("127.0.0.1", 17890),
            manager,
            ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174"],
            runtime=runtime,
        )
        thread = threading.Thread(target=server.serve_forever, name="ohmyclash-backend", daemon=True)
        thread.start()
        return manager, server, thread
    except BaseException:
        logger.warning("backend startup did not complete; stopping managed Mihomo instances")
        manager.close()
        raise


def _disable_owned_system_proxy(runtime: ManagedRuntime) -> None:
    status = system_proxy_status()
    logger.info("system proxy state before cleanup enabled=%s server=%s", status["enabled"], status["server"])
    if not status["enabled"]:
        return
    core_id = runtime.core_id_for_proxy_server(status["server"])
    if not core_id:
        logger.info("system proxy is enabled for an external endpoint; leaving it unchanged")
        return
    core = runtime.get_core(core_id)
    set_system_proxy(False, core.mixed_port)
    logger.info("disabled OhMyClash system proxy before runtime start core=%s", core_id)


def _start_frontend() -> subprocess.Popen[str] | None:
    if _is_alive(FRONTEND_URL):
        return None
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return subprocess.Popen(
        [npm, "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=str(PROJECT_ROOT),
        creationflags=_creation_flags(),
    )


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _stop_backend(manager: MihomoManager | None, server: BackendServer | None) -> None:
    runtime = server.runtime if server is not None else None
    try:
        if server is not None:
            server.stop_runtime()
        elif manager is not None:
            manager.close()
    finally:
        if runtime is not None:
            _disable_owned_system_proxy(runtime)


def main() -> int:
    configure_logging(PROJECT_ROOT)
    logger.info("desktop startup")
    parser = argparse.ArgumentParser(description="Launch OhMyClash with WebViewUI and Mihomo backend")
    parser.add_argument("--dist", action="store_true", help="open dist/index.html instead of the Vite dev server")
    parser.add_argument("--devtools", action="store_true", help="enable WebView2 devtools")
    parser.add_argument("--startup", action="store_true", help="mark this launch as a Windows startup launch")
    parser.add_argument("--startup-task-helper", choices=("install", "remove"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.startup_task_helper:
        from backend.startup_task import run_helper
        return run_helper(args.startup_task_helper, PROJECT_ROOT)

    if not WEBVIEWUI_ROOT.is_dir():
        raise RuntimeError(f"WebViewUI checkout not found: {WEBVIEWUI_ROOT}")
    sys.path.insert(0, str(WEBVIEWUI_ROOT))

    from WebViewUI import WebViewApp, WindowApi

    frontend_process = None
    backend_manager = None
    backend_server = None

    try:
        if args.dist or getattr(sys, "frozen", False):
            entry_url = str((PROJECT_ROOT / "dist" / "index.html").resolve())
            if not Path(entry_url).is_file():
                raise FileNotFoundError("dist/index.html does not exist; run npm run build first")
        else:
            frontend_process = _start_frontend()
            _wait_for(FRONTEND_URL, frontend_process, timeout=30)
            entry_url = FRONTEND_URL

        backend_manager, backend_server, _ = _start_backend()
        _wait_for(f"{BACKEND_URL}/api/health", None, timeout=30)

        class OhMyClashApi(WindowApi):
            def __init__(self):
                super().__init__()
                self.tray_resident = False
                self.tray: TrayController | None = None

            def get_start_with_windows(self):
                from backend.startup_task import task_exists
                return {"success": True, "enabled": task_exists()}

            def set_start_with_windows(self, enabled):
                from backend.startup_task import set_enabled
                return {"success": True, "enabled": set_enabled(bool(enabled), PROJECT_ROOT)}

            def set_tray_resident(self, enabled):
                self.tray_resident = bool(enabled)
                if self.tray_resident:
                    self._ensure_tray()
                elif self.tray is not None:
                    self.tray.stop()
                    self.tray = None
                return {"success": True, "enabled": self.tray_resident}

            def _ensure_tray(self):
                if self.tray is None:
                    self.tray = TrayController(self._show_main_window, self._quit_from_tray)
                    self.tray.start()

            def _show_main_window(self):
                win = self._get_window("")
                if win is not None:
                    win.show()
                    try:
                        win.restore()
                    except Exception:
                        pass

            def _quit_from_tray(self):
                self.tray_resident = False
                self.tray = None
                win = self._get_window("")
                if win is not None:
                    super()._do_close(win, "")
                _stop_backend(backend_manager, backend_server)

            def _do_close(self, win, prefix):
                if not prefix and self.tray_resident:
                    self._ensure_tray()
                    win.hide()
                    return {"success": True, "hiddenToTray": True}
                result = super()._do_close(win, prefix)
                if not prefix:
                    if self.tray is not None:
                        self.tray.stop()
                        self.tray = None
                    _stop_backend(backend_manager, backend_server)
                return result

        window_width, window_height = _logical_size_for_physical_pixels(1488, 1056)
        min_width, min_height = _logical_size_for_physical_pixels(1100, 760)
        desktop_api = OhMyClashApi()

        class OhMyClashApp(WebViewApp):
            def __init__(self, *app_args, **app_kwargs):
                self._tray_close_hooked = False
                super().__init__(*app_args, **app_kwargs)

            def _on_shown(self):
                super()._on_shown()
                if not self._tray_close_hooked and self._win is not None:
                    self._win.events.closing += self._on_native_closing
                    self._tray_close_hooked = True

            def _on_native_closing(self):
                if desktop_api.tray_resident and self._win is not None:
                    desktop_api._ensure_tray()
                    self._win.hide()
                    return False
                return True

        app = OhMyClashApp(
            entry_url=entry_url,
            js_api=desktop_api,
            title="OhMyClash",
            width=window_width,
            height=window_height,
            min_size=(min_width, min_height),
            brand="OhMyClash",
            titlebar_height=43,
            use_bootstrap=False,
            use_native_nav_cover=False,
            devtools=args.devtools,
        )
        app.run()
        return 0
    finally:
        logger.info("desktop shutdown")
        _stop_backend(backend_manager, backend_server)
        _stop_process(frontend_process)


if __name__ == "__main__":
    raise SystemExit(main())
