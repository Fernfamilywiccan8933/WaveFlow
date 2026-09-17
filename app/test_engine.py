"""The local engine, as launched from source AND from a frozen build.

Run: venv/Scripts/python.exe app/test_engine.py -> ENGINE_OK

Every assertion here is a bug that was real on 2026-09-16:

  * the frozen build refused to run an engine at all, so the README's easiest setup did not work
    in the build people download;
  * its launch command handed a script to an executable that is not Python, which opened a second
    GUI instead of an engine;
  * its working folder did not exist in a frozen build, so Popen failed before anything started;
  * engine_pids() could not see a frozen engine, so one left from a previous session could never
    be stopped and locked the folder against uninstall;
  * on macOS, stopping the engine would have killed WaveFlow itself, because the engine shared the
    app's process group and kill_tree() ends whole groups.
"""
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testenv  # noqa: E402,F401 — isolate settings/log BEFORE any app import

import local_engine as L  # noqa: E402
import setup_logic as S  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


eng = L.LocalEngine()
cfg = {"engine": "onnx-cpu", "threads": 4}

# --- the launch command, source vs frozen ---------------------------------------------------
with mock.patch.object(sys, "frozen", False, create=True):
    cmd = eng.command(cfg)
    check("source: runs python on the server script", cmd[1].endswith("parakeet_server.py"), True)
    check("source: never passes --serve", "--serve" in cmd, False)

with mock.patch.object(sys, "frozen", True, create=True):
    cmd = eng.command(cfg)
    check("frozen: runs ITSELF", cmd[0], sys.executable)
    check("frozen: in engine mode", cmd[1], "--serve")
    check("frozen: never hands a .py script to a non-Python binary",
          any(a.endswith(".py") for a in cmd), False)
    check("frozen: still passes the engine's own arguments", "--engine" in cmd, True)

# --- available() no longer refuses a frozen build that carries the engine --------------------
with mock.patch.object(sys, "frozen", True, create=True):
    ok, why = eng.available()
    # This test process has parakeet_server importable only if server/ is on the path.
    sys.path.insert(0, str(S.ROOT / "server"))
    ok, why = eng.available()
    check("frozen: available when the engine is bundled", ok, True)
    check("frozen: no longer says it 'cannot run the engine itself'",
          "cannot run the engine itself" in why, False)

# --- waveflow dispatches --serve BEFORE the GUI -------------------------------------------
src = (Path(__file__).resolve().parent / "waveflow.py").read_text(encoding="utf-8")
main_at = src.index("def main() -> int:")
serve_at = src.index('if sys.argv[1:2] == ["--serve"]', main_at)
argparse_at = src.index("ap = argparse.ArgumentParser()", main_at)
qapp_at = src.index("QApplication(sys.argv)", main_at)
check("--serve is checked before argparse (which rejects engine flags)", serve_at < argparse_at, True)
check("--serve is checked before any QApplication exists", serve_at < qapp_at, True)

# --- finding a frozen engine so it can be stopped ------------------------------------------
root = S.ROOT
exe = r"C:\Apps\WaveFlow\WaveFlow.exe"
other = r"D:\Other\WaveFlow.exe"
script = str(root / "server" / "parakeet_server.py")
check("source engine of this copy is found",
      L.is_engine_of_this_copy(f"python.exe {script} --engine onnx", root, exe), True)
check("frozen engine of this copy is found",
      L.is_engine_of_this_copy(f"{exe} --serve --engine onnx --port 8756", root, exe), True)
check("this copy's GUI (no --serve) is NOT mistaken for an engine",
      L.is_engine_of_this_copy(f"{exe}", root, exe), False)
check("ANOTHER copy's frozen engine is never matched",
      L.is_engine_of_this_copy(f"{other} --serve --engine onnx", root, exe), False)
check("an unrelated process is never matched",
      L.is_engine_of_this_copy("notepad.exe --serve", root, exe), False)
check("path separators do not matter",
      L.is_engine_of_this_copy(exe.replace("\\", "/") + " --serve", root, exe), True)
