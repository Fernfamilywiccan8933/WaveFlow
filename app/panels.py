"""Panels shared by the setup wizard's last page and the Settings window, so both show the same
mic meter, sensitivity control, skin picker and uninstall checklist (mock wizard-hybrid-v3.html)."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

import setup_logic as S
from wizard_ui import LevelMeter, SensSlider, SkinCard

SENS_HINTS = {
    "high": "Picks up whispers and quiet rooms. May catch breathing or a fan.",
    "balanced": "Balanced for most rooms.",
    "low": "Only clear speech. Good for noisy rooms or loud keyboards.",
}
SKINS = [("aurora", "Aurora", "Chromatic ribbons that follow your voice"),
         ("halo", "Halo", "Centre-out bars with a glowing rim")]


def lbl(text, name=None, wrap=True):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


class MicPanel(QWidget):
    """Microphone device, live level against the speech line, and High/Balanced/Low sensitivity.
    The mic opens only while the panel is shown (start()/stop())."""

    changed = Signal()

    def __init__(self, cfg: dict, devices_fn=None, parent=None):
        super().__init__(parent)
        self._mic = None
        self.tracker = S.LevelTracker()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(lbl("Microphone", "lbl"))
        self.device = QComboBox()
        try:
            devs, _default = devices_fn() if devices_fn else ([], None)
        except Exception:
            devs = []
        self.device.addItem("System default", None)
        seen = set()
        for _idx, name in devs:
            if name not in seen:
                seen.add(name)
                self.device.addItem(name, name)
        cur = cfg.get("device_name")
        if cur:
            i = self.device.findData(cur)
            if i < 0:                                  # saved mic is unplugged: keep it visible, say so
                self.device.addItem(f"{cur} (not connected)", cur)
                i = self.device.count() - 1
            self.device.setCurrentIndex(i)
        self.device.currentIndexChanged.connect(lambda _: (self.stop(), self.start(), self.changed.emit()))
        v.addWidget(self.device)
        if not devs:
            v.addWidget(lbl("No microphone found. Plug one in, then reopen this page.", "warn"))
        v.addSpacing(6)
        v.addWidget(lbl("Level", "lbl"))
        self.meter = LevelMeter()
        v.addWidget(self.meter)
        self.status = lbl("", "hint")
        v.addWidget(self.status)
        v.addSpacing(6)
        v.addWidget(lbl("Mic sensitivity", "lbl"))
        self.sens = SensSlider(S.sensitivity_value(cfg.get("mic_sensitivity", "balanced")))
        self.sens.setMaximumWidth(420)
        self.sens.changed.connect(self._on_sens)
        v.addWidget(self.sens)
        self.sens_hint = lbl("", "hint")
        v.addWidget(self.sens_hint)
        v.addWidget(lbl("It still adapts to your room's noise on its own. This sets how far above the noise "
                        "speech must be.", "hint"))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._on_sens(self.sens.value, emit=False)

    def sensitivity(self):
        """A preset name when the slider sits on a mark (readable config), else the number 0-100."""
        v = round(self.sens.value)
        return {0: "high", 50: "balanced", 100: "low"}.get(v, v)

    def device_name(self):
        return self.device.currentData()

    def _on_sens(self, value, emit=True):
        self.sens_hint.setText(SENS_HINTS[S.sensitivity_label(value).lower()])
        self._draw_line()
        if emit:
            self.changed.emit()

    def _draw_line(self):
        k = S.sensitivity_params(self.sens.value)["k"]
        floor = self.tracker.floor()
        self.meter.set_threshold(S.meter_pos(self.tracker.threshold(k), floor))

    def start(self):
        if self._mic is not None:
            return
        try:
            from audio import MicPermissionError, MicStream, resolve_device_name
            name = self.device.currentData()
            dev = resolve_device_name(name)[0] if name else None
            self._mic = MicStream(device=dev)
            self._mic.start()
            self.tracker = S.LevelTracker()
            self._timer.start(50)
            self.status.setText("Speak to see the level. The white line marks where speech starts.")
        except MicPermissionError as e:
            # NOT a broken microphone, and saying so would send the user hunting for the wrong
            # thing. 'undetermined' means macOS has simply never asked, so ask — the answer
            # arrives on an AVFoundation queue, and the user presses Retry once they have
            # answered. 'denied' cannot be re-prompted; only System Settings can undo it.
            self._mic = None
            if e.status == 'undetermined':
                import osbridge
                asked = osbridge.request_microphone()
                self.status.setText(
                    'Allow the microphone in the prompt, then press Start again.' if asked else
                    'macOS cannot ask for the microphone from a plain python run. Build the app '
                    '(python app/build.py --install) and open that instead.')
            else:
                self.status.setText('Microphone access is turned off for this app. '
                                    'System Settings -> Privacy & Security -> Microphone.')
        except Exception as e:
            self._mic = None
            self.status.setText(f"Could not open this microphone: {e}")

    def _tick(self):
        if self._mic is None:
            return
        level = self.tracker.push(self._mic.rms())
        self.meter.set_level(S.meter_pos(level, self.tracker.floor()))
        self._draw_line()                     # the floor moves with the room, so the line does too

    def stop(self):
        self._timer.stop()
        if self._mic is not None:
            try:
                self._mic.stop()
            except Exception:
                pass
            self._mic = None
        self.meter.set_level(0)


PERMISSIONS = (("Microphone", "to hear you"),
               ("Accessibility", "to type the words into the app you are using"),
               ("Input Monitoring", "to notice your hotkey while another app is in front"))


class PermissionPanel(QWidget):
    """macOS permissions, live: one row each with an Allow button that makes macOS ITSELF ask.

    Operator requirement, 2026-09-16: setup asks for each permission, instead of sending the user
    to find WaveFlow in System Settings. No app can switch these on — macOS requires the user's
    own click in its own UI — so Allow raises the system prompt (which also puts WaveFlow in the
    list already), and the row turns green by itself when the grant lands: polled every second
    while the panel is on screen, redrawn only when something changed.

    Reset and ask again handles the stale grant after an update: WaveFlow shows as ON in the list,
    but the entry belongs to an older build. It clears only WaveFlow's own entries, after asking.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        import osbridge
        self._os = osbridge
        self._state = None
        self._asked = set()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(lbl("Permissions macOS needs", "lbl"))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        v.addLayout(self.rows)
        self.reset_btn = QPushButton("Allowed but still not working? Reset and ask again")
        self.reset_btn.setObjectName("pill")
        self.reset_btn.clicked.connect(self._reset)
        v.addWidget(self.reset_btn, 0, Qt.AlignLeft)
        self.note = lbl("", "hint")
        self.note.setTextFormat(Qt.RichText)
        v.addWidget(self.note)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self.refresh(force=True)

    def missing(self) -> set[str]:
        try:
            return {n for n, _ in self._os.missing_permissions()}
        except Exception:
            return set()

    def all_granted(self) -> bool:
        return not self.missing()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh(force=True)
        self._timer.start()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def refresh(self, force: bool = False):
        missing = self.missing()
        state = frozenset(missing)
        if state == self._state and not force:
            return
        before, self._state = self._state, state
        if before is not None and "Input Monitoring" in before and "Input Monitoring" not in missing:
            self._os.retry_hotkeys()            # the hotkey works now, not after a relaunch
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.hide()                     # gone NOW, not at the next event-loop turn
                w.setParent(None)
                w.deleteLater()
        from wizard_ui import MINT, WARN
        for name, why in PERMISSIONS:
            granted = name not in missing
            r = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(1)
            tick = lbl(("✓  " if granted else "•  ") + name, "rowt", wrap=False)
            tick.setStyleSheet(f"color:{MINT if granted else WARN};")
            col.addWidget(tick)
            col.addWidget(lbl(why, "hint"))
            r.addLayout(col, 1)
            if not granted:
                b = QPushButton("Allow")
                b.setObjectName("pill")
                b.clicked.connect(lambda _=False, n=name: self._allow(n))
                r.addWidget(b)
            w = QWidget()
            w.setLayout(r)
            self.rows.addWidget(w)
        import sys as _sys
        frozen = bool(getattr(_sys, "frozen", False))
        stale_possible = frozen and bool(missing & {"Accessibility", "Input Monitoring"})
        self.reset_btn.setVisible(stale_possible and bool(self._asked))
        if not frozen:
            # From source macOS attributes the permissions to Python / Terminal, never WaveFlow.
            self.note.setText("Running from source, so macOS asks on behalf of <b>Python</b> or "
                              "<b>Terminal</b>. Build the app to get WaveFlow's own entries:<br>"
                              "<code>venv/bin/python app/build.py --install</code>")
        elif missing:
            self.note.setText(html.escape(self._os.permission_note()))
        else:
            self.note.setText("All set.")
        if before is not None and state != before:
            self.changed.emit()

    def _allow(self, name: str):
        self._asked.add(name)
        if name == "Microphone" and self._os.microphone_status() == "denied":
            # Refused once: macOS will not prompt again, only its Settings page can change it.
            self._os.open_permission_settings(name)
        elif not self._os.request_permission(name):
            self._os.open_permission_settings(name)
        self.refresh(force=True)

    def _reset(self):
        if QMessageBox.question(
                self, "Reset permissions",
                "This removes WaveFlow's own Accessibility and Input Monitoring entries, so macOS "
                "can ask again for this version. Nothing else is changed.") != QMessageBox.Yes:
            return
        if not self._os.reset_permissions():
            QMessageBox.warning(self, "Reset permissions",
                                "macOS did not allow the reset. Open System Settings → Privacy & "
                                "Security, remove WaveFlow from both lists with −, then press Allow.")
            return
        for name in ("Accessibility", "Input Monitoring"):
            self._os.request_permission(name)
        self.refresh(force=True)


