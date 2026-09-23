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
from desktop.frontend_server import (
    PACKAGED_FRONTEND_URL,
    start_packaged_frontend,
    stop_packaged_frontend,
)
from desktop.elevation import (
    ElevationLaunchError,
    is_user_admin,
    launch_elevated_tun,
    wait_for_process_exit,
)
from desktop.tray import TrayController
from desktop.window_state import WindowSizeMemory
from backend.windows_proxy import set_system_proxy, system_proxy_status


PROJECT_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
WEBVIEWUI_ROOT = PROJECT_ROOT / "WebViewUI"
FRONTEND_URL = "http://127.0.0.1:5173/"
BACKEND_URL = "http://127.0.0.1:17890"
logger = get_logger("desktop")


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _logical_size_for_physical_pixels(width: int, height: int, dpi: int | None = None) -> tuple[int, int]:
    """Convert physical WinForms pixels into the logical units used at startup."""
    if os.name != "nt":
        return width, height
    actual_dpi = dpi if dpi is not None else int(ctypes.windll.user32.GetDpiForSystem())
    if actual_dpi <= 0:
        raise RuntimeError(f"invalid Windows DPI: {actual_dpi}")
    scale = actual_dpi / 96
    return round(width / scale), round(height / scale)


def _logical_size_for_window_pixels(window: object, width: int, height: int) -> tuple[int, int]:
    if os.name != "nt":
        return width, height
    native_window = getattr(window, "native", None)
    handle = getattr(native_window, "Handle", None)
    if handle is None:
        raise RuntimeError("native window handle is not available for DPI conversion")
    hwnd = int(handle.ToInt32())
    dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    return _logical_size_for_physical_pixels(width, height, dpi)


def _is_alive(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.8) as response:
            return 200 <= response.status < 300
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


def _start_backend(
    enable_tun_core_id: str | None = None,
    preserve_system_proxy: bool = False,
) -> tuple[MihomoManager, BackendServer, threading.Thread]:
    runtime = create_managed_runtime(PROJECT_ROOT)
    if not preserve_system_proxy:
        _disable_owned_system_proxy(runtime)
    manager: MihomoManager | None = None
    try:
        if enable_tun_core_id:
            runtime.get_core(enable_tun_core_id)
            runtime.update_core(enable_tun_core_id, {"tunEnabled": True})
            logger.info("applying elevated TUN startup request core=%s", enable_tun_core_id)
        manager = runtime.build_manager()
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
        if manager is not None:
            try:
                manager.close()
            except Exception:
                logger.exception("failed to close managed Mihomo instances after startup failure")
        if enable_tun_core_id:
            try:
                runtime.update_core(enable_tun_core_id, {"tunEnabled": False})
                logger.info("rolled back elevated TUN startup request core=%s", enable_tun_core_id)
            except Exception:
                logger.exception("failed to roll back elevated TUN startup request core=%s", enable_tun_core_id)
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


def _stop_backend(
    manager: MihomoManager | None,
    server: BackendServer | None,
    preserve_system_proxy: bool = False,
) -> None:
    runtime = server.runtime if server is not None else None
    try:
        if server is not None:
            server.stop_runtime()
        elif manager is not None:
            manager.close()
    finally:
        if runtime is not None and not preserve_system_proxy:
            _disable_owned_system_proxy(runtime)


