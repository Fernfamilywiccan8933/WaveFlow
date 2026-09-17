"""Settings window — design/mocks/settings-icons-v1.html layout A (side menu), picked 2026-09-14.

Pages: Connection · Engine · Microphone · Hotkey & look · Advanced · Uninstall. Everything except
token rotation and uninstall is applied when Save is pressed. Token rotation changes the server at
once, so its new token is handed to the app right away (apply_now), even if Settings is cancelled.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import threading
import time

# requests is imported HERE, on the main thread, not inside the worker that uses it.
# A first-time import on a background thread while the main thread is busy with Qt is a
# race, and it segfaults: caught 2026-09-16 when a test opened Settings (which starts a
# health-check thread) and then drove the wizard. The real app happens to be safe today
# only because waveflow imports stt, which imports requests, before any of this runs —
# which is luck, not design. requests is a hard dependency either way, so there is
# nothing to gain by deferring it.
import requests
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QKeySequenceEdit,
                               QScrollArea,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout,
                               QWidget)

import setup_logic as S
from panels import MicPanel, PermissionPanel, SkinPicker, UninstallPanel, lbl
from wizard import QSS as WIZARD_QSS
from wizard import _from_qt, _to_qt
from wizard_ui import (BAD, MINT, Card, NavItem, Segmented, TitleBar, Toggle, fit_to_screen,
                       round_window_corners)

VERSION = "1.0.0"
PAGES = [("conn", "Connection"), ("eng", "Engine"), ("mic", "Microphone"), ("look", "Hotkey & look"),
         ("adv", "Advanced"), ("un", "Uninstall")]
QSS = WIZARD_QSS.replace("QDialog#wizard", "QDialog#settings") + """
#card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.08);border-radius:10px;}
QLabel#h4{color:#5d6477;font-size:11px;letter-spacing:1px;font-weight:600;}
QLabel#rowt{color:#e9ecf3;font-size:13px;}
QLabel#statuspill{border:1px solid rgba(255,255,255,0.09);border-radius:10px;padding:2px 8px;font-size:11px;color:#8d94a6;}
QDoubleSpinBox{background:#0c0f15;border:1px solid rgba(255,255,255,0.1);border-radius:7px;padding:6px 8px;}
"""
VOCAB_USER = S.ROOT / "server" / "vocab.user.json"
VOCAB_EXAMPLE = S.ROOT / "server" / "vocab.example.json"


class _Bus(QObject):
    checks = Signal(list, str)
    health = Signal(bool, int)
    rotated = Signal(bool, str, str)     # ok, message, new token


def card(title: str, right_widget=None):
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(8)
    head = QHBoxLayout()
    head.addWidget(lbl(title.upper(), "h4", wrap=False))
    head.addStretch(1)
    if right_widget is not None:
        head.addWidget(right_widget)
    v.addLayout(head)
    return f, v


def row(v, title, hint="", widget=None, stretch_widget=False):
    h = QHBoxLayout()
    h.setSpacing(10)
    t = QVBoxLayout()
    t.setSpacing(1)
    t.addWidget(lbl(title, "rowt"))
    if hint:
        t.addWidget(lbl(hint, "hint"))
    h.addLayout(t, 0 if stretch_widget else 1)
    if widget is not None:
        h.addWidget(widget, 1 if stretch_widget else 0)
    v.addLayout(h)
    return h


def _health_ms(url: str, token: str) -> tuple[bool, int]:
    from stt import auth_headers
    t0 = time.time()
    try:
        ok = requests.get(f"{url}/health", headers=auth_headers(token), timeout=3).json().get("status") == "ok"
    except Exception:
        ok = False
    return ok, int((time.time() - t0) * 1000)


class SettingsWindow(QDialog):
    def __init__(self, cfg: dict, engine=None, devices_fn=None, apply_now=None, open_setup=None, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self.setWindowTitle("WaveFlow Settings")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setStyleSheet(QSS)
        self.resize(1000, 640)
        self.cfg = dict(cfg)
        self.engine, self.devices_fn = engine, devices_fn
        self.apply_now = apply_now or (lambda _d: None)
        self.open_setup = open_setup
        self.result_cfg: dict | None = None
        self.quit_requested = False
        self.c = S.choices_from_config(self.cfg)
        self.hw = S.detect()
        self.bus = _Bus()
        self.bus.checks.connect(self._on_checks)
        self.bus.health.connect(self._on_health)
        self.bus.rotated.connect(self._on_rotated)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self, "WaveFlow Settings"))
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
        rail.setMaximumWidth(200)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(10, 16, 10, 14)
        rl.setSpacing(2)
        self.nav = {}
        for key, name in PAGES:
            it = NavItem(key, name, danger=key == "un")
            it.clicked.connect(lambda _=False, k=key: self.go(k))
            rl.addWidget(it)
            self.nav[key] = it
        rl.addStretch(1)
        self.status = lbl("checking engine…", "statuspill", wrap=False)
        rl.addWidget(self.status)
        rl.addWidget(lbl(f"v{VERSION}", "hint"))
        again = QPushButton("Run setup again…")
        again.setObjectName("ghost")
        again.setCursor(Qt.PointingHandCursor)
        again.clicked.connect(self._run_setup)
        rl.addWidget(again)
        body.addWidget(rail)

        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(26, 22, 26, 18)
        mv.setSpacing(4)
        self.title = lbl("", "h2")
        self.lead = lbl("", "lead")
        mv.addWidget(self.title)
        mv.addWidget(self.lead)
        mv.addSpacing(10)
        self.stack = QStackedWidget()
        # A QStackedWidget reports the LARGEST minimum of all its pages, and those pages made it
        # 1130x870 — bigger than the window ever asked for, so resize() was silently ignored and
        # on a smaller screen the footer (and Continue) sat below the bottom edge with no
        # draggable frame to fix it. Reported from a real Mac, 2026-09-15.
        # A scroll area breaks that chain: the window may now be any size, and content that no
        # longer fits scrolls instead of shoving the window off the screen.
        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QFrame.NoFrame)
        _scroll.setWidget(self.stack)
        _scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # With widgetResizable a QScrollArea adopts its widget's minimum as its own, which
        # defeats the whole point: the window still could not shrink. An explicit small
        # minimum is what actually lets content be larger than the view and scroll.
        _scroll.setMinimumSize(360, 240)
        mv.addWidget(_scroll, 1)
        self.foot = QWidget()
        fl = QHBoxLayout(self.foot)
        fl.setContentsMargins(0, 12, 0, 0)
        fl.addWidget(lbl("Changes apply when you press Save.", "hint", wrap=False))
        fl.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("pri")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        fl.addWidget(cancel)
        fl.addWidget(save)
        mv.addWidget(self.foot)
        body.addWidget(main, 1)

        self.leads = {"conn": "Where the app sends your voice.",
                      "eng": "Which speech engine runs, and how hard it works.",
                      "mic": "Which mic, and how loud you must talk before it listens.",
                      "look": "How you start talking, and what the overlay looks like.",
                      "adv": "Things most people never change.",
                      "un": "Removes only what WaveFlow made. Pick what goes."}
        builders = {"conn": self._page_conn, "eng": self._page_eng, "mic": self._page_mic,
                    "look": self._page_look, "adv": self._page_adv, "un": self._page_un}
        self.page_index = {}
        for key, _ in PAGES:
            self.page_index[key] = self.stack.addWidget(builders[key]())
        self.go("conn")
        self._check_health()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        place = getattr(self, "_fit_place_grip", None)
        if place:
            place()

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, '_fitted', False):
            self._fitted = True
            fit_to_screen(self, 1000, 640)
        round_window_corners(self)

    def go(self, key):
        self.nav[key].setChecked(True)
        self.title.setText(dict(PAGES)[key])
        self.lead.setText(self.leads[key])
        self.stack.setCurrentIndex(self.page_index[key])
        self.foot.setVisible(key != "un")
        if key == "mic":
            self.mic.start()
        else:
            self.mic.stop()
        if key == "un":
            self.uninstall.refresh()

    def _grid(self):
        page = QWidget()
        g = QGridLayout(page)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(14)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        return page, g

    # ------------------------------------------------------------ connection
    def _page_conn(self):
        page, g = self._grid()
        self.conn_pill = lbl("checking…", "statuspill", wrap=False)
        f, v = card("Server", self.conn_pill)
        row(v, "Runs on", "chosen in setup", lbl(S.OPTION_NAMES[self.c.option], "rowt", wrap=False))
        self.addr = QLineEdit(self.cfg.get("url", ""))
        remote = self.c.option in S.REMOTE_OPTIONS
        self.addr.setReadOnly(not remote)
        opn = QPushButton("Open")
        opn.setObjectName("pill")
        opn.setToolTip("Open the address in your browser. It shows a short status, not a web page.")
        opn.clicked.connect(self._open_address)
        ah = row(v, "Address", "", self.addr, stretch_widget=True)
        ah.addWidget(opn)
        v.addWidget(lbl("Where the app sends your voice. The engine listens at this address; it has no web page. "
                        + ("Edit it if your server moved, then Test now and Save." if remote else
                           "It is set by where the engine runs. To change that, use Run setup again…"), "hint"))
        test = QPushButton("Test now")
        test.clicked.connect(self._run_checks)
        row(v, "Test connection", "4 checks: reach, engine, token, text", test)
        self.check_text = lbl("", "hint")
        self.check_text.setTextFormat(Qt.RichText)
        v.addWidget(self.check_text)
        v.addStretch(1)
        g.addWidget(f, 0, 0)

        f, v = card("Access token")
        if self.c.option == "local":
            _h = "Mac" if S.IS_MAC else "PC"
            v.addWidget(lbl(f"This {_h} mode listens only on this {_h}, so it needs no token.", "hint"))
        else:
            has_token = bool(self.cfg.get("token"))
            if not has_token:
                # An empty masked box read as "the token is missing / broken" (operator, 2026-09-15). A
                # server without a token is a real setup (his home server also serves another tokenless
                # client), so say what it means instead.
                v.addWidget(lbl("<b style='color:#ffc46b'>This server has no token.</b> Anyone who can reach "
                                "it on your network can use it. Fine on a private home network; add a token "
                                "if other people or devices share that network.", "warnbox"))
            tok_row = QWidget()
            tr = QHBoxLayout(tok_row)
            tr.setContentsMargins(0, 0, 0, 0)
            tok_row.setVisible(has_token)
            self.tok = QLineEdit(self.cfg.get("token", ""))
            self.tok.setReadOnly(True)
            self.tok.setEchoMode(QLineEdit.Password)
            tr.addWidget(self.tok, 1)
            show = QPushButton("Show")
            show.setObjectName("pill")
            show.setCheckable(True)
            show.toggled.connect(lambda on: self.tok.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
            copy = QPushButton("Copy")
            copy.setObjectName("pill")
            copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cfg.get("token", "")))
            tr.addWidget(show)
            tr.addWidget(copy)
            v.addWidget(tok_row)
            self.tok_row = tok_row
            self.rotate_btn = QPushButton("Rotate…" if has_token else "Add a token…")
            self.rotate_btn.clicked.connect(self._rotate)
            if has_token:
                row(v, "Rotate token", "makes a new token; the old one stops working", self.rotate_btn)
            else:
                row(v, "Add a token", "every app that uses this server will then need the same token",
                    self.rotate_btn)
            self.rotate_msg = lbl("", "hint")
            v.addWidget(self.rotate_msg)
            v.addWidget(lbl("<b style='color:#e9ecf3'>Docker on this PC:</b> Rotate writes the new token and "
                            "restarts the container for you.<br><b style='color:#e9ecf3'>Onsite / VPS:</b> Rotate "
                            "shows the commands to run on the server, then saves the token when you press Done.",
                            "hint"))
        v.addStretch(1)
        g.addWidget(f, 0, 1)
        g.setRowStretch(1, 1)
        return page

    def _check_health(self):
        url, tok = self.cfg.get("url", ""), self.cfg.get("token", "")
        threading.Thread(target=lambda: self.bus.health.emit(*_health_ms(url, tok)), daemon=True).start()

    def _on_health(self, ok, ms):
        text = f"● Engine ready · {ms} ms" if ok else "● Engine offline"
        style = f"color:{MINT};" if ok else f"color:{BAD};"
        for w in (self.status, self.conn_pill):
            w.setText(text if w is self.status else ("connected" if ok else "offline"))
            w.setStyleSheet(style)

    def _address(self) -> str:
        if self.c.option not in S.REMOTE_OPTIONS:
            return self.cfg.get("url", "")
        port = S.DOCKER_SERVICE.get(self.c.engine, ("", 8756))[1]
        return S.normalize_url(self.addr.text(), https=self.c.option == "vps", default_port=port) \
            or self.cfg.get("url", "")

    def _open_address(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self._address() + "/"))

    def _run_checks(self):
        self.check_text.setText("Testing…")
        url, tok, remote = self._address(), self.cfg.get("token", ""), self.c.option != "local"
        threading.Thread(target=lambda: self.bus.checks.emit(*S.run_checks(url, tok, require_token=remote)),
                         daemon=True).start()

    def _on_checks(self, checks, verdict):
        lines = [f"<span style='color:{MINT if ch.ok else BAD}'>{'✓' if ch.ok else '✕'}</span> "
                 f"{html.escape(ch.name)} — {html.escape(ch.detail)}" for ch in checks]
        self.check_text.setText("<br>".join(lines) + f"<br><b>{html.escape(verdict)}</b>")
        self._check_health()

    def _rotate(self):
        new = S.new_token()
        rot = S.rotation_plan(self.cfg, new)
        if rot.kind == "here":
            if QMessageBox.question(self, "Rotate token",
                                    "WaveFlow will write a new token and restart its container:\n\n"
                                    + rot.commands[0] + "\n\nDictation pauses for a few seconds.") != QMessageBox.Yes:
                return
            self.rotate_btn.setEnabled(False)
            self.rotate_msg.setText("Restarting the container…")
            threading.Thread(target=self._rotate_here, args=(rot, new), daemon=True).start()
            return
        dlg = QDialog(self)
        dlg.setObjectName("settings")
        dlg.setStyleSheet(QSS)
        dlg.setWindowTitle("Rotate token on your server")
        v = QVBoxLayout(dlg)
        v.addWidget(lbl("Run these on your server. The old token stops working once the server restarts.", "lead"))
        t = QTextEdit()
        t.setReadOnly(True)
        t.setPlainText("\n".join(rot.commands))
        t.setMinimumSize(560, 120)
        v.addWidget(t)
        h = QHBoxLayout()
        cp = QPushButton("Copy")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText("\n".join(rot.commands)))
        h.addWidget(cp)
        h.addStretch(1)
        no = QPushButton("Cancel")
        no.clicked.connect(dlg.reject)
        done = QPushButton("Done — I restarted it")
        done.setObjectName("pri")
        done.clicked.connect(dlg.accept)
        h.addWidget(no)
        h.addWidget(done)
        v.addLayout(h)
        if dlg.exec() == QDialog.Accepted:
            self._on_rotated(True, "New token saved. Testing the connection…", new)

    def _rotate_here(self, rot, new):
        try:
            Path(rot.env_path).parent.mkdir(parents=True, exist_ok=True)
            Path(rot.env_path).write_text(rot.env_text, encoding="utf-8")
            r = subprocess.run(rot.commands[0], shell=True, capture_output=True, text=True, errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                self.bus.rotated.emit(False, "Docker error: " + (r.stderr or r.stdout).strip()[-200:], "")
                return
        except Exception as e:
            self.bus.rotated.emit(False, f"Rotate failed: {e}", "")
            return
        for _ in range(120):
            if _health_ms(self.cfg.get("url", ""), new)[0]:
                break
            time.sleep(1)
        self.bus.rotated.emit(True, "New token active. Testing the connection…", new)

    def _on_rotated(self, ok, msg, new):
        if hasattr(self, "rotate_btn"):
            self.rotate_btn.setEnabled(True)
        self.rotate_msg.setText(msg)
        if not ok:
            return
        self.cfg["token"] = new
        self.tok.setText(new)
        self.tok_row.setVisible(True)
        self.apply_now({"token": new})       # the server already changed: never leave the app on the old one
        self._run_checks()

    # ------------------------------------------------------------ engine
    def _page_eng(self):
        page, g = self._grid()
        f, v = card("Engine")
        editable = self.c.option == "local"
        self.eng_cards = []
        for e in S.engines_for(self.c.option, self.c.method, self.hw):
            cd = Card(e.label, e.detail, badge="in use" if e.engine == self.c.engine else "", note=e.reason)
            cd.setAutoExclusive(True)
            cd.setChecked(e.engine == self.c.engine)
            cd.setEnabled(editable and e.available)
            cd.clicked.connect(lambda _=False, k=e.engine: setattr(self.c, "engine", k))
            v.addWidget(cd)
            self.eng_cards.append(cd)
        if self.c.option == "connect":
            # engines_for() returned nothing on purpose — see setup_logic. Saying "use Run setup
            # again" here would be a lie: setup cannot change an engine on a server it never touched.
            v.addWidget(lbl("You run this server yourself, so it chose its own engine. WaveFlow only "
                            "sends audio to it. To change the engine, change it on that server.", "warn"))
        elif not editable:
            v.addWidget(lbl("This engine runs in Docker or on your server. To change it, use Run setup again…",
                            "warn"))
        v.addStretch(1)
        g.addWidget(f, 0, 0)

        f, v = card("CPU threads")
        self.auto = Toggle(self.c.auto_threads)
        row(v, "Auto", f"{self.hw.perf_cores} = your performance cores (of {self.hw.all_cores})", self.auto)
        self.thr = QSpinBox()
        self.thr.setRange(S.THREADS_MIN, S.THREADS_MAX)
        self.thr.setValue(S.clamp_threads(self.c.threads))
        self.thr.setFixedWidth(90)
        row(v, "Threads", "1–64. More threads is not always faster.", self.thr)
        self.auto.toggled.connect(self._auto_threads)
        self._auto_threads(self.c.auto_threads)
        for w in (self.auto, self.thr):
            w.setEnabled(editable and w.isEnabled())
        v.addWidget(lbl("Changing the engine or threads restarts the engine when you press Save.", "hint"))
        v.addStretch(1)
        g.addWidget(f, 0, 1)
        g.setRowStretch(1, 1)
        return page

    def _auto_threads(self, on):
        if on:
            self.thr.setValue(self.hw.perf_cores)
        self.thr.setEnabled(not on)

    # ------------------------------------------------------------ microphone
    def _page_mic(self):
        page, g = self._grid()
        f, v = card("Microphone and sensitivity")
        self.mic = MicPanel(self.cfg, self.devices_fn)
        v.addWidget(self.mic)
        v.addStretch(1)
        g.addWidget(f, 0, 0, 1, 2)
        g.setRowStretch(1, 1)
        return page

    # ------------------------------------------------------------ hotkey & look
    def _page_look(self):
        page, g = self._grid()
        f, v = card("Hotkey")
        hk = QHBoxLayout()
        self.hk = QKeySequenceEdit(QKeySequence(_to_qt(self.cfg.get("hotkey_show", "ctrl+alt+w"))))
        hk.addWidget(self.hk, 1)
        clr = QPushButton("Clear")
        clr.setObjectName("pill")
        clr.clicked.connect(self.hk.clear)
        hk.addWidget(clr)
        v.addWidget(lbl("Show / dictate", "lbl"))
        v.addLayout(hk)
        self.mode = Segmented(["Live", "Burst"])
        self.mode.set(0 if self.cfg.get("live_mode", True) else 1)
        row(v, "Mode", "Live types as you talk; Burst types when you stop", self.mode)
        self.silence = QDoubleSpinBox()
        self.silence.setRange(2.0, 60.0)
        self.silence.setSingleStep(0.5)
        self.silence.setSuffix(" s")
        self.silence.setValue(float(self.cfg.get("silence_commit_s", 6.0)))
        row(v, "Stop after silence", "the overlay closes after this much quiet", self.silence)
        self.autostart = Toggle(S.autostart_enabled())
        # macOS has no registry Run key: osbridge writes a LaunchAgent in the user's own
        # ~/Library/LaunchAgents, so the promise "only for your account" holds on both.
        _boot = "Start at login" if S.IS_MAC else "Start with Windows"
        _who = "only for your macOS account" if S.IS_MAC else "only for your Windows account"
        row(v, _boot, _who, self.autostart)
        v.addStretch(1)
        g.addWidget(f, 0, 0)
        f, v = card("Overlay skin")
        self.skin = SkinPicker(self.cfg.get("skin", "aurora"))
        v.addWidget(self.skin)
        # v4 adaptive glass. "System" follows the OS APPEARANCE setting, not the wallpaper —
        # a dark OS theme over a light wallpaper stays dark, which is why Dark and Light exist
        # as overrides at all.
        cur = self.cfg.get("pill_theme", "system")
        self.pill_theme = Segmented(["System", "Dark", "Light"])
        try:
            self.pill_theme.set(["system", "dark", "light"].index(cur))
        except ValueError:
            self.pill_theme.set(0)
        row(v, "Pill appearance", "the glass takes the colour of what is behind it",
            self.pill_theme)
        v.addWidget(lbl(f"System follows your {'macOS' if S.IS_MAC else 'Windows'} light/dark "
                        "setting. If your wallpaper is light but your theme is dark, pick Light "
                        "here.", "hint"))
        v.addStretch(1)
        g.addWidget(f, 0, 1)
        if S.IS_MAC:
            # The same live panel as setup: permissions can be revoked or go stale after setup.
            f, v = card("Permissions")
            self.perms = PermissionPanel()
            v.addWidget(self.perms)
            g.addWidget(f, 1, 0, 1, 2)
            g.setRowStretch(2, 1)
        else:
            g.setRowStretch(1, 1)
        return page

    # ------------------------------------------------------------ advanced
    def _page_adv(self):
        page, g = self._grid()
        f, v = card("Debug recording")
        self.record = Toggle(bool(self.cfg.get("record")))
        row(v, "Save my audio clips", "off by default · for bug reports only", self.record)
        row(v, "Folder", str(S.app_data() / "recordings"))
        logs = QPushButton("Open")
        logs.clicked.connect(lambda: _open(S.log_dir()))
        row(v, "Log folder", "waveflow.log", logs)
        v.addStretch(1)
        g.addWidget(f, 0, 0)
        f, v = card("Vocabulary")
        edit = QPushButton("Edit…")
        edit.clicked.connect(self._edit_vocab)
        row(v, "Personal words", "names and terms the engine should spell your way", edit)
        self.vocab_count = lbl("", "rowt", wrap=False)
        row(v, "Words loaded", "read by the engine when it starts", self.vocab_count)
        v.addWidget(lbl("This file is used by an engine on this PC. A server elsewhere reads its own "
                        "server/vocab.user.json.", "hint"))
        v.addStretch(1)
        g.addWidget(f, 0, 1)
        g.setRowStretch(1, 1)
        self._count_vocab()
        return page

    def _count_vocab(self):
        try:
            data = json.loads(VOCAB_USER.read_text(encoding="utf-8"))
            self.vocab_count.setText(str(sum(1 for k in data if not k.startswith("_"))))
        except FileNotFoundError:
            self.vocab_count.setText("0")
        except Exception:
            self.vocab_count.setText("file has an error")

    def _edit_vocab(self):
        if not VOCAB_USER.exists() and VOCAB_EXAMPLE.exists():
            shutil.copyfile(VOCAB_EXAMPLE, VOCAB_USER)
        _open(VOCAB_USER)

    # ------------------------------------------------------------ uninstall
    def _page_un(self):
        self.uninstall = UninstallPanel(self.cfg, self.engine)
        self.uninstall.finished.connect(self._uninstalled)
        return self.uninstall

    def _uninstalled(self, quit_app):
        if quit_app:
            self.quit_requested = True
            self.reject()

    # ------------------------------------------------------------ save
    def _run_setup(self):
        if self.open_setup:
            self.mic.stop()
            self.reject()
            self.open_setup()

    def collect(self) -> dict:
        out = dict(self.cfg)
        eng = dict(out.get("engine") or {})
        if self.c.option == "local":
            eng.update({"mode": "local", "engine": self.c.engine, "auto_threads": self.auto.isChecked(),
                        "threads": S.clamp_threads(self.thr.value()),
                        # the GPU device is per-OS: DirectML does not exist on a Mac
                        "device": ("coreml" if S.IS_MAC else "dml") if self.c.engine == "onnx-gpu" else "cpu"})
            out["engine"] = eng
        out["hotkey_show"] = _from_qt(self.hk.keySequence().toString())
        out["device_name"] = self.mic.device_name()
        out["mic_sensitivity"] = self.mic.sensitivity()
        out["live_mode"] = self.mode.index() == 0
        out["silence_commit_s"] = round(self.silence.value(), 1)
        out["skin"] = self.skin.value()
        out["pill_theme"] = ["system", "dark", "light"][self.pill_theme.index()]
        out["url"] = self._address()
        if self.record.isChecked():
            out["record"] = out.get("record") or str(S.app_data() / "recordings")
        else:
            out.pop("record", None)
        return out

    def _save(self):
        # Refuse a hotkey this OS cannot register BEFORE saving it, instead of letting the app
        # start with a dead hotkey and a misleading reason in the log.
        import osbridge
        hk = _from_qt(self.hk.keySequence().toString())
        if not osbridge.hotkey_supported(hk):
            QMessageBox.warning(self, "Hotkey", f"'{hk}' can't be used as a hotkey here. "
                                "Pick a letter, number, F-key or Space with modifiers.")
            return
        self.result_cfg = self.collect()
        try:
            if self.autostart.isChecked() != S.autostart_enabled():
                S.set_autostart(self.autostart.isChecked())
        except OSError as e:
            QMessageBox.warning(self, "Start at login" if S.IS_MAC else "Start with Windows",
                                f"Could not change it: {e}")
        self.mic.stop()
        self.accept()

    def reject(self):
        self.mic.stop()
        super().reject()


def _open(path: Path):
    try:
        os.startfile(str(path))            # noqa: S606 — opens with the user's own default app
    except Exception:
        pass
