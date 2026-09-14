"""WaveFlow — self-hosted voice dictation widget (v5).

Design registry: F:/AI_Projects/WaveFlow_Local/DESIGN.md (per-app) +
~/.claude/design/DESIGN-PROFILE.md (universals). v5 spec:
  - LIQUID GLASS pill: real Windows acrylic blur-behind + light tint + DWM-rounded
    window + top sheen. The desktop blurs through — not an opaque gradient.
  - Face: flowy chromatic waveform only. No text on the widget.
  - HOVER-REVEAL controls (invisible-until-needed ≠ nonexistent): grip-dot move
    handle on the left, gear = mic device menu, × = hide to tray, size grip
    bottom-right. Right-click menu, tray, hotkey, double-click all work too.
  - Ghost prediction at the caret (faint gray overlay) → solid injection on release.
  - Observability: waveflow.log next to the exe (device open, per-request STT
    latency, errors), tray balloons on failure, last latency in the tray tooltip.

Run:  venv/Scripts/python.exe app/waveflow.py [--url ...] [--hotkey ctrl+alt+space]
CI:   --demo <wav> --shot-exit 3.5 --self-shot out.png ;  --mic-check (console+log)
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import threading
import time
import wave
from ctypes import wintypes
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio import MicStream, ReplayMic, clean_input_devices, float_to_wav16k  # noqa: E402
from stt import (auth_headers, cleanup, focus_window, inject_text, send_backspaces,  # noqa: E402
                 send_text, strip_fillers, transcribe, window_title)

from PySide6.QtCore import (QAbstractNativeEventFilter, QRectF, Qt, QTimer,  # noqa: E402
                            Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QIcon,  # noqa: E402
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRegion)
from PySide6.QtWidgets import (QApplication, QLabel, QMenu, QPushButton,  # noqa: E402
                               QSizeGrip, QSystemTrayIcon, QWidget)

STREAM_EVERY = 0.6
PARTIAL_WINDOW_S = 12         # partials transcribe a recent window (fast); final = full
MAX_UTTERANCE_S = 90          # auto-finalize cap: bounds worst-case CPU latency
VAD_RMS_THRESH = 300
# ---------- skins (DESIGN.md, user pick 2026-09-12: "i like B and C") ----------
# Two faces over the SAME glass pill and the same engine. Both shrink hard from the old
# 430x84: the shipped Wispr Flow pill is small and near-black, and a face that survives
# being small is the whole reason it reads as finished rather than as a bar of chrome.
MINT, SKY, BLUSH = (0x37, 0xE0, 0xC8), (0x57, 0xC8, 0xFF), (0xFF, 0x7B, 0xC8)
# The mock uses two violets on purpose: a slightly deeper one in the RIBBONS and a brighter
# one in halo's RIM, which has to hold its own against the glass behind it.
VIOLET = (0x8A, 0x7B, 0xFF)        # ribbons  (#8a7bff)
VIOLET_RIM = (0x9B, 0x7B, 0xFF)    # halo rim (#9b7bff)
SKINS = {
    # B — the widget's own chromatic wave, finished: hero ribbon + 2 companions over a
    # bloom, so it reads as a lit ribbon instead of the pale hairline it was.
    "aurora": {"size": (390, 68), "min": (300, 58), "rad": 26.0, "rim": False},
    # C — symmetrical centre-out bars; the pill's own RIM is the level meter, so it is
    # readable from the corner of the eye without looking straight at it.
    "halo":   {"size": (320, 56), "min": (260, 50), "rad": 24.0, "rim": True},
}
DEFAULT_SKIN = "aurora"
# How far inside the window rectangle the window MASK (the visible silhouette) sits. The body
# is painted past it, so the outline is always cut through solid dark interior.
MASK_INSET = 1.5
HALO_BARS = 19             # odd: there must be a true centre bar to grow outward from

WAVE_POINTS = 120
WAVE_LINES = 7             # a READABLE fan (an earlier ribbon-wave widget): 34 overlapping
                           # hairlines smeared into the 'really thin' line
WAVE_BANDS = 4             # harmonics driving the standing-wave shape
# NOTE: no amplitude gain/gamma. Fan SEPARATION is proportional to amplitude, so any
# taming to "fit the pill" collapses the 7 lines into one (the "single line" a
# user saw). Uses that earlier widget's exact amplitude and clips to the widget instead.
SILENCE_COMMIT_S = 30.0    # quiet this long with nothing pending -> end session (hide)
# Patience BEFORE the first word. The idle limit was lowered to 6s so the widget actually
# auto-collapses when a dictation ends — but the same timer starts at mic-open, so summoning
# the widget and pausing to think closed it before a word was said (observed 2026-09-12:
# "idle 6.1s — ending session" with zero speech). Two different questions deserve two
# different answers: "have you finished?" is only askable once you have started.
PRESPEECH_GRACE_S = 20.0
PHRASE_PAUSE_S = 1.3       # pause this long -> commit+type the phrase, keep listening
CHROMA = [QColor(120, 165, 255), QColor(150, 130, 250), QColor(105, 205, 225)]

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parent
log = logging.getLogger("waveflow")
logging.basicConfig(filename=str(APP_DIR / "waveflow.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

CONFIG_PATH = APP_DIR / "config.json"
# STT server: this machine by default. A remote server is set in config.json "url" (+ "token").
DEFAULT_CONFIG = {"hotkey_show": "ctrl+alt+w",   # summon/hide (summon auto-starts listening)
                  "hotkey_capture": "",          # OPTIONAL toggle-capture key, opt-in ("" = off)
                  "device_name": None,   # mic by NAME (indexes reshuffle between boots)
                  "silence_commit_s": SILENCE_COMMIT_S,  # thinking-pause patience
                  "stream_latency": 16,  # streaming right-context: 1|16|33 (16=480ms, live+accurate)
                  "url": "http://127.0.0.1:8756",
                  "token": ""}


def load_config() -> dict:
    import json
    try:
        cfg = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
    except Exception:
        cfg = dict(DEFAULT_CONFIG)
    return cfg


def save_config(cfg: dict) -> None:
    import json
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        log.info("config saved: %s", cfg)
    except Exception as e:
        log.error("config save failed: %s", e)


# ---------- Windows: acrylic blur-behind + rounded window ----------
def disable_window_frame(hwnd: int) -> None:
    """No acrylic: just make sure Windows adds no outline of its own. Corner rounding off
    (the painted capsule is the shape) and the Win11 1px frame colour set to none."""
    try:
        d = ctypes.windll.dwmapi
        hr_c = d.DwmSetWindowAttribute(int(hwnd), 33, ctypes.byref(ctypes.c_int(1)), 4)
        hr_b = d.DwmSetWindowAttribute(int(hwnd), 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)
        log.info("glass=painted (acrylic off) dwm corner=0x%08X border=0x%08X",
                 hr_c & 0xFFFFFFFF, hr_b & 0xFFFFFFFF)
    except Exception as e:
        log.warning("dwm attributes failed: %s", e)


def enable_liquid_glass(hwnd: int) -> bool:
    """ACCENT_ENABLE_ACRYLICBLURBEHIND via SetWindowCompositionAttribute +
    Win11 DWM rounded corners. Returns True if acrylic applied."""
    try:
        class ACCENT(ctypes.Structure):
            _fields_ = [("State", ctypes.c_int), ("Flags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class DATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                        ("SizeOfData", ctypes.c_size_t)]

        # tint AABBGGRR. Was 0x48524E4A — a LIGHT grey at 28%, from the July design ("iOS
        # liquid glass is translucent, not a dark plate"). The 2026-09-12 mock the user
        # picked IS dark glass, and a light host tint means any pixel the pill does not cover
        # glows grey: it was the colour of every frame, gap and corner observed. Dark
        # host tint = a leftover pixel reads as shadow, not as a border.
        accent = ACCENT(4, 2, 0xB4191410, 0)
        data = DATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                    ctypes.sizeof(accent))
        ok = bool(ctypes.windll.user32.SetWindowCompositionAttribute(
            int(hwnd), ctypes.byref(data)))
        try:  # DWMWA_WINDOW_CORNER_PREFERENCE=33, DWMWCP_DONOTROUND=1
            # The window MASK is the shape now. DWM's own ~8px rounding on top of a masked
            # ~24px capsule is a second, different outline — let the mask be the only one.
            hr_c = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(hwnd), 33, ctypes.byref(ctypes.c_int(1)), 4)
            # DWMWA_BORDER_COLOR=34, DWMWA_COLOR_NONE=0xFFFFFFFE. Windows 11 paints its OWN
            # 1px border on a DWM-rounded window, and on dark glass that reads as a grey ring
            # around the pill (a grey border was still visible, 2026-09-12). It is
            # drawn by the compositor, OUTSIDE Qt's paintEvent, so no amount of tuning the
            # edge pen can remove it — it has to be turned off here.
            hr_b = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(hwnd), 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)
            # HRESULT 0 = applied. Logged because "did the border removal even work?" was
            # unanswerable for three rounds; a silent try/except is not evidence either way.
            log.info("dwm corner=0x%08X border=0x%08X", hr_c & 0xFFFFFFFF, hr_b & 0xFFFFFFFF)
        except Exception as e:
            log.warning("dwm attributes failed: %s", e)
        log.info("acrylic=%s", ok)
        return ok
    except Exception as e:
        log.warning("acrylic unavailable: %s", e)
        return False


# ---------- system-registered hotkeys (Win32 RegisterHotKey — not a hook) ----------
_MODS = {"ctrl": 2, "control": 2, "alt": 1, "shift": 4,
         "win": 8, "windows": 8, "meta": 8}
_VKS = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
        "backspace": 0x08, "back": 0x08, "delete": 0x2E, "del": 0x2E,
        "insert": 0x2D, "ins": 0x2D, "escape": 0x1B, "esc": 0x1B,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21,
        "pagedown": 0x22, "pgdn": 0x22, "up": 0x26, "down": 0x28,
        "left": 0x25, "right": 0x27}


def parse_combo(combo: str):
    """'ctrl+alt+w' -> (modifier_mask, virtual_key) or None."""
    mods, vk = 0, None
    for part in combo.lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _VKS:
            vk = _VKS[part]
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x6F + int(part[1:])
        elif len(part) == 1:
            r = ctypes.windll.user32.VkKeyScanW(ord(part))
            vk = (r & 0xFF) if r != -1 else None
        else:
            return None
    return (mods, vk) if vk else None


class HotkeyFilter(QAbstractNativeEventFilter):
    """Dispatches WM_HOTKEY (0x0312) from the Qt message loop to callbacks."""

    def __init__(self):
        super().__init__()
        self.callbacks: dict[int, object] = {}

    def nativeEventFilter(self, etype, message):
        if etype == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.callbacks:
                self.callbacks[msg.wParam]()
        return False, 0


# ---------- paced live typer (calm, human-ish output — not a blast) ----------
class LiveTyper(threading.Thread):
    """Types toward a goal string in the target window: a few chars per tick,
    paced backspace corrections. Smooth and quiet instead of aggressive bursts."""

    def __init__(self, target_ok):
        super().__init__(daemon=True)
        self._target_ok = target_ok        # callable: safe to type right now?
        self._lock = threading.Lock()
        self.goal = ""
        self.typed = ""
        self._stop = threading.Event()
        self.start()

    def set_goal(self, text: str):
        with self._lock:
            self.goal = text

    def reset(self):
        with self._lock:
            self.goal = ""
            self.typed = ""

    def has_output(self) -> bool:
        with self._lock:
            return bool(self.typed or self.goal)

    def run(self):
        while not self._stop.is_set():
            time.sleep(0.016)
            with self._lock:
                goal, typed = self.goal, self.typed
            if goal == typed or not self._target_ok():
                continue
            common = 0
            for a, b in zip(typed, goal):
                if a == b:
                    common += 1
                else:
                    break
            if len(typed) > common:
                n = min(5, len(typed) - common)
                send_backspaces(n)
                with self._lock:
                    self.typed = self.typed[:-n]
            else:
                chunk = goal[len(typed):len(typed) + 3]
                if chunk:
                    send_text(chunk)
                    with self._lock:
                        self.typed += chunk


# ---------- caret ghost ----------
class _GTI(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


def caret_screen_pos():
    u32 = ctypes.windll.user32
    info = _GTI(cbSize=ctypes.sizeof(_GTI))
    if not u32.GetGUIThreadInfo(0, ctypes.byref(info)) or not info.hwndCaret:
        return None
    pt = wintypes.POINT(info.rcCaret.left, info.rcCaret.bottom)
    u32.ClientToScreen(info.hwndCaret, ctypes.byref(pt))
    return pt.x, pt.y


# ---------- UIA caret: the real insertion point in ANY app (native, browser,
# Electron) via TextPattern2.GetCaretRange — so the live grey prediction sits at
# the cursor, not above the widget. ----------
_UIA = None


def _uia():
    global _UIA
    if _UIA is None:
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as C
        inst = comtypes.client.CreateObject(C.CUIAutomation, interface=C.IUIAutomation)
        _UIA = (inst, C)
    return _UIA


def caret_rect_uia():
    """(x, y, h) screen position of the text caret in the focused app, via UIA.
    Falls back to the focused field's top-left, else None."""
    try:
        iuia, C = _uia()
        el = iuia.GetFocusedElement()
        for pid, iface in ((C.UIA_TextPattern2Id, C.IUIAutomationTextPattern2),
                           (C.UIA_TextPatternId, C.IUIAutomationTextPattern)):
            try:
                patt = el.GetCurrentPattern(pid)
                if not patt:
                    continue
                tp = patt.QueryInterface(iface)
                if pid == C.UIA_TextPattern2Id:
                    a = ctypes.c_int(0)
                    try:
                        rng = tp.GetCaretRange(ctypes.byref(a))
                    except TypeError:
                        _a, rng = tp.GetCaretRange()
                else:
                    sel = tp.GetSelection()
                    if not (sel and sel.Length):
                        continue
                    rng = sel.GetElement(0)
                arr = rng.GetBoundingRectangles()
                if arr and len(arr) >= 4:
                    return (int(arr[0]), int(arr[1]), int(arr[3]))
            except Exception:
                continue
        br = el.CurrentBoundingRectangle       # last resort: field top-left
        return (int(br.left) + 4, int(br.top) + 4, int(br.bottom - br.top))
    except Exception:
        return None


