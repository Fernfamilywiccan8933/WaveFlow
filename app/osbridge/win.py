"""Windows backend.

Deliberately thin. The Win32 code in `stt.py` and `waveflow.py` is the code that shipped, that
the operator has used daily, and that carries hard-won comments (the 1.5 s clipboard restore, the
ALT-tap that unlocks SetForegroundWindow, the caret-height sanity check). Copying it here would
create a second copy to drift; this module CALLS it.

So this file is a routing table, and the Windows behaviour after the port is byte-for-byte the
behaviour before it. The new code all lives in mac.py, where there is nothing to regress.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

NAME = "windows"

_KEYEVENTF_KEYUP = 0x0002


# ---------------------------------------------------------------- typing
def can_type() -> bool:
    return True          # SendInput needs no permission for a normal-integrity target


def type_text(text: str) -> int:
    import stt
    return stt.send_text(text)


def paste_text(text: str) -> bool:
    import stt
    return stt.paste_text(text)


def send_backspaces(n: int) -> int:
    import stt
    return stt.send_backspaces(n)


# ---------------------------------------------------------------- windows
def foreground_window():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return hwnd or None


def focus_window(handle) -> bool:
    import stt
    return stt.focus_window(handle)


def window_title(handle) -> str:
    import stt
    return stt.window_title(handle)


def caret_rect():
    """UIA first (works in native, browser and Electron windows), Win32 caret as a fallback.

    waveflow.caret_rect_uia already applies the sanity check that matters: a "caret" as tall as
    the whole window is not a caret, and trusting it printed the ghost at a random place on
    screen. That check stays where it is.
    """
    import waveflow
    return waveflow.caret_rect_uia()


# ---------------------------------------------------------------- hotkeys
# WM_HOTKEY arrives in the Qt event loop, so waveflow.HotkeyFilter does the work. Registration
# also lives there, next to the vk/modifier parsing it depends on.
def register_hotkey(hotkey_id: int, combo: str) -> bool:
    import waveflow
    parsed = waveflow.parse_combo(combo)
    if not parsed:
        return False
    mods, vk = parsed
    return bool(ctypes.windll.user32.RegisterHotKey(None, hotkey_id, mods | 0x4000, vk))


def hotkey_supported(combo: str) -> bool:
    import waveflow
    return waveflow.parse_combo(combo) is not None


def unregister_hotkey(hotkey_id: int) -> None:
    try:
        ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
    except Exception:
        pass


def needs_native_filter() -> bool:
    return True          # WM_HOTKEY arrives in the Qt message loop


def set_hotkey_callback(on_pressed) -> None:
    """Nothing to do: waveflow.HotkeyFilter dispatches straight from its own callbacks dict."""
    return None


# ---------------------------------------------------------------- the glass pill
def make_frameless(win_id: int) -> None:
    import waveflow
    waveflow.disable_window_frame(win_id)


def enable_glass(win_id: int, theme: str = "dark") -> bool:
    """Windows 11's documented acrylic first, then the old undocumented call.

    DWMWA_SYSTEMBACKDROP_TYPE (38) with DWMSBT_TRANSIENTWINDOW (3) is the material Windows 11's
    own menus and flyouts use. It is documented, it survives OS updates, and it follows the
    system light/dark setting by itself. SetWindowCompositionAttribute — what shipped — is
    undocumented and has been quietly changed by Microsoft before.

    Neither has a SATURATION control, which macOS does have. That is why the Windows pill reads
    slightly flatter than the design mock, and no amount of tuning here closes that gap.
    """
    if _system_backdrop(win_id, theme):
        return True
    import waveflow
    return waveflow.enable_liquid_glass(win_id)


def _system_backdrop(win_id: int, theme: str) -> bool:
    DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_SYSTEMBACKDROP_TYPE = 20, 38
    DWMSBT_TRANSIENTWINDOW = 3
    try:
        d = ctypes.windll.dwmapi
        # Tell DWM which way to tint BEFORE asking for the backdrop, or the first frame flashes
        # the wrong one.
        d.DwmSetWindowAttribute(int(win_id), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                ctypes.byref(ctypes.c_int(1 if theme == "dark" else 0)), 4)
        hr = d.DwmSetWindowAttribute(int(win_id), DWMWA_SYSTEMBACKDROP_TYPE,
                                     ctypes.byref(ctypes.c_int(DWMSBT_TRANSIENTWINDOW)), 4)
        return hr == 0            # S_OK. Anything else = this Windows build has no backdrop type.
    except Exception:
        return False


def os_theme() -> str:
    """HKCU AppsUseLightTheme: 0 = dark, 1 = light. Missing key = dark."""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "dark"


# ---------------------------------------------------------------- files
def app_data_dir():
    import setup_logic
    return setup_logic.app_data()


def open_path(path) -> bool:
    try:
        os.startfile(str(path))       # noqa: S606 — the user's own default app
        return True
    except Exception:
        return False


def autostart_enabled() -> bool:
    import setup_logic
    return setup_logic.autostart_enabled()


def set_autostart(on: bool) -> None:
    import setup_logic
    setup_logic.set_autostart(on)


# ---------------------------------------------------------------- permissions
def microphone_status() -> str:
    """Windows does not gate a desktop app's microphone the way macOS does, and opening an
    input never blocks on a permission prompt. Always "granted"."""
    return "granted"


def request_microphone(callback=None) -> bool:
    if callback:
        callback(True)
    return True


def missing_permissions() -> list[tuple[str, str]]:
    """Windows asks for nothing. SendInput and RegisterHotKey work for any desktop process."""
    return []


def open_permission_settings(name: str) -> bool:
    return False


def request_permission(name: str) -> bool:
    return False         # nothing to ask for


def retry_hotkeys() -> bool:
    return True          # RegisterHotKey never waits on a permission


def reset_permissions() -> bool:
    return False


def permission_note() -> str:
    return ""


# Silence linters about the unused imports kept for symmetry with mac.py.
_ = (sys, Path, _KEYEVENTF_KEYUP)
