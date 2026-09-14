"""First-run setup wizard — design: A+C hybrid (step rail + live preview), picked 2026-09-14.

Steps: Welcome · Where it runs · Configure · Test connection · Hotkey & mic.
Configure and Test show a live preview on the right: the exact commands / .env / config that will
be written or run, and the raw request log. Nothing runs until the primary button is pressed.
All rules (engine matrix, threads, validation, plan, checks) live in setup_logic.py.
"""
from __future__ import annotations

import html
import subprocess
import textwrap
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QStackedWidget, QTextEdit,
                               QVBoxLayout, QWidget)

import setup_logic as S

STEPS = ["Welcome", "Where it runs", "Configure", "Test connection", "Hotkey & mic"]
MINT, SKY, VIO, BLUSH, BAD, WARN = "#37e0c8", "#57c8ff", "#8a7bff", "#ff7bc8", "#ff6b7f", "#ffc46b"

QSS = f"""
QDialog{{background:#12151c;}}
QWidget{{color:#e9ecf3;font-family:'Segoe UI Variable Text','Segoe UI';font-size:13px;}}
#rail{{background:#0d1016;border-right:1px solid rgba(255,255,255,0.08);}}
#preview{{background:#07090d;border-left:1px solid rgba(255,255,255,0.08);}}
QLabel#h2{{font-size:18px;font-weight:600;}}
QLabel#lead,QLabel#hint{{color:#8d94a6;}} QLabel#hint{{font-size:11.5px;}}
QLabel#lbl{{color:#5d6477;font-size:10.5px;letter-spacing:1px;}}
QLabel#err{{color:{BAD};}} QLabel#warn{{color:{WARN};}}
QPushButton{{background:#222632;border:1px solid rgba(255,255,255,0.16);border-radius:8px;padding:7px 14px;}}
QPushButton:hover{{border-color:rgba(255,255,255,0.3);}}
QPushButton:disabled{{color:#5d6477;border-color:rgba(255,255,255,0.06);}}
QPushButton#pri{{background:#3aa9e6;border:none;color:#04121c;font-weight:600;}}
QPushButton#pri:disabled{{background:#23445a;color:#6d8797;}}
QPushButton#ghost{{background:transparent;border:none;color:#8d94a6;}}
QPushButton#step{{background:transparent;border:none;text-align:left;padding:9px 10px;color:#8d94a6;}}
QPushButton#step:checked{{background:rgba(255,255,255,0.05);color:#e9ecf3;}}
QPushButton#card{{text-align:left;padding:12px 14px;border:1px solid rgba(255,255,255,0.09);background:rgba(255,255,255,0.02);border-radius:10px;}}
QPushButton#card:checked{{border:1.5px solid {SKY};}}
QPushButton#card:disabled{{color:#5d6477;}}
QLineEdit,QSpinBox,QComboBox,QKeySequenceEdit{{background:#0c0f15;border:1px solid rgba(255,255,255,0.1);border-radius:7px;padding:6px 8px;}}
QTextEdit{{background:#07090d;border:none;font-family:'Cascadia Code',Consolas;font-size:12px;}}
QCheckBox{{color:#c9cfdc;}}
QProgressBar{{background:rgba(255,255,255,0.06);border:none;border-radius:4px;height:8px;}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {MINT},stop:0.5 {SKY},stop:1 {VIO});border-radius:4px;}}
"""


def _lbl(text, name=None, wrap=True):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def _seg(labels, on_change, parent):
    box = QWidget(parent)
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    grp = QButtonGroup(box)
    grp.setExclusive(True)
    for i, t in enumerate(labels):
        b = QPushButton(t)
        b.setCheckable(True)
        b.setObjectName("card")
        b.setMinimumWidth(len(t) * 8 + 44)      # stylesheet font/padding is not applied yet here
        grp.addButton(b, i)
        lay.addWidget(b)
    lay.addStretch(1)
    grp.idClicked.connect(on_change)
    return box, grp


class _Bus(QObject):
    progress = Signal(str)
    done = Signal(bool, str)
    checks = Signal(list, str)


