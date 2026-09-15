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


def _chroma(width: float) -> QLinearGradient:
    g = QLinearGradient(0, 0, width, 0)
    for at, col in ((0, MINT), (0.45, SKY), (0.8, VIO), (1, BLUSH)):
        g.setColorAt(at, QColor(col))
    return g


class SkinCard(QAbstractButton):
    """Skin row (mock v3): a drawn preview of the overlay on a tinted stage, then name + description."""

    def __init__(self, key: str, label: str, desc: str = "", parent=None):
        super().__init__(parent)
        self.key, self.label, self.desc = key, label, desc
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(90)
        self.setMinimumWidth(300)

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isChecked():
            _ring(p, r, 12, SKY)
        else:
            p.setPen(QPen(LINE, 1))
            p.drawPath(rounded(r, 12))
        stage = QRectF(r.x() + 12, r.y() + 12, min(200.0, r.width() * 0.52), r.height() - 24)
        sg = QLinearGradient(stage.topLeft(), stage.bottomRight())
        sg.setColorAt(0, QColor("#2b3a4d"))
        sg.setColorAt(1, QColor("#3b2d4a"))
        p.fillPath(rounded(stage, 10), sg)
        if self.key == "halo":
            pill = QRectF(0, 0, stage.width() * 0.72, 38)
        else:
            pill = QRectF(0, 0, stage.width() - 10, 44)
        pill.moveCenter(stage.center())
        rad = pill.height() / 2
        p.fillPath(rounded(pill, rad), QColor(8, 10, 14, 225))
        if self.key == "halo":
            p.setPen(QPen(QColor(138, 123, 255, 180), 1.5))
            p.drawPath(rounded(pill, rad))
            bars = [4, 7, 11, 15, 19, 22, 19, 15, 11, 7, 4]
            bw, gap = 3.0, 5.0
            x0 = pill.center().x() - (len(bars) * (bw + gap) - gap) / 2
            for i, bh in enumerate(bars):
                br = QRectF(x0 + i * (bw + gap), pill.center().y() - bh / 2, bw, bh)
                g = QLinearGradient(0, br.top(), 0, br.bottom())
                g.setColorAt(0, QColor(MINT))
                g.setColorAt(1, QColor(VIO))
                p.fillPath(rounded(br, 1.5), g)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            p.drawPath(rounded(pill, rad))
            g = QLinearGradient(pill.left(), 0, pill.right(), 0)
            for at, col in ((0, MINT), (0.45, SKY), (0.8, VIO), (1, BLUSH)):
                g.setColorAt(at, QColor(col))
            for amp, wd, alpha, ph in ((8, 5.5, 45, 0), (8, 2.2, 255, 0), (5, 1.2, 140, 0.8)):
                path = QPainterPath()
                x0, x1, cy = pill.left() + 14, pill.right() - 14, pill.center().y()
                for k in range(61):
                    t = k / 60
                    y = cy + amp * math.sin(t * math.pi * 3 + ph)
                    (path.lineTo if k else path.moveTo)(x0 + (x1 - x0) * t, y)
                p.setOpacity(alpha / 255)
                p.strokePath(path, QPen(g, wd, Qt.SolidLine, Qt.RoundCap))
            p.setOpacity(1)
        tx = stage.right() + 14
        p.setFont(font(13.5, QFont.DemiBold))
        p.setPen(TX)
        p.drawText(QRectF(tx, r.y(), r.right() - tx - 8, r.height() / 2 - 1), Qt.AlignBottom | Qt.AlignLeft, self.label)
        p.setFont(font(11.5))
        p.setPen(MUT)
        p.drawText(QRectF(tx, r.center().y() + 3, r.right() - tx - 8, r.height() / 2 - 6),
                   Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, self.desc)


