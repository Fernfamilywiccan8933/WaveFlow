"""The only place WaveFlow is allowed to know which operating system it is on.

Why this exists
---------------
The client is 7,400 lines. Measured 2026-09-15, about 170 of them touch a Windows API — in
`waveflow.py` (53), `setup_logic.py` (48) and `stt.py` (34). That is 2%. Porting to macOS is
therefore a shim, not a rewrite, and this package is the shim.

The contract
------------
Every function below exists on EVERY platform and never raises because of the platform. When a
platform cannot do something, it returns the documented "did not happen" value — 0, False, None,
"" — and the caller carries on. That rule is what lets the rest of the app stay platform-blind.

The one that matters most is `caret_rect()`. On Windows it asks UIA; on macOS it asks the
Accessibility API. Plenty of apps answer neither. The app already handles that: no caret means no
ghost preview, and the words are still typed (`waveflow.py`, "No real caret -> NO ghost"). So
`caret_rect()` returning None is a normal Tuesday, not an error.

NOT named `platform`
--------------------
`platform` is a standard-library module. A package with that name inside `app/` would shadow it
for every module in this folder, and the failure would appear far from the cause.

Permissions
-----------
Windows needs none of this. macOS needs two grants before dictation works at all:
  * Accessibility    — to type into another app, and to read where its caret is
  * Input Monitoring — to see the hotkey while another app is in front
`missing_permissions()` reports which are absent so the UI can ask for them instead of silently
doing nothing. Without a paid Apple Developer signature macOS drops both grants whenever the app
binary changes, i.e. after every update — see `permission_note()`.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_MAC:
    from . import mac as _impl
elif IS_WINDOWS:
    from . import win as _impl
else:
    from . import posix as _impl

NAME = _impl.NAME


# ---------------------------------------------------------------- typing into the focused app
def type_text(text: str) -> int:
    """Type `text` into whatever app has focus. Returns events sent; 0 means nothing happened."""
    return _impl.type_text(text)


def paste_text(text: str) -> bool:
    """Put `text` on the clipboard and press the paste chord. Restores the old clipboard late,
    off-thread — slow apps read the clipboard well after the keystroke."""
    return _impl.paste_text(text)


def send_backspaces(n: int) -> int:
    """`n` backspace presses, for live-typing corrections. Returns events sent."""
    return _impl.send_backspaces(n)


def can_type() -> bool:
    """Will keystrokes sent to another app actually arrive?

    macOS DROPS synthetic key events from an app without Accessibility, with no error — the
    typing call "succeeds" and nothing appears. Seen on a Mac 2026-09-16: every sentence was
    heard right, logged as typed, and none reached the window. Ask this BEFORE typing, so
    the words can go to the clipboard instead of into the void. Windows: always True.
    """
    return _impl.can_type()


def inject_text(text: str, prefer_paste: bool = True) -> str:
    """Put text at the cursor. Returns the method used: paste | type | failed | empty."""
    if not text:
        return "empty"
    if prefer_paste and paste_text(text):
        return "paste"
    return "type" if type_text(text) else "failed"


# ---------------------------------------------------------------- the other app's window
def foreground_window():
    """An opaque handle for the focused window, or None. Only ever passed back to this package."""
    return _impl.foreground_window()


def focus_window(handle) -> bool:
    return _impl.focus_window(handle)


def window_title(handle) -> str:
    return _impl.window_title(handle)


def caret_rect():
    """(x, y, height) of the text caret in screen pixels, or None when nothing will say.

    None is ordinary: the caller draws no ghost and types anyway.
    """
    return _impl.caret_rect()


# ---------------------------------------------------------------- global hotkeys
def register_hotkey(hotkey_id: int, combo: str) -> bool:
    """Claim `combo` (e.g. "ctrl+alt+space") system-wide. False if the OS refused it."""
    return _impl.register_hotkey(hotkey_id, combo)


def hotkey_supported(combo: str) -> bool:
    """Can this platform even express `combo` as a hotkey? Answers WITHOUT registering, so a
    failed registration can be told apart: unsupported key vs taken / not permitted. The Mac
    log once called an unparseable key a permission refusal, and the user re-granted a
    permission that was never the problem."""
    return _impl.hotkey_supported(combo)


def unregister_hotkey(hotkey_id: int) -> None:
    _impl.unregister_hotkey(hotkey_id)


def needs_native_filter() -> bool:
    """True when the caller must install its OWN Qt native event filter to receive hotkeys.

    Windows delivers WM_HOTKEY through the Qt message loop, so a filter is required there.
    macOS delivers through a CGEventTap on its own run loop, so no filter exists to install
    and this is False.

    This used to be one function that both answered the question AND built the filter, which
    is why it tried to construct the app's filter class with an argument it does not take.
    The caller already owns its filter object; all it needs from here is the question.
    """
    return _impl.needs_native_filter()


def set_hotkey_callback(on_pressed) -> None:
    """Register fn(hotkey_id, pressed) for platforms that call back instead of filtering.

    A no-op on Windows. On macOS the tap calls this FROM ITS OWN THREAD.
    """
    _impl.set_hotkey_callback(on_pressed)


# ---------------------------------------------------------------- the glass pill
def make_frameless(win_id: int) -> None:
    """Remove any frame or border the OS would draw on its own."""
    _impl.make_frameless(win_id)


def enable_glass(win_id: int, theme: str = "dark") -> bool:
    """Real blur-behind under the pill. False means the caller must paint its own body.

    `theme` is "dark" or "light" — already resolved, never "system".
    """
    return _impl.enable_glass(win_id, theme)


def os_theme() -> str:
    """"light" or "dark", from the OS appearance setting. Falls back to "dark".

    This is the OS THEME, not the wallpaper. A dark theme over a light wallpaper reports dark,
    which is correct: the user told the OS which they prefer.
    """
    return _impl.os_theme()


# ---------------------------------------------------------------- where files live
def app_data_dir():
    """The per-user folder for models, logs and settings."""
    return _impl.app_data_dir()


def open_path(path) -> bool:
    """Open a file or folder with the user's own default app."""
    return _impl.open_path(path)


