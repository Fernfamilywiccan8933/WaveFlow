"""First-run setup wizard — design: A+C hybrid (step rail + live preview), picked 2026-09-14.

Steps: Welcome · Where it runs · Configure · Test connection · Hotkey & mic.
Configure and Test show a live preview on the right: the exact commands / .env / config that will
be written or run, and the raw request log. Nothing runs until the primary button is pressed.
All rules (engine matrix, threads, validation, plan, checks) live in setup_logic.py; the
hand-drawn widgets live in wizard_ui.py.
"""
from __future__ import annotations

import html
import json
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QScrollArea,
                               QKeySequenceEdit, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

import remote_install as RI
import setup_logic as S
from panels import MicPanel, PermissionPanel, SkinPicker
from wizard_ui import (BAD, BLUSH, MINT, SKY, VIO, WARN, Card, CheckRow, Segmented,
                       StepItem, TitleBar, families, fit_to_screen,
                       round_window_corners)

STEPS = ["Welcome", "Where it runs", "Configure", "Test connection", "Hotkey, mic & look"]

_QSS_CACHE = None


def qss() -> str:
    """The stylesheet, built once the font families are known.

    It was a module-level f-string, which froze whichever families were resolvable at
    IMPORT time — and on macOS the real answer needs a live QApplication to ask Qt for the
    system font. Building it on demand means the sheet always names the font that will
    actually be used.
    """
    global _QSS_CACHE
    if _QSS_CACHE is None:
        DISPLAY, TEXT, MONO, FALLBACK = families()
        _QSS_CACHE = f"""
QDialog#wizard{{background:#12151c;border:1px solid rgba(255,255,255,0.08);}}
QWidget{{color:#e9ecf3;font-family:'{TEXT}','{FALLBACK}';font-size:13px;background:transparent;}}
#titlebar{{background:#12151c;border-bottom:1px solid rgba(255,255,255,0.08);}}
QPushButton#winbtn{{background:transparent;border:none;color:#5d6477;font-size:13px;border-radius:0;padding:0;}}
QPushButton#winbtn:hover{{background:rgba(255,255,255,0.06);color:#e9ecf3;}}
#rail{{background:rgba(0,0,0,0.18);border-right:1px solid rgba(255,255,255,0.08);}}
#preview{{background:#07090d;border-left:1px solid rgba(255,255,255,0.08);}}
QLabel#h2{{font-family:'{DISPLAY}','{FALLBACK}';font-size:19px;font-weight:600;}}
QLabel#lead{{color:#8d94a6;font-size:13px;}}
QLabel#hint{{color:#5d6477;font-size:11.5px;}}
QLabel#lbl{{color:#8d94a6;font-size:11.5px;}}
QLabel#plbl{{color:#5d6477;font-size:10.5px;letter-spacing:1px;}}
QLabel#err{{color:{BAD};font-size:12px;}} QLabel#warn{{color:{WARN};font-size:12px;}}
QLabel#detect{{color:#8d94a6;font-size:12px;}}
QFrame#divider{{background:rgba(255,255,255,0.08);max-height:1px;min-height:1px;}}
QPushButton{{background:rgba(34,38,50,0.9);border:1px solid rgba(255,255,255,0.18);border-radius:8px;padding:8px 16px;}}
QPushButton:hover{{border-color:rgba(255,255,255,0.32);}}
QPushButton:disabled{{color:#5d6477;border-color:rgba(255,255,255,0.06);}}
QPushButton#pri{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #5fd0ff,stop:1 #3aa9e6);border:none;
  color:#04121c;font-weight:600;padding:9px 20px;}}
QPushButton#pri:hover{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #78d9ff,stop:1 #4cb4ee);}}
QPushButton#pri:disabled{{background:#23445a;color:#6d8797;}}
QPushButton#ghost{{background:transparent;border:none;color:#8d94a6;}}
QPushButton#ghost:hover{{color:#e9ecf3;}}
QPushButton#pill{{background:transparent;border:1px solid rgba(255,255,255,0.09);border-radius:11px;color:#8d94a6;
  padding:3px 10px;font-size:11.5px;}}
QPushButton#pill:hover,QPushButton#pill:checked{{color:#e9ecf3;border-color:rgba(255,255,255,0.25);}}
#segbox{{border:1px solid rgba(255,255,255,0.09);border-radius:9px;background:rgba(0,0,0,0.2);}}
QPushButton#seg{{background:transparent;border:none;border-radius:6px;color:#8d94a6;padding:6px 14px;font-size:12.5px;}}
QPushButton#seg:checked{{background:rgba(87,200,255,0.16);color:#e9ecf3;}}
QLineEdit,QSpinBox,QComboBox,QKeySequenceEdit{{background:#0c0f15;border:1px solid rgba(255,255,255,0.1);
  border-radius:7px;padding:7px 10px;selection-background-color:#3aa9e6;}}
QLineEdit:focus,QSpinBox:focus,QComboBox:focus{{border-color:rgba(87,200,255,0.6);}}
QComboBox QAbstractItemView{{background:#12151c;border:1px solid rgba(255,255,255,0.12);selection-background-color:#223344;}}
QTextEdit{{background:#07090d;border:none;font-family:'{MONO}',monospace;font-size:12px;color:#cfd6e4;}}
QCheckBox{{color:#c9cfdc;spacing:6px;}}
QProgressBar{{background:rgba(255,255,255,0.06);border:none;border-radius:4px;max-height:8px;}}
QPushButton#danger{{background:rgba(255,107,127,0.1);border:1px solid rgba(255,107,127,0.45);color:#ffb3bd;
  font-weight:600;padding:9px 20px;}}
QPushButton#danger:hover{{background:rgba(255,107,127,0.18);}}
QPushButton#danger:disabled{{color:#5d6477;border-color:rgba(255,255,255,0.06);background:transparent;}}
QLabel#warnbox{{background:rgba(255,196,107,0.06);border:1px solid rgba(255,196,107,0.3);border-radius:10px;
  padding:10px 12px;color:#c9cfdc;font-size:12px;}}
#urow{{border:1px solid rgba(255,255,255,0.08);border-radius:10px;}}
QCheckBox::indicator{{width:16px;height:16px;border:1px solid rgba(255,255,255,0.28);border-radius:4px;background:#0c0f15;}}
QCheckBox::indicator:checked{{background:#57c8ff;border-color:#57c8ff;}}
QCheckBox::indicator:disabled{{border-color:rgba(255,255,255,0.08);background:transparent;}}
QTextEdit#previewtext{{background:#07090d;border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:8px;font-family:'{MONO}',monospace;font-size:12px;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {MINT},stop:0.45 {SKY},stop:0.8 {VIO},stop:1 {BLUSH});border-radius:4px;}}
"""
    return _QSS_CACHE


