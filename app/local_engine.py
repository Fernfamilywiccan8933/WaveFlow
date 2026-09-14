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
        self.log_path = S.app_data() / "engine.log"

    @staticmethod
    def available() -> tuple[bool, str]:
        if getattr(sys, "frozen", False):
            return False, "the .exe build cannot run the engine itself yet — run the engine from source or Docker"
        if not (S.ROOT / "server" / "parakeet_server.py").exists():
            return False, "server/parakeet_server.py not found next to the app"
        return True, ""

    def command(self, engine_cfg: dict) -> list[str]:
        c = S.Choices(option="local", engine=engine_cfg.get("engine", "onnx-cpu"),
                      threads=engine_cfg.get("threads", 4))
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
        logf = open(self.log_path, "w", encoding="utf-8", errors="replace")
        # Models download into this copy's own data folder, never the shared user-profile cache.
        env = {**os.environ, "HF_HOME": str(S.models_dir())}
        self.proc = subprocess.Popen(self.command(engine_cfg), stdout=logf, stderr=subprocess.STDOUT,
                                     cwd=str(S.ROOT / "server"), env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def _health(url: str) -> bool:
    try:
        return requests.get(f"{url}/health", timeout=1.5).json().get("status") == "ok"
    except Exception:
        return False
