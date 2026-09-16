"""WaveFlow client — push-to-talk dictation for Windows.

Hold the hotkey, speak, release: audio goes to the STT server (local now,
a remote server later — one base-URL change), optional LLM cleanup via Ollama,
result typed at the cursor via SendInput (clipboard-paste fallback for apps
where Unicode SendInput fails, e.g. DirectInput games).

Run:  venv/Scripts/python.exe client/client.py [--url http://127.0.0.1:8756]
      [--hotkey "ctrl+alt+space"] [--cleanup-url http://.../api/generate]
Test: --wav path.wav injects that file's transcript once and exits (no mic),
      used by the automated round-trip test.
"""
import argparse
import ctypes
import io
import sys
import time
import wave
import requests

SAMPLE_RATE = 16000
IS_WINDOWS = sys.platform == "win32"

# ---------- SendInput (Unicode) ----------
# `ctypes.wintypes` EXISTS ONLY ON WINDOWS. On macOS importing it raises
# "ValueError: _type_ 'v' not supported" at IMPORT time — before one line of this file runs.
# It used to be a plain top-level import, and that single line is why `import stt`, and so the
# whole app, could not even load on a Mac.
#
# The structs below read wintypes.WORD in their CLASS BODIES, so a stub object that raises on
# attribute access would not help: the whole block has to be conditional. Everything from
# "pipeline" onward is portable and is always defined.
#
# Direction of delegation, so this can never become a loop: osbridge.win calls INTO this module.
# This module must never import osbridge.
KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

if IS_WINDOWS:
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        # Present only to size the union correctly — SendInput validates
        # cbSize against the FULL Win32 INPUT struct (union of all three).
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTunion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def send_text(text: str) -> int:
    """Type text at the focused control via Unicode SendInput. Returns events sent.

    Windows only. On any other OS this returns 0 and the caller should be going through
    osbridge.type_text() instead — see the module header for why the delegation runs one way.
    """
    if not IS_WINDOWS:
        return 0
    inputs = []
    for ch in text:
        code = ord(ch)
        inputs.append(INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0))))
        inputs.append(INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))
    arr = (INPUT * len(inputs))(*inputs)
    return ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def paste_text(text: str) -> bool:
    """Clipboard paste: preserve prior clipboard, set text, Ctrl+V, restore.
    Instant regardless of length and immune to per-char app slowness. Returns
    True on success. Fails only where the target ignores Ctrl+V (rare).

    Windows only; osbridge.mac has the NSPasteboard + Cmd+V version."""
    if not IS_WINDOWS:
        return False
    import pyperclip
    try:
        prev = ""
        try:
            prev = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        # Ctrl+V via SendInput scancodes (works even if a hotkey mod is stale)
        u = ctypes.windll.user32
        VK_CONTROL, VK_V = 0x11, 0x56
        u.keybd_event(VK_CONTROL, 0, 0, 0)
        u.keybd_event(VK_V, 0, 0, 0)
        u.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        u.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        # restore the clipboard LATE, off-thread: slow apps (browsers/Electron)
        # read the clipboard well after Ctrl+V — restoring at 150ms raced them
        # and they pasted the OLD clipboard (or nothing). 1.5s is safe.
        def _restore(prev=prev):
            time.sleep(1.5)
            try:
                pyperclip.copy(prev)
            except Exception:
                pass
        import threading
        threading.Thread(target=_restore, daemon=True).start()
        time.sleep(0.12)
        return True
    except Exception:
        return False


def send_backspaces(n: int) -> int:
    """n Backspace presses via SendInput — used by live-typing corrections. Windows only."""
    if n <= 0 or not IS_WINDOWS:
        return 0
    VK_BACK = 0x08
    inputs = []
    for _ in range(n):
        inputs.append(INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(VK_BACK, 0, 0, 0, 0))))
        inputs.append(INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(VK_BACK, 0, KEYEVENTF_KEYUP, 0, 0))))
    arr = (INPUT * len(inputs))(*inputs)
    return ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def strip_fillers(text: str) -> str:
    """Remove filler words (um, uh...) deterministically - no LLM needed."""
    import re
    out = re.sub(r"\b(?:um+|uh+|uhm+|erm+|hmm+)\b[,.]?\s*", "", text,
                 flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return out.strip()


def focus_window(hwnd: int) -> bool:
    """Legally take foreground (ALT-tap unlocks SetForegroundWindow for a
    background process), verify we actually got it. Windows only — macOS has no such lock,
    so osbridge.mac just activates the app."""
    if not hwnd or not IS_WINDOWS:
        return False
    u = ctypes.windll.user32
    for _ in range(3):
        u.keybd_event(0x12, 0, 0, 0)              # ALT down
        u.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.08)
        if u.GetForegroundWindow() == hwnd:
            return True
    return False