def __getattr__(name):
    """So `from wizard import QSS` still works — settings.py builds on it."""
    if name == "QSS":
        return qss()
    raise AttributeError(name)



def _lbl(text, name=None, wrap=True):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def _divider():
    f = QFrame()
    f.setObjectName("divider")
    return f


class _Bus(QObject):
    progress = Signal(str)
    done = Signal(bool, str)
    checks = Signal(list, str)
    step = Signal(int, object, str)      # remote install checklist: index, None/True/False, detail
    log = Signal(str)                    # remote install live log line


class SetupWizard(QDialog):
    def __init__(self, cfg: dict, engine, devices_fn=None, parent=None):
        super().__init__(parent)
        self.setObjectName("wizard")
        self.setWindowTitle("WaveFlow setup")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setStyleSheet(qss())
        self.resize(1040, 660)          # refit in showEvent, once the pages exist
        self.cfg = dict(cfg)
        self.engine = engine                  # LocalEngine owned by the app
        self.devices_fn = devices_fn
        self.hw = S.detect()
        prev = self.cfg.get("engine") or {}
        self.c = S.Choices(option=prev.get("mode", "local") if prev.get("mode") in S.OPTIONS else "local",
                           engine=prev.get("engine", "onnx-cpu"), method=prev.get("method", "docker"),
                           threads=prev.get("threads", self.hw.perf_cores),
                           auto_threads=prev.get("auto_threads", True),
                           address=self.cfg.get("url", "") if prev.get("mode") in S.REMOTE_OPTIONS else "",
                           token=self.cfg.get("token") or
                           ("" if prev.get("mode") == "connect" else S.new_token()))
        if self.c.auto_threads:
            self.c.threads = self.hw.perf_cores
        self.result_cfg: dict | None = None
        self.plan = S.build_plan(self.c)
        self.bus = _Bus()
        self.bus.progress.connect(self._on_progress)
        self.bus.done.connect(self._on_action_done)
        self.bus.checks.connect(self._on_checks)
        self.bus.step.connect(self._on_step)
        self.bus.log.connect(self._on_log)
        self.ssh_user = prev.get("ssh_user", "")
        self.ssh_folder = prev.get("folder", "~/waveflow")
        self._install_log: list[str] = []
        self._install_view = False
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self, "WaveFlow setup"))
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        rail = QFrame()
        rail.setObjectName("rail")
        # Not setFixedWidth: on a narrow screen a fixed rail is 200px the content can
        # never borrow, and the window ends up wider than the display with no way to
        # shrink it. A maximum lets it give ground when it has to.
        rail.setMinimumWidth(150)
        rail.setMaximumWidth(212)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(10, 16, 10, 16)
        rl.setSpacing(2)
        self.steps = []
        for i, s in enumerate(STEPS):
            it = StepItem(i + 1, s)
            it.clicked.connect(lambda _=False, i=i: self.go(i))
            rl.addWidget(it)
            self.steps.append(it)
        rl.addStretch(1)
        self.rail_foot = _lbl("", "hint")
        rl.addWidget(self.rail_foot)
        body.addWidget(rail)

        self.stack = QStackedWidget()
        # No scroll area around the STACK: each page scrolls its own content and keeps its
        # footer pinned below it (see _shell). Wrapping the whole stack instead is what
        # scrolled Continue off the bottom of Welcome and Where.
        body.addWidget(self.stack, 1)
        self.pages = [self._page_welcome(), self._page_where(), self._page_configure(),
                      self._page_test(), self._page_hotkey()]
        for p in self.pages:
            self.stack.addWidget(p)
        self.step = 0
        self.go(0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        place = getattr(self, "_fit_place_grip", None)
        if place:
            place()

    def showEvent(self, e):
        super().showEvent(e)
        # AFTER the pages are built: a flat resize() in __init__ is undone by the layout, and
        # on a smaller screen the footer (and Continue) then sits below the bottom edge of a
        # window with no draggable frame. Real Mac, 2026-09-15.
        if not getattr(self, '_fitted', False):
            self._fitted = True
            fit_to_screen(self, 1040, 660)
        round_window_corners(self)

    # ------------------------------------------------------------ shell
    def _shell(self, with_preview=False):
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        # The column is split in two on purpose:
        #
        #   [ scrollable content ]   <- grows, and scrolls when it does not fit
        #   [ footer             ]   <- pinned, ALWAYS visible
        #
        # An earlier fix put the WHOLE page inside one scroll area. That let the window shrink,
        # but the footer scrolled with everything else: Welcome and Where are 913px tall in a
        # 608px viewport, so Continue sat at y=862 and was simply not on screen. The operator's
        # words: "the continue button is gone from the wizard". The control a user needs in order
        # to make progress must never be the thing that scrolls out of reach.
        main = QWidget()
        mainv = QVBoxLayout(main)
        mainv.setContentsMargins(0, 0, 0, 0)
        mainv.setSpacing(0)

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(28, 24, 28, 10)
        v.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        # Without an explicit minimum a QScrollArea adopts its widget's, which is exactly what
        # stopped the window shrinking before.
        scroll.setMinimumSize(320, 180)
        mainv.addWidget(scroll, 1)

        foot_slot = QVBoxLayout()
        foot_slot.setContentsMargins(28, 6, 28, 18)
        mainv.addLayout(foot_slot)
        v._foot_slot = foot_slot        # _foot() puts the buttons here, outside the scroll

        h.addWidget(main, 58)
        prev = None
        if with_preview:
            pw = QFrame()
            pw.setObjectName("preview")
            # 360 made the Test page demand 1100px on its own, which does not fit a 12-inch
            # MacBook once the rail is added. The preview is a log and a command pane: it can be
            # narrow and scroll. The window fitting the screen matters more than this being wide.
            pw.setMinimumWidth(220)
            pv = QVBoxLayout(pw)
            pv.setContentsMargins(18, 18, 18, 18)
            prev = QTextEdit()
            prev.setReadOnly(True)
            prev.setLineWrapMode(QTextEdit.WidgetWidth)
            pv.addWidget(prev)
            h.addWidget(pw, 42)
        return page, v, prev

    def _heading(self, v, title, lead):
        t = _lbl(title, "h2")
        v.addWidget(t)
        l = _lbl(lead, "lead")
        v.addWidget(l)
        v.addSpacing(6)
        return t, l

    def _foot(self, v, back=True, primary="Continue", on_primary=None, extra=()):
        row = QHBoxLayout()
        b = QPushButton("Back")
        b.setObjectName("ghost")
        b.clicked.connect(lambda: self.go(self.step - 1))
        b.setVisible(back)
        row.addWidget(b)
        row.addStretch(1)
        for w in extra:
            row.addWidget(w)
        p = QPushButton(primary)
        p.setObjectName("pri")
        p.setCursor(Qt.PointingHandCursor)
        p.clicked.connect(on_primary or (lambda: self.go(self.step + 1)))
        row.addWidget(p)
        slot = getattr(v, "_foot_slot", None)
        if slot is not None:
            # Outside the scroll area, so it cannot scroll away. The stretch stays in the content
            # column so short pages still push their own body to the top.
            v.addStretch(1)
            slot.addLayout(row)
        else:
            v.addStretch(1)
            v.addLayout(row)
        return p

    def _detect_line(self):
        def b(x):
            return f"<b style='color:#e9ecf3'>{x}</b>"
        host = "This Mac" if S.IS_MAC else "This PC"
        # Naming the wrong accelerator is worse than naming none: DirectML does not exist on
        # a Mac and CoreML does not exist on Windows.
        gpu = (('CoreML ready' if self.hw.coreml else 'CoreML not installed') if S.IS_MAC
               else ('DirectX 12' if self.hw.directml else 'DirectML not installed'))
        return (f"{host}: {b(f'{self.hw.perf_cores} performance cores')} &nbsp;&nbsp; "
                f"GPU: {b(gpu)} &nbsp;&nbsp; "
                f"Docker: {b('found' if self.hw.docker else 'not found')} &nbsp;&nbsp; "
                f"NVIDIA: {b('found' if self.hw.nvidia else 'not found')}")

    def go(self, i):
        if self._busy or not 0 <= i < len(STEPS):
            return
        self._stop_mic()
        self.step = i
        self.stack.setCurrentIndex(i)
        for k, it in enumerate(self.steps):
            it.state = "done" if k < i else "current" if k == i else "future"
            it.update()
        self._update_rail_foot()
        if i == 2:
            self._show_install_rows(False)    # a fresh visit shows the engine picker again
            self._refresh_configure()
        if i == 3:
            self._run_checks()
        if i == 4:
            self._start_mic()

    def _update_rail_foot(self):
        self.rail_foot.setText(f"{S.OPTION_NAMES[self.c.option]}\n"
                               f"{'🔒 token' if self.c.option != 'local' else 'local only'}")

    # ------------------------------------------------------------ 0 welcome
    def _page_welcome(self):
        page, v, _ = self._shell()
        v.addSpacing(18)
        mark = QLabel()
        mark.setFixedSize(64, 12)
        mark.setStyleSheet(f"border-radius:6px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                           f"stop:0 {MINT},stop:0.45 {SKY},stop:0.8 {VIO},stop:1 {BLUSH});")
        v.addWidget(mark)
        v.addSpacing(10)
        self._heading(v, "Private dictation, on your hardware",
                      "WaveFlow types what you say into any app. Speech recognition runs on a machine you "
                      "choose — this PC, a server at home, or your own VPS. No cloud account.")
        v.addWidget(_divider())
        d = _lbl(f"Takes <b style='color:#e9ecf3'>about 2 minutes</b> &nbsp;&nbsp; Needs "
                 f"<b style='color:#e9ecf3'>~700 MB</b> for the model &nbsp;&nbsp; Change anything later in "
                 f"<b style='color:#e9ecf3'>⚙ → Setup</b>", "detect")
        d.setTextFormat(Qt.RichText)
        v.addWidget(d)
        self._foot(v, back=False, primary="Get started")
        return page

    # ------------------------------------------------------------ 1 where
    def _page_where(self):
        page, v, _ = self._shell()
        self._heading(v, "Where should speech recognition run?", "Audio only goes to the machine you pick.")
        grid = QGridLayout()
        grid.setSpacing(8)
        _here = "this Mac" if S.IS_MAC else "this PC"
        _gpu = "an Apple Silicon or Intel Mac GPU" if S.IS_MAC else "a DirectX 12 GPU"
        lines = {"local": f"Runs quietly with the app. No Docker. Any 4+ core CPU or {_gpu}.",
                 "docker": f"Engine in a container on {_here}. Docker Desktop.",
                 "onsite": "A box at home: LAN, Tailscale or VPN. Docker or Python venv.",
                 "vps": "Your own server on the internet. Docker, a domain, HTTPS.",
                 "connect": "It is already up. Setup installs nothing — it asks for the address "
                            "and the token, then tests them."}
        self.opt_cards = []
        for n, k in enumerate(S.INSTALL_OPTIONS):
            c = Card(S.OPTION_NAMES[k], lines[k], badge="Recommended" if k == "local" else "")
            c.setAutoExclusive(True)
            c.setChecked(k == self.c.option)
            c.clicked.connect(lambda _=False, n=n: self._set_option(n))
            grid.addWidget(c, n // 2, n % 2)
            self.opt_cards.append(c)
        # "connect" sits apart, under an "or", spanning both columns. The four cards above all mean
        # "install a server for me"; this one means the opposite. As a fifth card in the same grid
        # it read as a fifth flavour of install (mock A, operator pick 2026-09-15).
        row = (len(S.INSTALL_OPTIONS) + 1) // 2
        orx = _lbl("or", "detect")
        orx.setAlignment(Qt.AlignCenter)
        grid.addWidget(orx, row, 0, 1, 2)
        nc = S.OPTIONS.index("connect")
        cc = Card(S.OPTION_NAMES["connect"], lines["connect"], badge="No install")
        cc.setAutoExclusive(True)
        cc.setChecked(self.c.option == "connect")
        cc.clicked.connect(lambda _=False, n=nc: self._set_option(n))
        grid.addWidget(cc, row + 1, 0, 1, 2)
        self.opt_cards.append(cc)
        v.addLayout(grid)
        v.addSpacing(6)
        v.addWidget(_divider())
        d = _lbl(self._detect_line(), "detect")
        d.setTextFormat(Qt.RichText)
        v.addWidget(d)
        self._foot(v)
        return page

    def _set_option(self, n):
        was = self.c.option
        self.c.option = S.OPTIONS[n]
        if self.c.option == "vps" and self.c.address.startswith("http://"):
            self.c.address = ""
        # The token we generate is ours to put ON a server we install. For "connect" the server is
        # already running and only accepts the token IT was started with, so a pre-filled one is a
        # guaranteed 401 that looks like a typo. Clear it, and put a fresh one back on the way out.
        if self.c.option == "connect" and was != "connect":
            self._made_token, self.c.token = self.c.token, ""
        elif was == "connect" and self.c.option != "connect" and not self.c.token:
            self.c.token = getattr(self, "_made_token", "") or S.new_token()
        if hasattr(self, "tok"):
            self.tok.setText(self.c.token)
        self._update_rail_foot()

    # ------------------------------------------------------------ 2 configure
    def _page_configure(self):
        page, v, prev = self._shell(with_preview=True)
        self.cfg_preview = prev
        self.cfg_title, _ = self._heading(v, "", "The right side updates as you change things. "
                                                 "Nothing runs until you press the button.")

        self.addr_box = QWidget()
        al = QVBoxLayout(self.addr_box)
        al.setContentsMargins(0, 0, 0, 4)
        al.setSpacing(5)
        self.addr_label = _lbl("", "lbl")
        self.addr = QLineEdit(self.c.address)
        self.addr.textChanged.connect(lambda t: (setattr(self.c, "address", t), self._refresh_configure(False)))
        self.addr_hint = _lbl("", "hint")
        for w in (self.addr_label, self.addr, self.addr_hint):
            al.addWidget(w)
        # SSH target (mock wizard-onsite-ssh-v1 A): only for Onsite Docker and VPS, where setup installs.
        self.ssh_box = QWidget()
        sg = QGridLayout(self.ssh_box)
        sg.setContentsMargins(0, 4, 0, 0)
        sg.setHorizontalSpacing(10)
        sg.setVerticalSpacing(5)
        self.ssh_user_edit = QLineEdit(self.ssh_user)
        self.ssh_user_edit.setPlaceholderText("your user on the server")
        self.ssh_user_edit.textChanged.connect(lambda t: (setattr(self, "ssh_user", t.strip()),
                                                          self._refresh_configure(False)))
        self.ssh_folder_edit = QLineEdit(self.ssh_folder)
        self.ssh_folder_edit.textChanged.connect(lambda t: (setattr(self, "ssh_folder", t.strip()),
                                                            self._refresh_configure(False)))
        sg.addWidget(_lbl("SSH user", "lbl"), 0, 0)
        sg.addWidget(_lbl("Install folder on the server", "lbl"), 0, 1)
        sg.addWidget(self.ssh_user_edit, 1, 0)
        sg.addWidget(self.ssh_folder_edit, 1, 1)
        sg.addWidget(_lbl("Uses your SSH key from ~/.ssh. WaveFlow never asks for a password.", "hint"),
                     2, 0, 1, 2)
        al.addWidget(self.ssh_box)
        v.addWidget(self.addr_box)

        self.method_row = QWidget()
        mr = QHBoxLayout(self.method_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.addWidget(_lbl("Install method", "lbl", wrap=False))
        self.method = Segmented(["Docker", "Python venv"])
        self.method.changed.connect(self._set_method)
        mr.addWidget(self.method)
        mr.addStretch(1)
        v.addWidget(self.method_row)

        self.reach_row = QWidget()
        rr = QHBoxLayout(self.reach_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(_lbl("Reachable from", "lbl", wrap=False))
        self.reach = Segmented(["This Mac only" if S.IS_MAC else "This PC only", "My network"])
        self.reach.changed.connect(self._set_reach)
        rr.addWidget(self.reach)
        rr.addStretch(1)
        v.addWidget(self.reach_row)

        self.eng_label = _lbl("Engine", "lbl")
        v.addWidget(self.eng_label)
        self.eng_cards = []
        for i, _e in enumerate(S.ENGINES):
            c = Card()
            c.setAutoExclusive(True)
            c.clicked.connect(lambda _=False, i=i: self._set_engine(i))
            v.addWidget(c)
            self.eng_cards.append(c)
        # macOS needs no extra package: the official onnxruntime wheel already carries CoreML, so
        # this button only ever REPAIRS an install there and must not promise otherwise.
        self.gpu_install = QPushButton(
            "Reinstall onnxruntime  ·  adds CoreML" if S.IS_MAC
            else "Install GPU support  ·  onnxruntime-directml")
        self.gpu_install.clicked.connect(self._install_directml)
        v.addWidget(self.gpu_install)

        self.thr_box = QWidget()
        tb = QVBoxLayout(self.thr_box)
        tb.setContentsMargins(0, 4, 0, 0)
        tb.setSpacing(5)
        tb.addWidget(_lbl("CPU threads  ·  used by ONNX · CPU", "lbl"))
        tr = QHBoxLayout()
        self.thr = QSpinBox()
        self.thr.setRange(S.THREADS_MIN, S.THREADS_MAX)
        self.thr.setValue(S.clamp_threads(self.c.threads))
        self.thr.setFixedWidth(84)
        self.thr.valueChanged.connect(lambda n: (setattr(self.c, "threads", n), self._refresh_configure(False)))
        self.thr_auto = QCheckBox("Auto")
        self.thr_auto.setChecked(self.c.auto_threads)
        self.thr_auto.toggled.connect(self._set_auto_threads)
        self.thr_hint = _lbl("", "hint")
        self.thr_hint.setMinimumWidth(10)
        tr.addWidget(self.thr)
        tr.addWidget(self.thr_auto)
        tr.addWidget(self.thr_hint, 1)
        tb.addLayout(tr)
        v.addWidget(self.thr_box)

        self.tok_box = QWidget()
        tl = QVBoxLayout(self.tok_box)
        tl.setContentsMargins(0, 4, 0, 0)
        tl.setSpacing(5)
        tl.addWidget(_lbl("Token", "lbl"))
        trow = QHBoxLayout()
        trow.setSpacing(6)
        self.tok = QLineEdit(self.c.token)
        self.tok.setEchoMode(QLineEdit.Password)
        self.tok.textChanged.connect(lambda t: (setattr(self.c, "token", t.strip()), self._refresh_configure(False)))
        trow.addWidget(self.tok, 1)
        self.tok_buttons = {}
        for text, fn, checkable in (("Show", None, True), ("Copy", lambda: QGuiApplication.clipboard().setText(self.c.token), False),
                                    ("Paste", lambda: self.tok.setText((QGuiApplication.clipboard().text() or "").strip()), False),
                                    ("New", lambda: self.tok.setText(S.new_token()), False)):
            b = QPushButton(text)
            b.setObjectName("pill")
            b.setCheckable(checkable)
            if checkable:
                b.toggled.connect(lambda on: self.tok.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
            else:
                b.clicked.connect(fn)
            trow.addWidget(b)
            self.tok_buttons[text] = b
        tl.addLayout(trow)
        self.tok_hint = _lbl("", "hint")
        tl.addWidget(self.tok_hint)
        v.addWidget(self.tok_box)

        # Remote install checklist: replaces the engine picker while installing.
        self.install_box = QWidget()
        il = QVBoxLayout(self.install_box)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(2)
        self.install_rows = []
        for name in RI.STEPS:
            r = CheckRow()
            r.set(None, name, "")
            il.addWidget(r)
            self.install_rows.append(r)
        self.install_box.setVisible(False)
        v.addWidget(self.install_box)

        self.msg = _lbl("", "err")
        self.warn = _lbl("", "warn")
        self.progress = _lbl("", "hint")
        for w in (self.msg, self.warn, self.progress):
            v.addWidget(w)
        self.cmds_only = QPushButton("Show commands only")
        self.cmds_only.setObjectName("ghost")
        self.cmds_only.clicked.connect(lambda: self.go(3))
        self.cfg_primary = self._foot(v, primary="Continue", on_primary=self._configure_primary,
                                      extra=(self.cmds_only,))
        return page

    # ------------------------------------------------------------ remote install (onsite docker / vps)
    def _remote_install_mode(self) -> bool:
        return self.c.option == "vps" or (self.c.option == "onsite" and self.c.method == "docker")

    def _show_install_rows(self, on: bool):
        """While installing, the checklist takes the place of the engine, threads and token rows."""
        self._install_view = on          # a flag: isVisible() is False whenever the page itself is hidden
        self.install_box.setVisible(on)
        for w in [self.eng_label, self.thr_box, *self.eng_cards]:
            w.setVisible(not on)
        self.tok_box.setVisible(not on and self.c.option != "local")

    def _do_remote(self, _plan):
        c = S.Choices(**vars(self.c))
        ok, text = RI.install(c, self.ssh_user, self.ssh_folder,
                              lambda kind, *a: self.bus.step.emit(*a) if kind == "step" else self.bus.log.emit(*a))
        self.bus.done.emit(ok, text)

    def _on_step(self, i, state, detail):
        short = detail if len(detail) <= 30 else detail[:29] + "…"   # full text goes to the log / message
        self.install_rows[i].set(state, RI.STEPS[i], short)
        if state is False:
            self.progress.setText(detail)

    def _on_log(self, line):
        self._install_log = (self._install_log + [line])[-400:]
        body = "\n".join(html.escape(x) for x in self._install_log)
        self.cfg_preview.setHtml("<p style='color:#5d6477;font-size:10px;letter-spacing:1px;margin:0 0 6px 0'>"
                                 f"LIVE LOG</p><pre style='white-space:pre-wrap;margin:0'>{body}</pre>")
        sb = self.cfg_preview.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _install_directml(self):
        cmds = S.gpu_install_commands()
        shown = "\n".join(" ".join(c[2:]) for c in cmds)
        if QMessageBox.question(self, "Install GPU support",
                                f"This replaces the CPU build of ONNX Runtime with the DirectML build:\n\n"
                                f"{shown}\n\nThe CPU engine keeps working with it.") != QMessageBox.Yes:
            return
        if self.engine.running():
            self.engine.stop()          # its DLLs would block the swap

        def work(_plan):
            for c in cmds:
                self.bus.progress.emit("Running: " + " ".join(c[2:]))
                r = subprocess.run(c, capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0:
                    self.bus.done.emit(False, "Install failed: " + (r.stderr or r.stdout).strip()[-200:])
                    return
            self.hw = S.detect()
            self.bus.done.emit(False, "GPU support installed." if self.hw.directml else
                               "Installed, but DirectML is still not available on this PC.")
        self._run_async(work, None)

    def _set_method(self, i):
        self.c.method = ["docker", "venv"][i]
        self._refresh_configure()

    def _set_reach(self, i):
        self.c.lan = i == 1
        self._refresh_configure(False)

    def _set_engine(self, i):
        self.c.engine = S.ENGINES[i]
        self._refresh_configure()

    def _set_auto_threads(self, on):
        self.c.auto_threads = on
        if on:
            self.thr.setValue(self.hw.perf_cores)
        self.thr.setEnabled(not on)
        self._refresh_configure(False)

    def _refresh_configure(self, rebuild=True):
        c = self.c
        connect = c.option == "connect"
        self.cfg_title.setText(f"Configure — {S.OPTION_NAMES[c.option]}")
        self.addr_box.setVisible(c.option in S.REMOTE_OPTIONS)
        if connect:
            self.addr_label.setText("Server address")
            self.addr_hint.setText("Where WaveFlow should send audio. LAN name, IP, Tailscale name, "
                                   "or an https:// domain.")
            self.addr.setPlaceholderText("server.local:8756")
        elif c.option == "onsite":
            self.addr_label.setText("Server address")
            self.addr_hint.setText("LAN name, IP, or Tailscale name.")
            self.addr.setPlaceholderText("server.local:8756")
        else:
            self.addr_label.setText("Domain")
            self.addr_hint.setText("HTTPS only. The DNS name must point at the VPS first.")
            self.addr.setPlaceholderText("https://stt.example.com")
        self.method_row.setVisible(c.option == "onsite")
        self.method.set(0 if c.method == "docker" else 1)
        self.reach_row.setVisible(c.option == "docker")
        self.reach.set(1 if c.lan else 0)
        self.tok_box.setVisible(c.option != "local" and not self._install_view)
        # connect: the server already chose its engine and its thread count. Showing either control
        # would invite a change that does nothing, so both blocks go away entirely.
        self.eng_label.setVisible(not connect)
        self.thr_box.setVisible(not connect)
        for card in self.eng_cards:
            card.setVisible(not connect)
        # "New" would mint a token the running server has never heard of; "Paste" is what you
        # actually need here, because the real token lives on that server.
        self.tok_buttons["New"].setVisible(not connect)
        self.tok_buttons["Paste"].setVisible(connect)
        choices = S.engines_for(c.option, c.method, self.hw)
        if rebuild:
            valid = [e.engine for e in choices if e.available]
            if c.engine not in valid and valid:
                c.engine = valid[0]
        for card, e in zip(self.eng_cards, choices):
            card.set_content(e.label, e.detail, note=e.reason)
            card.setEnabled(e.available)
            card.setChecked(e.engine == c.engine)
        have_gpu = self.hw.coreml if S.IS_MAC else self.hw.directml
        need_dml = c.option == "local" and c.engine == "onnx-gpu" and not have_gpu
        # A frozen build has no install commands (pip cannot reach inside it), so no button.
        self.gpu_install.setVisible(need_dml and bool(S.gpu_install_commands()))
        self.thr.setEnabled(not c.auto_threads)
        self.thr_hint.setText(f"Auto = {self.hw.perf_cores} performance cores on this PC. More is slower on hybrid CPUs."
                              if c.option in ("local", "docker") else
                              "Set to the server's physical performance cores.")
        remote = self._remote_install_mode()
        self.tok_hint.setText(
            # connect is the one mode where the token is NOT ours to make. Offering a generated one
            # would guarantee a 401: the running server only accepts the token it was started with.
            "Paste the token your server was started with — its WAVEFLOW_TOKEN. "
            "Leave it empty if you started it without one." if connect else
            "Made for you. Install puts it on the server for you, and it is saved in Settings → Connection "
            "(Show / Copy) for reinstalling or reconnecting." if remote else
            "Made for you and saved in Settings → Connection. Put the same token in the server's "
            "WAVEFLOW_TOKEN (see the right side)." if c.option in ("onsite", "vps") else
            "Made for you. Setup writes it into Docker's settings and saves it in Settings → Connection.")
        self.ssh_box.setVisible(remote)
        self.cmds_only.setVisible(remote)
        errs = S.validate(c)
        if remote:
            errs += [e for e in RI.validate_target(self.ssh_user, RI.host_of(c.address), self.ssh_folder)
                     if not e.startswith("Enter the server address")]
        if need_dml:
            errs.append("Install GPU support first, or choose ONNX · CPU.")
        self.msg.setText("  ".join(errs))
        self.warn.setText("  ".join(S.warnings(c)))
        self.cfg_primary.setEnabled(not errs and not self._busy)
        if remote:
            self.cfg_primary.setText(f"Install on {RI.host_of(c.address) or 'server'}")
        else:
            self.cfg_primary.setText({"local": "Start engine", "docker": "Build & start"}.get(c.option, "Continue"))
        if self._install_view:
            return                      # the live log owns the preview during AND after an install attempt
        self.cfg_preview.setHtml(self._preview_html(S.build_plan(c) if not errs or c.option == "local" else None, errs))

    def _preview_html(self, plan, errs):
        def mask(s):
            return s.replace(self.c.token, "••••••••••••") if self.c.token else s

        def label(t):
            return f"<p style='color:#5d6477;font-size:10px;letter-spacing:1px;margin:0 0 6px 0'>{t}</p>"
        if plan is None:
            return label("NOT READY") + f"<p style='color:{WARN}'>{html.escape(' '.join(errs))}</p>"
        out = [label(plan.where.upper()), "<pre style='white-space:pre-wrap;margin:0 0 16px 0'>"]
        if plan.env_text:
            out.append(f"<span style='color:#5d6477'># {html.escape(plan.env_path)}</span>\n")
            for line in plan.env_text.splitlines():
                k, _, val = line.partition("=")
                col = BLUSH if "TOKEN" in k else MINT if k in ("HOST_BIND", "WAVEFLOW_DOMAIN") else SKY
                out.append(f"{k}=<span style='color:{col}'>{html.escape(mask(val))}</span>\n")
            out.append("\n")
        for cmd in plan.commands:
            out.append(html.escape(mask(cmd)) + "\n")
        out.append("</pre>" + label("APP CONFIG") + "<pre style='white-space:pre-wrap;margin:0'>")
        out.append(html.escape(mask(json.dumps(plan.config, indent=2))) + "</pre>")
        return "".join(out)

    def _configure_primary(self):
        c = self.c
        plan = S.build_plan(c)
        self.plan = plan
        if c.option == "local":
            self._run_async(self._do_local, plan)
        elif c.option == "docker":
            if QMessageBox.question(self, "Build & start",
                                    "Setup will write the .env file and run:\n\n" + plan.commands[0].replace(
                                        c.token, "…") + "\n\nThe first build can take several minutes.") \
                    != QMessageBox.Yes:
                return
            self._run_async(self._do_docker, plan)
        elif self._remote_install_mode():
            host = RI.host_of(c.address)
            if QMessageBox.question(self, f"Install on {host}",
                                    f"Setup will connect as {self.ssh_user}@{host} with your SSH key, copy the "
                                    f"server files to {self.ssh_folder}, write its .env with the token, and run:\n\n"
                                    + RI.compose_command(plan) + "\n\nThe first build can take several minutes.") \
                    != QMessageBox.Yes:
                return
            self._install_log = []
            for r, name in zip(self.install_rows, RI.STEPS):
                r.set(None, name, "")
            self._show_install_rows(True)
            self.progress.setText("")
            self._run_async(self._do_remote, plan)
        else:
            self.go(3)

    def _run_async(self, fn, plan):
        self._busy = True
        self.cfg_primary.setEnabled(False)
        threading.Thread(target=fn, args=(plan,), daemon=True).start()

    def _do_local(self, plan):
        st = self.engine.start(plan.config["engine"], plan.url)
        if st.startswith("cannot"):
            self.bus.done.emit(False, st)
            return
        self.bus.progress.emit("Starting the engine. The first start downloads the model — this can take a few minutes.")
        ok = self.engine.wait_ready(plan.url, tick=lambda line: self.bus.progress.emit(line))
        self.bus.done.emit(ok, "Engine ready." if ok else
                           f"The engine stopped. See {self.engine.log_path}: {self.engine.last_log_line()}")

    def _do_docker(self, plan):
        try:
            Path(plan.env_path).parent.mkdir(parents=True, exist_ok=True)
            Path(plan.env_path).write_text(plan.env_text, encoding="utf-8")
            p = subprocess.Popen(plan.commands[0], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in p.stdout:
                if line.strip():
                    self.bus.progress.emit(line.strip()[-160:])
            rc = p.wait()
        except Exception as e:
            self.bus.done.emit(False, f"Docker failed: {e}")
            return
        if rc != 0:
            self.bus.done.emit(False, "Docker returned an error — see the last line above.")
            return
        from local_engine import _health
        for _ in range(600):
            if _health(plan.url):
                self.bus.done.emit(True, "Container ready.")
                return
            time.sleep(1)
        self.bus.done.emit(False, "The container started but the engine did not become ready in 10 minutes.")

    def _on_progress(self, line):
        self.progress.setText(line)

    def _on_action_done(self, ok, text):
        self._busy = False
        self.progress.setText(text)
        self._refresh_configure(False)
        if self._install_view:
            self.cfg_primary.setText("Try again" if not ok else self.cfg_primary.text())
        if ok:
            self.go(3)

    # ------------------------------------------------------------ 3 test
    def _page_test(self):
        page, v, prev = self._shell(with_preview=True)
        self.test_preview = prev
        _, self.test_lead = self._heading(v, "Test connection", "")
        self.check_rows = []
        for _ in range(4):
            r = CheckRow()
            v.addWidget(r)
            self.check_rows.append(r)
        v.addSpacing(6)
        self.verdict = _lbl("", "lead")
        self.verdict.setStyleSheet("padding:10px 12px;border-radius:10px;")
        v.addWidget(self.verdict)
        again = QPushButton("Test again")
        again.clicked.connect(self._run_checks)
        self.skip = QPushButton("Save without testing")
        self.skip.setObjectName("ghost")
        self.skip.clicked.connect(lambda: self.go(4))
        self.test_next = self._foot(v, primary="Continue", on_primary=lambda: self.go(4), extra=(self.skip, again))
        return page

    def _run_checks(self):
        plan = S.build_plan(self.c)
        self.plan = plan
        self.test_lead.setText(f"{S.OPTION_NAMES[self.c.option]}  ·  {plan.url}"
                               + ("  ·  DNS and HTTPS certificate checked first" if self.c.option == "vps" else "")
                               + ("  ·  nothing was installed" if self.c.option == "connect" else ""))
        names = ["Server answers", "Engine ready", "Token accepted", "Sample clip transcribed"]
        for r, n in zip(self.check_rows, names):
            r.set(None, n, "…")
        self.verdict.setText("Testing…")
        self.verdict.setStyleSheet("padding:10px 12px;border-radius:10px;color:#8d94a6;")
        self.test_next.setEnabled(False)
        # connect keeps the escape hatch too: the server is meant to be up, but it can be rebooting,
        # and without this a temporarily-down box would leave setup impossible to finish.
        self.skip.setVisible(self.c.option in S.REMOTE_OPTIONS)
        # require_token asks "should a server with NO auth be treated as a failure?". For the
        # options we install, yes — we set the token, so a tokenless server means it did not take.
        # For "connect" it depends entirely on what the operator typed: a tokenless server he runs
        # himself is a legitimate setup, and failing it would be us refusing his own machine.
        remote = (bool(self.c.token) if self.c.option == "connect"
                  else self.c.option != "local")
        threading.Thread(target=lambda: self.bus.checks.emit(
            *S.run_checks(plan.url, plan.token, require_token=remote)), daemon=True).start()

    def _on_checks(self, checks, verdict):
        allok = all(ch.ok for ch in checks)
        for r, ch in zip(self.check_rows, checks):
            r.set(ch.ok, ch.name, ch.detail)
        self.verdict.setText(verdict)
        col, bg, bd = ((MINT, "rgba(55,224,200,0.06)", "rgba(55,224,200,0.25)") if allok else
                       (BAD, "rgba(255,107,127,0.06)", "rgba(255,107,127,0.3)"))
        self.verdict.setStyleSheet(f"padding:10px 12px;border-radius:10px;color:{col};background:{bg};border:1px solid {bd};")
        self.test_next.setEnabled(allok)
        tok = "(none)" if not self.plan.token else "••••••••"
        c3 = f"<span style='color:{MINT if checks[2].ok else BAD}'>{html.escape(checks[2].detail)}</span>"
        log = (f"<span style='color:#5d6477'>GET</span>  {html.escape(self.plan.url)}/health\n"
               f"     {html.escape(checks[0].detail)}  {html.escape(checks[1].detail)}\n\n"
               f"<span style='color:#5d6477'>POST</span> /v1/audio/transcriptions\n"
               f"     Authorization: Bearer <span style='color:{BLUSH}'>{tok}</span>\n"
               f"     {c3}  {html.escape(checks[3].detail)}")
        self.test_preview.setHtml("<p style='color:#5d6477;font-size:10px;letter-spacing:1px'>REQUEST LOG</p>"
                                  f"<pre style='white-space:pre-wrap'>{log}</pre>")

    # ------------------------------------------------------------ 4 hotkey, mic & look
    def _page_hotkey(self):
        page, v, _ = self._shell()
        self._heading(v, "Hotkey, microphone & look",
                      "Last step. Speak to see the level; the line marks where speech starts.")
        two = QHBoxLayout()
        two.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(_lbl("Show / dictate", "lbl"))
        self.hk = QKeySequenceEdit(QKeySequence(_to_qt(self.cfg.get("hotkey_show", "ctrl+alt+w"))))
        self.hk.setMaximumWidth(300)
        left.addWidget(self.hk)
        left.addSpacing(8)
        self.mic = MicPanel(self.cfg, self.devices_fn)
        left.addWidget(self.mic)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(_lbl("Look", "lbl"))
        self.skin = SkinPicker(self.cfg.get("skin", "aurora"))
        right.addWidget(self.skin)
        # macOS ONLY. Windows needs no permission to type into another app or to claim a hotkey;
        # macOS refuses both until they are granted, and refuses them SILENTLY — the app simply
        # does nothing. Putting this next to the hotkey field is deliberate: that is the control
        # that stops working without Input Monitoring.
        # Allow buttons make macOS itself ask; rows turn green on their own (panels.PermissionPanel).
        self.perm_box = QWidget()
        pv = QVBoxLayout(self.perm_box)
        pv.setContentsMargins(0, 14, 0, 0)
        if S.IS_MAC:
            self.perms = PermissionPanel()
            pv.addWidget(self.perms)
        right.addWidget(self.perm_box)
        self.perm_box.setVisible(S.IS_MAC)
        right.addStretch(1)
        two.addLayout(left, 1)
        two.addLayout(right, 1)
        v.addLayout(two)
        self._foot(v, primary="Finish", on_primary=self._finish)
        return page

    def _start_mic(self):
        if self.step == 4:
            self.mic.start()

    def _stop_mic(self):
        if getattr(self, "mic", None) is not None:
            self.mic.stop()

    def _finish(self):
        self._stop_mic()
        plan = S.build_plan(self.c)
        out = dict(self.cfg)
        out.update(plan.config)
        if self._remote_install_mode():
            out["engine"] = {**out["engine"], "ssh_user": self.ssh_user, "folder": self.ssh_folder}
        out["hotkey_show"] = _from_qt(self.hk.keySequence().toString())
        out["device_name"] = self.mic.device_name()
        out["mic_sensitivity"] = self.mic.sensitivity()
        out["skin"] = self.skin.value()
        out["setup_done"] = True
        self.result_cfg = out
        self.accept()

    def reject(self):
        if self._busy:
            return                      # a build/start is running; closing would orphan it silently
        self._stop_mic()
        super().reject()


def _to_qt(kb: str) -> str:
    return S.hotkey_to_qt(kb)


def _from_qt(seq: str) -> str:
    return S.qt_to_hotkey(seq) or "ctrl+alt+w"