class SkinPicker(QWidget):
    changed = Signal(str)

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.cards = []
        for key, name, desc in SKINS:
            c = SkinCard(key, name, desc)
            c.setChecked(key == (current if current in dict((k, 1) for k, *_ in SKINS) else "aurora"))
            c.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            v.addWidget(c)
            self.cards.append(c)

    def value(self) -> str:
        return next((c.key for c in self.cards if c.isChecked()), "aurora")


class UninstallPanel(QWidget):
    """Checklist of what WaveFlow made (uninstall.scan), the exact steps, and the server commands.
    Nothing is removed until Uninstall is pressed and confirmed."""

    finished = Signal(bool)          # True = uninstall ran: the app closes

    def __init__(self, cfg: dict, engine=None, parent=None):
        super().__init__(parent)
        import uninstall as U
        self.U, self.cfg, self.engine = U, cfg, engine
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(6)
        h.addLayout(left, 58)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(2)
        left.addLayout(self.rows_box)
        self.remote = lbl("", "warnbox")
        self.remote.setTextFormat(Qt.RichText)
        left.addWidget(self.remote)
        self.result = lbl("", "hint")
        left.addWidget(self.result)
        left.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.go_btn = QPushButton("Uninstall")
        self.go_btn.setObjectName("danger")
        self.go_btn.setCursor(Qt.PointingHandCursor)
        self.go_btn.clicked.connect(self._run)
        row.addWidget(self.go_btn)
        left.addLayout(row)

        right = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(lbl("WILL RUN ON THIS PC", "plbl", wrap=False))
        head.addStretch(1)
        copy = QPushButton("Copy all")
        copy.setObjectName("pill")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._plain()))
        head.addWidget(copy)
        right.addLayout(head)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName("previewtext")
        right.addWidget(self.preview, 1)
        h.addLayout(right, 42)
        self.checks = {}
        self.refresh()

    def refresh(self):
        while self.rows_box.count():
            w = self.rows_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.items = self.U.scan()
        self.checks = {}
        for it in self.items:
            line = QWidget()
            line.setObjectName("urow")
            line.setAttribute(Qt.WA_StyledBackground, True)
            lh = QHBoxLayout(line)
            lh.setContentsMargins(10, 6, 10, 6)
            cb = QCheckBox()
            cb.setChecked(it.default and it.present)
            cb.setEnabled(it.present)
            cb.toggled.connect(self._update_preview)
            lh.addWidget(cb)
            txt = lbl(f"<b>{html.escape(it.title)}</b><br><span style='color:#5d6477;font-size:11.5px'>"
                      f"{html.escape(it.detail if it.present else 'nothing to remove')}</span>")
            txt.setTextFormat(Qt.RichText)
            lh.addWidget(txt, 1)
            lh.addWidget(lbl(self.U.human(it.size) if it.size else "—", "hint", wrap=False))
            self.rows_box.addWidget(line)
            self.checks[it.key] = cb
        folder, vocab = self.checks.get("folder"), self.checks.get("vocab")
        if folder is not None and vocab is not None and vocab.isEnabled():
            # vocab.user.json lives INSIDE the app folder: removing the folder removes it too. Say so,
            # instead of an unticked box that would be deleted anyway.
            def couple(on, vocab=vocab):
                vocab.blockSignals(True)
                if on:
                    vocab.setChecked(True)
                vocab.setEnabled(not on)
                vocab.setToolTip("Inside the app folder — untick the app folder to keep it." if on else "")
                vocab.blockSignals(False)
                self._update_preview()
            folder.toggled.connect(couple)
            couple(folder.isChecked())
        cmds = self.U.remote_commands(self.cfg)
        self.remote.setVisible(bool(cmds))
        if self.U.remote_target(self.cfg):
            self.remote.setText("<b style='color:#ffc46b'>Server install</b> is removed over SSH with your key. "
                                "Anything else on the server is left alone.")
        else:
            self.remote.setText("<b style='color:#ffc46b'>Onsite or VPS server?</b> This server was not installed "
                                "by setup, so WaveFlow does not log in to it. Run the commands on the right there.")
        self._update_preview()

    def chosen(self) -> set[str]:
        return {k for k, cb in self.checks.items() if cb.isChecked()}

    def _plain(self) -> str:
        out = self.U.plan_lines(self.items, self.chosen())
        cmds = [] if self.U.remote_target(self.cfg) else self.U.remote_commands(self.cfg)
        if cmds:
            out += ["", "# on your server:"] + cmds
        return "\n".join(out)

    def _update_preview(self):
        lines = self.U.plan_lines(self.items, self.chosen())
        body = "\n".join(html.escape(x) for x in lines) or "<span style='color:#5d6477'>nothing selected</span>"
        cmds = [] if self.U.remote_target(self.cfg) else self.U.remote_commands(self.cfg)
        if cmds:
            body += ("\n\n<span style='color:#5d6477'># run these on your server yourself</span>\n" +
                     "\n".join(html.escape(c) for c in cmds))
        self.preview.setHtml(f"<pre style='white-space:pre-wrap;margin:0'>{body}</pre>")
        self.go_btn.setEnabled(bool(lines))

    def _run(self):
        chosen = self.chosen()
        folder = "folder" in chosen
        msg = "Remove the selected items?\n\n" + "\n".join(self.U.plan_lines(self.items, chosen))
        msg += ("\n\nWaveFlow will close, then its folder is deleted." if folder else
                "\n\nWaveFlow will close when this is done.")
        if QMessageBox.warning(self, "Uninstall WaveFlow", msg,
                               QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)      # removing a server install can take a minute
        try:
            errors = self.U.run(self.items, chosen, engine=self.engine, log=lambda _l: None)
        finally:
            QApplication.restoreOverrideCursor()
        if errors:
            QMessageBox.warning(self, "Uninstall WaveFlow", "Some items were not removed:\n\n" + "\n".join(errors))
        self.finished.emit(True)             # its files are gone: never keep running half-uninstalled