def window_title(hwnd: int) -> str:
    if not IS_WINDOWS:
        return ""
    u = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(128)
    u.GetWindowTextW(hwnd, buf, 128)
    return buf.value


def inject_text(text: str, prefer_paste: bool = True) -> str:
    """Put text at the cursor. Paste-primary (instant, length-independent);
    SendInput char-by-char fallback. Returns the method used."""
    if not text:
        return "empty"
    if prefer_paste and paste_text(text):
        return "paste"
    n = send_text(text)
    return "sendinput" if n else "failed"


# ---------- pipeline ----------
def auth_headers(token: str) -> dict:
    """Bearer header for a server started with --token. Empty token = no header."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def transcribe(url: str, wav_bytes: bytes, beam: int = 5,
               timeout: float = 25, token: str = "") -> tuple[str, int]:
    """timeout is deliberately SHORT (was 180s — a single stalled request could
    hang the whole app for ~3 min, the reported symptom). A real transcribe of a
    90s-capped buffer is a few seconds; anything past `timeout` is a stall and
    should fail fast with a visible error, not block."""
    t0 = time.perf_counter()
    r = requests.post(f"{url}/v1/audio/transcriptions",
                      files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                      data={"model": "default", "beam": str(beam)}, timeout=timeout,
                      headers=auth_headers(token))
    r.raise_for_status()
    return r.json().get("text", ""), int((time.perf_counter() - t0) * 1000)


def cleanup(cleanup_url: str, cleanup_model: str, text: str) -> str:
    """Optional LLM rewrite (Ollama /api/generate). Falls back to raw on any error."""
    if not cleanup_url or not text:
        return text
    prompt = ("Clean up this dictated text: fix punctuation and casing, remove "
              "filler words (um, uh), apply self-corrections. Output ONLY the "
              f"cleaned text, nothing else.\n\n{text}")
    try:
        r = requests.post(cleanup_url, json={"model": cleanup_model, "prompt": prompt,
                                             "stream": False}, timeout=30)
        r.raise_for_status()
        out = r.json().get("response", "").strip()
        return out or text
    except Exception as e:
        print(f"  cleanup skipped ({e.__class__.__name__}) — using raw transcript")
        return text


def record_while_held(hotkey: str) -> bytes:
    """Record from default mic while the hotkey is held; return WAV bytes."""
    import keyboard
    import sounddevice as sd
    frames = []

    def cb(indata, n, t, status):
        frames.append(bytes(indata))

    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=cb):
        while keyboard.is_pressed(hotkey):
            time.sleep(0.02)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def dictate_once(args, wav_bytes: bytes) -> str:
    text, ms = transcribe(args.url, wav_bytes)
    print(f"  transcript ({ms}ms): {text!r}")
    text = cleanup(args.cleanup_url, args.cleanup_model, text)
    if not text:
        print("  (empty — nothing injected)")
        return ""
    time.sleep(args.inject_delay)
    if args.paste:
        paste_text(text)
    else:
        sent = send_text(text)
        if sent == 0:  # SendInput refused (rare: secure desktop etc.)
            print("  SendInput returned 0 — falling back to clipboard paste")
            paste_text(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8756")
    ap.add_argument("--hotkey", default="ctrl+alt+space")
    ap.add_argument("--cleanup-url", default="", help="Ollama /api/generate URL; empty = raw")
    ap.add_argument("--cleanup-model", default="llama3.2")
    ap.add_argument("--paste", action="store_true", help="force clipboard fallback")
    ap.add_argument("--inject-delay", type=float, default=0.0)
    ap.add_argument("--wav", default="", help="test mode: transcribe+inject this file once")
    args = ap.parse_args()

    if args.wav:
        dictate_once(args, open(args.wav, "rb").read())
        return 0

    import keyboard
    print(f"ready — hold {args.hotkey} to dictate (server {args.url}); ctrl+c quits")
    while True:
        keyboard.wait(args.hotkey)
        print("listening...")
        wav_bytes = record_while_held(args.hotkey)
        try:
            dictate_once(args, wav_bytes)
        except requests.RequestException as e:
            print(f"  STT server error: {e}")


if __name__ == "__main__":
    sys.exit(main())