class GhostText(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.label = QLabel(self)
        self.label.setStyleSheet(
            "color: rgba(165,170,180,210); background: rgba(28,30,35,205);"
            "border-radius: 6px; padding: 3px 9px; font-size: 13px;")
        self.hide()

    def show_prediction(self, text: str, caret):
        """Live grey prediction AT the caret (caret = (x, y, h) from UIA, works
        in native/browser/Electron). No caret -> don't show a misplaced ghost."""
        if not text or not caret:
            self.hide()
            return
        self.label.setText(text[-110:])
        self.label.adjustSize()
        self.resize(self.label.size())
        x, y, ch = caret
        # grey text starts at the caret x, vertically aligned to the caret line
        self.move(x + 1, y + (ch - self.height()) // 2)
        if not self.isVisible():
            self.show()


# ---------- waveform ----------
class WaveWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.env = np.zeros(WAVE_POINTS, dtype=np.float32)
        self.bands = np.zeros(WAVE_BANDS, dtype=np.float32)
        self.phase = 0.0
        self.energy = 0.2          # smoothed scalar; floors at 0.2 so it never flatlines
        self.target = 0.2
        self._t = np.linspace(0, 1, WAVE_POINTS)
        self._taper = np.sin(self._t * np.pi) ** 0.8    # ends taper to nothing
        self.active = False
        self.paused = False
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # OWN 33ms clock (an earlier ribbon-wave widget): the flow must keep moving and the
        # energy must keep easing even between audio callbacks. Driving phase from
        # set_spectrum() tied the motion to the audio tick rate, so it never
        # actually flowed — this is why "only the colour changed".
        self._tm = QTimer(self)
        self._tm.timeout.connect(self._anim)
        self._tm.start(33)

    def _anim(self):
        self.phase += 0.42
        # ASYMMETRIC easing: snap UP to a syllable, settle DOWN gently. One shared 0.18 meant a
        # word could be over before the face had risen to meet it — observed as "just isn't
        # reactive enough". Fast attack is what reads as "it hears me"; slow release keeps it
        # from strobing between words.
        k = 0.55 if self.target > self.energy else 0.10
        self.energy += (self.target - self.energy) * k
        self.update()

    def level(self) -> float:
        """Perceptual 0..1 voice level for the faces.

        `energy` floors at 0.2 and normal speech lives in its lower half, so a linear map
        spends most of its range on shouting. The 0.6 power lifts ordinary speech into the
        visible range — the same reason audio meters are not linear.
        """
        return float(np.clip((self.energy - 0.2) / 0.8, 0.0, 1.0)) ** 0.6

    def push(self, amp: float):
        # kept for demo compatibility: treat the scalar as broadband energy
        self.set_spectrum(np.full(WAVE_BANDS, float(np.clip(amp, 0, 1)),
                                  dtype=np.float32))

    def set_spectrum(self, mags):
        """Feed per-band magnitudes; drive ONE smoothed energy scalar.

        Ported from an earlier ribbon-wave widget (whose docstring credits this widget —
        it improved on it). The old model drove each band straight off the live
        FFT, so the ribbon jittered band-by-band ("unbalanced, moves around a
        lot") and collapsed to a hairline in the gaps between words ("really
        thin"). Two fixes, both from that earlier version:
          * ONE energy value, lerped toward the target — motion stays smooth and
            coherent instead of 6 bands fighting each other frame to frame.
          * energy FLOORS at 0.2 — the ribbon always keeps body and breathes at
            rest, rather than flatlining into a thin line whenever you pause.
        Shape still comes from harmonics + drift, so it warps in place.
        """
        m = np.clip(np.asarray(mags, dtype=np.float32), 0, 1)
        self.target = float(np.clip(m.max() if m.size else 0.0, 0.2, 1.0))

    def _smooth(self, ys, w):
        xs = np.linspace(0, w, len(ys))
        path = QPainterPath()
        path.moveTo(xs[0], ys[0])
        for i in range(1, len(ys)):
            path.quadTo(xs[i - 1], ys[i - 1],
                        (xs[i - 1] + xs[i]) / 2, (ys[i - 1] + ys[i]) / 2)
        path.lineTo(xs[-1], ys[-1])
        return path

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if getattr(self, "skin", DEFAULT_SKIN) == "halo":
            self._paint_halo(p)
        else:
            self._paint_aurora(p)
        p.end()

    # ---- skin C: centre-out bar spectrum -------------------------------------------
    def _paint_halo(self, p):
        """Bars grown from the MIDDLE outward, mirrored.

        Wispr Flow's shipped pill uses dense vertical bars, not a curve, and the reason is
        legibility at small sizes: a smooth line goes mushy when it is only 40px tall, while
        bars stay crisp. Mirroring from the centre is what makes it read as "live" instead of
        "scrolling" — there is no direction of travel to mistake for progress.
        """
        w, h = self.width(), self.height()
        mid_y, mid_i = h / 2, HALO_BARS // 2
        pitch = w / float(HALO_BARS + 1)
        bw = max(2.4, min(3.4, pitch * 0.34))
        voice = self.level()
        p.setPen(Qt.NoPen)
        for i in range(HALO_BARS):
            d = abs(i - mid_i) / float(mid_i)          # 0 at centre -> 1 at the ends
            # Each bar rides its own harmonic so the block breathes instead of pulsing as
            # one slab. The ripple is kept SHALLOW (0.72-1.0): at 0.1-1.0 it cut most bars
            # to a fraction of the level at any instant, so half the face stayed stubby even
            # mid-sentence — a big part of "not reactive enough".
            osc = 0.72 + 0.28 * np.sin(self.phase * (0.7 + d * 0.9) + i * 0.85)
            # rest height 0.12 so the bars are always visible; the perceptual level() lifts
            # ordinary speech into the visible range instead of saving it for shouting
            lvl = (0.12 + 0.88 * voice) * osc * (1.0 - d * 0.45)
            # x1.35 headroom: at normal speaking volume the centre bars reached only ~half the
            # face, which still read as timid. Clipping keeps a shout from overflowing.
            bh = max(3.0, (h - 10) * float(np.clip(lvl * 1.35, 0.06, 1.0)))
            x = pitch * (i + 1) - bw / 2
            # No pause colours. `paused` flips on every gap between words; amber/grey bars
            # made the face blink its colour through a sentence. Colour is constant; the
            # voice shows up as HEIGHT and brightness, which is what reacting means.
            # mock's cBar runs violet -> sky -> mint, so the bars use the RIM violet and pass
            # through sky on the way out rather than fading straight to purple
            mixed = (MINT, SKY, VIOLET_RIM)
            seg = d * 2.0
            i0 = 0 if seg < 1.0 else 1
            f0 = seg - i0
            a0, b0 = mixed[i0], mixed[i0 + 1]
            c = QColor(int(a0[0] + (b0[0] - a0[0]) * f0),
                       int(a0[1] + (b0[1] - a0[1]) * f0),
                       int(a0[2] + (b0[2] - a0[2]) * f0),
                       int((215 - 60 * d) * (0.55 + 0.45 * voice)))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, mid_y - bh / 2, bw, bh), bw / 2, bw / 2)

    def rim_energy(self) -> float:
        """0..1 for the halo skin's glowing rim.

        Not gated on `paused`: that flag flips on every gap between words, so the rim
        switched off mid-sentence. It follows the smoothed voice level instead, so it fades
        rather than blinks. NO resting floor: a rim drawn faintly enough to be "present at
        rest" is desaturated by the dark glass into a faint GREY ring round the whole pill —
        measured, 169 grey outline pixels at rest, 0 when loud. The bars are the always-on
        part of this face; the rim is the reaction.
        """
        if not self.active:
            return 0.0
        return self.level()

    # ---- skin B: chromatic ribbons ---------------------------------------------------
    def _paint_aurora(self, p):
        # Reference model: a STANDING wave that bends/expands in place. Each
        # frequency band drives its own harmonic; the summed shape warps as the
        # spectrum changes. The fine-line fan spreads around that shape.
        w, h = self.width(), self.height()
        mid = h / 2
        t = self._t
        # ONE energy scalar drives every harmonic (an earlier ribbon-wave model), so the
        # bundle moves as one coherent ribbon instead of 6 bands fighting per frame.
        energy = self.energy
        # That earlier widget's ribbon amplitude, EXACTLY: harmonics summed UN-normalised and
        # amp = (mid-3). An earlier "port" divided by the weight-sum (~2.8) AND by 1.8 to fit
        # the fan — which is precisely why only the colour changed and the flow stayed
        # flat. The over-drive IS the look: the ribbon swells into the pill and the
        # outer fan lines ride its edges, instead of tracing a tame hairline.
        def wave(offset: float, scale: float):
            """One ribbon's y-curve. `offset` shifts its harmonic phase so the three
            ribbons weave around each other instead of moving as one thick line."""
            # LOW harmonics, steeply weighted. Frequencies of (k+2) half-cycles with a gentle
            # 1/(1+0.35k) rolloff put 4-5 wiggles across the pill and read as noise; the
            # approved mock is a smooth sweeping S of about two humps. Frequency and weight
            # both matter — dropping the rolloff alone just makes a quieter scribble.
            s = np.zeros(WAVE_POINTS, dtype=np.float32)
            for k in range(WAVE_BANDS):
                drift = np.sin(self.phase * (0.42 + k * 0.18) + k * 1.7 + offset)
                s += (np.sin(t * np.pi * (1.25 + k * 0.7) + drift * 0.8 + offset)
                      / (1.0 + k * 1.35))
            # Amplitude comes ONLY from `amp` below, never from inside the harmonic sum —
            # so loudness has one control, with a floor that keeps the ribbons visible.
            return mid + (mid - 3) * self._taper * s * scale * 1.12 * amp

        # ALWAYS the three ribbons. The previous face swapped to a single resting line
        # whenever `paused` was true — and `paused` flips on every sub-second gap between
        # words, so the ribbons blinked in and out all through a sentence (observed: "the
        # visual comes in and out, it should be always showing, but react when i talk").
        # Now the face never changes; only its size and brightness follow the voice.
        lvl = self.level()
        amp = 0.24 + 0.76 * lvl          # floor: calm and visible at rest, never collapsed
        glow = 0.45 + 0.55 * lvl         # brightness follows the voice the same way

        # THREE ribbons, as picked. The previous build shipped the OLD 7-line grey fan and
        # only shrank the pill, which is why the response was "the ui is still the old one":
        # a fan of near-white hairlines on pale glass has almost no colour separation, so the
        # face never reads as the chromatic ribbon the mock promised. These are three
        # distinct, saturated curves that weave — the thing that was actually approved.
        hero = self._smooth(wave(0.0, 1.0), w)
        # BLOOM first: the hero curve as a few wide, very faint strokes. Qt has no cheap blur
        # inside paintEvent, so stacked translucent strokes are the standard approximation.
        for width, alpha in ((13.0, 18), (8.0, 26), (4.5, 38)):
            p.setPen(QPen(QColor(SKY[0], SKY[1], SKY[2], int(alpha * lvl)),
                          width, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(hero)

        def ribbon_pen(stops, width, alpha):
            g = QLinearGradient(0, 0, w, 0)
            for at, rgb in stops:
                g.setColorAt(at, QColor(rgb[0], rgb[1], rgb[2], alpha))
            return QPen(g, width, Qt.SolidLine, Qt.RoundCap)

        # companions first so the hero sits ON TOP and stays the thing the eye lands on
        p.setPen(ribbon_pen(((0.0, VIOLET), (0.5, BLUSH), (1.0, MINT)), 1.9, int(158 * glow)))
        p.drawPath(self._smooth(wave(2.1, 0.74), w))
        p.setPen(ribbon_pen(((0.0, SKY), (0.55, MINT), (1.0, SKY)), 1.7, int(116 * glow)))
        p.drawPath(self._smooth(wave(4.2, 0.55), w))
        p.setPen(ribbon_pen(((0.0, MINT), (0.5, SKY), (1.0, VIOLET)), 2.4, int(255 * glow)))
        p.drawPath(hero)


# ---------- glossy gray glass pill (how iOS does it: specular top edge + sheen
# gradient on the upper third + light-top->dark-bottom depth, over the blur) -----
def paint_pill(p, r, acrylic, rad=22.0, rim=0.0):
    path = QPainterPath()
    path.addRoundedRect(r, rad, rad)
    # HALO rim (skin C): the pill's own edge is the level meter. Drawn UNDER the body as
    # widening, fading strokes so the colour blooms outward past the glass rather than
    # sitting on it — the same stacked-stroke glow trick as the aurora bloom.
    rim_draw = []
    if rim > 0.0:
        # Bloom INWARD: progressively inset paths, faint and wide first, bright hairline
        # last. Drawn AFTER the body (see below) so it is not painted over. An outward
        # glow needs window margin, and margin is exactly what showed the acrylic.
        # steps start INSIDE the window mask (inset MASK_INSET) so the bright hairline is
        # never half-cut by the silhouette
        # The bright HAIRLINE keeps its strength and fades in over a short window; only the
        # soft glow scales with level. Scaling every layer's alpha together made a half-lit
        # rim half-transparent, and a half-transparent colour over dark glass is a muted
        # blue-GREY — measured at mid volume, right on the outline. Colour stays vivid;
        # loudness shows as how much glow surrounds it.
        on = float(np.clip((rim - 0.04) / 0.12, 0.0, 1.0))
        # The widest layer (7px at alpha 30) is gone: that faint it is not a glow, it is a
        # grey smear on dark glass — pixel profile showed it as the rows of blue-grey just
        # inside the top edge at mid volume.
        # Loudness drives glow WIDTH at a fixed, saturated alpha — never alpha alone. Any
        # colour thinned by alpha on dark glass lands at the same muted blue-grey; three rounds
        # of measurement showed every alpha-scaled layer failing the same way.
        for step, width, alpha, scaled in ((3.4, 1.2 + 3.0 * rim, 96, True),
                                           (2.4, 1.8, 168, False)):
            a = int(alpha * on * (1.0 if scaled else (0.75 + 0.25 * rim)))
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            g.setColorAt(0.0, QColor(MINT[0], MINT[1], MINT[2], a))
            g.setColorAt(0.5, QColor(SKY[0], SKY[1], SKY[2], a))
            g.setColorAt(1.0, QColor(VIOLET_RIM[0], VIOLET_RIM[1], VIOLET_RIM[2], a))
            rp = QPainterPath()
            rp.addRoundedRect(QRectF(r).adjusted(step, step, -step, -step),
                              max(1.0, rad - step), max(1.0, rad - step))
            # QPen(gradient) alone is not a valid PySide6 overload — the width must be in
            # the constructor, as the edge pen below already does.
            rim_draw.append((QPen(g, width), rp))
    # BODY: gray, lighter top -> darker bottom (glass depth). Translucent tint
    # over the acrylic blur when available; a solid gray body without it.
    body = QLinearGradient(0, r.top(), 0, r.bottom())
    if acrylic:
        # DARK glass, not a pale wash. At alpha 66/30/70 the tint was ~15-26% opaque, so the
        # pill took the colour of whatever was behind it and rendered near-WHITE on a light
        # desktop — "the ui is still the old one". DESIGN.md has said
        # "dark-gray liquid glass" since 2026-07-12; this is that. Still translucent enough
        # to see the blur move behind it, which is what makes it glass rather than paint.
        # EXACTLY the mock's stops (#39404b .82 / #1b1f27 .90 / #101319 .95). A first pass
        # eyeballed these at 74-84% alpha, which let too much of a light desktop through and
        # read as GREY rather than dark glass — noticed immediately. When a
        # mock has been approved, take its numbers; do not re-derive them by eye.
        # Top stop darkened and made more opaque than the mock's (#39404b at .82). The mock was
        # drawn over a DARK backdrop; over a pale desktop that top stop let enough light through
        # to put a band of grey pixels (~rgb 96,104,112) along the entire top edge — measured,
        # 514 of them, every one on the top straight edge. Design for the worst backdrop.
        body.setColorAt(0.0, QColor(46, 52, 62, 234))
        body.setColorAt(0.46, QColor(27, 31, 39, 238))
        body.setColorAt(1.0, QColor(16, 19, 25, 244))
    else:
        # Painted glass (the default since 2026-09-13): the SAME approved dark stops. The old
        # values here were the July light-grey body, which would have undone the retoning the
        # moment acrylic was switched off.
        body.setColorAt(0.0, QColor(46, 52, 62, 236))
        body.setColorAt(0.46, QColor(27, 31, 39, 240))
        body.setColorAt(1.0, QColor(16, 19, 25, 246))
    # SILHOUETTE RULE (2026-09-13 audit, after two edge fixes that did not hold): NOTHING
    # light may sit on the outline. The window mask (inset MASK_INSET) is the silhouette,
    # and the body is painted OVERSIZED past it, so every pixel the mask lets through is
    # fully-covered dark interior — no antialiased partial pixel, no sliver of bare acrylic
    # between a polygon mask and a painted curve. There is deliberately no edge stroke:
    # a highlight traced along the outline IS a frame, whatever alpha it is drawn at.
    if acrylic:
        # masked window: overfill so the hard mask edge cuts through solid interior
        big = QPainterPath()
        big.addRoundedRect(QRectF(r).adjusted(-2, -2, 2, 2), rad + 2, rad + 2)
        p.fillPath(big, body)
    else:
        # painted glass: the antialiased capsule itself is the silhouette
        p.fillPath(path, body)
    # GLOSS: a thin sheen streak across the top, inset from BOTH rounded ends. Filling the
    # top band of the whole path lit the upper curve of each rounded end — light corners.
    inner_w = max(10.0, r.width() - rad * 2.2)
    # sits 4.5px below the silhouette, not 1.6: on the outline band a light streak is
    # indistinguishable from a frame (measured: it was the last grey on aurora's edge)
    streak = QRectF(r.left() + rad * 1.1, r.top() + MASK_INSET + 4.5, inner_w, r.height() * 0.5)
    sheen = QLinearGradient(streak.left(), 0, streak.right(), 0)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
    # dimmed while halo's rim is lit: white on top of the coloured rim desaturates both
    sheen.setColorAt(0.5, QColor(255, 255, 255, int(46 * max(0.0, 1.0 - 2.6 * rim))))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(QPen(sheen, 1.3))
    p.drawArc(streak, 30 * 16, 120 * 16)
    # halo's lit rim, clipped to the pill so not one pixel of it lands on bare window
    if rim_draw:
        p.save()
        p.setClipPath(path)
        for pen, rp in rim_draw:
            p.setPen(pen)
            p.drawPath(rp)
        p.restore()
    # A single soft sheen across the top only — the inner bevel and the specular arc were a
    # second and third light source on the same 40px edge, which is what made it a ring.
    streak = QRectF(r.left() + 26, r.top() + 2.0, r.width() - 52, r.height() * 0.42)
    p.setPen(QPen(QColor(255, 255, 255, 34), 1.3))
    p.drawArc(streak, 34 * 16, 112 * 16)


# ---------- hover-reveal control button ----------
def _mini_button(parent, glyph, tip, cb) -> QPushButton:
    b = QPushButton(glyph, parent)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(24, 24)
    b.setStyleSheet(
        "QPushButton{background:rgba(255,255,255,14);color:rgba(225,230,240,190);"
        "border:none;border-radius:12px;font-size:13px;}"
        "QPushButton:hover{background:rgba(255,255,255,34);color:white;}")
    b.clicked.connect(cb)
    b.hide()
    return b


class HeldFinal(str):
    """A live utterance's final that arrived while there was nowhere to type, AFTER part of
    it was already on screen. The string value is the whole sentence (what a different box,
    or the clipboard, should receive); `hwnd`/`typed`/`want` let the same box be completed
    from what it already shows instead of receiving the sentence a second time."""

    def __new__(cls, goal, hwnd, typed, want):
        s = super().__new__(cls, goal)
        s.hwnd, s.typed, s.want = hwnd, typed, want
        return s


# ---------- main widget ----------
class WaveFlow(QWidget):
    partial_sig = Signal(str)
    final_sig = Signal(str)
    toggle_sig = Signal()
    commit_sig = Signal()
    error_sig = Signal(str)

    show_sig = Signal()

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.cfg = load_config()
        if args.hotkey:  # session-only capture-key override, not persisted
            self.cfg["hotkey_capture"] = args.hotkey
        self.mic: MicStream | None = None
        # persist the device NAME, not the index — Windows reshuffles indexes
        # between sessions, so a saved index silently becomes a DIFFERENT mic
        # (the "spoke into the Yeti, app listened to the BlackShark" bug)
        from audio import resolve_device_name
        devs, default = clean_input_devices()
        if args.device is not None:
            self.device = args.device
            self.device_name = dict(devs).get(args.device, "")
        else:
            self.device, self.device_name = resolve_device_name(
                self.cfg.get("device_name"))
        self.url = args.url or self.cfg.get("url") or "http://127.0.0.1:8756"
        # Server token (--token / config "token" / env WAVEFLOW_TOKEN). Never logged.
        self.token = (args.token or self.cfg.get("token") or os.environ.get("WAVEFLOW_TOKEN", ""))
        log.info("devices=%s default=%s chosen=%s (%r) url=%s",
                 devs, default, self.device, self.device_name, self.url)
        self.state = "idle"
        self._offline = False
        self._err_shown = False
        self._target_hwnd = 0
        self._lvl_peak = 0.02   # auto-gain reference for the ribbon
        self._caret_rect = None
        self._typed = ""
        self._prev_raw = ""
        self._commit_reason = "hotkey"
        self.typer = LiveTyper(self._typing_ok)
        self._stream_stop = threading.Event()
        # Recorder state lives for the app's whole life, not a session's: the
        # session-start path calls _close_recorder() before anything opens one.
        self._rec = None
        self._rec_path = None
        self._rec_lock = threading.Lock()
        self._session = 0              # increments per start; a close carries the id it owns
        self._start_queued = False     # a press arrived while the last session was closing
        self._pending_bursts = []
        self._pending_lock = threading.Lock()
        self._busy = threading.Lock()
        self._drag = None
        self._hover = False
        self._acrylic = False
        self.last_ms = 0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                            Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # NEVER steal focus from the app you're dictating into — otherwise the caret
        # leaves the target and the paste has nowhere to land (observed: "text appears
        # as ghost but never prints"). The target keeps focus the whole time.
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.resize(430, 84)
        self.setMinimumSize(300, 64)

        self.wave = WaveWidget(self)
        self.ghost = GhostText()

        self.btn_close = _mini_button(self, "✕", "Hide to tray", self.hide_to_tray)
        self.btn_mic = _mini_button(self, "⚙", "Microphone & settings", self._open_menu)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(16, 16)
        self.grip.setStyleSheet("background: transparent;")
        self.grip.hide()
        # persist:False — applying the saved value must not rewrite config on every launch
        self._apply_skin(self.cfg.get("skin", DEFAULT_SKIN), persist=False)

        self.partial_sig.connect(self._on_partial)
        self.final_sig.connect(self._on_final)
        self.toggle_sig.connect(self.toggle_listen)
        self.commit_sig.connect(self.finalize)
        self.show_sig.connect(self.toggle_visible)
        self.error_sig.connect(self._on_error)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

        self._last_partial = ""
        self._listen_t0 = 0.0
        self._worker: threading.Thread | None = None
        self._hk_filter = HotkeyFilter()
        QApplication.instance().installNativeEventFilter(self._hk_filter)

        self._make_tray()
        self._start_hotkey()

    # ---- skins ----
    def _on_skin_action(self, name: str):
        """A Skin menu item was activated. Logged with its source because a skin the user
        picked was observed reverting seconds later (halo -> aurora at session end), and the
        log could not say WHO asked for it."""
        log.info("skin action fired: %s (current=%s)", name, getattr(self, "skin", None))
        if name == getattr(self, "skin", None):
            return                      # already there; do not rewrite config
        self._apply_skin(name)

    def _apply_skin(self, name: str, persist: bool = True):
        """Switch face. Engine untouched — DESIGN-PROFILE rule 0: capability is never traded
        for polish; a skin decides how things are SURFACED, never whether they exist."""
        name = name if name in SKINS else DEFAULT_SKIN
        self.skin = name
        self.wave.skin = name
        sk = SKINS[name]
        self.setMinimumSize(*sk["min"])
        self.resize(*sk["size"])
        self._layout_children()
        # resize() to the SAME size emits no resizeEvent, so re-cut the mask explicitly —
        # otherwise a skin switch between equal sizes would keep the old radius.
        self._apply_pill_mask()
        self.update()
        # Log ALWAYS, not only on a change. "Is the new UI even running?" was unanswerable
        # from the log after a rebuild, which cost a whole round-trip to establish.
        log.info("skin=%s size=%s rim=%s", name, sk["size"], sk["rim"])
        if persist:
            self.cfg["skin"] = name
            save_config(self.cfg)

    # ---- layout ----
    def _layout_children(self):
        w, h = self.width(), self.height()
        sk = SKINS.get(getattr(self, "skin", DEFAULT_SKIN), SKINS[DEFAULT_SKIN])
        # Give the face the room it needs. The first cut stacked a 12px base inset AND a
        # per-skin pad on both edges, which left halo's bars a 20px-tall widget inside a
        # 60px window — so they could never grow past a row of dots no matter what the
        # level was. The numbers below are measured against the pill, not guessed: halo's
        # pill is inset 5px for its glow, so 13px clears the rim and leaves 34px of face.
        top = 11 if sk["rim"] else 10
        self.wave.setGeometry(30, top, w - 60, max(16, h - top * 2))
        self.btn_close.move(w - 30, 6)
        self.btn_mic.move(w - 58, 6)
        self.grip.move(w - 18, h - 18)

    def resizeEvent(self, _):
        self._layout_children()
        self._apply_pill_mask()

    def _apply_pill_mask(self):
        """Cut the WINDOW itself to the pill shape.

        The window is a rectangle and the pill is a rounded capsule, so every pixel of window
        outside the capsule — the corners beyond the rounded ends — is bare acrylic. Windows
        tints and blurs the whole window, not just what Qt paints, so that leftover area
        renders as a light frame around a dark pill. Feedback, 2026-09-13: "it's like you
        planted the visual on top of the window but didn't refine the window viewing." Exactly
        that. Shrinking the inset only shrank the leftover; the shape had to change.

        DWM's own corner rounding cannot do this — it offers ~8px, and a pill needs
        height/2 (~28px). A mask can. The cost is a hard-edged silhouette (no antialiasing on
        the outline); at this radius that is far less visible than the grey frame it removes.
        """
        if not getattr(self, "_acrylic", False):
            return      # painted glass: per-pixel alpha is the shape, and a mask would alias it
        try:
            sk = SKINS.get(getattr(self, "skin", DEFAULT_SKIN), SKINS[DEFAULT_SKIN])
            path = QPainterPath()
            m = MASK_INSET
            # inset, so the mask edge falls INSIDE the painted body rather than on its
            # antialiased edge — the first mask sat exactly on the curve and left light
            # partial pixels in the corners, which is where the frame was still visible
            path.addRoundedRect(QRectF(self.rect()).adjusted(m, m, -m, -m),
                                sk["rad"] - m, sk["rad"] - m)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        except Exception as e:
            log.warning("pill mask failed (%s) — window stays rectangular", e)

    def showEvent(self, _):
        # cached here, on the GUI thread: _typing_ok runs on the receive thread and must not
        # call into Qt to ask for its own window handle
        self._own_hwnd = int(self.winId())
        if not getattr(self, "_glass_setup", False):
            self._glass_setup = True
            # Acrylic OFF by default (config "acrylic": true re-enables it). Measured on the
            # REAL screen, 2026-09-13: Windows draws the acrylic layer over the whole window
            # RECTANGLE and ignores the window mask — a 2px band along every side and a ~9px
            # wedge in each corner, in exactly the accent tint colour (16,20,25). That was the
            # "border shell" through five rounds of paint fixes; nothing painted can cover it.
            # And the approved mock's glass is 92-95% opaque, so the blur it bought was nearly
            # invisible anyway. Without it the window is a per-pixel-alpha layer: the capsule
            # IS the window, with antialiased edges and nothing around it.
            if self.cfg.get("acrylic", False):
                self._acrylic = enable_liquid_glass(self.winId())
                self._apply_pill_mask()
            else:
                self._acrylic = False
                disable_window_frame(self.winId())
                self.clearMask()
            self.update()

    # ---- paint: liquid glass (glossy gray) ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        sk = SKINS.get(getattr(self, "skin", DEFAULT_SKIN), SKINS[DEFAULT_SKIN])
        # The pill must FILL the window. Insetting it 5px to give halo's glow room left a
        # 5px margin of bare window — and Windows applies the acrylic to the WHOLE window,
        # so that margin rendered as a light rounded rect hugging the dark pill. That was
        # the "grey border": not a stroke, not Windows' frame, just the acrylic showing
        # where nothing was painted over it. The glow now blooms INWARD instead.
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_pill(p, r, self._acrylic, sk["rad"],
                   self.wave.rim_energy() if sk["rim"] else 0.0)

        if self._hover:  # grip-dot move handle, left edge
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(220, 226, 238, 120))
            cx = 16
            cy0 = self.height() / 2 - 14
            for row in range(4):
                for col in range(2):
                    p.drawEllipse(QRectF(cx - 5 + col * 7, cy0 + row * 9, 2.6, 2.6))
        p.end()

    # ---- hover reveal ----
    def enterEvent(self, _):
        self._hover = True
        for x in (self.btn_close, self.btn_mic, self.grip):
            x.show()
        self.update()

    def leaveEvent(self, _):
        self._hover = False
        for x in (self.btn_close, self.btn_mic, self.grip):
            x.hide()
        self.update()

    # ---- menus ----
    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        devs, _ = clean_input_devices()
        mic_menu = m.addMenu("Microphone")
        grp = QActionGroup(mic_menu)
        for idx, name in devs:
            a = QAction(name, mic_menu, checkable=True)
            a.setChecked(idx == self.device)
            a.triggered.connect(lambda _, i=idx: self._set_device(i))
            grp.addAction(a)
            mic_menu.addAction(a)
        skin_menu = m.addMenu("Skin")
        sgrp = QActionGroup(skin_menu)
        for key, label in (("aurora", "Aurora — chromatic ribbons"),
                           ("halo", "Halo — bars + glowing rim")):
            a = QAction(label, skin_menu, checkable=True)
            a.setChecked(key == getattr(self, "skin", DEFAULT_SKIN))
            a.triggered.connect(lambda _, k=key: self._on_skin_action(k))
            sgrp.addAction(a)
            skin_menu.addAction(a)
        m.addAction(f"Last STT: {self.last_ms}ms" if self.last_ms else "Last STT: —").setEnabled(False)
        m.addSeparator()
        m.addAction("Settings…", self._open_settings)
        m.addAction("Hide", self.hide_to_tray)
        m.addAction("Quit", QApplication.quit)
        return m

    def _set_device(self, idx):
        self.device = idx
        devs, _ = clean_input_devices()
        self.device_name = dict(devs).get(idx, "")
        self.cfg["device_name"] = self.device_name   # NAME survives index reshuffles
        self.cfg.pop("device", None)
        save_config(self.cfg)
        log.info("device switched to %s (%r)", idx, self.device_name)

    def _open_menu(self):
        # Every _build_menu() made a NEW QMenu parented to this widget, so menus (and their
        # still-connected Skin actions) accumulated for the life of the app — one stale
        # exclusive QActionGroup per open. Destroy it once it closes.
        m = self._build_menu()
        m.setAttribute(Qt.WA_DeleteOnClose, True)
        m.exec(self.btn_mic.mapToGlobal(self.btn_mic.rect().bottomLeft()))

    def contextMenuEvent(self, e):
        self._build_menu().exec(e.globalPos())

    # ---- window move ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, _):
        self.toggle_listen()

    # ---- tray / hotkey ----
    def _icon(self):
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor(44, 46, 52))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(2, 2, 28, 28, 14, 14)
        p.setPen(QPen(CHROMA[0], 2))
        for i, hgt in enumerate((6, 12, 8, 14, 7)):
            p.drawLine(8 + i * 4, 16 - hgt // 2, 8 + i * 4, 16 + hgt // 2)
        p.end()
        return QIcon(pix)

    def _make_tray(self):
        self.tray = QSystemTrayIcon(self._icon(), self)
        self.tray.setToolTip("WaveFlow")
        menu = QMenu()
        menu.addAction("Show / Dictate", self._summon)
        menu.addAction("Settings…", self._open_settings)
        menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._summon() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _start_hotkey(self):
        """SYSTEM-registered hotkeys via Win32 RegisterHotKey (reliable across apps,
        including elevated windows — unlike the keyboard-hook approach it replaces)."""
        u32 = ctypes.windll.user32
        for hid in list(self._hk_filter.callbacks):
            u32.UnregisterHotKey(None, hid)
        self._hk_filter.callbacks.clear()
        results = {}
        # ONE control: ctrl+alt+w summons+listens, pressed again commits+hides.
        # The separate capture hotkey was redundant (removed 2026-07-12).
        for hid, name, combo, sig in ((1, "show", self.cfg["hotkey_show"], self.show_sig),):
            if not combo:
                results[name] = "disabled"
                continue
            parsed = parse_combo(combo)
            if not parsed:
                results[name] = f"unparseable: {combo}"
                continue
            mods, vk = parsed
            if u32.RegisterHotKey(None, hid, mods | 0x4000, vk):  # MOD_NOREPEAT
                self._hk_filter.callbacks[hid] = (
                    lambda n=name, s=sig: (log.info("hotkey FIRED: %s", n), s.emit()))
                results[name] = combo
            else:
                results[name] = f"IN USE by another app: {combo}"
                self.error_sig.emit(f"Hotkey '{combo}' is taken — pick another in ⚙ Settings")
        log.info("hotkeys (RegisterHotKey): %s", results)

    def _capture_target(self):
        """Remember the window that had focus when dictation began, so the text
        lands THERE even if focus drifts during the STT round trip (text once
        went into the Start-search because focus moved while waiting)."""
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd and hwnd != int(self.winId()):
            self._target_hwnd = hwnd

    def toggle_visible(self):
        """ctrl+alt+w — ONE consistent model: idle -> summon+listen;
        listening -> commit+hide. (The old visible-but-idle state made the
        first press HIDE the widget — the press-count desync users hit.)"""
        if self.state == "listening":
            self._commit_reason = "hotkey"
            self.finalize()
            QTimer.singleShot(900, self.hide_to_tray_if_idle)
        else:
            self._capture_target()   # BEFORE we show (we never steal focus)
            self.show()
            self.raise_()
            self.start_listen()

    def hide_to_tray_if_idle(self):
        # not while a start is queued: the old session's close lands in between and would
        # hide a pill that is about to start listening — live but invisible
        if self.state == "idle" and not self._start_queued:
            self.hide()
            self.ghost.hide()

    # ---- settings dialog (⚙ → Settings…) ----
    def _open_settings(self):
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                                       QKeySequenceEdit, QLabel as QL)
        from PySide6.QtGui import QKeySequence

        def to_kb(seq: str) -> str:
            # Qt "Ctrl+Alt+Space" -> keyboard-lib "ctrl+alt+space"
            return seq.replace("Meta", "windows").lower().replace(" ", "")

        dlg = QDialog(self)
        dlg.setWindowTitle("WaveFlow settings")
        dlg.setStyleSheet(
            "QDialog{background:#26282e;} QLabel{color:#cdd4e0;font-size:12px;}"
            "QKeySequenceEdit{background:#32353d;color:#e8ecf4;border:1px solid #454a56;"
            "border-radius:6px;padding:4px;}")
        from PySide6.QtWidgets import QHBoxLayout, QPushButton as QP, QWidget as QW
        form = QFormLayout(dlg)
        show_edit = QKeySequenceEdit(QKeySequence(self.cfg["hotkey_show"] or ""))

        def row(edit):
            box = QW()
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(edit, 1)
            clr = QP("Clear")
            clr.setFixedWidth(52)
            clr.setStyleSheet("QPushButton{background:#3a3e48;color:#cdd4e0;border:none;"
                              "border-radius:6px;padding:4px;font-size:11px;}"
                              "QPushButton:hover{background:#454a56;}")
            clr.clicked.connect(edit.clear)
            lay.addWidget(clr)
            return box

        form.addRow("Summon / dictate hotkey", row(show_edit))
        hint = QL("Press once to open and start listening; press again to\n"
                  "type what you said and hide. Double-click the pill also works.")
        hint.setStyleSheet("color:#8a93a3;font-size:11px;")
        form.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            self.cfg["hotkey_show"] = to_kb(show_edit.keySequence().toString()) or "ctrl+alt+w"
            save_config(self.cfg)
            self._start_hotkey()

    # ---- dictation ----
    def _summon(self):
        self._capture_target()
        self.show()
        self.raise_()
        if self.state == "idle":
            self.toggle_listen()

    def hide_to_tray(self):
        if self.state == "listening":
            self.finalize()
        self.hide()
        self.ghost.hide()

    def toggle_listen(self):
        if self.state == "idle":
            if not self.isVisible():
                self.show()
                self.raise_()
            self.start_listen()
        elif self.state == "listening":
            self.finalize()

    def _server_up(self) -> bool:
        import requests
        try:
            requests.get(f"{self.url}/health", timeout=2).raise_for_status()
            return True
        except Exception:
            return False

    def start_listen(self, _tries: int = 0):
        # A press while the previous session is still closing used to either race it (and get
        # its mic stopped from under it) or be IGNORED ("worker still alive"), so the user
        # had to press again. Never stack workers — but WAIT and start, don't drop the press.
        if self.state == "finalizing" or (self._worker and self._worker.is_alive()):
            if _tries < 50:                      # ~6s at 120ms
                if _tries == 0:
                    log.info("previous session still closing — start queued")
                self._start_queued = True
                QTimer.singleShot(120, lambda: self.start_listen(_tries + 1))
            else:
                self._start_queued = False
                log.warning("previous session did not close in 6s — start dropped")
            return
        self._start_queued = False
        if self.state == "listening":
            return
        if not self.isVisible():                 # the old close may have hidden it meanwhile
            self.show()
            self.raise_()
        if not self._server_up():  # fail BEFORE opening the mic — one calm message
            self._offline = True
            log.warning("STT backend offline at %s", self.url)
            if not self._err_shown:  # show the balloon ONCE, not every attempt
                self.error_sig.emit(f"STT backend offline. Start it, or set the URL in ⚙ Settings.\n({self.url})")
                self._err_shown = True
            return
        self._offline = False
        self._err_shown = False
        # capture the caret NOW (target still has focus — widget is non-activating);
        # UIA gives it in native/browser/Electron, Win32 caret as a cheap fallback.
        # A REAL caret is a thin tall rect — a "caret" the height of the whole
        # field is the fallback, and drawing ghost text there is the
        # "prints to a random place on screen" bug. No real caret -> NO ghost.
        self._caret_rect = caret_rect_uia()
        if self._caret_rect and self._caret_rect[2] > 90:
            log.info("caret fallback rejected (h=%s) — ghost disabled this utterance",
                     self._caret_rect[2])
            self._caret_rect = None
        if not self._caret_rect:
            w32 = caret_screen_pos()
            self._caret_rect = (w32[0], w32[1] - 16, 20) if w32 else None
        log.info("caret at %s", self._caret_rect)
        self._session += 1
        self.state = "listening"
        self.wave.active = True
        self._stream_stop.clear()
        self._last_partial = ""
        self._typed = ""
        self._prev_raw = ""
        self._typed_any = False        # have we typed a phrase yet this session?
        self._pending_bursts = []      # committed words waiting for the target to refocus
        self._pending_lock = threading.Lock()
        self._live_typed = ""          # exactly what mode=live has typed for THIS utterance
        self._live_prefix = ""         # its leading separator, fixed for the utterance
        self._close_recorder()         # a previous session's WAV must not absorb this one
        self.typer.reset()
        self._listen_t0 = time.time()
        try:
            if getattr(self.args, "replay", ""):
                self.mic = ReplayMic(self.args.replay)
                self._auto_finalize_when_done()
            else:
                self.mic = MicStream(device=self.device)
            self.mic.start()
            log.info("mic OPEN device=%s sr=%s ch=%s", self.device, self.mic.sr, self.mic.ch)
        except Exception as e:
            log.error("mic open FAILED device=%s: %s", self.device, e)
            self.error_sig.emit(f"Mic failed ({e.__class__.__name__}) — right-click ⚙ to pick another device")
            self.state = "idle"
            self.wave.active = False
            return
        self._streaming_live = False
        self._worker = threading.Thread(target=self._stream_ws_worker, daemon=True)
        self._worker.start()

    def _stream_ws_worker(self):
        """TRUE live word-by-word: stream mic PCM to the cache-aware streaming
        backend; cumulative STABLE hypotheses feed the tail-correcting typer.
        If the streaming endpoint is unreachable, fall back to segment-commit."""
        import json as _json
        try:
            import websocket
            lat = int(self.cfg.get("stream_latency", 16))   # [70,16] = 480ms, live+accurate
            # mode=live: the server stops cutting mid-word. It reports `stable` (committed,
            # append-only, never rewritten) and `tail` (still revisable), instead of one
            # already-committed `delta`. That is what ends "an A"+"aI brain". Opt-out with
            # config "live_mode": false to fall back to the burst protocol.
            self._live_mode = bool(self.cfg.get("live_mode", True))
            wsurl = self.url.replace("http", "ws", 1) + f"/v1/audio/stream?latency={lat}"
            if self._live_mode:
                wsurl += "&mode=live"
            ws = websocket.create_connection(
                wsurl, timeout=4, header=[f"{k}: {v}" for k, v in auth_headers(self.token).items()])
            # create_connection's timeout becomes the socket's READ timeout too, and
            # 4s is shorter than a normal pause in real speech. recv() then raised
            # WebSocketTimeoutException the moment the user stopped for breath,
            # the rx thread returned, and the watchdog tore the link down and
            # reconnected — losing every in-flight word. That is the whole
            # "text stops part-way / the end gets lost" class on long dictation.
            # Connect stays fast; reads get a budget longer than the idle limit that
            # ends the session anyway, so a read timeout is now only ever real.
            ws.settimeout(max(30.0, float(self.cfg.get("silence_commit_s",
                                                       SILENCE_COMMIT_S))) + 15.0)
        except Exception as e:
            log.info("streaming unavailable (%s) — segment-commit fallback", e)
            self._stream_worker()
            return
        self._streaming_live = True
        log.info("STREAMING mode: %s (read timeout %.0fs)", wsurl, ws.gettimeout() or 0)

        self._rx_alive = True
        self._last_burst_t = time.time()

        def rx():
            """APPEND-ONLY: type each burst's `delta` once, at the caret. Never
            diff, never backspace, never hold a model of the box's contents.

            The goal-diffing typer this replaces assumed it OWNED the text box:
            it tracked what it had typed and backspaced to reconcile. The instant
            the user edited by hand, that model desynced from reality and the
            app fought the edit ("as soon as I make an edit I have to restart").
            Append-only deletes the whole class — a burst the server already
            committed is never revisited, so nothing the user types can be clobbered."""
            while not self._stream_stop.is_set():
                try:
                    raw = ws.recv()
                    if not raw:
                        # Empty frame = the socket was closed, normally by our own session
                        # end. Parsing it raised JSONDecodeError and logged "stream rx died
                        # ... link is one-way" on every ordinary close — a false alarm that
                        # reads exactly like the real failure it was written to catch.
                        return
                    m = _json.loads(raw)
                except websocket.WebSocketTimeoutException:
                    # A quiet stretch is NOT a dead link. Belt-and-braces with the
                    # long read timeout above: even if one fires, keep listening —
                    # the watchdog in the send loop is what decides the link is dead,
                    # and it only does so while real speech is going out unanswered.
                    continue
                except Exception as e:
                    # NEVER die silently. This receiver vanishing while the send
                    # loop kept streaming is exactly why the widget sat there with
                    # the wave moving and no text: mic fine, audio going out, but
                    # nobody reading the replies. Flag it so the worker recovers.
                    if not self._stream_stop.is_set():
                        log.error("stream rx died (%s) — link is one-way, recovering",
                                  e.__class__.__name__)
                    self._rx_alive = False
                    return
                self._last_burst_t = time.time()
                if m.get("reset"):
                    self._typed_any = False
                    self._live_typed = ""
                    continue
                if "stable" in m:
                    self._on_live(m)
                    continue
                delta = strip_fillers(m.get("delta", "")).strip()
                if not delta:
                    continue
                if getattr(self.args, "replay", "") or self.args.demo:
                    log.info("REPLAY would-append-burst: %r", delta)
                    continue
                # HOLD, never drop. This used to `continue`, which DELETED the burst:
                # one notification stealing focus for a moment lost a whole sentence
                # with nothing on screen to say so. The words are already committed
                # server-side and can never be re-derived, so park them and type them
                # when the target comes back. Order is preserved because only this
                # thread appends and only _flush_pending() drains.
                self._pending_bursts.append(delta)
                if not self._flush_pending():
                    log.info("burst HELD %d queued (target not focused): %r",
                             len(self._pending_bursts), delta)
        threading.Thread(target=rx, daemon=True).start()

        last_speech_t = time.time()
        spoke_yet = False
        idle_limit = float(self.cfg.get("silence_commit_s", SILENCE_COMMIT_S))
        # DEBUG RECORDER (--record <dir>). Opened once per SESSION, not per socket:
        # this worker re-enters itself on reconnect, so opening it inside the loop
        # would start a new file mid-sentence and split the evidence in two.
        self._open_recorder()
        try:
            while not self._stream_stop.is_set():
                time.sleep(0.1)
                # Drain any burst held during a focus blip. Doing it here (not only
                # on the next burst) is what stops a queue filling at the END of a
                # sentence from never being typed at all.
                self._flush_pending()
                if not self.mic:
                    continue
                pcm = self.mic.drain_pcm16k()
                if pcm:
                    ws.send_binary(pcm)
                    # Tee EXACTLY the bytes the server saw, so a replay of this
                    # file reproduces the session's transcription bug-for-bug.
                    self._write_recorder(pcm)
                speaking = self.mic.rms() >= self.mic.speech_thresh()
                now = time.time()
                if speaking:
                    last_speech_t = now
                    spoke_yet = True
                self.wave.paused = not speaking
                # WATCHDOG: the link can rot without either side raising —
                # send/recv race on one websocket-client socket, a half-open TCP
                # connection, a server that stopped replying. The symptom was a
                # live-looking widget that never typed. If the receiver is gone,
                # or we've been streaming real speech for a while with nothing
                # coming back, treat the link as dead and RECONNECT.
                stale = (not self._rx_alive) or (
                    speaking and now - self._last_burst_t > 20.0)
                if stale and not self._stream_stop.is_set():
                    log.error("stream link stale (rx_alive=%s, %.0fs since a burst) "
                              "— reconnecting", self._rx_alive, now - self._last_burst_t)
                    raise ConnectionError("link stale")
                limit = idle_limit if spoke_yet else max(idle_limit, PRESPEECH_GRACE_S)
                if now - last_speech_t > limit:
                    log.info("idle %.1fs (limit %.0fs, spoke=%s) — ending session",
                             now - last_speech_t, limit, spoke_yet)
                    self._commit_reason = "idle"
                    self.commit_sig.emit()
                    break
        except Exception as e:
            # A dead link must never masquerade as a live one. RECONNECT first —
            # a user is mid-sentence and a dropped socket shouldn't cost them
            # the session (previously this returned and left the widget looking
            # alive with the wave moving and nothing typing).
            log.error("stream link down (%s) — reconnecting", e)
            self._streaming_live = False
            try:
                ws.close()
            except Exception:
                pass
            if self._stream_stop.is_set():
                return
            for attempt in (1, 2, 3):
                time.sleep(0.4 * attempt)
                if self._stream_stop.is_set():
                    return
                try:
                    self._stream_ws_worker()   # fresh socket, fresh rx thread
                    return
                except Exception as e2:
                    log.error("reconnect attempt %d failed: %s", attempt, e2)
            # Reconnect exhausted: keep CAPTURING via the batch path rather than
            # silently doing nothing.
            log.error("reconnect exhausted — falling back to segment-commit")
            self.error_sig.emit("Streaming dropped — switched to burst capture.")
            if not self._stream_stop.is_set():
                self._stream_worker()
            return
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _stable(self, new: str) -> str:
        """Damp hypothesis bouncing: accept text that extends the previous partial;
        otherwise fall back to the words both agree on. Streaming-native STT on
        the GPU server replaces this heuristic with stable partials from the model."""
        old = self._last_partial
        ow, nw = old.split(), new.split()
        common = 0
        for a, b in zip(ow, nw):
            if a.lower().strip(".,!?") == b.lower().strip(".,!?"):
                common += 1
            else:
                break
        if len(nw) >= len(ow) and common >= max(0, len(ow) - 2):
            self._last_partial = new          # accepted -> new comparison base
            return new
        # rejected bounce: keep the ACCEPTED text as base so the next real
        # extension of it is recognized (storing the bounce broke that)
        return " ".join(nw[:common]) + " …" if common else old

    def _typing_ok(self) -> bool:
        """Safe to emit keystrokes right now? Never in test modes, never into a
        window other than the dictation target."""
        return bool(self._typing_target())

    def _typing_target(self) -> int:
        """The window dictation should type into RIGHT NOW, or 0 to hold.

        FOLLOW FOCUS, like Wispr Flow and other dictation apps: text goes where the cursor is when the words
        arrive. The old rule locked onto whichever window had focus at the moment of the
        hotkey press. Measured on 2026-09-13's logs: every session whose hotkey fired while a
        different field had focus (caret at 2309,1259) HELD every sentence even after
        clicking into the chat box — "it's not typing in the chat box" — and every
        session started from the chat box typed fine. The lock was written for the old
        record-then-send mode, where focus drifting DURING a multi-second round trip put text
        in the Start search; live text arrives continuously, so the current field is right.

        Our OWN windows (the pill, its menu, the ghost chip) are never a target. If the pill
        itself took focus because the user clicked it, hand focus back to the last real window —
        but not mid-drag (left button down), which would fight the move. A menu or dialog
        of ours being open means the user is busy with us: hold.
        """
        if getattr(self.args, "replay", "") or self.args.demo:
            return 0
        u = ctypes.windll.user32
        fg = u.GetForegroundWindow()
        if not fg:
            return 0
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value != ctypes.windll.kernel32.GetCurrentProcessId():
            self._target_hwnd = fg          # remembered as the last REAL window the user used
            return fg
        if (fg == getattr(self, "_own_hwnd", 0) and self._target_hwnd
                and not (u.GetAsyncKeyState(0x01) & 0x8000)):
            ok = focus_window(self._target_hwnd)
            log.info("focus reclaimed from pill -> %r: %s",
                     window_title(self._target_hwnd), ok)
            return self._target_hwnd if ok else 0
        return 0

    def _open_recorder(self):
        """Open this session's debug WAV if --record <dir> was given, else None.

        Writes 16k mono PCM16 — the exact wire format streamed to the server — so
        `--replay <that file>` reproduces the session against any candidate fix.
        Idempotent: returns the already-open writer on a reconnect.
        """
        # Also honour config.json's "record", so the FROZEN exe can be put into
        # debug mode without changing how the user launches it.
        target = getattr(self.args, "record", "") or self.cfg.get("record", "")
        if not target:
            return None
        with self._rec_lock:
            if self._rec is not None:
                return self._rec
            try:
                d = Path(target)
                d.mkdir(parents=True, exist_ok=True)
                p = d / f"session-{time.strftime('%Y%m%d-%H%M%S')}.wav"
                w = wave.open(str(p), "wb")
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                self._rec = w
                self._rec_path = p
                w._wf_path = p        # the path travels WITH the writer a close was handed
                log.info("RECORDING session audio -> %s", p)
                return w
            except Exception as e:
                log.error("recorder open failed (%s) — continuing without it", e)
                self._rec = None
                return None

    def _write_recorder(self, pcm: bytes):
        """Append to the debug WAV, if one is open.

        MUST hold the lock and re-read self._rec. The first cut cached the writer in
        a local and wrote to it unguarded: finalize() closes the recorder from its own
        thread, so the send loop's next 0.1s tick hit a closed wave object
        ("'NoneType' object has no attribute 'write'"), which the loop's own handler
        then mistook for a dead link and "reconnected". A debug aid must never be able
        to fault the capture path.
        """
        with self._rec_lock:
            w = self._rec
            if w is None:
                return
            try:
                w.writeframes(pcm)
            except Exception as e:
                log.error("recorder write failed (%s) — recording off, capture continues", e)
                self._rec = None

    def _close_recorder(self, which=None):
        """Close the current session's WAV — or, given `which`, exactly that writer (a closing
        session's snapshot), leaving a newer session's recorder alone."""
        with self._rec_lock:
            w = self._rec if which is None else which
            if w is None:
                return
            if self._rec is w:
                self._rec = None
        try:
            w.close()
            log.info("RECORDING saved -> %s", getattr(w, "_wf_path", "?"))
        except Exception as e:
            log.error("recorder close failed: %s", e)

    def _on_live(self, m: dict):
        """mode=live: draw this utterance's text, correcting ONLY its unsettled tail.

        The server guarantees `stable` is append-only and never rewritten, so the only text
        this may ever backspace over is the tail it typed itself moments ago — at most
        LIVE_TAIL_MAX characters, all of it inside the current utterance. That bound is the
        whole reason the July live typer's failure mode cannot come back: it held a model of
        the WHOLE textbox and reconciled against it, so the moment the user typed by hand
        it fought them ("as soon as I make an edit I have to restart"). Here, a finalised
        utterance is released for good and never touched again.
        """
        stable = strip_fillers(m.get("stable", ""))
        tail = strip_fillers(m.get("tail", ""))
        final = bool(m.get("final"))
        goal = " ".join(x for x in (stable.strip(), tail.strip()) if x)

        if getattr(self.args, "replay", "") or self.args.demo:
            if final:
                log.info("REPLAY would-finalise(utt %s): %r", m.get("utt"), goal)
            return

        target = self._typing_target()
        if not target:
            # Nowhere safe to type (our own menu is open, or no window at all). Partials are
            # disposable; the FINAL is the durable unit, so only that gets queued for later.
            #
            # A held PARTIAL must NOT forget what is already typed. It used to reset
            # _live_typed here, but the words stay on screen — so when the menu closed the
            # typer believed the box was empty and typed the sentence again. Observed
            # 2026-09-13 18:52:57: gear -> Skin clicked mid-sentence, and "And also this is
            # looking much better." appeared twice. Keep the record; the next pass diffs
            # against it (same box) or goes append-only (different box).
            if not final:
                return
            if goal:
                if self._live_typed:
                    item = HeldFinal(goal, getattr(self, "_live_hwnd", 0), self._live_typed,
                                     self._live_prefix + goal)
                else:
                    item = goal
                self._pending_bursts.append(item)
                if not self._flush_pending():
                    # FULL text, not goal[-40:]. Cropping to the last 40 chars made complete
                    # sentences look like lost first words ("astly, ..." for "Lastly, ...")
                    # and sent a whole investigation after a bug that did not exist.
                    log.info("utterance HELD %d queued (no typing target): %r",
                             len(self._pending_bursts), goal)
            self._live_typed = ""
            return

        if self._pending_bursts:
            # Held finals go out BEFORE new words: they come first in speech, and a HeldFinal
            # may backspace its own tail, which is only safe while it is still the last thing
            # typed in that box.
            self._flush_pending()

        if not self._live_typed:
            # Decide the separator ONCE per utterance. _typed_any only flips on a final, so
            # the prefix cannot change mid-utterance and shift every later character.
            self._live_prefix = " " if self._typed_any else ""
            self._live_hwnd = target
            self._live_append_only = False
        elif target != getattr(self, "_live_hwnd", target):
            # The user moved to another window MID-utterance. The redraw may backspace, and a
            # backspace here would land in the NEW window and delete the user's real text. From now
            # until this utterance ends: append only, continuing from what was already typed.
            log.info("window changed mid-utterance -> %r (append-only)", window_title(target))
            self._live_hwnd = target
            self._live_append_only = True
            # Restart the new window at the last WORD boundary already typed, so it receives
            # "friend." rather than the fragment "nd." left over from "frie" in the old one.
            self._live_append_from = self._live_typed.rfind(" ") + 1
            self._live_new_sent = 0
        want = self._live_prefix + goal

        cur = self._live_typed
        if getattr(self, "_live_append_only", False):
            seg = want[self._live_append_from:]
            if len(seg) > self._live_new_sent:
                send_text(seg[self._live_new_sent:])
                self._live_new_sent = len(seg)
                self._live_typed = want
            if final:
                if goal:
                    self._typed_any = True
                self._live_typed = ""
                log.info("utterance %s finalised (append-only), %d chars: %r",
                         m.get("utt"), len(goal), goal)
            return
        n = 0
        while n < len(cur) and n < len(want) and cur[n] == want[n]:
            n += 1
        back = len(cur) - n
        if back > 0:
            if back > 240:      # sanity rail: a redraw this size means we lost sync
                log.error("live redraw of %d chars refused — dropping to append-only", back)
                self._live_typed = want
                return
            send_backspaces(back)
        if want[n:]:
            send_text(want[n:])
        self._live_typed = want

        if final:
            # Released. The next utterance starts a fresh append; nothing here is revisited,
            # so anything the user edits by hand afterwards is safe from us.
            if goal:
                self._typed_any = True
            self._live_typed = ""
            log.info("utterance %s finalised -> %r, %d chars: %r",
                     m.get("utt"), window_title(target), len(goal), goal)

    def _flush_pending(self, pending=None, lock=None) -> bool:
        """Type every held burst, oldest first. Returns True if the queue is empty
        afterwards (i.e. nothing is still owed).

        Called both from the receive thread (so a burst arriving while focused is
        typed immediately) and from the send loop's tick (so a queue that filled
        during a focus blip still drains even if no further burst ever arrives —
        the case that would otherwise silently swallow the end of a sentence).
        """
        # `pending`/`lock` let a closing session drain ITS OWN queue (a snapshot) rather than
        # whatever list self._pending_bursts points at once a newer session has started.
        pending = self._pending_bursts if pending is None else pending
        lock = self._pending_lock if lock is None else lock
        if not pending:
            return True
        target = self._typing_target()
        if not target:
            return False
        # Two threads drain this, so the pop and the keystrokes must be atomic
        # together — otherwise both could take a burst and type them out of order.
        with lock:
            while pending:
                delta = pending.pop(0)
                if isinstance(delta, HeldFinal) and target == delta.hwnd:
                    # Same box that already shows the start: fix its unsettled tail and
                    # finish the sentence, rather than typing the whole thing again.
                    n = 0
                    while (n < len(delta.typed) and n < len(delta.want)
                           and delta.typed[n] == delta.want[n]):
                        n += 1
                    if len(delta.typed) - n:
                        send_backspaces(len(delta.typed) - n)
                    send_text(delta.want[n:])
                else:
                    send_text((" " if self._typed_any else "") + delta)
                self._typed_any = True
                log.info("append-burst %d chars: %r", len(delta), delta)
        return True

    def _confirmed_words(self, new: str) -> str:
        """Only words that appear in TWO consecutive partials get typed — a
        one-off hallucinated word (mic-open pop, hotkey clack) never reaches
        the box, and the text flows forward instead of churning."""
        prev, self._prev_raw = self._prev_raw, new
        pw, nw = prev.split(), new.split()
        out = []
        for a, b in zip(pw, nw):
            if a.lower().strip(".,!?;:") == b.lower().strip(".,!?;:"):
                out.append(b)
            else:
                break
        return " ".join(out)

    def _on_partial(self, text: str):
        if self.state != "listening":
            return
        # don't type ANYTHING until real speech exists (kills the phantom word
        # from the mic-open pop / hotkey noise on app open)
        if self.mic and self.mic.speech_seconds() < 0.3:
            return
        confirmed = strip_fillers(self._confirmed_words(text))
        goal = self._stitch(self.typer.goal or self.typer.typed, confirmed) if confirmed else None
        if getattr(self.args, "replay", "") or self.args.demo:
            if goal:
                log.info("REPLAY would-live-type(stitched): %r", goal)
            return
        if goal:
            self.typer.set_goal(goal)

    def _stitch(self, typed: str, hyp: str) -> str:
        """Append-only overlap stitch. `hyp` is a transcription of a ROLLING
        recent window (bounded so partial latency stays flat no matter how long
        the user talks — the fix for 'freezes when I talk a long time').
        Because the window slides past already-typed words, we can't compare
        from char 0 (that stall was the earlier freeze); instead we find the
        longest suffix of typed words that matches a prefix of the hypothesis
        and append only what's genuinely new. Settled words are never rewritten."""
        if not typed:
            return hyp
        if not hyp:
            return typed
        norm = lambda w: w.lower().strip(".,!?;:")
        tw, hw = typed.split(), hyp.split()
        maxk = min(len(tw), len(hw))
        for k in range(maxk, 0, -1):
            if [norm(x) for x in tw[-k:]] == [norm(x) for x in hw[:k]]:
                rest = hw[k:]
                return typed + (" " + " ".join(rest) if rest else "")
        # no overlap: window has fully advanced past typed -> append as continuation
        return typed + " " + hyp

    def _settled_goal(self, new: str, tail: int = 18):
        """Typed words are SETTLED: live updates only append; a change further
        back than `tail` chars never rewrites the sentence under the user's
        eyes (the complaint it fixes: 'backspaces and rewrites the entire sentence')."""
        cur = self.typer.goal or self.typer.typed
        if not cur:
            return new
        common = 0
        for a, b in zip(cur, new):
            if a == b:
                common += 1
            else:
                break
        if len(cur) - common <= tail:
            return new                     # divergence only in the tail -> allow
        if len(new) > len(cur) and len(new.split()) > len(cur.split()):
            # early divergence but more words -> append the new words only
            return cur + " " + " ".join(new.split()[len(cur.split()):])
        return None                        # early rewrite attempt -> ignore

    def _stream_worker(self):
        """Segment-commit dictation: watch for a natural PAUSE, then transcribe
        the phrase just spoken and type it ONCE, and keep listening. Every word
        is typed exactly once from one clean transcription — no overlapping
        re-transcription, so no loop/jitter (batch STT can't stream stably), and
        the buffer stays phrase-sized so latency never climbs (no freeze)."""
        last_speech_t = time.time()
        seg_has_speech = False
        idle_limit = float(self.cfg.get("silence_commit_s", SILENCE_COMMIT_S))
        pause = float(self.cfg.get("phrase_pause_s", PHRASE_PAUSE_S))
        while not self._stream_stop.is_set():
            time.sleep(0.12)
            if self._stream_stop.is_set() or not self.mic:
                continue
            speaking = self.mic.rms() >= self.mic.speech_thresh()
            now = time.time()
            if speaking:
                last_speech_t = now
                seg_has_speech = True
            self.wave.paused = not speaking
            quiet = now - last_speech_t
            if seg_has_speech and quiet > pause:
                # natural pause -> commit+type this phrase, then keep listening
                self._commit_segment("phrase")
                seg_has_speech = False
                last_speech_t = time.time()   # re-anchor after the transcribe
            elif not seg_has_speech and quiet > idle_limit:
                # nothing pending + long quiet -> end the session (hide to tray)
                log.info("idle %.1fs — ending session", quiet)
                self._commit_reason = "idle"
                self.commit_sig.emit()
                return

    def _commit_segment(self, reason: str):
        """Transcribe the audio captured since the last commit and TYPE it once.
        Runs on the worker thread; typing is ctypes SendInput (no Qt), so it's
        thread-safe. Replay/demo NEVER type into the user's workspace."""
        if not self.mic:
            return
        wav, n = self.mic.take_segment_wav16k()
        if n < int(self.mic.sr * 0.25):        # < 0.25s of audio -> nothing said
            return
        if not self._busy.acquire(timeout=10):
            return
        try:
            t0 = time.perf_counter()
            text, _ = transcribe(self.url, wav, beam=1, token=self.token)
            self.last_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            log.error("segment stt (%s): %s", reason, e)
            return
        finally:
            self._busy.release()
        text = strip_fillers(text).strip()
        if not text:
            return
        if getattr(self.args, "replay", "") or self.args.demo:
            log.info("REPLAY would-type-segment(%s): %r", reason, text)
            return
        u = ctypes.windll.user32
        if self._target_hwnd and u.GetForegroundWindow() != self._target_hwnd:
            focus_window(self._target_hwnd)
        if self._target_hwnd and u.GetForegroundWindow() != self._target_hwnd:
            log.info("segment(%s): target not focused — skipped %r", reason, text)
            return
        send_text((" " if self._typed_any else "") + text)
        self._typed_any = True
        self.tray.setToolTip(f"WaveFlow — last STT {self.last_ms}ms")
        log.info("segment(%s) typed %d chars in %dms -> %r",
                 reason, len(text), self.last_ms, text)

    def finalize(self):
        """Hotkey (or idle) while listening: commit whatever phrase is pending,
        then end the session and hide. Phrases already committed on their pauses."""
        if self.state != "listening":
            return
        self.state = "finalizing"
        self.wave.active = False
        self._stream_stop.set()
        self.ghost.hide()
        # SNAPSHOT this session. The close runs in the background for up to ~4s, and a
        # second hotkey press used to start a new session DURING it — which reset the shared
        # held-words list (wiping them) and replaced self.mic, so the old close then stopped
        # the NEW mic and flipped state to idle under a live session. Observed 2026-09-13:
        # "sometimes it doesn't pick up my voice, other times I have to hit the hotkey again".
        snap = dict(sid=self._session, mic=self.mic, rec=self._rec,
                    pending=self._pending_bursts, lock=self._pending_lock,
                    live=getattr(self, "_streaming_live", False))
        threading.Thread(target=self._finalize_job, args=(snap,), daemon=True).start()

    def _finalize_job(self, snap):
        pending, lock = snap["pending"], snap["lock"]
        if snap["live"]:
            # streaming already typed everything live; a batch commit here would
            # DOUBLE-TYPE the utterance. Just let the typer finish converging.
            time.sleep(0.6)
            # Last chance to type anything held during a focus blip. Without this
            # the FINAL burst of a session — the one most likely to arrive while
            # the user is already clicking away — was lost for good.
            for _ in range(12):
                if self._flush_pending(pending, lock):
                    break
                time.sleep(0.25)
            # Still undeliverable at the end (the user switched apps, or no text box had focus):
            # the words go on the CLIPBOARD with a tray note, never into the void. Observed
            # 2026-09-13: "Yeah, this is looking better." and "Lastly, the border shell..."
            # were both heard perfectly, held, and silently discarded when the session closed.
            with lock:
                left = " ".join(pending).strip()
                pending.clear()
            if left:
                try:
                    import pyperclip
                    pyperclip.copy(left)
                    log.info("undelivered %d chars -> clipboard: %r", len(left), left)
                    self.error_sig.emit("Couldn't type it — your words are on the clipboard "
                                        "(Ctrl+V to paste).")
                except Exception as e:
                    log.error("undelivered text lost (clipboard failed: %s): %r", e, left)
        else:
            try:
                self._commit_segment("final")   # type any trailing phrase, once
            except Exception as e:
                log.error("finalize commit: %s", e)
        # Close the WAV only now: the send loop needs a tick or two to notice
        # _stream_stop, and its last writes are the end of the sentence. Only THIS session's
        # writer and mic — never whatever self._rec / self.mic point at by now.
        self._close_recorder(snap["rec"])
        if snap["mic"]:
            snap["mic"].stop()
        self.final_sig.emit(str(snap["sid"]))

    def _on_final(self, sid: str):
        # A close that finishes after a newer session has started must not mark the app idle
        # or hide it — that newer session is live.
        if sid and sid.isdigit() and int(sid) != self._session:
            log.info("stale close of session %s ignored (current %s)", sid, self._session)
            return
        self.ghost.hide()
        self.state = "idle"
        self._typed_any = False
        if getattr(self.args, "replay", ""):
            QTimer.singleShot(400, QApplication.quit)
        else:
            self.hide_to_tray_if_idle()

    def _auto_finalize_when_done(self):
        """Replay mode: when the WAV finishes feeding, stop as a real user would."""
        def watch():
            self.mic.finished.wait()
            time.sleep(0.3)
            log.info("replay: audio finished feeding -> auto-finalize")
            self.toggle_sig.emit()
        threading.Thread(target=watch, daemon=True).start()

    def _on_error(self, msg: str):
        self.tray.showMessage("WaveFlow", msg, QSystemTrayIcon.Warning, 4000)

    def _tick(self):
        if self.state == "listening" and self.mic:
            self.wave.set_spectrum(self._spectrum())
        else:
            self.wave.set_spectrum(np.zeros(WAVE_BANDS))
        self.wave.update()
        self.update()

    def _spectrum(self):
        """Last ~85ms of mic audio -> WAVE_BANDS log-spaced band magnitudes 0..1."""
        try:
            x = self.mic.recent_samples(int(self.mic.sr * 0.085))
            if len(x) < 256:
                return np.zeros(WAVE_BANDS)
            spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
            freqs = np.fft.rfftfreq(len(x), 1.0 / self.mic.sr)
            edges = np.geomspace(85, 4000, WAVE_BANDS + 1)   # speech range
            mags = np.zeros(WAVE_BANDS, dtype=np.float32)
            for k in range(WAVE_BANDS):
                m = (freqs >= edges[k]) & (freqs < edges[k + 1])
                if m.any():
                    mags[k] = spec[m].mean()
            # SHAPE from the relative spectrum (which bands dominate), HEIGHT
            # from loudness — so the wave warps strongly while speaking and
            # sits near-flat in silence.
            # AUTO-GAIN, not a magic multiplier. `rms * 14.0` was the fourth
            # hardcoded constant in this pipeline to assume a signal level and
            # meet a real one: a user's mic speech rms can be ~0.018, so
            # 0.018*14 = 0.25 capped the ribbon at a quarter energy FOREVER — a
            # permanent hairline no matter how loud the user spoke. Track the user's own
            # recent peak instead and scale against it, so the ribbon fills for
            # their voice, their mic, their gain, whatever they are.
            rms = float(np.sqrt(np.mean(x ** 2)))
            # decay 0.985/tick (~1.5s half-life at 33ms), was 0.995 (~4.6s). With the slow decay
            # one loud word set the reference so high that the next several seconds of ordinary
            # speech read as a fraction of it and the face looked unresponsive.
            self._lvl_peak = max(self._lvl_peak * 0.985,
                                 rms,
                                 0.004)                    # floor: never amplify silence
            loud = float(np.clip(rms / self._lvl_peak, 0.0, 1.0))
            if mags.max() > 1e-6:
                mags = mags / mags.max()
            return np.clip(mags * loud, 0, 1)
        except Exception:
            return np.zeros(WAVE_BANDS)

    # ---- demo/CI ----
    def run_demo(self, wav_path: Path, shot_exit: float):
        with wave.open(str(wav_path), "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        self.state = "listening"
        self.wave.active = True
        self.show()

        # Auto-gain from THIS file's own loudness. The fixed x11.5 was calibrated for some
        # other recording: a user's real mic sits at rms 0.006-0.018, which x11.5 tops
        # out at 0.21 — under the wave's own 0.2 floor. So the demo waveform never animated
        # for their voice, and any skin judged from a demo screenshot looked permanently idle.
        # Same rule as the VAD: scale a human signal from a MEASUREMENT of it, never a constant.
        # Measure the SAME quantity the feeder pushes — per-50ms-frame RMS — not peak
        # amplitude. Scaling from the peak gave gain 9.1 (vs the old 11.5) and changed
        # nothing, because for speech the frame RMS is ~5x below the peak: 0.018 x 9.1 =
        # 0.16, still under the wave's 0.2 floor. Measure the wrong statistic and the
        # auto-gain is just a differently-wrong constant.
        _f = pcm.astype(np.float32) / 32767
        _st = int(16000 * 0.05)
        _nf = len(_f) // _st
        if _nf:
            _r = np.sqrt((_f[:_nf * _st].reshape(_nf, _st) ** 2).mean(axis=1))
            _loud = float(np.percentile(_r[_r > _r.max() * 0.15], 90)) if _r.max() > 0 else 0.0
        else:
            _loud = 0.0
        gain = float(np.clip(0.92 / max(_loud, 1e-4), 4.0, 400.0))
        log.info("demo auto-gain=%.1f (p90 speech frame rms=%.4f)", gain, _loud)

        def feeder():
            step = int(16000 * 0.05)
            acc = np.zeros(0, dtype=np.int16)
            for i in range(0, len(pcm), step):
                acc = np.concatenate([acc, pcm[i:i + step]])
                chunk = pcm[i:i + step].astype(np.float32) / 32767
                self.wave.push(float(np.sqrt(np.mean(chunk ** 2))) * gain if len(chunk) else 0)
                if i and (i // step) % 12 == 0:
                    try:
                        t, _ = transcribe(self.args.url,
                                          float_to_wav16k(acc.astype(np.float32) / 32767),
                                          token=self.token)
                        self.partial_sig.emit(t)
                    except Exception:
                        pass
                time.sleep(0.02)

        threading.Thread(target=feeder, daemon=True).start()
        if self.args.self_shot:
            QTimer.singleShot(int(max(0.5, shot_exit - 0.9) * 1000), self._demo_shot)
        QTimer.singleShot(int(shot_exit * 1000), QApplication.quit)

    def _demo_shot(self):
        self._hover = True
        for x in (self.btn_close, self.btn_mic, self.grip):
            x.show()
        self.update()
        QTimer.singleShot(150, lambda: (
            self.grab().save(self.args.self_shot),
            self.ghost.isVisible() and self.ghost.grab().save(
                self.args.self_shot.replace(".png", "_ghost.png"))))


def _mic_check(device) -> int:
    devs, default = clean_input_devices()
    print("Input devices:")
    for i, name in devs:
        print(f"  [{i}] {name}" + ("  <-- DEFAULT" if i == default else ""))
    dev = device if device is not None else default
    try:
        mic = MicStream(device=dev)
        mic.start()
        log.info("mic-check OPEN device=%s sr=%s ch=%s", dev, mic.sr, mic.ch)
    except Exception as e:
        log.error("mic-check open FAILED device=%s: %s", dev, e)
        print(f"OPEN FAILED: {e}")
        return 1
    print(f"\nTesting [{dev}] at {mic.sr}Hz/{mic.ch}ch — SPEAK for 5s...")
    t0 = time.time()
    while time.time() - t0 < 5:
        time.sleep(0.25)
        lvl = int(mic.rms() * 32767)
        print(f"  {lvl:5d} |{'#' * min(40, lvl // 40):<40}|", flush=True)
    mic.stop()
    peak, rms16 = mic.peak_rms_16k()
    log.info("mic-check result device=%s peak=%s rms=%.0f", dev, peak, rms16)
    print(f"\npeak={peak}/32767  mean_rms={rms16:.0f}")
    if peak >= 500:
        print(f"PASS — real signal on [{dev}].")
        return 0
    print(f"FAIL — [{dev}] at the floor. Wrong mic, muted, or zero gain.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="",
                    help="STT server URL; empty = use config.json (default http://127.0.0.1:8756)")
    ap.add_argument("--token", default="",
                    help="server token; empty = config.json 'token' or env WAVEFLOW_TOKEN")
    ap.add_argument("--hotkey", default="",
                    help="session-only override for the CAPTURE hotkey (persisted keys live in config.json)")
    ap.add_argument("--cleanup-url", default="")
    ap.add_argument("--cleanup-model", default="llama3.2")
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--demo", default="")
    ap.add_argument("--shot-exit", type=float, default=0.0)
    ap.add_argument("--self-shot", default="")
    ap.add_argument("--record", default="",
                    help="DEBUG: save each session's streamed audio as 16k mono WAV "
                         "into this directory, for replay against candidate fixes")
    ap.add_argument("--mic-check", action="store_true")
    ap.add_argument("--caret-test", action="store_true",
                    help="focus Notepad + report UIA caret (verifies UIA works, esp. in the frozen exe)")
    ap.add_argument("--replay", default="",
                    help="feed this WAV through the FULL real pipeline (worker+finalize+inject) as if it were the mic")
    args = ap.parse_args()

    log.info("=== launch frozen=%s argv=%s ===", getattr(sys, "frozen", False), sys.argv[1:])
    if args.mic_check:
        return _mic_check(args.device)
    if args.caret_test:
        import time as _t
        try:
            import uiautomation as auto
            np = auto.WindowControl(searchDepth=4, ClassName="Notepad")
            if not np.Exists(1, 0.2):
                np = auto.WindowControl(searchDepth=4, RegexName=".*Notepad.*")
            doc = np.DocumentControl() if np.Exists(0, 0) else None
            if doc and doc.Exists(0.5, 0.2):
                doc.SetFocus()
                _t.sleep(0.3)
        except Exception as e:
            print("focus step failed:", e)
        rect = caret_rect_uia()
        ok = rect is not None and rect[2] < 120        # a caret is narrow/short, not a whole field
        print(f"caret_rect_uia() -> {rect}  [{'CARET OK' if ok else 'fallback/none'}]")
        log.info("caret-test frozen=%s -> %s", getattr(sys, "frozen", False), rect)
        return 0 if ok else 1

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ui = WaveFlow(args)
    app.aboutToQuit.connect(ui.ghost.close)   # never leave the ghost stuck on screen
    sw = app.primaryScreen().geometry()
    ui.move((sw.width() - ui.width()) // 2, sw.height() - ui.height() - 110)
    if args.demo:
        ui.run_demo(Path(args.demo), args.shot_exit or 3.5)
    elif args.replay:
        ui.show()
        log.info("replay: starting full pipeline on %s", args.replay)
        QTimer.singleShot(200, ui.start_listen)
        QTimer.singleShot(600000, QApplication.quit)  # 10-min safety
    else:
        # start in the TRAY, not visible-idle: the hotkey model is idle->listen /
        # listening->commit, so a visible idle widget at launch desynced the
        # user's press count (first press hid it instead of listening)
        ui.tray.showMessage("WaveFlow", "Ready — press the hotkey and speak.",
                            QSystemTrayIcon.Information, 2500)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
