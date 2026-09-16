"""Fallback backend for anything that is neither Windows nor macOS — in practice Linux.

Not a port. It exists so that importing WaveFlow's client code on another OS does not explode:
the tests, the setup logic and the server-side tools are all platform-neutral and are worth being
able to run anywhere. Every input function reports "nothing happened" and the app degrades to
what still works.

A real Linux port means X11 (XTest) and Wayland (which deliberately forbids global key injection
from an ordinary client), and it is a separate piece of work with a separate set of decisions.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

NAME = "posix"


def can_type() -> bool:
    return False         # type_text below never sends anything, so words go to the clipboard


def type_text(text: str) -> int:
    return 0


def paste_text(text: str) -> bool:
    return False


def send_backspaces(n: int) -> int:
    return 0


def foreground_window():
    return None


def focus_window(handle) -> bool:
    return False


def window_title(handle) -> str:
    return ""


def caret_rect():
    return None


def register_hotkey(hotkey_id: int, combo: str) -> bool:
    return False


def unregister_hotkey(hotkey_id: int) -> None:
    return None


def needs_native_filter() -> bool:
    return False


def set_hotkey_callback(on_pressed) -> None:
    return None


def make_frameless(win_id: int) -> None:
    return None


def enable_glass(win_id: int, theme: str = "dark") -> bool:
    return False        # the pill paints its own body


def os_theme() -> str:
    return "dark"


def app_data_dir():
    override = os.environ.get("WAVEFLOW_DATA")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "waveflow"


def open_path(path) -> bool:
    try:
        subprocess.run(["xdg-open", str(path)], check=False, timeout=10)
        return True
    except Exception:
        return False


def autostart_enabled() -> bool:
    return False


def set_autostart(on: bool) -> None:
    return None


def microphone_status() -> str:
    """Windows does not gate a desktop app's microphone the way macOS does, and opening an
    input never blocks on a permission prompt. Always "granted"."""
    return "granted"


def request_microphone(callback=None) -> bool:
    if callback:
        callback(True)
    return True


def missing_permissions() -> list[tuple[str, str]]:
    return []


def open_permission_settings(name: str) -> bool:
    return False


def permission_note() -> str:
    return ("Typing into other apps is not implemented on this system. WaveFlow will still "
            "transcribe; it cannot put the words in for you.")
