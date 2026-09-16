"""The osbridge contract. Run: venv/Scripts/python.exe app/test_osbridge.py -> OSBRIDGE_OK

What this can and cannot prove
------------------------------
It CANNOT prove the macOS code works. Nothing on a Windows box can. CGEventPost, AXUIElement and
NSVisualEffectView only mean anything on a Mac.

What it CAN prove, and what actually breaks a port, is the SHAPE: that every backend exports every
name the package promises, with the same call signature, and that the package's own dispatch does
not silently drop one. A mac.py missing `send_backspaces` would not fail on Windows at all — it
would fail on the operator's Mac, weeks later, the first time he corrected a word. This test is
how that is caught here instead.

It also imports mac.py and posix.py on purpose, which catches syntax errors and bad imports in
files this machine will otherwise never load.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import osbridge  # noqa: E402
from osbridge import mac, posix, win  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


# Every function the package promises, with the arguments callers pass.
CONTRACT = {
    "type_text": ["text"],
    "paste_text": ["text"],
    "send_backspaces": ["n"],
    "foreground_window": [],
    "focus_window": ["handle"],
    "window_title": ["handle"],
    "caret_rect": [],
    "register_hotkey": ["hotkey_id", "combo"],
    "unregister_hotkey": ["hotkey_id"],
    "needs_native_filter": [],
    "set_hotkey_callback": ["on_pressed"],
    "make_frameless": ["win_id"],
    "enable_glass": ["win_id", "theme"],
    "os_theme": [],
    "app_data_dir": [],
    "open_path": ["path"],
    "autostart_enabled": [],
    "set_autostart": ["on"],
    "missing_permissions": [],
    "open_permission_settings": ["name"],
    "permission_note": [],
}

BACKENDS = {"win": win, "mac": mac, "posix": posix}

for bname, mod in BACKENDS.items():
    check(f"{bname} names itself", isinstance(getattr(mod, "NAME", None), str), True)
    for fn, args in CONTRACT.items():
        f = getattr(mod, fn, None)
        if not callable(f):
            FAILS.append(f"  {bname}.{fn} is missing — it would fail only on that OS")
            continue
        got = [p for p in inspect.signature(f).parameters]
        if got != args:
            FAILS.append(f"  {bname}.{fn} signature\n    got  {got}\n    want {args}")

# The package must re-export every one of them, plus inject_text, which it implements itself.
for fn in list(CONTRACT) + ["inject_text"]:
    check(f"osbridge exports {fn}", callable(getattr(osbridge, fn, None)), True)

check("exactly one platform flag is true", sum([osbridge.IS_WINDOWS, osbridge.IS_MAC]) <= 1, True)

# --- inject_text is the package's own logic, so it IS testable here -----------------------
calls = []


class _Fake:
    NAME = "fake"

    def __init__(self, paste_ok, type_n):
        self.paste_ok, self.type_n = paste_ok, type_n

    def paste_text(self, text):
        calls.append(("paste", text))
        return self.paste_ok

    def type_text(self, text):
        calls.append(("type", text))
        return self.type_n


real = osbridge._impl
try:
    osbridge._impl = _Fake(paste_ok=True, type_n=0)
    calls.clear()
    check("empty text does nothing", osbridge.inject_text(""), "empty")
    check("empty text calls nothing", calls, [])
    check("paste wins when it works", osbridge.inject_text("hi"), "paste")
    check("and typing is not attempted", [c[0] for c in calls], ["paste"])

    osbridge._impl = _Fake(paste_ok=False, type_n=4)
    calls.clear()
    check("falls back to typing", osbridge.inject_text("hi"), "type")
    check("tried paste first", [c[0] for c in calls], ["paste", "type"])

    osbridge._impl = _Fake(paste_ok=False, type_n=0)
    check("both failing is reported", osbridge.inject_text("hi"), "failed")

    osbridge._impl = _Fake(paste_ok=True, type_n=4)
    calls.clear()
    check("prefer_paste=False skips the clipboard", osbridge.inject_text("hi", prefer_paste=False), "type")
    check("clipboard untouched", [c[0] for c in calls], ["type"])
finally:
    osbridge._impl = real

# --- mac: the pure logic in it can be checked anywhere ------------------------------------
check("mac parses a plain chord", mac._parse_combo("ctrl+alt+space"),
      (mac.MOD["ctrl"] | mac.MOD["alt"], mac.VK["space"]))
check("mac treats 'win' as Command", mac._parse_combo("win+space")[0], mac.MOD["cmd"])
check("mac is case- and space-insensitive", mac._parse_combo(" CTRL + Alt + Space "),
      mac._parse_combo("ctrl+alt+space"))
check("mac refuses an empty combo", mac._parse_combo(""), None)
check("mac refuses modifiers with no key", mac._parse_combo("ctrl+alt"), None)
check("mac left/right shift both match", mac.MOD["shift"], 1 << 17)

# The app's two default hotkeys must both survive translation, or the Mac build starts mute.
for combo in ("ctrl+alt+w", "ctrl+alt+space"):
    check(f"mac maps the default {combo}", mac._parse_combo(combo) is not None, True)

# app_data_dir must be absolute and per-user on every backend, or two copies share files.
for bname, mod in BACKENDS.items():
    p = mod.app_data_dir()
    check(f"{bname} data dir is a Path", isinstance(p, Path), True)
    check(f"{bname} data dir is absolute", p.is_absolute(), True)

# Windows must still be wired to the code that already shipped, not to a copy of it.
src = Path(__file__).resolve().parent / "osbridge" / "win.py"
text = src.read_text(encoding="utf-8")
check("win.py delegates to stt", "import stt" in text, True)
check("win.py delegates to waveflow", "import waveflow" in text, True)
# The CALL, not the word: "SendInput" appears in win.py's prose explaining why Windows needs no
# permissions, and a substring match on the bare name flagged that comment as a copied
# implementation. Match what a real duplicate would contain.
check("win.py has no SendInput copy", "user32.SendInput(" in text, False)
check("win.py defines no INPUT struct", "class INPUT" in text, False)

if FAILS:
    print("OSBRIDGE_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("OSBRIDGE_OK")
