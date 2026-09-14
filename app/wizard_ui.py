"""Hand-drawn widgets for the setup wizard, matched by eye to design/mocks/wizard-hybrid (A+C).

Plain QPushButtons cannot hold a bold title, a muted body, a badge and a glow ring, so the cards,
step circles, segmented control, check rows and the title bar paint themselves.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QTextOption
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

MINT, SKY, VIO, BLUSH, BAD, WARN = "#37e0c8", "#57c8ff", "#8a7bff", "#ff7bc8", "#ff6b7f", "#ffc46b"
TX, MUT, DIM = QColor("#e9ecf3"), QColor("#8d94a6"), QColor("#5d6477")
LINE, HAIR = QColor(255, 255, 255, 23), QColor(255, 255, 255, 46)
DISPLAY = "Segoe UI Variable Display"
TEXT = "Segoe UI Variable Text"


def font(size: float, weight=QFont.Normal, family=TEXT) -> QFont:
    f = QFont(family)
    f.setFamilies([family, "Segoe UI"])
    f.setPointSizeF(size * 0.75)          # px -> pt at 96 dpi
    f.setWeight(weight)
    return f


def rounded(r: QRectF, rad: float) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(r, rad, rad)
    return p


def _ring(p: QPainter, r: QRectF, rad: float, color: str, glow=True):
    if glow:
        for i, a in ((4, 18), (2, 34)):
            p.setPen(QPen(QColor(color).lighter(100), 1))
            c = QColor(color)
            c.setAlpha(a)
            p.setPen(QPen(c, i))
            p.drawPath(rounded(r, rad))
    p.setPen(QPen(QColor(color), 1.5))
    p.drawPath(rounded(r, rad))


class Card(QAbstractButton):
    """Selectable card: bold title, optional badge, muted body, optional warning note."""

    def __init__(self, title="", body="", badge="", note="", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.title, self.body, self.badge, self.note = title, body, badge, note
        self._hover = False
        self.setMinimumHeight(60)

    def set_content(self, title, body, badge="", note=""):
        self.title, self.body, self.badge, self.note = title, body, badge, note
        self.updateGeometry()
        self.update()

    def enterEvent(self, _):
        self._hover = True
        self.update()

    def leaveEvent(self, _):
        self._hover = False
        self.update()

    def _text_h(self, w: int) -> int:
        h = QFontMetrics(font(14, QFont.DemiBold)).height() + 4
        for txt, f in ((self.body, font(12)), (self.note, font(11.5))):
            if txt:
                h += QFontMetrics(f).boundingRect(0, 0, w, 10_000, Qt.TextWordWrap, txt).height() + 2
        return h + 26

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return max(60, self._text_h(w - 30))

    def sizeHint(self):
        return QSize(260, self.heightForWidth(260))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        p.fillPath(rounded(r, 10), QColor(255, 255, 255, 12 if self.isChecked() else 5))
        if self.isChecked():
            _ring(p, r, 10, SKY)
        else:
            p.setPen(QPen(HAIR if self._hover and self.isEnabled() else LINE, 1))
            p.drawPath(rounded(r, 10))
        x, y, w = r.x() + 14, r.y() + 11, r.width() - 28
        tf = font(14, QFont.DemiBold)
        p.setFont(tf)
        p.setPen(TX)
        fm = QFontMetrics(tf)
        p.drawText(QRectF(x, y, w, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        if self.badge:
            bf = font(11)
            bw = QFontMetrics(bf).horizontalAdvance(self.badge) + 16
            br = QRectF(r.right() - bw - 10, y, bw, fm.height())
            p.setPen(QPen(QColor(55, 224, 200, 110), 1))
            p.setBrush(QColor(55, 224, 200, 22))
            p.drawRoundedRect(br, br.height() / 2, br.height() / 2)
            p.setFont(bf)
            p.setPen(QColor(MINT))
            p.drawText(br, Qt.AlignCenter, self.badge)
            p.setBrush(Qt.NoBrush)
        y += fm.height() + 4
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WordWrap)
        for txt, f, col in ((self.body, font(12), MUT), (self.note, font(11.5), QColor(WARN))):
            if not txt:
                continue
            p.setFont(f)
            p.setPen(col)
            h = QFontMetrics(f).boundingRect(0, 0, int(w), 10_000, Qt.TextWordWrap, txt).height()
            p.drawText(QRectF(x, y, w, h), txt, opt)
            y += h + 2


class StepItem(QAbstractButton):
    """Rail step: circle (future ring / current sky ring / done mint check) + label."""

    def __init__(self, n: int, label: str, parent=None):
        super().__init__(parent)
        self.n, self.label, self.state = n, label, "future"
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.state == "current":
            p.fillPath(rounded(r.adjusted(0, 2, 0, -2), 8), QColor(255, 255, 255, 13))
        c = QRectF(12, r.center().y() - 11, 22, 22)
        if self.state == "done":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(MINT))
            p.drawEllipse(c)
            p.setPen(QPen(QColor("#053d33"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline([QPoint(int(c.x() + 6.5), int(c.y() + 11.5)), QPoint(int(c.x() + 9.5), int(c.y() + 14.5)),
                            QPoint(int(c.x() + 15.5), int(c.y() + 8))])
        else:
            cur = self.state == "current"
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(SKY) if cur else HAIR, 1.3))
            p.drawEllipse(c)
            p.setFont(font(11, QFont.DemiBold))
            p.setPen(QColor(SKY) if cur else DIM)
            p.drawText(c, Qt.AlignCenter, str(self.n))
        p.setFont(font(13, QFont.DemiBold if self.state == "current" else QFont.Normal))
        p.setPen(TX if self.state != "future" else MUT)
        p.drawText(QRectF(44, 0, r.width() - 48, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.label)


class Segmented(QWidget):
    """Joined segmented control. .group is a QButtonGroup (ids = index)."""

    changed = Signal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        for i, t in enumerate(labels):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("seg")
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.idClicked.connect(self.changed.emit)
        self.setObjectName("segbox")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set(self, i):
        self.group.button(i).setChecked(True)

    def index(self):
        return self.group.checkedId()


class CheckRow(QWidget):
    """Bordered test-connection row with a circle status icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state, self.name, self.detail = None, "", ""
        self.setFixedHeight(46)

    def set(self, state, name, detail):
        self.state, self.name, self.detail = state, name, detail
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        col = {True: QColor(MINT), False: QColor(BAD), None: DIM}[self.state]
        p.setPen(QPen(QColor(255, 107, 127, 120) if self.state is False else LINE, 1))
        p.drawPath(rounded(r, 10))
        c = QRectF(r.x() + 12, r.center().y() - 11, 22, 22)
        bg = QColor(col)
        bg.setAlpha(38 if self.state is not None else 0)
        p.setBrush(bg)
        p.setPen(Qt.NoPen if self.state is not None else QPen(DIM, 1, Qt.DashLine))
        p.drawEllipse(c)
        p.setPen(col)
        p.setFont(font(12, QFont.Bold))
        p.drawText(c, Qt.AlignCenter, {True: "✓", False: "✕", None: ""}[self.state])
        p.setFont(font(13))
        p.setPen(TX if self.state is not None else MUT)
        p.drawText(QRectF(c.right() + 12, r.y(), r.width() * 0.6, r.height()), Qt.AlignVCenter, self.name)
        p.setFont(QFont("Cascadia Code", 9))
        p.setPen(MUT)
        p.drawText(QRectF(r.x(), r.y(), r.width() - 14, r.height()), Qt.AlignVCenter | Qt.AlignRight, self.detail)


class TitleBar(QWidget):
    """Dark title bar for a frameless window: gradient mark, title, minimise, close, drag to move."""

    def __init__(self, window, title: str):
        super().__init__(window)
        self.win, self._drag = window, None
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 4, 0)
        lay.setSpacing(8)
        mark = QLabel()
        mark.setFixedSize(16, 7)
        mark.setStyleSheet(f"border-radius:3px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                           f"stop:0 {MINT},stop:0.45 {SKY},stop:0.8 {VIO},stop:1 {BLUSH});")
        t = QLabel(title)
        t.setStyleSheet("font-weight:600;font-size:12px;color:#e9ecf3;background:transparent;")
        lay.addWidget(mark)
        lay.addWidget(t)
        lay.addStretch(1)
        for glyph, fn in (("—", window.showMinimized), ("✕", window.reject)):
            b = QPushButton(glyph)
            b.setObjectName("winbtn")
            b.setFixedSize(40, 30)
            b.clicked.connect(fn)
            lay.addWidget(b)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("titlebar")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None


def round_window_corners(widget):
    """Windows 11: rounded corners + native shadow for a frameless window (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        pref = ctypes.c_int(2)            # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass
