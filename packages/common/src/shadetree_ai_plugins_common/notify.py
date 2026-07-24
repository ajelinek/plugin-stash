"""Best-effort native desktop notifications. Never raises."""

from __future__ import annotations

import platform
import shutil
import subprocess


def notify(title: str, message: str) -> bool:
    system = platform.system()
    try:
        if system == "Darwin":
            script = f'display notification "{_esc(message)}" with title "{_esc(title)}"'
            subprocess.run(["osascript", "-e", script], check=True, timeout=5)
            return True
        if system == "Linux":
            if shutil.which("notify-send") is None:
                return False
            subprocess.run(["notify-send", title, message], check=True, timeout=5)
            return True
        if system == "Windows":
            ps_cmd = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                f'$n.ShowBalloonTip(5000, "{_esc(title)}", "{_esc(message)}", '
                "[System.Windows.Forms.ToolTipIcon]::Info)"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=True, timeout=5)
            return True
    except Exception:
        return False
    return False


def _esc(value: str) -> str:
    return value.replace('"', '\\"')
