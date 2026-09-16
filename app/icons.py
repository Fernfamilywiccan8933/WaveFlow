"""App and tray icons — design/mocks/icons-v2.html option Z, picked 2026-09-14.

Fifteen short voice bars float on a W line, and two ribbon strands weave over and under them.
The tray variant is a 7-bar W drawn in one flat colour: white on a dark taskbar, black on a
light one, mint while listening. Everything is drawn in a 64-unit (app) or 16-unit (tray) box,
the same geometry as the mock, so every size comes from one drawing and none from a bitmap.

    venv\\Scripts\\python app\\icons.py   # writes app/assets/waveflow.ico (16-256 px)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

ASSETS = Path(__file__).resolve().parent / "assets"
ICO_PATH = ASSETS / "waveflow.ico"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
STOPS = ((0.0, "#37e0c8"), (0.45, "#57c8ff"), (0.8, "#8a7bff"), (1.0, "#ff7bc8"))
VOICE = (.8, 1, .7, .95, .75, 1, .85, .7, .9, 1, .75, .95, .7, 1, .8)
MINT_DARK, MINT_LIGHT = "#37e0c8", "#0f9e8a"


def w_y(t: float, top: float, bot: float) -> float:
    """The W line: high at both ends and the centre (centre a little lower), low at 1/4 and 3/4."""
    v = abs(((4 * t) % 2) - 1)
    return bot - (bot - top) * v * (0.7 if abs(t - 0.5) < 0.25 else 1.0)


def w_smooth(t: float, top: float, bot: float) -> float:
    vals = [w_y(min(1.0, max(0.0, t + d / 100)), top, bot) for d in range(-3, 4)]
    return sum(vals) / len(vals)


def bar_rects(n, x0, x1, top, bot, length, width) -> list[QRectF]:
    out = []
    for i in range(n):
        t = i / (n - 1)
        x, cy, h = x0 + (x1 - x0) * t, w_y(t, top, bot), length * VOICE[i % len(VOICE)]
        out.append(QRectF(x - width / 2, cy - h / 2, width, h))
    return out


def ribbon_path(x0, x1, y_of_t, thick, n=80) -> QPainterPath:
    """A band along a centre line, tapered to a point at both ends."""
    top, bot = [], []
    for i in range(n + 1):
        t = i / n
        x, y = x0 + (x1 - x0) * t, y_of_t(t)
        w = thick * math.sin(math.pi * t) ** 0.8 + 0.15
        top.append(QPointF(x, y - w / 2))
        bot.append(QPointF(x, y + w / 2))
    path = QPainterPath(top[0])
    for q in top[1:] + bot[::-1]:
        path.lineTo(q)
    path.closeSubpath()
    return path


def _gradient(width: float) -> QLinearGradient:
    g = QLinearGradient(0, 0, width, 0)
    for at, col in STOPS:
        g.setColorAt(at, QColor(col))
    return g


def paint_app(p: QPainter, size: int):
    """Option Z on its dark rounded tile."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0f1219"))
    p.drawRoundedRect(QRectF(3, 3, 58, 58), 14, 14)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 20), 1))
    p.drawRoundedRect(QRectF(3.5, 3.5, 57, 57), 13.5, 13.5)
    grad = _gradient(64)
    under = ribbon_path(8, 56, lambda t: w_smooth(t, 14, 38) + 2.2 * math.sin(2 * math.pi * 6 * t), 2.6)
    over = ribbon_path(8, 56, lambda t: w_smooth(t, 14, 38) - 2.2 * math.sin(2 * math.pi * 6 * t), 1.1)
    p.setPen(Qt.NoPen)
    if size >= 32:                                   # soft glow: the mock's blur, as widened strokes
        for wdt, alpha in ((4.5, 40), (2.5, 70)):
            c = QPen(grad, wdt, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setOpacity(alpha / 255)
            p.strokePath(under, c)
        p.setOpacity(1.0)
    p.fillPath(under, grad)
    p.setBrush(grad)
    for r in bar_rects(15, 12, 52, 14, 38, 10, 2.2):
        p.drawRoundedRect(r, 1.1, 1.1)
    p.setOpacity(0.55)
    p.fillPath(over, QColor("#ffffff"))
    p.restore()


def paint_tray(p: QPainter, size: int, color: str):
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 16, size / 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    for r in bar_rects(7, 2.5, 13.5, 4, 11, 3.6, 1.4):
        p.drawRoundedRect(r, 0.7, 0.7)
    p.setOpacity(0.6)
    p.fillPath(ribbon_path(1, 15, lambda t: w_smooth(t, 4, 11), 0.9), QColor(color))
    p.restore()


def render(size: int, tray_color: str | None = None) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    if tray_color:
        paint_tray(p, size, tray_color)
    else:
        paint_app(p, size)
    p.end()
    return img


def app_icon() -> QIcon:
    if ICO_PATH.exists():
        return QIcon(str(ICO_PATH))
    icon = QIcon()
    for s in ICO_SIZES:
        icon.addPixmap(QPixmap.fromImage(render(s)))
    return icon


def taskbar_is_light() -> bool:
    """Windows 'SystemUsesLightTheme' decides the taskbar colour (0 = dark, the default)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def tray_icon(listening: bool = False, light: bool | None = None) -> QIcon:
    light = taskbar_is_light() if light is None else light
    color = (MINT_LIGHT if light else MINT_DARK) if listening else ("#111111" if light else "#ffffff")
    icon = QIcon()
    for s in (16, 20, 24, 32, 40):
        icon.addPixmap(QPixmap.fromImage(render(s, color)))
    return icon


def ico_bytes(sizes=ICO_SIZES) -> bytes:
    """A Windows .ico holding one PNG per size (Vista+ format, what PyInstaller and Explorer read)."""
    import struct
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    pngs = []
    for s in sizes:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        render(s).save(buf, "PNG")
        pngs.append(bytes(ba))
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    table = b""
    for s, png in zip(sizes, pngs):
        dim = 0 if s >= 256 else s
        table += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    return head + table + b"".join(pngs)


ICNS_PATH = ASSETS / "waveflow.icns"
# (OSType, pixel size). PNG-payload types, readable by macOS 10.7+. Drawn from the vector
# geometry at each size, so the 1024 px Retina entry is sharp — not an upscaled 256 px .ico frame.
ICNS_TYPES = (("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256),
              ("ic09", 512), ("ic10", 1024), ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512))


def icns_bytes(types=ICNS_TYPES) -> bytes:
    """A macOS .icns: 'icns' + total length, then (type, entry length, PNG) per size, big-endian.

    Without it the Mac bundle showed PyInstaller's placeholder in Finder, notifications and the
    Privacy & Security lists — the very lists the user must find WaveFlow in (Mac, 2026-09-16)."""
    import struct
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    cache, body = {}, b""
    for ostype, s in types:
        if s not in cache:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            render(s).save(buf, "PNG")
            cache[s] = bytes(ba)
        body += ostype.encode("ascii") + struct.pack(">I", 8 + len(cache[s])) + cache[s]
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def main() -> int:
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)
    ICO_PATH.write_bytes(ico_bytes())
    print(f"wrote {ICO_PATH} ({ICO_PATH.stat().st_size} bytes, sizes {ICO_SIZES})")
    ICNS_PATH.write_bytes(icns_bytes())
    print(f"wrote {ICNS_PATH} ({ICNS_PATH.stat().st_size} bytes, {len(ICNS_TYPES)} entries up to 1024 px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