class LevelMeter(QWidget):
    """Mic level relative to the room's noise floor (mock v3: gradient fill, glowing white line at the
    speech threshold). Talking must push the fill past the line; room noise must stay left of it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.level, self.threshold = 0.0, 0.44
        self.setFixedHeight(20)
        self.setMinimumWidth(200)

    def set_level(self, pos: float):
        self.level = self.level * 0.4 + max(0.0, min(1.0, pos)) * 0.6
        self.update()

    def set_threshold(self, pos: float):
        self.threshold = max(0.0, min(1.0, pos))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 5, 0, -5)
        p.fillPath(rounded(r, 5), QColor(255, 255, 255, 15))
        if self.level > 0.005:
            fill = QRectF(r.x(), r.y(), max(r.height(), r.width() * self.level), r.height())
            p.fillPath(rounded(fill, 5), _chroma(r.width()))
        x = r.x() + r.width() * self.threshold
        glow = QColor(255, 255, 255, 60)
        p.setPen(QPen(glow, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPoint(int(x), 3), QPoint(int(x), self.height() - 3))
        p.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPoint(int(x), 1), QPoint(int(x), self.height() - 1))


class SensSlider(QWidget):
    """Drag slider 0-100 with High / Balanced / Low marks (mock settings-icons-v1). Snaps onto a
    mark when released close to it; arrow keys step by 5."""

    changed = Signal(float)
    SNAP = 4.0

    def __init__(self, value: float = 50.0, parent=None):
        super().__init__(parent)
        self.value = float(value)
        self._drag = False
        self.setFixedHeight(46)
        self.setMinimumWidth(240)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def _track(self) -> QRectF:
        return QRectF(11, 12, self.width() - 22, 5)

    def set_value(self, v: float, emit=True, snap=False):
        v = max(0.0, min(100.0, float(v)))
        if snap:
            for mark in (0.0, 50.0, 100.0):
                if abs(v - mark) <= self.SNAP:
                    v = mark
        if v != self.value:
            self.value = v
            self.update()
            if emit:
                self.changed.emit(v)

    def _from_x(self, x: float) -> float:
        t = self._track()
        return (x - t.x()) / t.width() * 100

    def mousePressEvent(self, e):
        self._drag = True
        self.set_value(self._from_x(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._drag:
            self.set_value(self._from_x(e.position().x()))

    def mouseReleaseEvent(self, e):
        self._drag = False
        self.set_value(self._from_x(e.position().x()), snap=True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Left, Qt.Key_Down):
            self.set_value(self.value - 5)
        elif e.key() in (Qt.Key_Right, Qt.Key_Up):
            self.set_value(self.value + 5)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._track()
        p.fillPath(rounded(t, 2.5), QColor("#262b37"))
        x = t.x() + t.width() * self.value / 100
        if x > t.x():
            p.fillPath(rounded(QRectF(t.x(), t.y(), x - t.x(), t.height()), 2.5), _chroma(t.width()))
        p.setFont(font(11))
        for mark, label, align in ((0, "High", Qt.AlignLeft), (50, "Balanced", Qt.AlignHCenter),
                                   (100, "Low", Qt.AlignRight)):
            mx = t.x() + t.width() * mark / 100
            p.setPen(QPen(HAIR, 1))
            p.drawLine(QPoint(int(mx), int(t.bottom() + 3)), QPoint(int(mx), int(t.bottom() + 7)))
            near = abs(self.value - mark) < 25 or (mark == 50 and 25 <= self.value <= 75)
            p.setPen(TX if near else DIM)
            w = 80
            box = QRectF(mx - (0 if align == Qt.AlignLeft else w if align == Qt.AlignRight else w / 2) - (11 if mark == 0 else 0)
                         + (11 if mark == 100 else 0), t.bottom() + 9, w, 16)
            p.drawText(box, align | Qt.AlignVCenter, label)
        halo = QColor(SKY)
        halo.setAlpha(80 if self.hasFocus() or self._drag else 50)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(x - 13, t.center().y() - 13, 26, 26))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x - 9, t.center().y() - 9, 18, 18))


class Toggle(QAbstractButton):
    """On/off switch (mint→sky when on)."""

    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1 if self.isEnabled() else 0.4)
        r = QRectF(1, 1, 36, 20)
        p.setPen(Qt.NoPen)
        if self.isChecked():
            g = QLinearGradient(0, 0, 36, 0)
            g.setColorAt(0, QColor(MINT))
            g.setColorAt(1, QColor(SKY))
            p.setBrush(g)
        else:
            p.setBrush(QColor("#2a2f3c"))
        p.drawRoundedRect(r, 10, 10)
        p.setBrush(QColor("#ffffff") if self.isChecked() else MUT)
        p.drawEllipse(QRectF(19 if self.isChecked() else 4, 4, 14, 14))


NAV_GLYPHS = {
    "conn": [((6, 10), (10, 6)), ((5, 7), (3.5, 8.5), (3.5, 11), (5, 12.5), (7, 12.5), (8.5, 11)),
             ((11, 9), (12.5, 7.5), (12.5, 5), (11, 3.5), (9, 3.5), (7.5, 5))],
    "eng": [((4, 4), (12, 4), (12, 12), (4, 12), (4, 4)), ((6, 1.5), (6, 4)), ((10, 1.5), (10, 4)),
            ((6, 12), (6, 14.5)), ((10, 12), (10, 14.5))],
    "mic": [((6.5, 3), (6.5, 8), (8, 9.5), (9.5, 8), (9.5, 3), (8, 1.5), (6.5, 3)),
            ((3.5, 7.5), (4.5, 10.5), (8, 12.5), (11.5, 10.5), (12.5, 7.5)), ((8, 12.5), (8, 14.5))],
    "look": [((1.5, 4), (14.5, 4), (14.5, 12), (1.5, 12), (1.5, 4)), ((4, 7), (5, 7)), ((7, 7), (8, 7)),
             ((10, 7), (12, 7)), ((4, 9.5), (12, 9.5))],
    "adv": [((3, 4), (13, 4)), ((3, 8), (13, 8)), ((3, 12), (13, 12)), ((6, 3), (6, 5)), ((10, 7), (10, 9)),
            ((5, 11), (5, 13))],
    "un": [((3, 4.5), (13, 4.5)), ((6, 4.5), (6, 3), (10, 3), (10, 4.5)), ((4.5, 4.5), (5.2, 13.5), (10.8, 13.5),
                                                                          (11.5, 4.5))],
}


class NavItem(QAbstractButton):
    """Settings side-menu row: line glyph + label; current row gets a sky edge."""

    def __init__(self, key: str, label: str, danger=False, parent=None):
        super().__init__(parent)
        self.key, self.label, self.danger = key, label, danger
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)

    def paintEvent(self, _):
        from PySide6.QtCore import QPointF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        if on:
            p.fillPath(rounded(r.adjusted(0, 2, 0, -2), 8), QColor(255, 255, 255, 15))
            p.fillRect(QRectF(0, 9, 2, r.height() - 18), QColor(SKY))
        col = QColor("#d98a96") if self.danger else (TX if on else MUT)
        p.setPen(QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.save()
        p.translate(12, r.height() / 2 - 8)
        for line in NAV_GLYPHS.get(self.key, []):
            p.drawPolyline([QPointF(*pt) for pt in line])
        p.restore()
        p.setFont(font(13, QFont.DemiBold if on else QFont.Normal))
        p.drawText(QRectF(38, 0, r.width() - 40, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.label)


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
