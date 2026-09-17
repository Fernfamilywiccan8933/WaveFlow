"""Settings + icon rules. Run: venv/Scripts/python.exe app/test_settings.py -> SETTINGS_OK

Covers: token rotation per install type, .env rewrite keeps other lines, the meter's speech line
matches the server's gate, the noise floor follows the room and not the voice, Settings saves what
it shows, and the Z icon renders at every size (not blank) for app, dark and light tray.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import testenv  # noqa: E402,F401 — isolate settings/log BEFORE any app import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import setup_logic as S  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


# --- .env rewrite ---------------------------------------------------------------------------
env = "WAVEFLOW_TOKEN=old\nHOST_BIND=127.0.0.1\nTHREADS=8\n"
check("env token replaced, rest kept", S.replace_env_value(env, "WAVEFLOW_TOKEN", "new"),
      "WAVEFLOW_TOKEN=new\nHOST_BIND=127.0.0.1\nTHREADS=8\n")
check("env key added when missing", S.replace_env_value("A=1\n", "WAVEFLOW_TOKEN", "t"), "A=1\nWAVEFLOW_TOKEN=t\n")

# --- rotation per install type ------------------------------------------------------------
check("local: no token to rotate", S.rotation_plan({"engine": {"mode": "local"}}, "N").kind, "none")
_isolated_data = os.environ["WAVEFLOW_DATA"]         # set by testenv; put back, never deleted
with tempfile.TemporaryDirectory() as d:
    os.environ["WAVEFLOW_DATA"] = d
    Path(d, "docker.env").write_text("WAVEFLOW_TOKEN=old\nHOST_BIND=0.0.0.0\nTHREADS=6\n")
    r = S.rotation_plan({"url": "http://127.0.0.1:8759", "token": "old",
                         "engine": {"mode": "docker", "engine": "onnx-gpu", "threads": 6}}, "N" * 20)
    check("docker: app does it", r.kind, "here")
    check("docker: keeps LAN bind from the real .env", "HOST_BIND=0.0.0.0" in r.env_text, True)
    check("docker: new token written", "WAVEFLOW_TOKEN=" + "N" * 20 in r.env_text, True)
    check("docker: restarts the same service without rebuilding",
          ("waveflow-onnx-gpu" in r.commands[0], "--build" in r.commands[0]), (True, False))
    os.environ["WAVEFLOW_DATA"] = _isolated_data
r = S.rotation_plan({"url": "http://gpu-box:8757", "token": "old",
                     "engine": {"mode": "onsite", "engine": "nemo", "method": "docker"}}, "NEW")
check("onsite docker: you do it", r.kind, "server")
check("onsite docker: commands carry new token + restart",
      ("WAVEFLOW_TOKEN=NEW" in r.commands[0], "up -d" in r.commands[1]), (True, True))
r = S.rotation_plan({"url": "https://stt.example.com", "token": "old",
                     "engine": {"mode": "vps", "engine": "onnx-cpu"}}, "NEW")
check("vps: uses the vps compose file", "compose.vps.yml" in r.commands[1], True)
check("choices_from_config junk mode -> local", S.choices_from_config({"engine": {"mode": "x"}}).option, "local")

# --- meter line = server gate --------------------------------------------------------------
import parakeet_server as P  # noqa: E402
for k in S.SENSITIVITIES:
    check(f"meter k matches server ({k})", S.SENSITIVITY_K[k], P.SENSITIVITY[k]["k"])
for v in (0, 10, 25, 50, 60, 87.5, 100, "high", "balanced", "low"):
    a, b = S.sensitivity_params(v), P.sensitivity_params(v)
    check(f"slider {v}: app blend == server blend",
          (a["vad_mode"], round(a["sustain_s"], 6), round(a["k"], 6)),
          (b["vad_mode"], round(b["sustain_s"], 6), round(b["k"], 6)))
check("server: unknown sensitivity -> defaults", P.sensitivity_params("loud"), None)
check("slider value from config", [S.sensitivity_value(x) for x in ("high", "low", 30, "junk", 250)],
      [0.0, 100.0, 30.0, 50.0, 100.0])
check("clamps", (S.VAD_MIN, S.VAD_MAX), (P.VAD_MIN, P.VAD_MAX))
check("meter clamps", (S.meter_pos(0.0001, 0.002), S.meter_pos(10, 0.002)), (0.0, 1.0))

t = S.LevelTracker()
for _ in range(180):
    t.push(0.002)                       # quiet room
for _ in range(40):
    t.push(0.02)                        # 2 s of loud speech
check("speech passes the balanced line", 0.02 > t.threshold(3.0), True)
check("floor does not climb to the voice", t.floor() < 0.003, True)
check("line moves with the slider", t.threshold(2.0) < t.threshold(3.0) < t.threshold(4.5), True)

# Regression 2026-09-15: a noise-gated headset reads ~0 between words; the old floor sank to 1e-5
# and every tiny sound showed as a full meter with nobody talking.
g = S.LevelTracker()
for _ in range(180):
    g.push(0.0)
check("gated mic: floor never below FLOOR_MIN", g.floor() >= S.FLOOR_MIN, True)
check("gated mic: tiny hiss is NOT near full", S.meter_pos(0.0008, g.floor()) < 0.3, True)
check("gated mic: hiss stays under the speech line", 0.0008 < g.threshold(3.0), True)
t2 = S.LevelTracker()
for _ in range(50):
    t2.push(0.05)
for _ in range(20):
    t2.push(0.003)
check("floor falls fast when the room gets quieter", t2.floor() < 0.006, True)

# --- Qt: icons + settings collect -----------------------------------------------------------
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)
import icons  # noqa: E402


def lit(img):
    return sum(1 for x in range(img.width()) for y in range(img.height()) if img.pixelColor(x, y).alpha() > 40)


for s in icons.ICO_SIZES:
    check(f"app icon {s}px not blank", lit(icons.render(s)) > s * s // 4, True)
for col in ("#ffffff", "#111111", icons.MINT_DARK):
    check(f"tray 16px {col} not blank", lit(icons.render(16, col)) > 20, True)
data = icons.ico_bytes()
check("ico header: icon type, all sizes", (data[:4], int.from_bytes(data[4:6], "little")),
      (b"\x00\x00\x01\x00", len(icons.ICO_SIZES)))
check("shipped .ico exists", icons.ICO_PATH.exists(), True)

import panels  # noqa: E402
import settings  # noqa: E402
panels.MicPanel.start = lambda self: None          # no real mic in a test
S.set_autostart = lambda on: FAILS.append("  autostart written by a test")
cfg = {"url": "http://127.0.0.1:8756", "token": "", "skin": "aurora", "hotkey_show": "ctrl+alt+w",
       "engine": {"mode": "local", "engine": "onnx-cpu", "threads": 8, "auto_threads": True}}
w = settings.SettingsWindow(cfg, None, devices_fn=lambda: ([(1, "Mic A"), (1, "Mic A"), (2, "Mic B")], 1))
check("duplicate mic names shown once", w.mic.device.count(), 3)
w.skin.cards[1].setChecked(True)
w.mic.sens.set_value(100)
w.mode.set(1)
w.silence.setValue(9.5)
w.record.setChecked(True)
w.auto.setChecked(False)
w.thr.setValue(4)
out = w.collect()
check("settings saves skin", out["skin"], "halo")
check("settings saves sensitivity", out["mic_sensitivity"], "low")
w.mic.sens.set_value(97, snap=True)
check("slider snaps onto a mark", w.mic.sensitivity(), "low")
w.mic.sens.set_value(62)
check("slider in between saves a number", w.mic.sensitivity(), 62)
check("local address is fixed", w.collect()["url"], "http://127.0.0.1:8756")
check("settings saves burst mode", out["live_mode"], False)
check("settings saves silence", out["silence_commit_s"], 9.5)
check("settings saves threads (auto off)", (out["engine"]["threads"], out["engine"]["auto_threads"]), (4, False))
check("recording path inside data folder", out["record"].startswith(str(S.app_data())), True)
w.record.setChecked(False)
check("recording off removes key", "record" in w.collect(), False)
check("original cfg untouched", "mic_sensitivity" in cfg, False)
w.close()
cfg2 = {**cfg, "device_name": "Old USB Mic"}
w = settings.SettingsWindow(cfg2, None, devices_fn=lambda: ([(1, "Mic A")], 1))
check("unplugged saved mic stays selected", w.collect()["device_name"], "Old USB Mic")
w.close()

if FAILS:
    print("SETTINGS_FAIL\n" + "\n".join(FAILS))
    sys.exit(1)
print("SETTINGS_OK")