def autostart_enabled() -> bool:
    return _impl.autostart_enabled()


def set_autostart(on: bool) -> None:
    _impl.set_autostart(on)


# ---------------------------------------------------------------- permissions (macOS only)
def microphone_status() -> str:
    """"granted" | "denied" | "undetermined" | "unknown".

    ALWAYS check this before opening an audio input. On macOS an undecided permission makes
    the stream constructor block forever rather than fail — no prompt, no timeout, no
    error. Windows never gates this and answers "granted".
    """
    return _impl.microphone_status()


def request_microphone(callback=None) -> bool:
    """Ask for the microphone. Returns at once; callback(granted) fires later, and NOT on
    the Qt thread. False means the request could not be made at all."""
    return _impl.request_microphone(callback)


def missing_permissions() -> list[tuple[str, str]]:
    """[(name, why)] for permissions this platform needs and does not have. Empty on Windows."""
    return _impl.missing_permissions()


def open_permission_settings(name: str) -> bool:
    """Open the OS settings page for one permission. False if there is nothing to open."""
    return _impl.open_permission_settings(name)


def request_permission(name: str) -> bool:
    """Make the OS itself ask for one permission ("Microphone", "Accessibility", "Input
    Monitoring"). The OS still needs the user's own click — no app may grant these. Returns True if
    the request was made; poll missing_permissions() to see it granted."""
    return _impl.request_permission(name)


def retry_hotkeys() -> bool:
    """Arm hotkeys that failed for lack of permission, now that it may have been granted."""
    return _impl.retry_hotkeys()


def reset_permissions() -> bool:
    """macOS: clear WaveFlow's own stale Accessibility / Input Monitoring entries so the OS asks
    again. Only for the built app, never Python's entries. False elsewhere."""
    return _impl.reset_permissions()


def permission_note() -> str:
    """One honest sentence about permissions, or "" when there is nothing to say."""
    return _impl.permission_note()
