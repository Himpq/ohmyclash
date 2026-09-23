from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


class ElevationLaunchError(RuntimeError):
    """Raised when Windows cannot start the elevated TUN handoff."""


def is_user_admin() -> bool:
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def launch_elevated_tun(
    core_id: str,
    project_root: Path,
    parent_pid: int,
    preserve_system_proxy: bool = False,
) -> None:
    if os.name != "nt":
        raise ElevationLaunchError("TUN 自动提权仅支持 Windows 桌面版")
    if is_user_admin():
        raise ElevationLaunchError("应用已具有管理员权限，无需重新启动")

    executable = Path(sys.executable).resolve()
    app_arguments = [
        "--enable-tun-on-start",
        core_id,
        "--handoff-parent-pid",
        str(parent_pid),
    ]
    if preserve_system_proxy:
        app_arguments.append("--preserve-system-proxy")
    if not getattr(sys, "frozen", False):
        app_arguments = ["-m", "desktop.launcher", *app_arguments]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell_execute = shell32.ShellExecuteW
    shell_execute.restype = ctypes.c_void_p
    shell_execute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    ctypes.set_last_error(0)
    result = shell_execute(
        None,
        "runas",
        str(executable),
        subprocess.list2cmdline(app_arguments),
        str(project_root.resolve()),
        1,
    )
    if result is None or int(result) <= 32:
        error_code = int(result or 0)
        windows_error = ctypes.get_last_error()
        if error_code == 5 or windows_error == 1223:
            raise ElevationLaunchError("管理员权限申请未获批准，TUN 设置未更改")
        raise ElevationLaunchError(
            f"Windows 无法启动管理员实例（返回代码 {error_code}，系统错误 {windows_error}）"
        )


def wait_for_process_exit(process_id: int, timeout_seconds: float) -> bool:
    if os.name != "nt":
        raise RuntimeError("process handoff wait is only supported on Windows")
    if process_id <= 0 or process_id == os.getpid():
        raise ValueError("invalid handoff parent process id")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    synchronize = 0x00100000
    wait_object_0 = 0
    wait_timeout = 0x00000102
    error_invalid_parameter = 87
    handle = open_process(synchronize, 0, process_id)
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code == error_invalid_parameter:
            return True
        raise OSError(error_code, "could not wait for the previous OhMyClash process")

    try:
        timeout_ms = max(0, int(timeout_seconds * 1000))
        result = wait_for_single_object(handle, timeout_ms)
        if result == wait_object_0:
            return True
        if result == wait_timeout:
            return False
        raise OSError(ctypes.get_last_error(), "could not wait for the previous OhMyClash process")
    finally:
        close_handle(handle)
