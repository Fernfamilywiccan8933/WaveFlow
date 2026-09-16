"""Run the engine on THIS PC as a child process of the app ("This PC — background app").

The app starts it on launch when config engine.mode == "local", and stops it on quit. No Windows
service, nothing left running after the app closes. Output goes to <app data>/engine.log so a
failed start can be read. Runs from a source checkout (same Python as the app).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

import setup_logic as S


class LocalEngine:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._logf = None
        self.log_path = S.app_data() / "engine.log"

    @staticmethod
    def available() -> tuple[bool, str]:
        if getattr(sys, "frozen", False):
            # The frozen app carries the engine inside itself and starts it with `--serve`. This
            # used to refuse outright — which meant the README's easiest setup did not work in the
            # build most people download. Checked by import, because in a bundle there is no
            # parakeet_server.py file to look for; the module is compiled into the archive.
            try:
                import importlib.util
                if importlib.util.find_spec("parakeet_server") is None:
                    return False, "this build does not include the engine — rebuild with app/build.py"
            except (ImportError, ValueError):
                return False, "this build does not include the engine — rebuild with app/build.py"
            return True, ""
        if not (S.ROOT / "server" / "parakeet_server.py").exists():
            return False, "server/parakeet_server.py not found next to the app"
        return True, ""

    def command(self, engine_cfg: dict) -> list[str]:
        c = S.Choices(option="local", engine=engine_cfg.get("engine", "onnx-cpu"),
                      threads=engine_cfg.get("threads", 4))
        if getattr(sys, "frozen", False):
            # sys.executable IS WaveFlow here, not a Python interpreter, so it cannot run a
            # script. It runs ITSELF in engine mode instead: waveflow.main() sees --serve before
            # anything else and hands the rest of argv to the engine. Passing the old
            # [executable, script] form here opened a second copy of the GUI.
            return [sys.executable, "--serve", *S.server_args(c)]
        return [sys.executable, str(S.ROOT / "server" / "parakeet_server.py"), *S.server_args(c)]

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, engine_cfg: dict, url: str = "http://127.0.0.1:8756") -> str:
        """Start unless something already answers on the URL. Returns a status line."""
        if _health(url):
            return "already running"
        ok, why = self.available()
        if not ok:
            return f"cannot start: {why}"
        self.stop()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = self._logf = open(self.log_path, "w", encoding="utf-8", errors="replace")
        # Models download into this copy's own data folder, never the shared user-profile cache.
        env = {**os.environ, "HF_HOME": str(S.models_dir())}
        # The working folder must EXIST. From source that is <root>/server. In a frozen build there
        # is no server folder — the engine is compiled into the executable — and Popen with a
        # missing cwd raises before anything starts, so the frozen engine could never have been
        # launched from the app at all. A direct launch of the exe hid this; the app's own path
        # did not (found 2026-09-16).
        server_dir = S.ROOT / "server"
        workdir = server_dir if server_dir.is_dir() else S.app_data()
        workdir.mkdir(parents=True, exist_ok=True)
        extra = {}
        if os.name != "nt":
            # Its OWN session, so it leads its own process group. kill_tree() on macOS/Linux ends
            # the whole group — and without this the engine shared the APP's group, so stopping
            # the engine would have killed WaveFlow itself.
            extra["start_new_session"] = True
        self.proc = subprocess.Popen(self.command(engine_cfg), stdout=logf, stderr=subprocess.STDOUT,
                                     cwd=str(workdir), env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                     **extra)
        return "starting"

    def wait_ready(self, url: str, timeout: float = 900, tick=None) -> bool:
        """Poll /health until ok. The first start downloads the model, so allow minutes."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if _health(url):
                return True
            if self.proc is not None and self.proc.poll() is not None:
                return False
            if tick:
                tick(self.last_log_line())
            time.sleep(1.0)
        return False

    def last_log_line(self) -> str:
        try:
            lines = [l for l in Path(self.log_path).read_text(errors="replace").splitlines() if l.strip()]
            return lines[-1][-160:] if lines else ""
        except OSError:
            return ""

    def stop(self):
        if self.running():
            # A venv's python.exe is a LAUNCHER with the real interpreter as its child: kill the tree.
            kill_tree(self.proc.pid)
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        # start() ADOPTS an engine that already answers (e.g. one from before an app restart) without
        # owning its process, so stop() could not end it: it kept port 8756 and locked the folder,
        # and uninstall left files behind (operator, 2026-09-15). End every engine of THIS copy.
        stop_all_engines()
        if self._logf is not None:          # an open handle blocks deleting engine.log on Windows
            self._logf.close()
            self._logf = None


NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def kill_tree(pid: int):
    """Kill a process and every child it started."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWINDOW)
        return
    # macOS / Linux. Kill the GROUP only when the process leads its own — never a group we share.
    # killpg(getpgid(pid)) on an engine started without its own session is the app's own group,
    # which would end WaveFlow itself. start() now gives engines their own session, but an engine
    # found by engine_pids() may have been started some other way, so this does not assume it.
    try:
        pgid = os.getpgid(pid)
    except Exception:
        return
    try:
        if pgid == pid and pgid != os.getpgid(0):
            os.killpg(pgid, 15)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def is_engine_of_this_copy(cmd: str, root: Path | None = None, exe: str | None = None) -> bool:
    """Does this command line belong to an engine started by THIS copy of WaveFlow?

    Two shapes, one per build:
      * source — a python process running <root>/server/parakeet_server.py
      * frozen — this copy's OWN executable run with --serve

    The first shape was the only one ever matched. A frozen engine is `WaveFlow.exe --serve …`:
    no python in the name and no script in the command line, so it matched neither. An engine a
    previous session left running could then never be stopped, kept its port, and locked the app
    folder against uninstall — the exact failure this module's stop() already documents, quietly
    reintroduced by the frozen build (found 2026-09-16, launching the built exe).

    Frozen matching is by this copy's exact executable path, so another copy's engine is never
    touched — the same promise the source rule makes by script path.
    """
    low = (cmd or "").lower().replace("\\", "/")
    script = str((root or S.ROOT) / "server" / "parakeet_server.py").lower().replace("\\", "/")
    if script in low:
        return True
    own = (exe if exe is not None else sys.executable) or ""
    own = own.lower().replace("\\", "/")
    return bool(own) and own in low and "--serve" in low


def engine_pids(root: Path | None = None) -> list[int]:
    """PIDs of engines started from THIS copy — source or frozen — including ones an earlier app
    run left behind. Another copy's engine is never matched."""
    out = []
    for pid, cmd in _process_list():
        if pid != os.getpid() and is_engine_of_this_copy(cmd, root):
            out.append(pid)
    return out


def _process_list() -> list[tuple[int, str]]:
    """(pid, command line) for every process, on this OS."""
    rows = []
    try:
        if os.name == "nt":
            # All processes, not just python%: a frozen engine is WaveFlow.exe.
            ps = ("Get-CimInstance Win32_Process | "
                  "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                               text=True, timeout=30, creationflags=NOWINDOW)
            for line in r.stdout.splitlines():
                pid, _, cmd = line.partition("|")
                if pid.strip().isdigit():
                    rows.append((int(pid), cmd))
        else:
            # macOS and Linux. This returned [] before, so on a Mac an engine left from a previous
            # session was invisible to stop() and to uninstall.
            r = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True, text=True,
                               timeout=30)
            for line in r.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if parts and parts[0].isdigit():
                    rows.append((int(parts[0]), parts[1] if len(parts) > 1 else ""))
    except Exception:
        return []
    return rows


def stop_all_engines(root: Path | None = None) -> int:
    pids = engine_pids(root)
    for pid in pids:
        kill_tree(pid)
    return len(pids)


def _health(url: str) -> bool:
    try:
        return requests.get(f"{url}/health", timeout=1.5).json().get("status") == "ok"
    except Exception:
        return False