check("engine_pids never returns this process",
      os.getpid() in L.engine_pids(), False)

# --- the engine's working folder must exist in every build --------------------------------
started = {}


class _P:
    pid = 999999

    def poll(self):
        return None


def _fake_popen(cmd, **kw):
    started.update(kw, cmd=cmd)
    return _P()


# The first version of this check was a FALSE PASS. It pointed ROOT at a missing folder, so
# available() said "not found" and start() returned before ever calling Popen. `started` stayed
# empty, cwd came back as "", and Path("").is_dir() is True — an empty path means the current
# folder. Nothing had run and it passed anyway. So now it must prove Popen was actually called.
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as _tmp:
    frozen_root = Path(_tmp) / "WaveFlow"          # shaped like a frozen build: NO server/ folder
    frozen_root.mkdir()
    data = Path(_tmp) / "data"                     # nothing lands on the real disk
    started.clear()
    with mock.patch.object(L, "_health", lambda url: False), \
         mock.patch.object(L.subprocess, "Popen", _fake_popen), \
         mock.patch.object(L.LocalEngine, "stop", lambda self: None), \
         mock.patch.object(S, "ROOT", frozen_root), \
         mock.patch.object(S, "app_data", lambda: data), \
         mock.patch.object(S, "models_dir", lambda: data / "models"), \
         mock.patch.object(sys, "frozen", True, create=True):
        eng2 = L.LocalEngine()
        eng2.log_path = data / "engine.log"
        result = eng2.start(cfg)
        check("frozen start() actually reached Popen (not a vacuous pass)", "cmd" in started, True)
        check("frozen start() reports it is starting", result, "starting")
        _cwd = started.get("cwd")
        check("frozen: a working folder was given", bool(_cwd), True)
        check("frozen: that folder exists", bool(_cwd) and Path(_cwd).is_dir(), True)
        check("frozen: it is NOT the missing server folder",
              bool(_cwd) and Path(_cwd) != frozen_root / "server", True)
        check("frozen: the launch is the engine-mode command", started.get("cmd", [None, None])[1], "--serve")
        if eng2._logf is not None:
            eng2._logf.close()
    if os.name != "nt":
        check("posix: engine gets its own session", started.get("start_new_session"), True)

# --- the same "sys.executable is Python" class, found in the Mac .app 2026-09-16 -------------------
# The provider probe ran `WaveFlow -c ...`; the app's argparse rejected -c with exit 2, read as
# "no providers", so a built app could never see DirectML or CoreML.
seen = {}


class _R:
    returncode, stdout = 0, "DmlExecutionProvider,CPUExecutionProvider\n"


def _fake_run(cmd, **kw):
    seen["cmd"] = cmd
    return _R()


with mock.patch.object(S.subprocess, "run", _fake_run), mock.patch.object(S, "FROZEN", True):
    check("frozen: providers parsed", S.onnx_providers(), ["DmlExecutionProvider", "CPUExecutionProvider"])
    check("frozen: probe asks the app itself", seen["cmd"], [sys.executable, "--providers"])
    check("frozen: no pip commands (pip cannot reach inside a build)", S.gpu_install_commands(), [])
with mock.patch.object(S.subprocess, "run", _fake_run), mock.patch.object(S, "FROZEN", False):
    S.onnx_providers()
    check("source: probe still uses python -c", seen["cmd"][1], "-c")
    check("source: pip commands still offered", bool(S.gpu_install_commands()), True)

main_src = src[src.index("def main() -> int:"):]
check("--providers is handled before argparse",
      main_src.index('["--providers"]') < main_src.index("ap = argparse.ArgumentParser()"), True)
wiz = (Path(__file__).resolve().parent / "wizard.py").read_text(encoding="utf-8")
check("wizard hides the GPU install button when there is nothing to run",
      "need_dml and bool(S.gpu_install_commands())" in wiz, True)

# The real frozen answer, when a build is named (skipped otherwise).
_exe = os.environ.get("WAVEFLOW_TEST_EXE")
if _exe:
    import subprocess
    r = subprocess.run([_exe, "--providers"], capture_output=True, text=True, timeout=120)
    check("built app answers --providers", (r.returncode, "CPUExecutionProvider" in r.stdout), (0, True))

