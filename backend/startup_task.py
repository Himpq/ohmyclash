from __future__ import annotations

import ctypes
import html
import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .logging_config import get_logger


TASK_NAME = "OhMyClash"
logger = get_logger("startup")


class StartupTaskError(RuntimeError):
    """Raised when the OhMyClash logon task cannot be changed."""


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _require_windows() -> None:
    if os.name != "nt":
        raise StartupTaskError("开机启动任务仅支持 Windows")


def _is_admin() -> bool:
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["schtasks.exe", *arguments],
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
        creationflags=_creation_flags(),
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise StartupTaskError(detail or f"schtasks.exe exited with code {result.returncode}")
    return result


def task_exists() -> bool:
    _require_windows()
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
        creationflags=_creation_flags(),
        check=False,
    )
    return result.returncode == 0


def _account_name() -> str:
    username = os.environ.get("USERNAME") or os.getlogin()
    domain = os.environ.get("USERDOMAIN", "").strip()
    return f"{domain}\\{username}" if domain else username


def _task_target(project_root: Path) -> tuple[Path, str, Path]:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return executable, "--dist --startup", project_root.resolve()
    return executable, "-m desktop.launcher --startup", project_root.resolve()


def _task_xml(project_root: Path) -> str:
    executable, arguments, working_directory = _task_target(project_root)
    esc = lambda value: html.escape(str(value), quote=False)
    account = esc(_account_name())
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Start OhMyClash at user logon with the configured TUN privileges.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{account}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>false</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{esc(executable)}</Command>
      <Arguments>{esc(arguments)}</Arguments>
      <WorkingDirectory>{esc(working_directory)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''


def _install_task_direct(project_root: Path) -> None:
    xml_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix="ohmyclash-task-", suffix=".xml")
        os.close(descriptor)
        xml_path = Path(raw_path)
        xml_path.write_text(_task_xml(project_root), encoding="utf-16")
        _run_schtasks(["/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
    finally:
        if xml_path is not None:
            xml_path.unlink(missing_ok=True)
    if not task_exists():
        raise StartupTaskError("计划任务创建后无法读取")
    logger.info("startup task installed name=%s", TASK_NAME)


def _remove_task_direct() -> None:
    if task_exists():
        _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    logger.info("startup task removed name=%s", TASK_NAME)


def _run_elevated_helper(action: str, project_root: Path) -> None:
    executable = Path(sys.executable).resolve()
    helper_arguments = ["--startup-task-helper", action] if getattr(sys, "frozen", False) else [
        "-m", "desktop.launcher", "--startup-task-helper", action,
    ]
    parameters = subprocess.list2cmdline(helper_arguments)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        str(executable),
        parameters,
        str(project_root.resolve()),
        0,
    )
    if result <= 32:
        raise StartupTaskError("用户取消了管理员授权，计划任务未修改")

    expected = action == "install"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if task_exists() == expected:
            return
        time.sleep(0.25)
    raise StartupTaskError("等待管理员进程完成计划任务操作超时")


def install_task(project_root: Path) -> None:
    _require_windows()
    if _is_admin():
        _install_task_direct(project_root)
        return
    _run_elevated_helper("install", project_root)


def remove_task(project_root: Path) -> None:
    _require_windows()
    if not task_exists():
        return
    if _is_admin():
        _remove_task_direct()
        return
    _run_elevated_helper("remove", project_root)


def set_enabled(enabled: bool, project_root: Path) -> bool:
    if enabled:
        install_task(project_root)
    else:
        remove_task(project_root)
    return task_exists()


def run_helper(action: str, project_root: Path) -> int:
    _require_windows()
    if not _is_admin():
        raise StartupTaskError("计划任务助手必须以管理员身份运行")
    if action == "install":
        _install_task_direct(project_root)
    elif action == "remove":
        _remove_task_direct()
    else:
        raise StartupTaskError(f"unknown startup task action: {action}")
    return 0
