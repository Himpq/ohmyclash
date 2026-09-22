from __future__ import annotations

import ctypes
import os
from typing import Any

from .mihomo_manager import ManagerError


INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37
INTERNET_SETTINGS_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def system_proxy_status() -> dict[str, Any]:
    _require_windows()
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS_PATH) as key:
        enabled = bool(winreg.QueryValueEx(key, "ProxyEnable")[0])
        try:
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
        except FileNotFoundError:
            server = ""
    return {"enabled": enabled, "server": server}


def set_system_proxy(enabled: bool, port: int) -> dict[str, Any]:
    _require_windows()
    import winreg

    if not 1 <= port <= 65535:
        raise ManagerError("system proxy port is invalid")
    server = f"127.0.0.1:{port}"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(enabled))
        if enabled:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
    internet_set_option = ctypes.windll.wininet.InternetSetOptionW
    internet_set_option(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
    internet_set_option(None, INTERNET_OPTION_REFRESH, None, 0)
    return system_proxy_status()


def _require_windows() -> None:
    if os.name != "nt":
        raise ManagerError("system proxy is only supported on Windows")