# --- a hotkey while OUR engine is still loading waits; it is not "offline" (Mac, 2026-09-16) ------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import waveflow as W  # noqa: E402


class _Sig:
    def __init__(self):
        self.sent = []

    def emit(self, m=None):
        self.sent.append(m)


class _Eng:
    def __init__(self):
        self.alive, self.log_path = True, Path("engine.log")

    def running(self):
        return self.alive

    def last_log_line(self):
        return "loading"


class _App:
    ENGINE_WAIT_S = W.WaveFlow.ENGINE_WAIT_S

    def __init__(self, mode="local"):
        self.cfg = {"engine": {"mode": mode}}
        self.engine, self.error_sig = _Eng(), _Sig()
        self.state, self._worker, self._err_shown, self.url = "idle", None, False, "http://127.0.0.1:8756"
        self.words, self.up, self.listened = [], False, []

    def isVisible(self):
        return True

    def set_word(self, w):
        self.words.append(w)

    def _server_up(self):
        return self.up


for _n in ("_local_engine_starting", "_wait_for_local_engine"):
    setattr(_App, _n, getattr(W.WaveFlow, _n))
timers = []
with mock.patch.object(W.QTimer, "singleShot", lambda ms, fn: timers.append(ms)):
    a = _App()
    a.start_listen = lambda _tries=0, a=a: (a.listened.append(_tries) if _tries == -1
                                            else W.WaveFlow.start_listen(a, _tries))
    W.WaveFlow.start_listen(a)
    check("engine loading: no 'offline' error", a.error_sig.sent, [])
    check("engine loading: the pill says it is starting", a.words[-1], "Starting engine…")
    check("engine loading: polls every 250 ms", timers, [250])
    W.WaveFlow.start_listen(a)
    check("a second press while waiting starts no second wait", timers, [250])
    a.up = True
    a._wait_for_local_engine()
    check("engine ready: the SAME press goes on to listen", a.listened, [-1])
    check("engine ready: wait cleared", a._engine_wait_t0, None)

    b = _App()
    b._wait_for_local_engine()
    b.engine.alive = False
    b._wait_for_local_engine()
    check("engine died while starting: says so, names the log",
          len(b.error_sig.sent) == 1 and "stopped" in b.error_sig.sent[0] and "engine.log" in b.error_sig.sent[0], True)

    c = _App()
    c._wait_for_local_engine()
    c._engine_wait_t0 -= W.WaveFlow.ENGINE_WAIT_S + 1
    c._wait_for_local_engine()
    check("engine never ready: says 'still not ready', not 'offline'",
          len(c.error_sig.sent) == 1 and "still not ready" in c.error_sig.sent[0], True)

    # A remote server is NOT checked before the mic opens any more: a slow first name lookup
    # (2.7 s, measured on Windows 2026-09-17) used to swallow the first words of every session.
    d = _App(mode="connect")
    d.up_calls = 0
    d._server_up = lambda d=d: (setattr(d, "up_calls", d.up_calls + 1) or d.up)
    d._engine_wait_t0 = None
    with mock.patch.object(W, "caret_rect_uia", side_effect=RuntimeError("reached the mic path")):
        try:
            W.WaveFlow.start_listen(d)
        except RuntimeError:
            pass
    check("remote: the press goes straight to opening the mic, no network wait", d.up_calls, 0)
    check("remote: no 'offline' before the mic even opens", d.error_sig.sent, [])
    # ...and the worker, already capturing, reports a server that is really down.
    d.commit_sig, d._err_shown = _Sig(), False
    W.WaveFlow._stream_ws_worker(d)
    check("remote down: reported offline by the worker",
          len(d.error_sig.sent) == 1 and "offline" in d.error_sig.sent[0], True)
    check("remote down: the session is ended, not left listening", len(d.commit_sig.sent), 1)

if FAILS:
    print("ENGINE_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("ENGINE_OK")
