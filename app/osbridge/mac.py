"""macOS backend.

Needs pyobjc: `pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz
pyobjc-framework-ApplicationServices`.

WRITTEN, NOT YET RUN. Everything here was written on a Windows machine against Apple's published
APIs. No line of it has executed on a Mac. Treat every claim in these comments as a design
intention until the operator has run it once and the log says otherwise.

Two permissions gate almost all of it, and macOS grants them per-binary:
  * Accessibility    — typing into another app, and reading its caret
  * Input Monitoring — seeing the hotkey while another app is in front
Without a paid Apple Developer signature the binary's identity changes on every rebuild, so both
grants are dropped on every update. `permission_note()` says so out loud rather than letting the
app look broken.

Every function degrades instead of raising. A missing framework, a refused permission and an app
that will not answer all come back as 0 / False / None, and dictation keeps working in whatever
reduced form is still possible — exactly as a missing caret already does on Windows.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

NAME = "macos"

# Virtual key codes (Carbon `Events.h`). These are POSITIONS on the keyboard, not letters, and
# they do not change with the user's layout — which is the point: Cmd+V must be the V key
# wherever V happens to be printed.
VK = {"a": 0x00, "c": 0x08, "v": 0x09, "space": 0x31, "delete": 0x33, "escape": 0x35,
      "w": 0x0D, "return": 0x24, "tab": 0x30}
# Modifier masks (CGEventFlags)
MOD = {"shift": 1 << 17, "ctrl": 1 << 18, "control": 1 << 18, "alt": 1 << 19, "option": 1 << 19,
       "cmd": 1 << 20, "command": 1 << 20, "win": 1 << 20, "windows": 1 << 20, "meta": 1 << 20}

_TAP_STATE: dict = {"tap": None, "thread": None, "hotkeys": {}, "callback": None}


def _quartz():
    try:
        import Quartz
        return Quartz
    except ImportError:
        return None


def _appkit():
    try:
        import AppKit
        return AppKit
    except ImportError:
        return None


# ---------------------------------------------------------------- typing
def type_text(text: str) -> int:
    """Type `text` into the focused app.

    CGEventKeyboardSetUnicodeString posts the CHARACTERS, not key positions, so accents, emoji
    and any layout all come out right — the same reason Windows uses KEYEVENTF_UNICODE.

    Chunked at 20 UTF-16 units. The API takes a length and a buffer, and long strings posted as
    one event have been reported to truncate; 20 is comfortably under any limit and still means
    a normal sentence is a handful of events, not one per character.
    """
    Q = _quartz()
    if not Q or not text:
        return 0
    sent = 0
    try:
        for i in range(0, len(text), 20):
            chunk = text[i:i + 20]
            for keydown in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, keydown)
                if ev is None:
                    return sent
                Q.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
                Q.CGEventPost(Q.kCGHIDEventTap, ev)
                sent += 1
            time.sleep(0.001)      # the receiving app needs a run-loop turn between events
    except Exception:
        return sent
    return sent


def _post_chord(vk: int, flags: int = 0) -> bool:
    Q = _quartz()
    if not Q:
        return False
    try:
        for keydown in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, vk, keydown)
            if ev is None:
                return False
            if flags:
                Q.CGEventSetFlags(ev, flags)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
        return True
    except Exception:
        return False


def paste_text(text: str) -> bool:
    """Clipboard + Cmd+V, with the old clipboard put back late and off-thread.

    The 1.5 s delay is not arbitrary: on Windows the operator hit browsers and Electron apps
    reading the clipboard long after the keystroke, and restoring at 150 ms made them paste the
    OLD text. macOS has the same asynchronous pasteboard behaviour, so the same delay is used.
    """
    A = _appkit()
    if not A:
        return False
    try:
        pb = A.NSPasteboard.generalPasteboard()
        prev = pb.stringForType_(A.NSPasteboardTypeString) or ""
        pb.clearContents()
        if not pb.setString_forType_(text, A.NSPasteboardTypeString):
            return False
        if not _post_chord(VK["v"], MOD["cmd"]):
            return False

        def _restore():
            time.sleep(1.5)
            try:
                pb2 = A.NSPasteboard.generalPasteboard()
                pb2.clearContents()
                pb2.setString_forType_(prev, A.NSPasteboardTypeString)
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()
        time.sleep(0.12)
        return True
    except Exception:
        return False


def send_backspaces(n: int) -> int:
    if n <= 0:
        return 0
    sent = 0
    for _ in range(n):
        if not _post_chord(VK["delete"]):
            break
        sent += 2
        time.sleep(0.001)
    return sent


# ---------------------------------------------------------------- windows
def foreground_window():
    """The frontmost application, not a window handle.

    macOS has no cross-process window handle a normal app may hold. The app only uses this to
    tell "is the thing I was dictating into still in front?", and the frontmost APPLICATION
    answers that question just as well.
    """
    A = _appkit()
    if not A:
        return None
    try:
        app = A.NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.processIdentifier() if app else None
    except Exception:
        return None


def focus_window(handle) -> bool:
    """Bring that process back to the front.

    No ALT-tap trick is needed: macOS has no SetForegroundWindow lock to work around.
    """
    A = _appkit()
    if not A or not handle:
        return False
    try:
        app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(handle))
        if app is None:
            return False
        # ActivateIgnoringOtherApps == 1 << 1
        return bool(app.activateWithOptions_(1 << 1))
    except Exception:
        return False


def window_title(handle) -> str:
    """The app's name. macOS will not give another app's window title without Accessibility, and
    the caller only uses this for a log line, so the app name is the honest answer."""
    A = _appkit()
    if not A or not handle:
        return ""
    try:
        app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(handle))
        return str(app.localizedName() or "") if app else ""
    except Exception:
        return ""


def caret_rect():
    """(x, y, height) of the caret in screen pixels, or None.

    The Accessibility route: focused app -> focused element -> selected range -> bounds of that
    range. Many apps answer none of it, and that is fine — no caret means no ghost preview and
    the text is still typed. Windows has exactly the same hole (`waveflow.py`: "No real caret ->
    NO ghost").

    Coordinates: AXValue returns Quartz screen space, which has its ORIGIN AT THE TOP-LEFT of the
    main display and grows downward — the same convention Qt uses. That is why nothing is flipped
    here. Cocoa's NSScreen space is bottom-left and would need flipping; this is not that.
    """
    try:
        import ApplicationServices as AS
        import Quartz
    except ImportError:
        return None
    try:
        pid = foreground_window()
        if not pid:
            return None
        app = AS.AXUIElementCreateApplication(int(pid))
        err, focused = AS.AXUIElementCopyAttributeValue(app, AS.kAXFocusedUIElementAttribute, None)
        if err or focused is None:
            return None
        err, rng = AS.AXUIElementCopyAttributeValue(focused, AS.kAXSelectedTextRangeAttribute, None)
        if err or rng is None:
            return None
        err, bounds = AS.AXUIElementCopyParameterizedAttributeValue(
            focused, AS.kAXBoundsForRangeParameterizedAttribute, rng, None)
        if err or bounds is None:
            return None
        rect = Quartz.CGRect()
        if not AS.AXValueGetValue(bounds, AS.kAXValueCGRectType, rect):
            return None
        x, y = float(rect.origin.x), float(rect.origin.y)
        h = float(rect.size.height)
        # Same sanity check Windows needed: an element as tall as a window is not a caret, and
        # trusting it put the ghost in a random place on screen. Reject anything implausible.
        if h <= 0 or h > 90:
            return None
        return int(x), int(y), int(h)
    except Exception:
        return None


# ---------------------------------------------------------------- hotkeys
def _parse_combo(combo: str):
    """"ctrl+alt+space" -> (flags, keycode), or None if this layout-independent table has no
    key for it. Unknown single letters map through VK when we know them and are refused when we
    do not, because a wrong keycode would claim the WRONG key system-wide."""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    if not parts:
        return None
    flags, key = 0, None
    for p in parts:
        if p in MOD:
            flags |= MOD[p]
        else:
            key = p
    if key is None:
        return None
    if key in VK:
        return flags, VK[key]
    if len(key) == 1:
        # Ask the current keyboard layout where this character lives, so the hotkey means the
        # letter the user typed rather than a US-layout position.
        code = _keycode_for_char(key)
        if code is not None:
            return flags, code
    return None


def _keycode_for_char(ch: str):
    try:
        import Quartz
        src = Quartz.TISCopyCurrentKeyboardLayoutInputSource()
        data = Quartz.TISGetInputSourceProperty(src, Quartz.kTISPropertyUnicodeKeyLayoutData)
        if data is None:
            return None
    except Exception:
        return None
    # Walking a UCKeyboardLayout from Python is fragile; ask each keycode what it produces.
    try:
        import Quartz as Q
        for code in range(0, 128):
            ev = Q.CGEventCreateKeyboardEvent(None, code, True)
            if ev is None:
                continue
            got = Q.CGEventKeyboardGetUnicodeString(ev, 4, None, None)
            if got and len(got) >= 2 and got[1] and got[1].lower() == ch:
                return code
    except Exception:
        return None
    return None


def register_hotkey(hotkey_id: int, combo: str) -> bool:
    """Claim `combo` system-wide with a CGEventTap.

    A tap — not Carbon's RegisterEventHotKey — because the app also wants to know when the key is
    RELEASED (push-to-talk), which RegisterEventHotKey does not report.

    The tap needs Input Monitoring. Without it CGEventTapCreate returns None, and this returns
    False so the UI can ask for the permission instead of appearing dead.

    The tap does NOT swallow the key: the event is passed through. Swallowing a chord the user
    also uses elsewhere would be a surprise we have no right to spring.
    """
    parsed = _parse_combo(combo)
    if not parsed:
        return False
    _TAP_STATE["hotkeys"][hotkey_id] = parsed
    return _ensure_tap()


def unregister_hotkey(hotkey_id: int) -> None:
    _TAP_STATE["hotkeys"].pop(hotkey_id, None)


def _ensure_tap() -> bool:
    if _TAP_STATE["tap"] is not None:
        return True
    Q = _quartz()
    if not Q:
        return False
    # Only the modifier bits matter; the device-dependent low bits differ between the left and
    # right Shift keys and would make a comparison miss half the time.
    MASK = MOD["shift"] | MOD["ctrl"] | MOD["alt"] | MOD["cmd"]

    def _cb(proxy, etype, event, _refcon):
        try:
            if etype == Q.kCGEventTapDisabledByTimeout:
                # macOS switches a slow tap off. Turn it back on or hotkeys silently die.
                Q.CGEventTapEnable(_TAP_STATE["tap"], True)
                return event
            code = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
            flags = Q.CGEventGetFlags(event) & MASK
            cb = _TAP_STATE["callback"]
            if cb:
                for hid, (want_flags, want_code) in list(_TAP_STATE["hotkeys"].items()):
                    if code == want_code and flags == want_flags:
                        cb(hid, etype == Q.kCGEventKeyDown)
                        break
        except Exception:
            pass
        return event        # always pass the key through

    try:
        mask = Q.CGEventMaskBit(Q.kCGEventKeyDown) | Q.CGEventMaskBit(Q.kCGEventKeyUp)
        tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap,
                                 Q.kCGEventTapOptionListenOnly, mask, _cb, None)
        if tap is None:
            return False        # Input Monitoring not granted
        _TAP_STATE["tap"] = tap
        src = Q.CFMachPortCreateRunLoopSource(None, tap, 0)

        def _run():
            Q.CFRunLoopAddSource(Q.CFRunLoopGetCurrent(), src, Q.kCFRunLoopCommonModes)
            Q.CGEventTapEnable(tap, True)
            Q.CFRunLoopRun()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        _TAP_STATE["thread"] = t
        return True
    except Exception:
        return False


def needs_native_filter() -> bool:
    return False         # the CGEventTap calls back; there is no Qt native event to filter


def set_hotkey_callback(on_pressed) -> None:
    """The tap calls this FROM ITS OWN THREAD. Whatever the app does with it must hop to the
    Qt thread — the app's callbacks only emit a Signal, which does that by itself."""
    _TAP_STATE["callback"] = on_pressed


# ---------------------------------------------------------------- the glass pill
def make_frameless(win_id: int) -> None:
    """Qt's FramelessWindowHint already does it. macOS draws no extra border of its own — the
    Windows 11 compositor frame this exists to remove has no equivalent here."""
    return None


def enable_glass(win_id: int, theme: str = "dark") -> bool:
    """NSVisualEffectView behind the pill.

    This is the API Windows does not have: a real, documented, saturating backdrop. `.hudWindow`
    is the material Apple uses for floating HUD panels, which is what the pill is.

    `win_id` is Qt's winId(), which on macOS is an NSView*. objc.objc_object wraps that pointer
    back into a Python proxy for the real view.
    """
    A = _appkit()
    if not A or not win_id:
        return False
    try:
        import objc
        view = objc.objc_object(c_void_p=win_id)
        window = view.window()
        if window is None:
            return False
        effect = A.NSVisualEffectView.alloc().initWithFrame_(view.bounds())
        effect.setMaterial_(A.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(A.NSVisualEffectStateActive)
        effect.setAutoresizingMask_((1 << 1) | (1 << 4))     # width | height
        # Follow the app's own choice, not the system's, so the pill's Light/Dark/System setting
        # actually decides. "system" is resolved to a concrete value before it reaches here.
        name = A.NSAppearanceNameVibrantLight if theme == "light" else A.NSAppearanceNameVibrantDark
        effect.setAppearance_(A.NSAppearance.appearanceNamed_(name))
        window.setOpaque_(False)
        window.setBackgroundColor_(A.NSColor.clearColor())
        content = window.contentView()
        content.addSubview_positioned_relativeTo_(effect, -1, None)    # -1 = below everything
        return True
    except Exception:
        return False


def os_theme() -> str:
    A = _appkit()
    if not A:
        return "dark"
    try:
        style = A.NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle")
        return "dark" if style and "dark" in str(style).lower() else "light"
    except Exception:
        return "dark"


# ---------------------------------------------------------------- files
def app_data_dir():
    """~/Library/Application Support/WaveFlow — the Apple-sanctioned place, and the equivalent of
    the Windows build keeping everything inside its own folder. WAVEFLOW_DATA still overrides."""
    override = os.environ.get("WAVEFLOW_DATA")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "WaveFlow"


def open_path(path) -> bool:
    try:
        subprocess.run(["open", str(path)], check=False, timeout=10)
        return True
    except Exception:
        return False


# A LaunchAgent, not a login item: it is a plain file this app may write and delete without
# asking the user to approve anything, and `launchctl` is not needed for it to take effect at
# the next login.
def _agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.waveflow.client.plist"


def autostart_enabled() -> bool:
    return _agent_plist().exists()


def set_autostart(on: bool) -> None:
    import plistlib
    import sys as _sys
    p = _agent_plist()
    if not on:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    if getattr(_sys, "frozen", False):
        args = [_sys.executable]
    else:
        args = [_sys.executable, str(Path(__file__).resolve().parent.parent / "waveflow.py")]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            plistlib.dump({"Label": "com.waveflow.client", "ProgramArguments": args,
                           "RunAtLoad": True, "KeepAlive": False}, f)
    except OSError:
        pass


# ---------------------------------------------------------------- permissions
def missing_permissions() -> list[tuple[str, str]]:
    out = []
    if not _has_accessibility():
        out.append(("Accessibility",
                    "so WaveFlow can type the words into the app you are using"))
    if not _has_input_monitoring():
        out.append(("Input Monitoring",
                    "so WaveFlow notices your hotkey while another app is in front"))
    return out


def _has_accessibility() -> bool:
    """AXIsProcessTrusted, asked WITHOUT the prompt option.

    The prompting variant shows a system dialog. This function is called to draw a settings
    screen, and a screen that nags every time it repaints would be intolerable — the app asks
    for the permission when the user presses the button, not when the list is drawn.
    """
    try:
        import ApplicationServices as AS
        return bool(AS.AXIsProcessTrusted())
    except Exception:
        return False


def _has_input_monitoring() -> bool:
    """There is no clean public predicate, so ask the thing we actually need: can a tap be made?

    A listen-only tap is cheap and has no side effect. If it comes back None the permission is
    missing. An existing tap means it already worked.
    """
    if _TAP_STATE["tap"] is not None:
        return True
    Q = _quartz()
    if not Q:
        return False
    try:
        tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap,
                                 Q.kCGEventTapOptionListenOnly,
                                 Q.CGEventMaskBit(Q.kCGEventKeyDown),
                                 lambda *a: a[2], None)
        if tap is None:
            return False
        Q.CFMachPortInvalidate(tap)
        return True
    except Exception:
        return False


_PANES = {
    "Accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "Input Monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "Microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}


def open_permission_settings(name: str) -> bool:
    url = _PANES.get(name)
    if not url:
        return False
    try:
        subprocess.run(["open", url], check=False, timeout=10)
        return True
    except Exception:
        return False


def permission_note() -> str:
    """Said plainly, because the alternative is the app looking broken after every update."""
    return ("macOS ties these permissions to the exact app file. WaveFlow is not signed by a paid "
            "Apple developer account, so macOS forgets them whenever the app is updated. "
            "Tick them again after an update.")
