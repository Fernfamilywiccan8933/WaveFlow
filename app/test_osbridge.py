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
import testenv  # noqa: E402,F401 — isolate settings/log BEFORE any app import

import osbridge  # noqa: E402
from osbridge import mac, posix, win  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


# Every function the package promises, with the arguments callers pass.
CONTRACT = {
    "can_type": [],
    "type_text": ["text"],
    "paste_text": ["text"],
    "send_backspaces": ["n"],
    "foreground_window": [],
    "focus_window": ["handle"],
    "window_title": ["handle"],
    "caret_rect": [],
    "register_hotkey": ["hotkey_id", "combo"],
    "unregister_hotkey": ["hotkey_id"],
    "hotkey_supported": ["combo"],
    "needs_native_filter": [],
    "set_hotkey_callback": ["on_pressed"],
    "make_frameless": ["win_id"],
    "order_front_without_focus": ["win_id"],
    "enable_glass": ["win_id", "theme"],
    "os_theme": [],
    "app_data_dir": [],
    "open_path": ["path"],
    "autostart_enabled": [],
    "set_autostart": ["on"],
    "microphone_status": [],
    "request_microphone": ["callback"],
    "missing_permissions": [],
    "open_permission_settings": ["name"],
    "request_permission": ["name"],
    "retry_hotkeys": [],
    "reset_permissions": [],
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


# --- the microphone must never BLOCK the app -----------------------------------------------
# On macOS, with permission undecided, CoreAudio does not fail and does not time out:
# sd.InputStream(...) blocks forever waiting for a prompt that a process with no bundle and no
# usage string can never show. Isolated on real hardware 2026-09-16 — still blocked after 12
# seconds. So the permission is READ first and the caller gets an error it can render.
import time  # noqa: E402

import audio  # noqa: E402


class _FakeStream:
    """Stands in for sd.InputStream. If the gate ever lets a blocked state through, this records
    it — the real thing would hang here instead, which no test can catch."""

    opened = []

    def __init__(self, **kw):
        _FakeStream.opened.append(kw)

    def start(self):
        pass


_real_status, _real_sd = audio.mic_permission, audio.sd
try:
    audio.sd = type("sd", (), {"InputStream": _FakeStream})
    for _status, _should_raise in (("granted", False), ("unknown", False),
                                   ("denied", True), ("undetermined", True)):
        audio.mic_permission = (lambda s=_status: s)
        m = audio.MicStream.__new__(audio.MicStream)
        m.device, m.sr, m.ch, m.chunks, m._stream = None, 16000, 1, [], None
        m._cb = lambda *a: None
        _FakeStream.opened.clear()
        _t0 = time.time()
        try:
            m.start()
            _raised = None
        except audio.MicPermissionError as e:
            _raised = e.status
        _ms = (time.time() - _t0) * 1000
        check(f"mic {_status}: raises rather than blocking", _raised == _status, _should_raise)
        check(f"mic {_status}: answers immediately ({_ms:.0f}ms)", _ms < 500, True)
        # The real cost of getting this wrong: a stream opened in a state that blocks forever.
        check(f"mic {_status}: stream opened only when it is safe",
              bool(_FakeStream.opened), not _should_raise)
    # the error carries WHICH state, because the two need different answers from the user
    audio.mic_permission = lambda: "denied"
    try:
        m = audio.MicStream.__new__(audio.MicStream)
        m.start()
    except audio.MicPermissionError as e:
        check("the error says which state", e.status, "denied")
finally:
    audio.mic_permission, audio.sd = _real_status, _real_sd

# --- a CoreAudio stop that NEVER returns must not freeze WaveFlow (Mac, 2026-09-16) --------------
import threading  # noqa: E402

_forever = threading.Event()                     # never set: the "deadlocked IO thread"


class _HangingStream:
    def __init__(self, hang_on):
        self.hang_on, self.calls = hang_on, []

    def start(self):
        self.calls.append("start")
        if "start" in self.hang_on:
            _forever.wait()

    def stop(self):
        self.calls.append("stop")
        _forever.wait()                          # the real bug: a drain that waits forever

    def abort(self):
        self.calls.append("abort")
        if "abort" in self.hang_on:
            _forever.wait()

    def close(self):
        self.calls.append("close")


m = audio.MicStream.__new__(audio.MicStream)
m._stream = _HangingStream(hang_on={"abort"})
_t0 = time.time()
ok = m.stop(timeout=0.5)
check("a stop that hangs is abandoned, not waited on forever", (ok, time.time() - _t0 < 1.5), (False, True))
check("after an abandoned stop the stream is released", m._stream, None)
m._stream = s2 = _HangingStream(hang_on=set())
check("a normal stop succeeds", m.stop(timeout=2), True)
time.sleep(0.05)
check("abort() is used, never the draining stop()", ("abort" in s2.calls, "stop" in s2.calls), (True, False))

_real_status, _real_sd, _real_open = audio.mic_permission, audio.sd, audio.OPEN_TIMEOUT_S
try:
    audio.mic_permission = lambda: "granted"
    audio.OPEN_TIMEOUT_S = 0.5
    audio.sd = type("sd", (), {"InputStream": lambda **kw: _HangingStream(hang_on={"start"})})
    m = audio.MicStream.__new__(audio.MicStream)
    m.device, m.sr, m.ch, m.chunks, m._stream, m._cb = None, 16000, 1, [], None, (lambda *a: None)
    _t0 = time.time()
    try:
        m.start()
        _err = None
    except audio.MicStuckError:
        _err = "stuck"
    check("an open that hangs raises MicStuckError in time", (_err, time.time() - _t0 < 1.5), ("stuck", True))
finally:
    audio.mic_permission, audio.sd, audio.OPEN_TIMEOUT_S = _real_status, _real_sd, _real_open


class _FinalApp:
    def __init__(self, mic):
        self.emitted, self.final_sig = [], types_ns(emit=lambda sid: self.emitted.append(sid))
        self._mic = mic

    def _close_recorder(self, rec):
        pass

    def _commit_segment(self, reason):
        pass


def types_ns(**kw):
    import types as _t
    return _t.SimpleNamespace(**kw)


class _BoomMic:
    def stop(self):
        raise RuntimeError("device vanished")


import waveflow as _W  # noqa: E402

fa = _FinalApp(None)
try:
    _W.WaveFlow._finalize_job(fa, {"pending": [], "lock": threading.Lock(), "live": False, "rec": None,
                                   "mic": _BoomMic(), "sid": 7})
except RuntimeError:
    pass
check("the session ALWAYS reaches idle, even if releasing the mic fails", fa.emitted, ["7"])

# --- the app refuses to type into the void (Mac, 2026-09-16) ------------------------------------
# Without Accessibility, macOS DROPS synthetic keystrokes and the typing call still "works". Every
# sentence was heard, logged as typed, and none appeared. The app must ask first and hold instead.
check("mac can_type is the Accessibility check",
      "return _has_accessibility()" in inspect.getsource(mac.can_type), True)
check("windows can always type", win.can_type(), True)
check("posix cannot type (it sends nothing)", posix.can_type(), False)

import waveflow  # noqa: E402


class _Emit:
    def __init__(self):
        self.msgs = []

    def emit(self, m):
        self.msgs.append(m)


class _Args:
    replay, demo = "", ""


class _App:
    args = _Args()

    def __init__(self):
        self.error_sig = _Emit()


_real_can = osbridge.can_type
_real_fg = osbridge.foreground_window
try:
    fg_asked = []
    osbridge.foreground_window = lambda: fg_asked.append(1) or 4242
    osbridge.can_type = lambda: False
    a = _App()
    a._can_type = waveflow.WaveFlow._can_type.__get__(a)
    check("no permission -> no typing target", waveflow.WaveFlow._typing_target(a), 0)
    check("...decided before looking at any window", fg_asked, [])
    check("the user is told why, once", len(a.error_sig.msgs), 1)
    check("the message names Accessibility", "Accessibility" in a.error_sig.msgs[0], True)
    waveflow.WaveFlow._typing_target(a)
    check("not repeated on every partial", len(a.error_sig.msgs), 1)
finally:
    osbridge.can_type, osbridge.foreground_window = _real_can, _real_fg

# --- summoning the pill must never take focus from the user's text box (Mac, 2026-09-16) ---------
check("mac never hands back to Qt's activating raise_()", mac.order_front_without_focus(1234), True)
check("windows keeps its (non-activating) raise_()", win.order_front_without_focus(0), False)


class _Pill:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def raise_(self):
        self.calls.append("raise_")

    def winId(self):
        return 1234


for _label, _ret, _want in (("macOS", True, ["show"]), ("Windows", False, ["show", "raise_"])):
    _p = _Pill()
    _real_of = osbridge.order_front_without_focus
    osbridge.order_front_without_focus = lambda wid, r=_ret: r
    try:
        waveflow.WaveFlow._show_pill(_p)
    finally:
        osbridge.order_front_without_focus = _real_of
    check(f"{_label}: pill shown with {_want}", _p.calls, _want)
_wfs = Path(waveflow.__file__).read_text(encoding="utf-8")
_body = _wfs[_wfs.index("class WaveFlow(QWidget)"):]
_raises = [i for i in range(len(_body)) if _body.startswith("self.raise_()", i)]
check("the only raise_() left in the pill class is inside _show_pill's Windows branch", len(_raises), 1)

# --- never delete the USER's text: backspaces only while the cursor is where we left it ----------
# Operator, 2026-09-17: "if I go back in a sentence and add something, it will overwrite the
# previous word". WaveFlow knows what IT typed, not what you typed.
class _Eraser:
    CORRECT_WINDOW_S = waveflow.WaveFlow.CORRECT_WINDOW_S
    _caret_now = waveflow.WaveFlow._caret_now
    _note_write = waveflow.WaveFlow._note_write
    _can_erase = waveflow.WaveFlow._can_erase

    def __init__(self, carets):
        self._carets = list(carets)

    def _caret_at(self):
        return self._carets.pop(0) if self._carets else None


_realc1, _realc2 = waveflow.caret_rect_uia, waveflow.caret_screen_pos
try:
    waveflow.caret_screen_pos = lambda: None
    e = _Eraser([(10, 20, 16), (10, 20, 16)])          # cursor has not moved
    waveflow.caret_rect_uia = e._caret_at
    e._note_write()
    check("cursor still ours -> correcting is allowed", e._can_erase(), True)

    e = _Eraser([(10, 20, 16), (400, 90, 16)])          # the user clicked elsewhere
    waveflow.caret_rect_uia = e._caret_at
    e._note_write()
    check("cursor moved -> refuse to backspace", e._can_erase(), False)

    e = _Eraser([None, None])                           # an app that reports no cursor at all
    waveflow.caret_rect_uia = e._caret_at
    e._note_write()
    check("no cursor reported -> the time window alone decides", e._can_erase(), True)
    e._last_write_t -= waveflow.WaveFlow.CORRECT_WINDOW_S + 0.1
    e._carets = [None, None]
    check("...and an old keystroke is never corrected", e._can_erase(), False)
finally:
    waveflow.caret_rect_uia, waveflow.caret_screen_pos = _realc1, _realc2


class _TyperHost:
    def __init__(self, erase_ok):
        self.erase_ok, self.sent = erase_ok, []


_sent = []
_real_send, _real_back = waveflow.send_text, waveflow.send_backspaces
try:
    waveflow.send_text = lambda t: _sent.append(("type", t))
    waveflow.send_backspaces = lambda n: _sent.append(("erase", n))
    lt = waveflow.LiveTyper.__new__(waveflow.LiveTyper)
    lt._target_ok, lt._on_write = (lambda: True), (lambda: None)
    lt._lock, lt._stop = threading.Lock(), threading.Event()
    lt.goal, lt.typed, lt._paused = "hello wor", "hello world", False
    lt._can_erase = lambda: False
    lt._stop.set()                                   # one pass only
    # run() loops until stopped; drive its body once through a short run
    lt._stop.clear()
    threading.Thread(target=lt.run, daemon=True).start()
    time.sleep(0.15)
    lt._stop.set()
    check("the live typer refuses to backspace when the cursor moved", _sent, [])
    check("...and says so once", lt._paused, True)
finally:
    waveflow.send_text, waveflow.send_backspaces = _real_send, _real_back

_src = Path(waveflow.__file__).read_text(encoding="utf-8")
_live = _src[_src.index("    def _on_live"):_src.index("    def _flush_pending")]
check("live typing checks before backspacing", "if back > 0 and not self._can_erase():" in _live, True)
check("every write records where the cursor ended up", _live.count("self._note_write()") >= 3, True)
_flush = _src[_src.index("    def _flush_pending"):_src.index("    def _confirmed_words")]
check("held words only correct when the cursor is still ours", "and self._can_erase():" in _flush, True)

# --- the pill must survive clicking another app on macOS ------------------------------------------
_wf = Path(waveflow.__file__).read_text(encoding="utf-8")
for cls in ("class GhostText", "class WaveFlow(QWidget)"):
    body = _wf[_wf.index(cls):]
    body = body[:body.index("setWindowFlags") + 600]
    check(f"{cls}: WA_MacAlwaysShowToolWindow set with its window flags",
          "WA_MacAlwaysShowToolWindow, True" in body, True)

# --- server PINGs must not kill the stream reader (Mac, 2026-09-16: died exactly 20 s in) -------
import json as _json  # noqa: E402

import websocket  # noqa: E402

A = websocket.ABNF
check("a server PING with non-UTF-8 bytes is skipped", waveflow.ws_frame_text(A.OPCODE_PING, b"\xff\xfe\x00\x81"),
      ("skip", ""))
check("a PONG is skipped", waveflow.ws_frame_text(A.OPCODE_PONG, b"\x9c\x01"), ("skip", ""))
check("a binary frame is skipped", waveflow.ws_frame_text(A.OPCODE_BINARY, b"\x00"), ("skip", ""))
check("a CLOSE ends the reader", waveflow.ws_frame_text(A.OPCODE_CLOSE, b"\x03\xe8")[0], "close")
check("an empty text frame ends the reader", waveflow.ws_frame_text(A.OPCODE_TEXT, b"")[0], "close")
kind, text = waveflow.ws_frame_text(A.OPCODE_TEXT, '{"stable": "héllo"}'.encode("utf-8"))
check("a text frame is decoded", (kind, _json.loads(text)), ("text", {"stable": "héllo"}))
_rx = _wf[_wf.index("op, raw = ws.recv_data(control_frame=True)"):]
check("the reader sorts frames BEFORE json.loads",
      _rx.index("ws_frame_text(op, raw)") < _rx.index("_json.loads("), True)

# --- hotkeys: every key the recorder can save must register on a Mac (finding 18) ----------------
_names = (list("abcdefghijklmnopqrstuvwxyz0123456789") + [f"f{i}" for i in range(1, 21)]
          + ["space", "tab", "return", "enter", "backspace", "esc", "del", "ins", "home", "end",
             "pgup", "pgdown", "left", "right", "up", "down", "-", "=", "[", "]", ";", "'", ",", ".",
             "/", "\\", "`"])
_bad = [k for k in _names if mac._parse_combo(f"ctrl+alt+{k}") is None]
check("mac parses every recorder key", _bad, [])
check("mac: no two keys share a keycode", len({mac.HOTKEY_KEYS[k] for k in "abcdefghijklmnopqrstuvwxyz0123456789"}), 36)
check("mac: the reported combos now parse",
      [mac._parse_combo(c) is not None for c in ("windows+ctrl+m", "cmd+ctrl+m", "ctrl+alt+d", "ctrl+alt+1", "f5")],
      [True] * 5)
check("mac: M is kVK_ANSI_M (0x2E)", mac._parse_combo("ctrl+m")[1], 0x2E)
check("mac: an unknown key is refused, never guessed", mac._parse_combo("ctrl+alt+é"), None)
check("mac hotkey_supported matches the parser", mac.hotkey_supported("ctrl+alt+m"), True)
_rh = _wf[_wf.index("if osbridge.register_hotkey(hid, combo):"):]
check("an unsupported key is not blamed on permissions",
      _rh.index("osbridge.hotkey_supported(combo)") < _rh.index("Input Monitoring"), True)

# --- hotkeys: Qt's Ctrl/Meta swap on macOS (finding 20) -----------------------------------------
import setup_logic as S  # noqa: E402

check("mac: pressing ⌃⌥W (Qt 'Meta+Alt+W') saves ctrl+alt+w", S.qt_to_hotkey("Meta+Alt+W", mac=True), "ctrl+alt+w")
check("mac: pressing ⌘⌥W (Qt 'Ctrl+Alt+W') saves cmd+alt+w", S.qt_to_hotkey("Ctrl+Alt+W", mac=True), "cmd+alt+w")
check("mac: saved ctrl+alt+w loads as Qt Meta (⌃)", S.hotkey_to_qt("ctrl+alt+w", mac=True), "Meta+Alt+W")
check("mac: an old 'windows' (⌘) config loads as Qt Ctrl (⌘)", S.hotkey_to_qt("windows+alt+w", mac=True), "Ctrl+Alt+W")
check("windows: Qt Meta is the Windows key", S.qt_to_hotkey("Meta+Alt+W", mac=False), "windows+alt+w")
check("windows: unchanged for ctrl", S.qt_to_hotkey("Ctrl+Alt+Space", mac=False), "ctrl+alt+space")
check("only the first chord counts", S.qt_to_hotkey("Ctrl+A, Ctrl+B", mac=False), "ctrl+a")
check("nothing recorded -> empty", S.qt_to_hotkey("", mac=True), "")
import itertools  # noqa: E402

for _mac in (True, False):
    _mods = ["Ctrl", "Meta", "Alt", "Shift"]
    for r in range(0, 5):
        for combo in itertools.combinations(_mods, r):
            for key in ("W", "Space", "F5", "1"):
                seq = "+".join([*combo, key])
                back = S.hotkey_to_qt(S.qt_to_hotkey(seq, mac=_mac), mac=_mac)
                if back != seq:
                    FAILS.append(f"  round trip ({'mac' if _mac else 'win'}): {seq} -> {back}")
    # and every saved mac hotkey must parse on the Mac side
    if _mac:
        for combo in itertools.combinations(_mods, 2):
            hk = S.qt_to_hotkey("+".join([*combo, "M"]), mac=True)
            if mac._parse_combo(hk) is None:
                FAILS.append(f"  mac cannot register what it saved: {hk}")
check("Qt → config → mac flags: ⌃⌥W gives Control|Option, not Command",
      mac._parse_combo(S.qt_to_hotkey("Meta+Alt+W", mac=True))[0], mac.MOD["ctrl"] | mac.MOD["alt"])
for _f in ("settings.py", "wizard.py"):
    _t = (Path(__file__).resolve().parent / _f).read_text(encoding="utf-8")
    check(f"{_f} no longer hand-converts Meta", 'replace("Meta", "windows")' in _t, False)
_st = (Path(__file__).resolve().parent / "settings.py").read_text(encoding="utf-8")
check("settings saves coreml, not dml, for the Mac GPU", '"coreml" if S.IS_MAC else "dml"' in _st, True)

# --- setup ASKS for permissions (operator requirement 2026-09-16) --------------------------------
import types  # noqa: E402
from unittest import mock  # noqa: E402

asked = []
fake_as = types.SimpleNamespace(kAXTrustedCheckOptionPrompt="AXTrustedCheckOptionPrompt",
                                AXIsProcessTrustedWithOptions=lambda o: asked.append(("ax", dict(o))) or False)
fake_q = types.SimpleNamespace(CGRequestListenEventAccess=lambda: asked.append(("listen",)) or False,
                               CGPreflightListenEventAccess=lambda: False)
with mock.patch.dict(sys.modules, {"ApplicationServices": fake_as}), \
     mock.patch.object(mac, "_quartz", lambda: fake_q), \
     mock.patch.object(mac, "open_permission_settings", lambda n: asked.append(("settings", n)) or True):
    check("Accessibility: the system prompt, not a Settings link", mac.request_permission("Accessibility"), True)
    check("...with the prompt option ON", asked[-1], ("ax", {"AXTrustedCheckOptionPrompt": True}))
    check("Input Monitoring: CGRequestListenEventAccess", (mac.request_permission("Input Monitoring"), asked[-1]),
          (True, ("listen",)))
    check("Input Monitoring is read with the real predicate", mac._has_input_monitoring(), False)
    check("an unknown name asks nothing", mac.request_permission("Camera"), False)

ran = []
with mock.patch.object(mac.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or types.SimpleNamespace(returncode=0)):
    fake_ak = types.SimpleNamespace(NSBundle=types.SimpleNamespace(
        mainBundle=lambda: types.SimpleNamespace(bundleIdentifier=lambda: "com.waveflow.client")))
    # Even when the bundle id LOOKS right: a source run must never reset (a false pass came from
    # this check running where AppKit is absent, so the id check alone refused it).
    with mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(mac, "_appkit", lambda: fake_ak):
        check("reset refused from source (would clear PYTHON's grants)", mac.reset_permissions(), False)
    fake_ak = types.SimpleNamespace(NSBundle=types.SimpleNamespace(
        mainBundle=lambda: types.SimpleNamespace(bundleIdentifier=lambda: "org.python.python")))
    with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(mac, "_appkit", lambda: fake_ak):
        check("reset refused for any other bundle id", mac.reset_permissions(), False)
    fake_ak.NSBundle.mainBundle = lambda: types.SimpleNamespace(bundleIdentifier=lambda: "com.waveflow.client")
    with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(mac, "_appkit", lambda: fake_ak):
        check("reset in the built app", mac.reset_permissions(), True)
check("reset touches ONLY WaveFlow's two entries", ran,
      [["tccutil", "reset", "Accessibility", "com.waveflow.client"],
       ["tccutil", "reset", "ListenEvent", "com.waveflow.client"]])

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_qapp = QApplication.instance() or QApplication([])
import panels  # noqa: E402


class _FakeOS:
    def __init__(self):
        self.missing = {"Microphone", "Accessibility", "Input Monitoring"}
        self.log = []
        self.mic = "undetermined"

    def missing_permissions(self):
        return [(n, "") for n in sorted(self.missing)]

    def microphone_status(self):
        return self.mic

    def request_permission(self, n):
        self.log.append(("request", n))
        return True

    def open_permission_settings(self, n):
        self.log.append(("settings", n))
        return True

    def retry_hotkeys(self):
        self.log.append(("retry",))
        return True

    def reset_permissions(self):
        self.log.append(("reset",))
        return True

    def permission_note(self):
        return "note"


fos = _FakeOS()
with mock.patch.dict(sys.modules, {"osbridge": fos}), mock.patch.object(sys, "frozen", True, create=True):
    pp = panels.PermissionPanel()


def _buttons(p):
    from PySide6.QtWidgets import QPushButton
    return [b for b in p.findChildren(QPushButton) if b.text() == "Allow" and not b.parent() is None]


with mock.patch.object(sys, "frozen", True, create=True):
    check("three rows, one Allow each while all are missing", len([b for b in _buttons(pp)]), 3)
    check("no Open Settings button any more",
          any(b.text() == "Open Settings" for b in pp.findChildren(panels.QPushButton)), False)
    check("reset hidden until the user has asked once", pp.reset_btn.isVisibleTo(pp), False)
    pp._allow("Accessibility")
    check("Allow makes macOS ask", fos.log[-1], ("request", "Accessibility"))
    check("reset offered once asked and still missing", pp.reset_btn.isVisibleTo(pp), True)
    fos.mic = "denied"
    pp._allow("Microphone")
    check("a denied mic cannot be re-prompted: its Settings page opens", fos.log[-1], ("settings", "Microphone"))
    # the grant lands while the panel is open
    fos.missing.discard("Input Monitoring")
    fos.log.clear()
    pp.refresh()
    check("Input Monitoring granted -> hotkey armed at once", ("retry",) in fos.log, True)
    fos.log.clear()
    pp.refresh()
    check("no change -> nothing redone", fos.log, [])
    with mock.patch.object(panels.QMessageBox, "question", lambda *a, **k: QMessageBox.No):
        pp._reset()
    check("reset needs a yes", ("reset",) in fos.log, False)
    with mock.patch.object(panels.QMessageBox, "question", lambda *a, **k: QMessageBox.Yes):
        pp._reset()
    check("reset, then ask for both again", fos.log[-3:],
          [("reset",), ("request", "Accessibility"), ("request", "Input Monitoring")])
    fos.missing.clear()
    pp.refresh()
    check("all granted -> no Allow buttons", len(_buttons(pp)), 0)
    check("all granted -> no reset button", pp.reset_btn.isVisibleTo(pp), False)
with mock.patch.object(sys, "frozen", False, create=True):
    pp.refresh(force=True)
    check("from source the note says Python/Terminal get the grant", "Python" in pp.note.text(), True)

# --- the running app arms its hotkey once Input Monitoring is granted, quietly ------------------


class _HK:
    def __init__(self, ok, combo="ctrl+alt+m"):
        self._hotkey_ok, self.cfg, self.started = ok, {"hotkey_show": combo}, 0
        self.tray = None

    def _start_hotkey(self):
        self.started += 1
        self._hotkey_ok = True


_retry = waveflow.WaveFlow._retry_hotkey_if_permitted
with mock.patch.object(osbridge, "hotkey_supported", lambda c: c != "ctrl+alt+é"):
    for label, obj, missing, want in (
            ("already working -> nothing", _HK(True), [], 0),
            ("still no permission -> nothing (no warning every 3 s)", _HK(False), [("Input Monitoring", "")], 0),
            ("unsupported key -> nothing", _HK(False, "ctrl+alt+é"), [], 0),
            ("permission granted -> armed", _HK(False), [], 1)):
        with mock.patch.object(osbridge, "missing_permissions", lambda m=missing: m):
            _retry(obj)
        check(f"hotkey retry: {label}", obj.started, want)
_wfsrc = Path(waveflow.__file__).read_text(encoding="utf-8")
check("the retry timer runs on macOS", "self._hk_retry.timeout.connect(self._retry_hotkey_if_permitted)" in _wfsrc, True)
_wz = (Path(__file__).resolve().parent / "wizard.py").read_text(encoding="utf-8")
_st2 = (Path(__file__).resolve().parent / "settings.py").read_text(encoding="utf-8")
check("wizard uses the shared panel", "PermissionPanel()" in _wz, True)
check("Settings shows it too (permissions can be revoked later)", "PermissionPanel()" in _st2, True)

if FAILS:
    print("OSBRIDGE_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("OSBRIDGE_OK")