def main() -> int:
    configure_logging(PROJECT_ROOT)
    logger.info("desktop startup")
    parser = argparse.ArgumentParser(description="Launch OhMyClash with WebViewUI and Mihomo backend")
    parser.add_argument("--dist", action="store_true", help="serve the packaged dist build instead of the Vite dev server")
    parser.add_argument("--devtools", action="store_true", help="enable WebView2 devtools")
    parser.add_argument("--startup", action="store_true", help="mark this launch as a Windows startup launch")
    parser.add_argument("--startup-task-helper", choices=("install", "remove"), help=argparse.SUPPRESS)
    parser.add_argument("--enable-tun-on-start", metavar="CORE_ID", help=argparse.SUPPRESS)
    parser.add_argument("--handoff-parent-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--preserve-system-proxy", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.startup_task_helper:
        from backend.startup_task import run_helper
        return run_helper(args.startup_task_helper, PROJECT_ROOT)

    if args.handoff_parent_pid is not None:
        if not args.enable_tun_on_start:
            parser.error("--handoff-parent-pid requires --enable-tun-on-start")
        try:
            parent_exited = wait_for_process_exit(args.handoff_parent_pid, timeout_seconds=60)
        except OSError:
            logger.exception("could not wait for the previous OhMyClash process pid=%s", args.handoff_parent_pid)
            return 2
        if not parent_exited:
            logger.error("elevated startup handoff timed out waiting for parent pid=%s", args.handoff_parent_pid)
            return 2
        logger.info("elevated startup handoff parent exited pid=%s", args.handoff_parent_pid)
    if args.enable_tun_on_start and not is_user_admin():
        logger.error("elevated TUN startup was requested without administrator privileges")
        return 2

    if not WEBVIEWUI_ROOT.is_dir():
        raise RuntimeError(f"WebViewUI checkout not found: {WEBVIEWUI_ROOT}")
    sys.path.insert(0, str(WEBVIEWUI_ROOT))

    from WebViewUI import WebViewApp, WindowApi, wintitle

    frontend_process = None
    packaged_frontend_server = None
    packaged_frontend_thread = None
    backend_manager = None
    backend_server = None
    handoff_state = {"preserve_system_proxy": False}

    try:
        if args.dist or getattr(sys, "frozen", False):
            packaged_frontend_server, packaged_frontend_thread = start_packaged_frontend(PROJECT_ROOT / "dist")
            entry_url = PACKAGED_FRONTEND_URL
            try:
                _wait_for(entry_url, None, timeout=10)
            except Exception:
                logger.exception("packaged frontend did not become ready entry_url=%s", entry_url)
                raise
            logger.info("packaged frontend ready entry_url=%s", entry_url)
        else:
            frontend_process = _start_frontend()
            _wait_for(FRONTEND_URL, frontend_process, timeout=30)
            entry_url = FRONTEND_URL
            logger.info("development frontend ready entry_url=%s", entry_url)

        backend_manager, backend_server, _ = _start_backend(
            args.enable_tun_on_start,
            args.preserve_system_proxy,
        )
        _wait_for(f"{BACKEND_URL}/api/health", None, timeout=30)

        class OhMyClashApi(WindowApi):
            def __init__(self):
                super().__init__()
                self.tray_resident = False
                self.tray: TrayController | None = None
                self._elevation_lock = threading.Lock()
                self._elevation_handoff_started = False

            def prepare_tun_enable(self, core_id):
                if is_user_admin():
                    return {"success": True, "alreadyAdmin": True}
                if os.name != "nt":
                    return {"success": False, "message": "TUN 自动提权仅支持 Windows 桌面版"}
                if backend_server is None or backend_server.runtime is None:
                    return {"success": False, "message": "本地后端尚未就绪，无法申请 TUN 权限"}
                try:
                    backend_server.runtime.get_core(str(core_id))
                except Exception as exc:
                    return {"success": False, "message": str(exc)}
                if self._get_window("") is None:
                    return {"success": False, "message": "主窗口尚未就绪，无法申请 TUN 权限"}

                with self._elevation_lock:
                    if self._elevation_handoff_started:
                        return {"success": True, "restarting": True}
                    try:
                        proxy = system_proxy_status()
                        preserve_system_proxy = bool(
                            proxy["enabled"]
                            and backend_server.runtime.core_id_for_proxy_server(proxy["server"])
                        )
                        launch_elevated_tun(
                            str(core_id),
                            PROJECT_ROOT,
                            os.getpid(),
                            preserve_system_proxy,
                        )
                    except ElevationLaunchError as exc:
                        logger.info("elevated TUN handoff was not started core=%s detail=%s", core_id, exc)
                        return {"success": False, "message": str(exc)}
                    self._elevation_handoff_started = True
                    handoff_state["preserve_system_proxy"] = preserve_system_proxy

                timer = threading.Timer(0.35, self._close_for_elevated_handoff)
                timer.daemon = True
                timer.start()
                logger.info(
                    "elevated TUN handoff started core=%s parent_pid=%s preserve_system_proxy=%s",
                    core_id,
                    os.getpid(),
                    preserve_system_proxy,
                )
                return {"success": True, "restarting": True}

            def _close_for_elevated_handoff(self):
                self.tray_resident = False
                if self.tray is not None:
                    try:
                        self.tray.stop()
                    except Exception:
                        logger.exception("failed to stop tray before elevated TUN handoff")
                    self.tray = None
                window_size_memory.flush()
                win = self._get_window("")
                if win is None:
                    logger.error("main window disappeared before elevated TUN handoff shutdown")
                    return
                try:
                    win.destroy()
                except Exception:
                    logger.exception("failed to close the current app for elevated TUN handoff")

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
                _stop_backend(
                    backend_manager,
                    backend_server,
                    preserve_system_proxy=handoff_state["preserve_system_proxy"],
                )

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
                    _stop_backend(
                        backend_manager,
                        backend_server,
                        preserve_system_proxy=handoff_state["preserve_system_proxy"],
                    )
                return result

        default_size = _logical_size_for_physical_pixels(960, 680)
        min_width, min_height = _logical_size_for_physical_pixels(760, 520)
        window_size_memory = WindowSizeMemory(
            PROJECT_ROOT / "data" / "window_size.json",
            (min_width, min_height),
            wintitle.is_window_maximized,
            _logical_size_for_window_pixels,
        )
        window_width, window_height = window_size_memory.load(default_size)
        desktop_api = OhMyClashApi()

        class OhMyClashApp(WebViewApp):
            def __init__(self, *app_args, **app_kwargs):
                self._tray_close_hooked = False
                super().__init__(*app_args, **app_kwargs)

            def _on_shown(self):
                super()._on_shown()
                if not self._tray_close_hooked and self._win is not None:
                    self._win.events.closing += self._on_native_closing
                    window_size_memory.attach(self._win)
                    self._tray_close_hooked = True

            def _on_native_closing(self):
                window_size_memory.flush()
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
        try:
            _stop_backend(
                backend_manager,
                backend_server,
                preserve_system_proxy=handoff_state["preserve_system_proxy"],
            )
        finally:
            try:
                _stop_process(frontend_process)
            finally:
                stop_packaged_frontend(packaged_frontend_server, packaged_frontend_thread)


if __name__ == "__main__":
    raise SystemExit(main())