class SetupWizard(QDialog):
    def __init__(self, cfg: dict, engine, devices_fn=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WaveFlow setup")
        self.setStyleSheet(QSS)
        self.resize(1020, 600)
        self.cfg = dict(cfg)
        self.engine = engine                  # LocalEngine owned by the app
        self.devices_fn = devices_fn
        self.hw = S.detect()
        prev = self.cfg.get("engine") or {}
        self.c = S.Choices(option=prev.get("mode", "local") if prev.get("mode") in S.OPTIONS else "local",
                           engine=prev.get("engine", "onnx-cpu"), method=prev.get("method", "docker"),
                           threads=prev.get("threads", self.hw.perf_cores),
                           auto_threads=prev.get("auto_threads", True),
                           address=self.cfg.get("url", "") if prev.get("mode") in ("onsite", "vps") else "",
                           token=self.cfg.get("token") or S.new_token())
        if self.c.auto_threads:
            self.c.threads = self.hw.perf_cores
        self.result_cfg: dict | None = None
        self.bus = _Bus()
        self.bus.progress.connect(self._on_progress)
        self.bus.done.connect(self._on_action_done)
        self.bus.checks.connect(self._on_checks)
        self._busy = False
        self._mic = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(200)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(10, 18, 10, 14)
        self.step_btns = []
        for i, s in enumerate(STEPS):
            b = QPushButton(f"  {i + 1}   {s.replace('&', '&&')}")
            b.setObjectName("step")
            b.setCheckable(True)
            b.clicked.connect(lambda _, i=i: self.go(i))
            rl.addWidget(b)
            self.step_btns.append(b)
        rl.addStretch(1)
        self.rail_foot = _lbl("", "hint")
        rl.addWidget(self.rail_foot)
        root.addWidget(rail)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.pages = [self._page_welcome(), self._page_where(), self._page_configure(),
                      self._page_test(), self._page_hotkey()]
        for p in self.pages:
            self.stack.addWidget(p)
        self.step = 0
        self.go(0)

    # ------------------------------------------------------------ shell
    def _shell(self, with_preview=False):
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(26, 22, 26, 18)
        v.setSpacing(10)
        h.addWidget(main, 58)
        prev = None
        if with_preview:
            pw = QFrame()
            pw.setObjectName("preview")
            pw.setMinimumWidth(340)
            pv = QVBoxLayout(pw)
            pv.setContentsMargins(16, 16, 16, 16)
            prev = QTextEdit()
            prev.setReadOnly(True)
            prev.setLineWrapMode(QTextEdit.WidgetWidth)
            pv.addWidget(prev)
            h.addWidget(pw, 44)
        return page, v, prev

    def _foot(self, v, back=True, primary="Continue", on_primary=None):
        row = QHBoxLayout()
        b = QPushButton("Back" if back else "")
        b.setObjectName("ghost")
        b.clicked.connect(lambda: self.go(self.step - 1))
        b.setEnabled(back)
        row.addWidget(b)
        row.addStretch(1)
        p = QPushButton(primary)
        p.setObjectName("pri")
        p.clicked.connect(on_primary or (lambda: self.go(self.step + 1)))
        row.addWidget(p)
        v.addStretch(1)
        v.addLayout(row)
        return p

    def go(self, i):
        if self._busy or not 0 <= i < len(STEPS):
            return
        self._stop_mic()
        self.step = i
        self.stack.setCurrentIndex(i)
        for k, b in enumerate(self.step_btns):
            b.setChecked(k == i)
            b.setText(f"  {'✓' if k < i else k + 1}   {STEPS[k].replace('&', '&&')}")
        self.rail_foot.setText(f"{S.OPTION_NAMES[self.c.option]}\n"
                               f"{'🔒 token' if self.c.option != 'local' else 'local only'}")
        if i == 2:
            self._refresh_configure()
        if i == 3:
            self._run_checks()
        if i == 4:
            self._start_mic()

    # ------------------------------------------------------------ 0 welcome
    def _page_welcome(self):
        page, v, _ = self._shell()
        v.addWidget(_lbl("Private dictation, on your hardware", "h2"))
        v.addWidget(_lbl("WaveFlow types what you say into any app. Speech recognition runs on a "
                         "machine you choose — this PC, a server at home, or your own VPS. No cloud account.",
                         "lead"))
        v.addWidget(_lbl(f"This PC: {self.hw.perf_cores} performance cores of {self.hw.all_cores} · "
                         f"GPU (DirectML): {'ready' if self.hw.directml else 'not installed'} · "
                         f"Docker: {'found' if self.hw.docker else 'not found'} · "
                         f"NVIDIA: {'found' if self.hw.nvidia else 'not found'}", "hint"))
        v.addWidget(_lbl("Change anything later in ⚙ → Setup.", "hint"))
        self._foot(v, back=False, primary="Get started")
        return page

    # ------------------------------------------------------------ 1 where
    def _page_where(self):
        page, v, _ = self._shell()
        v.addWidget(_lbl("Where should speech recognition run?", "h2"))
        v.addWidget(_lbl("Audio only goes to the machine you pick.", "lead"))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.opt_grp = QButtonGroup(page)
        lines = {"local": "Runs quietly with the app. No Docker.\nAny 4+ core CPU or a DirectX 12 GPU.",
                 "docker": "Engine in a container on this PC.\nDocker Desktop.",
                 "onsite": "A box at home: LAN, Tailscale or VPN.\nDocker or Python venv.",
                 "vps": "Your own server on the internet.\nDocker, a domain, HTTPS."}
        for n, k in enumerate(S.OPTIONS):
            b = QPushButton(f"{S.OPTION_NAMES[k]}{'   · Recommended' if k == 'local' else ''}\n{lines[k]}")
            b.setObjectName("card")
            b.setCheckable(True)
            b.setMinimumHeight(78)
            b.setChecked(k == self.c.option)
            self.opt_grp.addButton(b, n)
            grid.addWidget(b, n // 2, n % 2)
        self.opt_grp.idClicked.connect(self._set_option)
        v.addLayout(grid)
        self._foot(v)
        return page

    def _set_option(self, n):
        self.c.option = S.OPTIONS[n]
        if self.c.option == "vps" and self.c.address.startswith("http://"):
            self.c.address = ""
        self.rail_foot.setText(S.OPTION_NAMES[self.c.option])

    # ------------------------------------------------------------ 2 configure
    def _page_configure(self):
        page, v, prev = self._shell(with_preview=True)
        self.cfg_preview = prev
        self.cfg_v = v
        self.cfg_title = _lbl("", "h2")
        v.addWidget(self.cfg_title)
        v.addWidget(_lbl("The right side updates as you change things. Nothing runs until you press the button.",
                         "lead"))

        # address (onsite / vps)
        self.addr_box = QWidget()
        al = QVBoxLayout(self.addr_box)
        al.setContentsMargins(0, 0, 0, 0)
        self.addr_label = _lbl("", "hint")
        self.addr = QLineEdit(self.c.address)
        self.addr.textChanged.connect(lambda t: (setattr(self.c, "address", t), self._refresh_configure(False)))
        al.addWidget(self.addr_label)
        al.addWidget(self.addr)
        v.addWidget(self.addr_box)

        # onsite method / docker reach
        self.method_box, self.method_grp = _seg(["Docker", "Python venv"], self._set_method, page)
        self.reach_box, self.reach_grp = _seg(["This PC only", "My network"], self._set_reach, page)
        v.addWidget(self.method_box)
        v.addWidget(self.reach_box)

        # engines
        v.addWidget(_lbl("ENGINE", "lbl"))
        self.eng_row = QVBoxLayout()       # full-width rows: three side by side clipped their text
        self.eng_row.setSpacing(6)
        self.eng_grp = QButtonGroup(page)
        self.eng_btns = []
        for i, e in enumerate(S.ENGINES):
            b = QPushButton()
            b.setObjectName("card")
            b.setCheckable(True)
            b.setMinimumHeight(52)
            self.eng_grp.addButton(b, i)
            self.eng_row.addWidget(b, 1)
            self.eng_btns.append(b)
        self.eng_grp.idClicked.connect(self._set_engine)
        v.addLayout(self.eng_row)
        self.gpu_install = QPushButton("Install GPU support (onnxruntime-directml)")
        self.gpu_install.clicked.connect(self._install_directml)
        v.addWidget(self.gpu_install)

        # threads
        v.addWidget(_lbl("CPU THREADS (ONNX · CPU)", "lbl"))
        tr = QHBoxLayout()
        self.thr = QSpinBox()
        self.thr.setRange(S.THREADS_MIN, S.THREADS_MAX)
        self.thr.setValue(S.clamp_threads(self.c.threads))
        self.thr.setFixedWidth(90)
        self.thr.valueChanged.connect(lambda n: (setattr(self.c, "threads", n), self._refresh_configure(False)))
        self.thr_auto = QCheckBox("Auto")
        self.thr_auto.setChecked(self.c.auto_threads)
        self.thr_auto.toggled.connect(self._set_auto_threads)
        self.thr_hint = _lbl("", "hint")
        self.thr_hint.setMinimumWidth(10)
        tr.addWidget(self.thr)
        tr.addWidget(self.thr_auto)
        tr.addWidget(self.thr_hint, 1)
        v.addLayout(tr)

        # token
        self.tok_box = QWidget()
        tl = QVBoxLayout(self.tok_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(_lbl("TOKEN", "lbl"))
        trow = QHBoxLayout()
        self.tok = QLineEdit(self.c.token)
        self.tok.setEchoMode(QLineEdit.Password)
        self.tok.textChanged.connect(lambda t: (setattr(self.c, "token", t.strip()), self._refresh_configure(False)))
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.tok.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        cp = QPushButton("Copy")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.c.token))
        new = QPushButton("New")
        new.clicked.connect(lambda: self.tok.setText(S.new_token()))
        for w in (self.tok, show, cp, new):
            trow.addWidget(w, 1 if w is self.tok else 0)
        tl.addLayout(trow)
        tl.addWidget(_lbl("Made for you. Use the same token on the server.", "hint"))
        v.addWidget(self.tok_box)

        self.msg = _lbl("", "err")
        self.warn = _lbl("", "warn")
        self.progress = _lbl("", "hint")
        v.addWidget(self.msg)
        v.addWidget(self.warn)
        v.addWidget(self.progress)
        self.cfg_primary = self._foot(v, primary="Continue", on_primary=self._configure_primary)
        return page

    def _install_directml(self):
        cmds = S.directml_install_commands()
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
        self.cfg_title.setText(f"Configure — {S.OPTION_NAMES[c.option]}")
        self.addr_box.setVisible(c.option in ("onsite", "vps"))
        self.addr_label.setText("Server address — LAN name, IP or Tailscale name (setup never logs in to it)"
                                if c.option == "onsite" else "Domain — HTTPS only; the DNS name must point at the VPS")
        self.addr.setPlaceholderText("http://gpu-box.local:8756" if c.option == "onsite" else "https://stt.example.com")
        self.method_box.setVisible(c.option == "onsite")
        self.method_grp.button(0 if c.method == "docker" else 1).setChecked(True)
        self.reach_box.setVisible(c.option == "docker")
        self.reach_grp.button(1 if c.lan else 0).setChecked(True)
        self.tok_box.setVisible(c.option != "local")
        choices = S.engines_for(c.option, c.method, self.hw)
        if rebuild:
            valid = [e.engine for e in choices if e.available]
            if c.engine not in valid and valid:
                c.engine = valid[0]
        for b, e in zip(self.eng_btns, choices):
            # QPushButton never wraps, and one long line stretched the whole window past 2000px.
            b.setText(f"{e.label}   ·   {e.detail}" + (f"\n{textwrap.fill(e.reason, 64)}" if e.reason else ""))
            b.setEnabled(e.available)
            b.setChecked(e.engine == c.engine)
        need_dml = c.option == "local" and c.engine == "onnx-gpu" and not self.hw.directml
        self.gpu_install.setVisible(need_dml)
        self.thr.setEnabled(not c.auto_threads)
        self.thr_hint.setText(f"Auto = {self.hw.perf_cores} performance cores on this PC."
                              if c.option in ("local", "docker") else
                              "Set to the server's physical performance cores.")
        errs = S.validate(c)
        if need_dml:
            errs.append("Install GPU support first, or choose ONNX · CPU.")
        self.msg.setText("  ".join(errs))
        self.warn.setText("  ".join(S.warnings(c)))
        self.cfg_primary.setEnabled(not errs and not self._busy)
        self.cfg_primary.setText({"local": "Start engine", "docker": "Build & start"}.get(c.option, "Continue"))
        self.cfg_preview.setHtml(self._preview_html(S.build_plan(c) if not errs or c.option == "local" else None, errs))

    def _preview_html(self, plan, errs):
        def mask(s):
            return s.replace(self.c.token, "••••••••••••") if self.c.token else s
        if plan is None:
            return f"<p style='color:{WARN}'>{html.escape(' '.join(errs))}</p>"
        out = [f"<p style='color:#5d6477;font-size:10px'>{plan.where.upper()}</p><pre style='white-space:pre-wrap'>"]
        if plan.env_text:
            out.append(f"<span style='color:#5d6477'># {html.escape(plan.env_path)}</span>\n")
            for line in plan.env_text.splitlines():
                k, _, val = line.partition("=")
                col = BLUSH if "TOKEN" in k else MINT
                out.append(f"{k}=<span style='color:{col}'>{html.escape(mask(val))}</span>\n")
            out.append("\n")
        for cmd in plan.commands:
            out.append(html.escape(mask(cmd)) + "\n")
        out.append("</pre><p style='color:#5d6477;font-size:10px'>APP CONFIG</p><pre style='white-space:pre-wrap'>")
        import json
        shown = json.dumps(plan.config, indent=2)
        out.append(html.escape(mask(shown)) + "</pre>")
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
        import time
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
        if ok:
            self.go(3)

    # ------------------------------------------------------------ 3 test
    def _page_test(self):
        page, v, prev = self._shell(with_preview=True)
        self.test_preview = prev
        v.addWidget(_lbl("Test connection", "h2"))
        self.test_lead = _lbl("", "lead")
        v.addWidget(self.test_lead)
        self.check_rows = []
        for _ in range(4):
            row = QHBoxLayout()
            icon, name, det = QLabel("·"), QLabel(""), _lbl("", "hint", wrap=False)
            icon.setFixedWidth(22)
            row.addWidget(icon)
            row.addWidget(name, 1)
            row.addWidget(det)
            v.addLayout(row)
            self.check_rows.append((icon, name, det))
        self.verdict = _lbl("", "lead")
        v.addWidget(self.verdict)
        row = QHBoxLayout()
        back = QPushButton("Back")
        back.setObjectName("ghost")
        back.clicked.connect(lambda: self.go(2))
        again = QPushButton("Test again")
        again.clicked.connect(self._run_checks)
        self.skip = QPushButton("Save without testing")
        self.skip.setObjectName("ghost")
        self.skip.clicked.connect(lambda: self.go(4))
        self.test_next = QPushButton("Continue")
        self.test_next.setObjectName("pri")
        self.test_next.clicked.connect(lambda: self.go(4))
        row.addWidget(back)
        row.addStretch(1)
        for w in (self.skip, again, self.test_next):
            row.addWidget(w)
        v.addStretch(1)
        v.addLayout(row)
        return page

    def _run_checks(self):
        plan = S.build_plan(self.c)
        self.plan = plan
        self.test_lead.setText(f"{S.OPTION_NAMES[self.c.option]} · {plan.url}")
        for icon, name, det in self.check_rows:
            icon.setText("…")
            det.setText("")
        self.verdict.setText("Testing…")
        self.test_next.setEnabled(False)
        self.skip.setVisible(self.c.option in ("onsite", "vps"))
        remote = self.c.option != "local"
        threading.Thread(target=lambda: self.bus.checks.emit(
            *S.run_checks(plan.url, plan.token, require_token=remote)), daemon=True).start()

    def _on_checks(self, checks, verdict):
        allok = all(ch.ok for ch in checks)
        for (icon, name, det), ch in zip(self.check_rows, checks):
            icon.setText({True: "✓", False: "✕", None: "·"}[ch.ok])
            icon.setStyleSheet(f"color:{MINT if ch.ok else BAD if ch.ok is False else '#5d6477'};font-weight:700")
            name.setText(ch.name)
            det.setText(ch.detail)
        self.verdict.setText(verdict)
        self.verdict.setStyleSheet(f"color:{MINT if allok else BAD}")
        self.test_next.setEnabled(allok)
        tok = "(none)" if not self.plan.token else "••••••••"
        log = [f"GET  {self.plan.url}/health", f"     {checks[0].detail} {checks[1].detail}",
               "", "POST /v1/audio/transcriptions", f"     Authorization: Bearer {tok}",
               f"     {checks[2].detail}  {checks[3].detail}"]
        self.test_preview.setHtml("<p style='color:#5d6477;font-size:10px'>REQUEST LOG</p><pre style='white-space:pre-wrap'>"
                                  + html.escape("\n".join(log)) + "</pre>")

    # ------------------------------------------------------------ 4 hotkey & mic
    def _page_hotkey(self):
        page, v, _ = self._shell()
        v.addWidget(_lbl("Hotkey & microphone", "h2"))
        v.addWidget(_lbl("Last step.", "lead"))
        v.addWidget(_lbl("SHOW / DICTATE", "lbl"))
        self.hk = QKeySequenceEdit(QKeySequence(_to_qt(self.cfg.get("hotkey_show", "ctrl+alt+w"))))
        self.hk.setMaximumWidth(260)
        v.addWidget(self.hk)
        v.addWidget(_lbl("MICROPHONE", "lbl"))
        self.mic_box = QComboBox()
        self.mic_box.setMaximumWidth(420)
        self._devs = []
        try:
            devs, default = self.devices_fn() if self.devices_fn else ([], None)
        except Exception:
            devs, default = [], None
        self._devs = devs
        self.mic_box.addItem("System default", None)
        for idx, name in devs:
            self.mic_box.addItem(name, name)
        cur = self.cfg.get("device_name")
        if cur:
            i = self.mic_box.findData(cur)
            self.mic_box.setCurrentIndex(max(0, i))
        if not devs:
            v.addWidget(_lbl("No microphone found. Plug one in, then reopen ⚙ → Setup.", "warn"))
        self.mic_box.currentIndexChanged.connect(lambda _: (self._stop_mic(), self._start_mic()))
        v.addWidget(self.mic_box)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setMaximumWidth(420)
        v.addWidget(self.meter)
        self.mic_err = _lbl("Speak to test.", "hint")
        v.addWidget(self.mic_err)
        v.addWidget(_lbl("LOOK", "lbl"))
        self.skin_box, self.skin_grp = _seg(["Aurora", "Halo"], lambda i: None, page)
        self.skin_grp.button(1 if self.cfg.get("skin") == "halo" else 0).setChecked(True)
        v.addWidget(self.skin_box)
        self._foot(v, primary="Finish", on_primary=self._finish)
        self._mt = QTimer(self)
        self._mt.timeout.connect(self._tick_meter)
        return page

    def _start_mic(self):
        if self.step != 4 or self._mic is not None:
            return
        try:
            from audio import MicStream, resolve_device_name
            name = self.mic_box.currentData()
            dev = resolve_device_name(name)[0] if name else None
            self._mic = MicStream(device=dev)
            self._mic.start()
            self._peak = 0.02
            self._mt.start(50)
            self.mic_err.setText("Speak to test.")
            self.mic_err.setObjectName("hint")
        except Exception as e:
            self._mic = None
            self.mic_err.setText(f"Could not open this microphone: {e}")

    def _tick_meter(self):
        if self._mic is None:
            return
        r = float(self._mic.rms())
        self._peak = max(self._peak * 0.995, r, 0.02)
        self.meter.setValue(int(min(1.0, r / self._peak) * 100))

    def _stop_mic(self):
        if getattr(self, "_mt", None):
            self._mt.stop()
        if self._mic is not None:
            try:
                self._mic.stop()
            except Exception:
                pass
            self._mic = None

    def _finish(self):
        self._stop_mic()
        plan = S.build_plan(self.c)
        out = dict(self.cfg)
        out.update(plan.config)
        seq = self.hk.keySequence().toString()
        out["hotkey_show"] = seq.replace("Meta", "windows").lower().replace(" ", "") or "ctrl+alt+w"
        out["device_name"] = self.mic_box.currentData()
        out["skin"] = "halo" if self.skin_grp.checkedId() == 1 else "aurora"
        out["setup_done"] = True
        self.result_cfg = out
        self.accept()

    def reject(self):
        if self._busy:
            return                      # a build/start is running; closing would orphan it silently
        self._stop_mic()
        super().reject()


def _to_qt(kb: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in (kb or "").split("+"))
