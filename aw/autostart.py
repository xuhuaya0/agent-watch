"""Cross-platform login auto-start (Windows Registry & macOS LaunchAgents)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys

APP_NAME = "AgentWatch"
MAC_LABEL = "com.agentwatch.tray"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAY_SCRIPT = os.path.join(HERE, "tray.py")


def _mac_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{MAC_LABEL}.plist")


def _win_command() -> str:
    # Prefer pythonw.exe next to sys.executable so no console window flashes on login
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else exe
    return f'"{runner}" "{TRAY_SCRIPT}"'


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, APP_NAME)
                return bool(val)
        except OSError:
            return False
    if sys.platform == "darwin":
        return os.path.exists(_mac_plist_path())
    return False


def set_enabled(enable: bool) -> bool:
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _win_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return is_enabled() == enable

    if sys.platform == "darwin":
        path = _mac_plist_path()
        if enable:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {
                "Label": MAC_LABEL,
                "ProgramArguments": [sys.executable, TRAY_SCRIPT],
                "WorkingDirectory": HERE,
                "RunAtLoad": True,
                "KeepAlive": False,
                "StandardOutPath": os.path.join(HERE, "data", "tray.out.log"),
                "StandardErrorPath": os.path.join(HERE, "data", "tray.err.log"),
            }
            with open(path, "wb") as f:
                plistlib.dump(data, f)
            subprocess.run(["launchctl", "load", "-w", path], check=False)
        else:
            if os.path.exists(path):
                subprocess.run(["launchctl", "unload", "-w", path], check=False)
                try:
                    os.remove(path)
                except OSError:
                    pass
        return is_enabled() == enable

    return False
