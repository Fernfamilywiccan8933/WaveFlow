"""Pill v4 rules. Run: venv/Scripts/python.exe app/test_pill.py -> PILL_OK

Judges what CAN be judged offscreen — geometry, state wiring, theme resolution, and the glow
measured on real painted pixels. It does NOT judge the look.

DESIGN.md is explicit about why: `QWidget.grab()` composites a translucent body over white and
lies, so the glass can only be judged live with `tools/screencap.py`. Every number here is one
that can drift silently and take the face with it.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import testenv  # noqa: E402,F401 — isolate settings/log BEFORE any app import

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import waveflow as W  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


app = QApplication.instance() or QApplication([])

# --- the operator's picks, as numbers ------------------------------------------------------
check("glow is +20%", W.GLOW_GAIN, 1.20)
check("word is 9pt (P3)", W.WORD_PT, 9.0)
check("edge is a half-pixel hairline", W.EDGE_W, 0.5)
check("three themes offered", W.PILL_THEMES, ("system", "dark", "light"))

# --- the word must not shrink the wave into dots -------------------------------------------
# This is the failure DESIGN.md already records: halo's bars squeezed into a 20px widget "could
# never grow past a row of dots at ANY volume". The word costs WORD_H; what is left must stay
# at or above the 24px the operator picked as P3.
for skin, sk in W.SKINS.items():
    h = sk["size"][1]
    top = 6 if sk["rim"] else 7
    wave_h = h - top * 2 - W.WORD_H
    check(f"{skin}: wave keeps P3's height", wave_h >= 23, True)
    check(f"{skin}: pill height unchanged", h, {"aurora": 68, "halo": 56}[skin])
check("a capsule would have cost 19px, plain text costs 11", W.WORD_H, 11)

# The regression that actually happened on 2026-09-15: reserving WORD_H off the bottom WITHOUT
# reducing the top inset left halo a 23px face, and its own bar padding cut that to 13px of bar
# — "a row of dots at ANY volume", which this project has now hit twice. P3 means the wave is the
# hero, so the tallest bar must reach roughly half the pill. Guarded as a fraction, not a
# constant, so it survives any future size change.
sk = W.SKINS["halo"]
pill_h = sk["size"][1]
wave_h = pill_h - (6 * 2) - W.WORD_H
max_bar = wave_h - 4                     # the padding inside _paint_halo
check("halo's loudest bar reaches about half the pill", max_bar / pill_h >= 0.45, True)
check("halo's bars are not dots", max_bar >= 24, True)

# --- theme resolution ----------------------------------------------------------------------
check("explicit dark wins", W.resolve_theme("dark"), "dark")
check("explicit light wins", W.resolve_theme("light"), "light")
check("system resolves to something real", W.resolve_theme("system") in ("dark", "light"), True)
check("junk falls back safely", W.resolve_theme("banana") in ("dark", "light"), True)

# --- light theme must actually be light ----------------------------------------------------
# The July bug was the pill going WHITE on a light desktop because it was too transparent. The
# fix is not "never be light" — it is "be light ON PURPOSE, and opaque enough to hold its shape".
for name, stops in (("dark", W.BODY_DARK), ("light", W.BODY_LIGHT)):
    for r, g, b, a in stops:
        check(f"{name} body stays opaque enough to hold the silhouette", a >= 220, True)
check("light body is genuinely light", W.BODY_LIGHT[0][0] > 200, True)
check("dark body is genuinely dark", W.BODY_DARK[2][0] < 40, True)


def render(theme, rim, gain=None):
    """Paint the real pill onto a KNOWN black backdrop at a KNOWN energy."""
    old = W.GLOW_GAIN
    if gain is not None:
        W.GLOW_GAIN = gain
    sk = W.SKINS["halo"]
    w, h = sk["size"]
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    W.paint_pill(p, QRectF(0.5, 0.5, w - 1, h - 1), False, sk["rad"], rim, theme)
    p.end()
    W.GLOW_GAIN = old
    return img


def mean_luma(img):
    tot = n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            tot += 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            n += 1
    return tot / n


def glow_pixels(img):
    """Coloured pixels. The pill body is grey, so saturation IS the glow."""
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 18:
                n += 1
    return n


dark_l, light_l = mean_luma(render("dark", 0.0)), mean_luma(render("light", 0.0))
check("the light pill really is brighter than the dark one", light_l > dark_l + 80, True)

# --- the glow increase, measured, not asserted ---------------------------------------------
for rim in (0.30, 0.75):
    before, after = glow_pixels(render("dark", rim, 1.0)), glow_pixels(render("dark", rim, 1.20))
    grew = (after / before) - 1.0
    check(f"glow at rim={rim} grew by roughly a fifth", 0.12 <= grew <= 0.32, True)
check("no glow at rest", glow_pixels(render("dark", 0.0)), 0)

# --- the word is driven by state, and idle shows nothing -----------------------------------
check("idle shows no word", W.WaveFlow.STATE_WORDS["idle"], "")
for s in ("listening", "finalizing", "typing", "offline"):
    check(f"{s} has a word", bool(W.WaveFlow.STATE_WORDS[s]), True)
check("an unknown state shows nothing rather than crashing",
      W.WaveFlow.STATE_WORDS.get("nonsense", ""), "")

# state is a property, so no assignment site can bypass the word
check("state is a property", isinstance(W.WaveFlow.state, property), True)
check("the property has a setter", W.WaveFlow.state.fset is not None, True)

if FAILS:
    print("PILL_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("PILL_OK")
