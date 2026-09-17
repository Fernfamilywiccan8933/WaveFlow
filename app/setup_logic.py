"""Setup wizard logic — no Qt here, so every rule is unit-tested (app/test_wizard.py).

What lives here:
  * hardware detection: physical PERFORMANCE cores, DirectML/CUDA, Docker, NVIDIA GPU;
  * the install-option x engine matrix (every engine is listed everywhere; one that cannot run
    in a place is returned with the reason, never dropped);
  * build_plan(): the exact commands, .env text and config the wizard shows and writes;
  * validate(): refuses unsafe combinations (VPS over http, remote without a token);
  * run_checks(): the 4-check Test connection, with one plain message per failure.
"""
from __future__ import annotations

import ctypes
import ipaddress
import math
import os
import secrets
import shutil
import subprocess
import sys
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
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# The WaveFlow folder. In the one-file .exe, __file__ points INTO PyInstaller's unpack folder under
# %TEMP%, so parent.parent was the user's whole Temp folder — and Uninstall's "The app folder" (ticked
# by default) would have deleted it. Frozen: the folder holding the .exe. (Found 2026-09-15, pre-build.)
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
_FROZEN_MAC = FROZEN and sys.platform == "darwin"

# A frozen macOS app must NEVER write inside its own bundle.
#
# "Everything lives in WaveFlow's own folder" is right for a portable Windows .exe and for a source
# checkout. In a Mac bundle, though, the executable's folder is WaveFlow.app/Contents/MacOS, so
# config, log and every downloaded model were being written INSIDE the app. Observed on disk on a
# real Mac 2026-09-16 — the log sat at WaveFlow.app/Contents/MacOS/waveflow.log. That breaks three
# things at once:
#   * every update deletes the user's settings and hundreds of MB of models, because installing
#     a new bundle replaces the old one wholesale;
#   * /Applications is usually not writable by a standard user, so the writes simply fail;
#   * writing into a signed bundle invalidates the signature, and macOS ties Accessibility and
#     Input Monitoring to that signature.
# So a frozen Mac app keeps its state where macOS expects it. Everything else is unchanged.
_MAC_SUPPORT = Path.home() / "Library" / "Application Support" / "WaveFlow"
_MAC_LOGS = Path.home() / "Library" / "Logs" / "WaveFlow"
# ONE place per Mac user — for the built app AND a source run. It used to be the app only, so the
# README's own steps (run from source, then build --install) made two WaveFlows: setup, permissions
# and a 631 MB model all landed in the source copy, and the installed app opened the wizard on every
# launch (clean reinstall on a Mac, 2026-09-16). WAVEFLOW_DATA still makes a checkout portable.
_MAC = sys.platform == "darwin"


def _mac_override() -> Path | None:
    o = os.environ.get("WAVEFLOW_DATA")
    return Path(o) if o else None


def app_dir() -> Path:
    """Where config.json lives. Computed from the CURRENT ROOT (tests may point ROOT elsewhere)."""
    if _MAC:
        return _mac_override() or _MAC_SUPPORT
    return ROOT if FROZEN else ROOT / "app"


def log_dir() -> Path:
    """Where waveflow.log lives. ~/Library/Logs on a Mac, which is also where Console.app looks;
    next to config.json everywhere else."""
    if _MAC:
        return _mac_override() or _MAC_LOGS
    return app_dir()


def migrate_mac_settings(log=print) -> bool:
    """Once: bring a source checkout's setup into the shared Mac folder.

    For everyone who already followed the old README — setup finished in the checkout, then the
    installed app started unconfigured. Runs only when the shared folder has NO config.json yet, so
    it can never overwrite anything. Config and vocabulary are copied; the downloaded models (hundreds
    of MB) are moved, not duplicated. A built app finds its checkout from the path build.py wrote into
    its Info.plist.
    """
    if not _MAC or _mac_override():
        return False
    dest = _MAC_SUPPORT
    if (dest / "config.json").exists():
        return False
    src_root = None
    if FROZEN:
        try:
            import plistlib
            info = plistlib.loads((Path(sys.executable).resolve().parent.parent / "Info.plist").read_bytes())
            src_root = Path(info.get("WaveFlowSourceCheckout", "")) if info.get("WaveFlowSourceCheckout") else None
        except Exception:
            src_root = None
    else:
        src_root = ROOT
    if not src_root or not (src_root / "app" / "config.json").is_file():
        return False
    try:
        import json
        cfg = json.loads((src_root / "app" / "config.json").read_text(encoding="utf-8"))
        if not cfg.get("setup_done"):
            return False
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_root / "app" / "config.json", dest / "config.json")
        src_data, dst_data = src_root / "data", dest / "data"
        dst_data.mkdir(parents=True, exist_ok=True)
        if (src_data / "models").is_dir() and not (dst_data / "models").exists():
            shutil.move(str(src_data / "models"), str(dst_data / "models"))
        for name in ("docker.env",):
            if (src_data / name).is_file() and not (dst_data / name).exists():
                shutil.copy2(src_data / name, dst_data / name)
        vocab = src_root / "server" / "vocab.user.json"
        if vocab.is_file() and not (dst_data / "vocab.user.json").exists():
            shutil.copy2(vocab, dst_data / "vocab.user.json")
        log(f"migrated settings from {src_root} -> {dest}")
        return True
    except Exception as e:
        log(f"settings migration from {src_root} failed: {e}")
        return False


APP_DIR = app_dir()                                   # config.json


def is_app_folder(p: Path) -> bool:
    """True only for a folder that is really WaveFlow's: the source checkout, or a folder holding a
    WaveFlow*.exe. Uninstall never schedules any other folder for deletion."""
    p = Path(p)
    if not p.is_dir() or p.parent == p or len(p.parts) <= 1:
        return False
    return (p / "app" / "waveflow.py").is_file() or any(p.glob("WaveFlow*.exe"))
SAMPLE_WAV = Path(__file__).resolve().parent / "assets" / "sample.wav"
SAMPLE_TEXT = "send it"

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

OPTIONS = ["local", "docker", "onsite", "vps", "connect"]
OPTION_NAMES = {"local": ("This Mac — background app" if IS_MAC else "This PC — background app"),
                "docker": ("This Mac — Docker" if IS_MAC else "This PC — Docker"),
                "onsite": "Onsite server", "vps": "Offsite VPS",
                "connect": "Connect to a server I already run"}
# The four above INSTALL a server. "connect" is the opposite: the engine is already running, so
# setup writes nothing, runs nothing and installs nothing — it saves an address and a token and
# tests them. Anything that acts on an install must exclude it, so those sets are named here
# instead of being spelled out at each call site (that is how "vps" got missed once already).
INSTALL_OPTIONS = ["local", "docker", "onsite", "vps"]
REMOTE_OPTIONS = ["onsite", "vps", "connect"]            # server elsewhere: needs an address
TOKEN_OPTIONS = ["docker", "onsite", "vps", "connect"]   # everything but the loopback local app
ENGINES = ["onnx-cpu", "onnx-gpu", "nemo"]
ENGINE_NAMES = {"onnx-cpu": "ONNX · CPU", "onnx-gpu": "ONNX · GPU", "nemo": "NeMo · NVIDIA GPU"}
# Docker service + host port per engine. Distinct ports so engines can run side by side.
DOCKER_SERVICE = {"onnx-cpu": ("waveflow", 8756), "nemo": ("waveflow-nemo", 8757),
                  "onnx-gpu": ("waveflow-onnx-gpu", 8759)}
THREADS_MIN, THREADS_MAX = 1, 64


# ---------------------------------------------------------------- hardware detection
def performance_cores() -> tuple[int, int]:
    """(performance cores, all physical cores).

    Threads should equal PHYSICAL PERFORMANCE cores: on a hybrid Intel CPU (8P+16E) onnxruntime
    with all 24 threads ran ~4x slower than with 4. Windows reports an EfficiencyClass per core;
    the highest class is the performance cores. A non-hybrid CPU has one class = every core.
    """
    if IS_WINDOWS:
        try:
            return _win_cores()
        except Exception:
            pass
    if IS_MAC:
        try:
            return _mac_cores()
        except Exception:
            pass
    n = os.cpu_count() or 4
    return max(1, n // 2), max(1, n // 2)       # assume SMT; never count hyperthreads


def _mac_cores() -> tuple[int, int]:
    """Apple Silicon splits cores the same way a hybrid Intel does, and macOS names them:
    hw.perflevel0.physicalcpu is the PERFORMANCE cores, perflevel1 the efficiency ones.
    An Intel Mac has no perflevel keys, so hw.physicalcpu is the answer there.

    The //2 fallback would be wrong on Apple Silicon in particular: it has no hyperthreading,
    so halving the count throws away half the real cores and the engine runs at half speed.
    """
    def sysctl(key):
        r = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0
    total = sysctl("hw.physicalcpu") or (os.cpu_count() or 4)
    perf = sysctl("hw.perflevel0.physicalcpu") or total
    return max(1, perf), max(1, total)


def _win_cores() -> tuple[int, int]:
    k32 = ctypes.windll.kernel32
    RelationProcessorCore = 0
    size = ctypes.c_ulong(0)
    k32.GetLogicalProcessorInformationEx(RelationProcessorCore, None, ctypes.byref(size))
    buf = (ctypes.c_byte * size.value)()
    if not k32.GetLogicalProcessorInformationEx(RelationProcessorCore, buf, ctypes.byref(size)):
        raise OSError("GetLogicalProcessorInformationEx failed")
    classes, off, raw = [], 0, bytes(buf)
    while off < size.value:
        # SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX: Relationship(4) Size(4) then
        # PROCESSOR_RELATIONSHIP: Flags(1) EfficiencyClass(1) ...
        rec = int.from_bytes(raw[off + 4:off + 8], "little")
        classes.append(raw[off + 9])
        off += rec
    return count_performance(classes), len(classes)


def count_performance(efficiency_classes: list[int]) -> int:
    """Cores in the highest efficiency class (all cores on a non-hybrid CPU)."""
    if not efficiency_classes:
        return 1
    top = max(efficiency_classes)
    return sum(1 for c in efficiency_classes if c == top)


def clamp_threads(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 4
    return max(THREADS_MIN, min(THREADS_MAX, n))


def onnx_providers() -> list[str]:
    """Asked in a SEPARATE process: importing onnxruntime here would lock its DLLs on Windows, and
    the wizard's "Install GPU support" button could then not replace it.

    Frozen, sys.executable is WaveFlow itself, not Python, and its argument parser rejected `-c`
    with exit 2 — read as "no providers", so a built app could never see DirectML or CoreML
    (found on a Mac 2026-09-16; identical on Windows). It asks itself via `--providers` instead,
    the same self re-entry `--serve` uses."""
    cmd = ([sys.executable, "--providers"] if FROZEN else
           [sys.executable, "-c", "import onnxruntime as o; print(','.join(o.get_available_providers()))"])
    try:
        r = subprocess.run(cmd,
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [p for p in r.stdout.strip().split(",") if p] if r.returncode == 0 else []
    except Exception:
        return []


def gpu_install_commands() -> list[list[str]]:
    """What to run so the GPU engine can work on THIS OS.

    Windows needs a different package: onnxruntime-directml replaces onnxruntime, and the two
    cannot be installed side by side, hence the uninstall first.

    macOS needs NOTHING. The official onnxruntime wheel for macOS ships the CoreML execution
    provider already. An earlier version of this function told Mac users to install
    `onnxruntime-silicon`, a third-party build whose last release was 1.16.3 in January 2024 —
    following that advice would have DOWNGRADED them from the current 1.30 and taken the CoreML
    support away with it. Checked against PyPI, 2026-09-16. Reinstalling the official wheel is
    the only sensible repair here, so that is what this offers.

    A frozen build returns NOTHING: pip cannot install into it, and `WaveFlow -m pip` is not even
    a valid command there. Whatever onnxruntime was bundled is final, so the wizard hides the button.
    """
    if FROZEN:
        return []
    py = sys.executable
    if IS_MAC:
        return [[py, "-m", "pip", "install", "--upgrade", "--force-reinstall", "onnxruntime"]]
    return [[py, "-m", "pip", "uninstall", "-y", "onnxruntime"],
            [py, "-m", "pip", "install", "onnxruntime-directml"]]


# Kept so nothing that already imports the old name breaks.
directml_install_commands = gpu_install_commands


def _runs(cmd: list[str], timeout: float = 6) -> bool:
    if not shutil.which(cmd[0]):
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except Exception:
        return False


@dataclass
class Hardware:
    perf_cores: int = 4
    all_cores: int = 4
    directml: bool = False          # Windows: any DirectX 12 GPU
    coreml: bool = False            # macOS: Apple Silicon or an Intel Mac's GPU
    cuda: bool = False
    docker: bool = False
    nvidia: bool = False


def detect() -> Hardware:
    p, a = performance_cores()
    prov = onnx_providers()
    return Hardware(perf_cores=p, all_cores=a,
                    directml="DmlExecutionProvider" in prov,
                    coreml="CoreMLExecutionProvider" in prov,
                    cuda="CUDAExecutionProvider" in prov,
                    docker=_runs(["docker", "version", "--format", "{{.Server.Version}}"]),
                    nvidia=_runs(["nvidia-smi", "-L"]))


# ---------------------------------------------------------------- option x engine matrix
@dataclass
class EngineChoice:
    engine: str
    label: str
    detail: str
    available: bool
    reason: str = ""          # why not available, or what must be installed first


def engines_for(option: str, method: str, hw: Hardware) -> list[EngineChoice]:
    """All three engines for an install option. None is ever omitted.

    "connect" returns NOTHING on purpose: the server is already running, so it has already decided
    which engine it uses. Offering a choice there would be a lie — picking one would change nothing.
    The wizard reads an empty list as "skip the Engine step".
    """
    out = []
    if option == "connect":
        return out
    if option == "local":
        out.append(EngineChoice("onnx-cpu", ENGINE_NAMES["onnx-cpu"],
                                f"int8 · 660 MB · {hw.perf_cores} performance cores", True))
        # The GPU story is per-OS and saying the wrong one is worse than saying none: DirectML
        # does not exist on a Mac, and CoreML does not exist on Windows. Same engine, different
        # accelerator, different install.
        if IS_MAC:
            # OFFERED BUT OFF. Tried on Apple Silicon 2026-09-16: CoreML aborts inside
            # onnxruntime while partitioning this graph —
            #   "is_in_range(access_tensor_rank) was false ... valid range [-2, 1]"
            # That is a bounds assert in ORT itself, not something a setting here can
            # avoid, and it happens before any audio is seen. The server falls back to CPU
            # rather than dying, but offering it as a working choice would be a lie.
            #
            # It stays VISIBLE with the reason, per this project's rule that an engine
            # which cannot run somewhere is shown greyed with the cause, never dropped.
            # Apple Silicon CPU is genuinely quick, so little is lost.
            out.append(EngineChoice("onnx-gpu", ENGINE_NAMES["onnx-gpu"],
                                    "fp32 · 2.4 GB · CoreML", False,
                                    "CoreML cannot run this model — onnxruntime aborts "
                                    "while preparing it. Apple Silicon CPU is fast; use "
                                    "ONNX · CPU."))
        else:
            out.append(EngineChoice("onnx-gpu", ENGINE_NAMES["onnx-gpu"],
                                    "fp32 · 2.4 GB · DirectML (any DirectX 12 GPU)", True,
                                    "" if hw.directml else
                                    "no DirectX 12 GPU found by this build" if FROZEN else
                                    "needs GPU support installed: pip install onnxruntime-directml"))
        out.append(EngineChoice("nemo", ENGINE_NAMES["nemo"], "max quality", False,
                                "needs Docker — choose “This PC — Docker”"))
    elif option == "docker":
        out.append(EngineChoice("onnx-cpu", ENGINE_NAMES["onnx-cpu"], "int8 · 660 MB", hw.docker,
                                "" if hw.docker else "Docker not found"))
        gpu_ok = hw.docker and hw.nvidia
        why = "" if gpu_ok else ("Docker not found" if not hw.docker else "no NVIDIA GPU found")
        out.append(EngineChoice("onnx-gpu", ENGINE_NAMES["onnx-gpu"],
                                "fp32 · CUDA · NVIDIA GPU", gpu_ok, why))
        out.append(EngineChoice("nemo", ENGINE_NAMES["nemo"],
                                "max quality · 1.6 GB VRAM · ~20 GB disk to build", gpu_ok, why))
    else:  # onsite / vps — the server is elsewhere; we cannot probe it, so all are offered
        venv = option == "onsite" and method == "venv"
        out.append(EngineChoice("onnx-cpu", ENGINE_NAMES["onnx-cpu"], "int8 · 660 MB · any 4+ core CPU", True))
        out.append(EngineChoice("onnx-gpu", ENGINE_NAMES["onnx-gpu"],
                                "fp32 · CUDA (Linux, NVIDIA), DirectML (Windows) or CoreML (Mac)", True))
        out.append(EngineChoice("nemo", ENGINE_NAMES["nemo"], "max quality · 1.6 GB VRAM",
                                not venv, "needs Docker — choose Docker as the install method" if venv else ""))
    return out


# ---------------------------------------------------------------- plan: what gets written/run
@dataclass
class Choices:
    option: str = "local"
    engine: str = "onnx-cpu"
    method: str = "docker"             # onsite only: "docker" | "venv"
    threads: int = 4
    auto_threads: bool = True
    address: str = ""                  # onsite: http://host:port ; vps: https://domain
    token: str = ""
    lan: bool = False                  # docker on this PC: reachable from the network


def new_token() -> str:
    return secrets.token_urlsafe(32)


def app_data() -> Path:
    """Everything WaveFlow writes (models, docker .env, engine log) lives INSIDE its own folder, so
    two copies on one PC never share files and deleting the folder removes it completely.
    WAVEFLOW_DATA overrides the location."""
    override = os.environ.get("WAVEFLOW_DATA")
    if override:
        return Path(override)
    # Frozen Mac: outside the bundle, for the reasons at the top of this file. An update must not
    # take the user's models with it.
    return (_MAC_SUPPORT / "data") if _MAC else (ROOT / "data")


def models_dir() -> Path:
    return app_data() / "models"


def server_args(c: Choices, host: str = "127.0.0.1", port: int = 8756) -> list[str]:
    """parakeet_server.py arguments for an ONNX engine (local app or venv)."""
    if c.engine == "onnx-gpu":
        # Local means THIS machine, so the accelerator is this machine's. A remote server is
        # assumed to be Linux+NVIDIA, which is what the Docker images build for.
        if c.option == "local":
            dev = "coreml" if IS_MAC else "dml"
        else:
            dev = "dml" if IS_WINDOWS else "cuda"
        a = ["--engine", "onnx", "--onnx-quant", "fp32", "--device", dev]
    else:
        a = ["--engine", "onnx", "--onnx-quant", "int8", "--device", "cpu",
             "--threads", str(clamp_threads(c.threads))]
    return a + ["--host", host, "--port", str(port)]


@dataclass
class Plan:
    url: str
    token: str
    config: dict
    env_path: str = ""
    env_text: str = ""
    commands: list[str] = field(default_factory=list)   # shown; run_here=True means the app runs them
    run_here: bool = False
    where: str = "Setup will run"


def build_plan(c: Choices) -> Plan:
    th = clamp_threads(c.threads)
    engine_cfg = {"mode": c.option, "engine": c.engine, "threads": th, "auto_threads": c.auto_threads}
    if c.option == "connect":
        # Nothing is written and nothing is run. No .env, no compose command, no engine and no
        # thread count — those all belong to whoever started that server, and guessing them here
        # would put wrong numbers in config.json and wrong claims on the Finish page.
        url = normalize_url(c.address)
        return Plan(url, c.token, {"url": url, "token": c.token, "engine": {"mode": "connect"}},
                    commands=[], where="Nothing is installed")
    if c.option == "local":
        url = "http://127.0.0.1:8756"
        cmd = "parakeet_server.py " + " ".join(server_args(c))
        engine_cfg.update({"device": ("coreml" if IS_MAC else "dml")
                           if c.engine == "onnx-gpu" else "cpu"})
        return Plan(url, "", {"url": url, "token": "", "engine": engine_cfg},
                    commands=[cmd], run_here=True, where="The app runs the engine")
    if c.option == "docker":
        svc, port = DOCKER_SERVICE[c.engine]
        env = app_data() / "docker.env"
        text = (f"WAVEFLOW_TOKEN={c.token}\nHOST_BIND={'0.0.0.0' if c.lan else '127.0.0.1'}\n"
                f"THREADS={th}\n")
        prof = {"nemo": " --profile nemo", "onnx-gpu": " --profile onnx-gpu"}.get(c.engine, "")
        cmd = (f'docker compose -f "{ROOT / "docker" / "compose.yml"}" --env-file "{env}"{prof} '
               f"up -d --build {svc}")
        url = f"http://127.0.0.1:{port}"
        return Plan(url, c.token, {"url": url, "token": c.token, "engine": engine_cfg},
                    env_path=str(env), env_text=text, commands=[cmd], run_here=True,
                    where="Setup will write and run")
    if c.option == "onsite":
        url = normalize_url(c.address, default_port=DOCKER_SERVICE[c.engine][1])
        engine_cfg["method"] = c.method
        if c.method == "venv":
            args = " ".join(server_args(c, host="0.0.0.0", port=urlparse(url).port or 8756))
            req = "requirements-server.txt"
            cmds = [f"pip install -r {req}" + ("  # then: pip install onnxruntime-gpu" if c.engine == "onnx-gpu" else ""),
                    f"export WAVEFLOW_TOKEN={c.token}",
                    f"python server/parakeet_server.py {args}"]
            return Plan(url, c.token, {"url": url, "token": c.token, "engine": engine_cfg},
                        commands=cmds, where="Run on your server")
        svc, _ = DOCKER_SERVICE[c.engine]
        prof = {"nemo": " --profile nemo", "onnx-gpu": " --profile onnx-gpu"}.get(c.engine, "")
        text = f"WAVEFLOW_TOKEN={c.token}\nHOST_BIND=0.0.0.0\nTHREADS={th}\n"
        return Plan(url, c.token, {"url": url, "token": c.token, "engine": engine_cfg},
                    env_path="docker/.env", env_text=text,
                    commands=[f"docker compose -f docker/compose.yml{prof} up -d --build {svc}"],
                    where="Run on your server")
    # vps
    url = normalize_url(c.address, https=True)
    host = urlparse(url).hostname or ""
    dockerfile = {"onnx-cpu": "docker/onnx/Dockerfile", "onnx-gpu": "docker/onnx-gpu/Dockerfile",
                  "nemo": "docker/nemo/Dockerfile"}[c.engine]
    text = (f"WAVEFLOW_DOMAIN={host}\nWAVEFLOW_TOKEN={c.token}\nTHREADS={th}\n"
            f"WAVEFLOW_DOCKERFILE={dockerfile}\n")
    gpu = " -f docker/compose.gpu.yml" if c.engine in ("onnx-gpu", "nemo") else ""
    return Plan(url, c.token, {"url": url, "token": c.token, "engine": engine_cfg},
                env_path="docker/.env", env_text=text,
                commands=[f"docker compose -f docker/compose.vps.yml{gpu} up -d --build"],
                where="Run on your VPS")


def choices_from_config(cfg: dict) -> Choices:
    """The Choices a saved config came from, so Settings can rebuild the same plan."""
    e = cfg.get("engine") or {}
    mode = e.get("mode") if e.get("mode") in OPTIONS else "local"
    return Choices(option=mode, engine=e.get("engine", "onnx-cpu") if e.get("engine") in ENGINES else "onnx-cpu",
                   method=e.get("method", "docker"), threads=clamp_threads(e.get("threads", 4)),
                   auto_threads=e.get("auto_threads", True),
                   address=cfg.get("url", "") if mode in REMOTE_OPTIONS else "", token=cfg.get("token", ""))


def replace_env_value(text: str, key: str, value: str) -> str:
    """Set KEY=value in .env text, keeping every other line as it was."""
    lines, done = [], False
    for line in text.splitlines():
        if line.split("=", 1)[0].strip() == key:
            lines.append(f"{key}={value}")
            done = True
        else:
            lines.append(line)
    if not done:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


@dataclass
class Rotation:
    kind: str                 # "none" (local: no token) | "here" (the app does it) | "server" (you do it)
    env_path: str = ""
    env_text: str = ""
    commands: list[str] = field(default_factory=list)


def rotation_plan(cfg: dict, new: str) -> Rotation:
    """What rotating the token means for this install. The old token stops working once the server
    restarts with the new one; the app saves the new token at the same moment."""
    c = choices_from_config(cfg)
    if c.option == "local":
        return Rotation("none")
    if c.option == "connect":
        # We did not install that server, so we do not know whether it runs under Docker, a venv or
        # something else. Printing a compose command we cannot stand behind would be worse than
        # saying plainly that the change happens there.
        return Rotation("server", commands=[
            f"# Set WAVEFLOW_TOKEN={new} on your server,",
            "# then restart it. WaveFlow saves this token at the same moment."])
    c.token = new
    plan = build_plan(c)
    if c.option == "docker":
        env = Path(plan.env_path)
        old = env.read_text(encoding="utf-8") if env.exists() else plan.env_text
        return Rotation("here", plan.env_path, replace_env_value(old, "WAVEFLOW_TOKEN", new),
                        [plan.commands[0].replace(" --build", "")])
    if c.option == "onsite" and c.method == "venv":
        return Rotation("server", commands=[f"export WAVEFLOW_TOKEN={new}",
                                            "# then restart python server/parakeet_server.py"])
    up = plan.commands[0].replace(" --build", "")
    return Rotation("server", commands=[f"sed -i 's/^WAVEFLOW_TOKEN=.*/WAVEFLOW_TOKEN={new}/' docker/.env", up])


# ---------------------------------------------------------------- microphone sensitivity
SENSITIVITIES = ["high", "balanced", "low"]
# Same table and blend as server/parakeet_server.py SENSITIVITY / sensitivity_params (a test holds
# them equal). The app's slider sends a number: 0 = high (hears whispers), 50 = balanced, 100 = low.
SENS_PRESETS = {"high": {"vad_mode": 0, "sustain_s": 0.45, "k": 2.0},
                "balanced": {"vad_mode": 1, "sustain_s": 0.60, "k": 3.0},
                "low": {"vad_mode": 3, "sustain_s": 0.80, "k": 4.5}}
SENS_VALUE = {"high": 0, "balanced": 50, "low": 100}
SENSITIVITY_K = {k: v["k"] for k, v in SENS_PRESETS.items()}
VAD_MIN, VAD_MAX = 0.0015, 0.0100    # the server's clamps on its speech threshold
FLOOR_MIN = 0.0005                    # a noise-gated mic reads ~0; never divide by that
METER_SPAN = 40.0                     # the meter's right edge = 40 x the room's noise floor


def sensitivity_value(v) -> float:
    """Config value (preset name or 0-100) -> 0-100. Unknown -> balanced."""
    if isinstance(v, str) and v in SENS_VALUE:
        return float(SENS_VALUE[v])
    try:
        return min(100.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 50.0


def sensitivity_params(v) -> dict:
    x = sensitivity_value(v)
    if x <= 50:
        a, b, t = SENS_PRESETS["high"], SENS_PRESETS["balanced"], x / 50
    else:
        a, b, t = SENS_PRESETS["balanced"], SENS_PRESETS["low"], (x - 50) / 50
    return {"vad_mode": int(round(a["vad_mode"] + (b["vad_mode"] - a["vad_mode"]) * t)),
            "sustain_s": a["sustain_s"] + (b["sustain_s"] - a["sustain_s"]) * t,
            "k": a["k"] + (b["k"] - a["k"]) * t}


def sensitivity_label(v) -> str:
    x = sensitivity_value(v)
    return "High" if x < 25 else "Low" if x > 75 else "Balanced"


class LevelTracker:
    """The room's noise floor measured the way the server measures it: the 10th percentile of the
    last ~9 s of mic levels. The speech line is that floor x k, inside the server's clamps.

    The first version kept a running floor with no lower bound. A headset with a noise gate sends
    near-zero between words, the floor sank to 0.00001, and every tiny sound read as 'maxed' —
    the meter bounced at full with nobody talking (operator, 2026-09-15)."""

    def __init__(self, window: int = 180):
        from collections import deque
        self.hist = deque(maxlen=window)

    def floor(self) -> float:
        if len(self.hist) < 10:
            return VAD_MIN
        vals = sorted(self.hist)
        return max(FLOOR_MIN, vals[int(len(vals) * 0.10)])

    def threshold(self, k: float) -> float:
        return min(VAD_MAX, max(VAD_MIN, self.floor() * k))

    def push(self, rms: float) -> float:
        self.hist.append(max(0.0, float(rms)))
        return float(rms)


def meter_pos(level: float, floor: float) -> float:
    """0..1 on a log scale: the floor sits at 0, METER_SPAN x the floor at 1."""
    floor = max(FLOOR_MIN, floor)
    if level <= floor:
        return 0.0
    return min(1.0, math.log(level / floor) / math.log(METER_SPAN))


# ---------------------------------------------------------------- hotkey <-> Qt key sequence
# Qt on macOS names ⌘ Command "Ctrl" and ⌃ Control "Meta" (so Ctrl+C means Copy everywhere).
# The recorder used to save Qt's text as-is, with Meta renamed "windows" — so pressing ⌃⌥W saved
# "windows+alt+w", which the Mac tap reads as ⌘⌥W, and loading turned a correct "ctrl+alt+w" into
# ⌥⌘W on screen. It looked like the hotkey never saved (Mac, 2026-09-16). One converter, used on
# save AND load, by the wizard AND Settings, is the only way the two stay inverses.
_QT_TO_HK_MAC = {"ctrl": "cmd", "meta": "ctrl", "alt": "alt", "shift": "shift"}
_QT_TO_HK_WIN = {"ctrl": "ctrl", "meta": "windows", "alt": "alt", "shift": "shift"}
_HK_TO_QT_MAC = {"cmd": "Ctrl", "command": "Ctrl", "win": "Ctrl", "windows": "Ctrl", "meta": "Ctrl",
                 "ctrl": "Meta", "control": "Meta", "alt": "Alt", "option": "Alt", "shift": "Shift"}
_HK_TO_QT_WIN = {"ctrl": "Ctrl", "control": "Ctrl", "win": "Meta", "windows": "Meta", "meta": "Meta",
                 "cmd": "Meta", "command": "Meta", "alt": "Alt", "option": "Alt", "shift": "Shift"}


def qt_to_hotkey(seq: str, mac: bool | None = None) -> str:
    """QKeySequence.toString() (portable text) -> config string, e.g. 'Meta+Alt+W' -> 'ctrl+alt+w'
    on a Mac. Only the first chord counts; "" when nothing was recorded."""
    mac = IS_MAC if mac is None else mac
    table = _QT_TO_HK_MAC if mac else _QT_TO_HK_WIN
    first = (seq or "").split(", ")[0].strip()
    if not first:
        return ""
    parts = first.split("+")
    if first.endswith("++"):                       # the "+" key itself
        parts = first[:-2].split("+") + ["+"]
    return "+".join(table.get(p.lower(), p.lower().replace(" ", "")) for p in parts if p)


def hotkey_to_qt(kb: str, mac: bool | None = None) -> str:
    """Config string -> text for QKeySequence(...). The exact inverse of qt_to_hotkey."""
    mac = IS_MAC if mac is None else mac
    table = _HK_TO_QT_MAC if mac else _HK_TO_QT_WIN
    return "+".join(table.get(p, p.capitalize() if len(p) > 1 else p.upper())
                    for p in (kb or "").lower().replace(" ", "").split("+") if p)


# ---------------------------------------------------------------- start with Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "WaveFlow"


def launch_parts() -> tuple[str, str]:
    """(program, arguments) that start the app with NO console window: the .exe itself, or the
    venv's pythonw.exe running waveflow.py. A console launch dies when its window is closed."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), f'"{ROOT / "app" / "waveflow.py"}"'


def autostart_command() -> str:
    prog, args = launch_parts()
    return f'"{prog}" {args}'.strip()


def console_launch() -> bool:
    """True when the app was started from a terminal and would die with it.

    Windows: `python.exe waveflow.py` (not the frozen .exe, not pythonw).
    macOS:   any unfrozen run whose stdout is a terminal. Closing Terminal sends SIGHUP to every
             process in its session, so a plain `venv/bin/python app/waveflow.py` dies with the
             window. Reported from a real Mac, 2026-09-16: "once i close the terminal window the
             app goes away and i cant reopen it".
    """
    if getattr(sys, "frozen", False):
        return False
    if IS_WINDOWS:
        return Path(sys.executable).name.lower() == "python.exe"
    if IS_MAC:
        try:
            return sys.stdout is not None and sys.stdout.isatty()
        except Exception:
            return False
    return False


def relaunch_detached() -> bool:
    """Start the same app outside this terminal, so closing the terminal does not close WaveFlow.

    Returns False if it could not, and the caller then just keeps running here — a WaveFlow tied
    to a terminal is worse than no WaveFlow, but only slightly.
    """
    if IS_MAC:
        # start_new_session=True is setsid(): the child leads its own session with no controlling
        # terminal, so the SIGHUP that Terminal sends on close never reaches it. The standard
        # streams must go to devnull as well — a process writing to a closed terminal dies of
        # SIGPIPE, which would undo the whole point.
        try:
            with open(os.devnull, "r+b") as null:
                subprocess.Popen([sys.executable, str(ROOT / "app" / "waveflow.py")],
                                 start_new_session=True, cwd=str(ROOT),
                                 stdin=null, stdout=null, stderr=null, close_fds=True)
            return True
        except OSError:
            return False
    prog, _ = launch_parts()
    if Path(prog).name.lower() != "pythonw.exe":
        return False
    cmd = [prog, str(ROOT / "app" / "waveflow.py")]
    base = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for flags in (base | 0x01000000, base):          # CREATE_BREAKAWAY_FROM_JOB, then without
        try:
            subprocess.Popen(cmd, creationflags=flags, cwd=str(ROOT), close_fds=True)
            return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------- shortcuts (Start menu + desktop)
SHORTCUT_NAME = "WaveFlow.lnk"


def shortcut_paths() -> list[Path]:
    """This user's own Start menu and desktop. Nothing machine-wide, no admin rights."""
    out = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORTCUT_NAME)
    try:
        import ctypes.wintypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)   # CSIDL_DESKTOPDIRECTORY
        if buf.value:
            out.append(Path(buf.value) / SHORTCUT_NAME)
    except Exception:
        home = Path.home() / "Desktop"
        if home.exists():
            out.append(home / SHORTCUT_NAME)
    return out


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def create_shortcuts() -> list[str]:
    """Create/refresh the Start menu and desktop shortcuts. Returns error lines.

    macOS does not do shortcuts: an .app IS the icon, and the user drags it to /Applications
    or the Dock themselves. Making files on their Desktop uninvited would be rude, so this
    does nothing there and the wizard hides the row.
    """
    if not IS_WINDOWS:
        return []
    prog, args = launch_parts()
    icon = Path(sys.executable) if FROZEN else ROOT / "app" / "assets" / "waveflow.ico"   # the .exe carries it
    errors = []
    for lnk in shortcut_paths():
        ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(lnk) + "); "
              "$s.TargetPath = " + _ps_quote(prog) + "; $s.Arguments = " + _ps_quote(args) + "; "
              "$s.WorkingDirectory = " + _ps_quote(ROOT) + "; $s.IconLocation = " + _ps_quote(icon) + "; "
              "$s.Description = 'WaveFlow dictation'; $s.Save()")
        try:
            lnk.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                               timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not lnk.exists():
                errors.append(f"{lnk}: {(r.stderr or r.stdout).strip()[-160:]}")
        except Exception as e:
            errors.append(f"{lnk}: {e}")
    return errors


def shortcuts_ours() -> list[Path]:
    """Existing shortcuts that point at THIS copy (another copy's shortcut is left alone)."""
    out = []
    target = str(ROOT).lower()
    for lnk in shortcut_paths():
        if not lnk.exists():
            continue
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(lnk) + "); "
                                "$s.TargetPath + '|' + $s.Arguments + '|' + $s.WorkingDirectory"],
                               capture_output=True, text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if target in r.stdout.lower():
                out.append(lnk)
        except Exception:
            pass
    return out


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        import osbridge
        return osbridge.autostart_enabled()      # macOS: a LaunchAgent plist
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0] == autostart_command()
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    """Only this user's Run entry named WaveFlow; nothing machine-wide.

    macOS has no registry: osbridge writes a LaunchAgent plist in the user's own
    ~/Library/LaunchAgents, which needs no admin rights either.
    """
    if not IS_WINDOWS:
        import osbridge
        osbridge.set_autostart(on)
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def normalize_url(s: str, https: bool = False, default_port: int = 8756) -> str:
    s = (s or "").strip().rstrip("/")
    if not s:
        return ""
    if "://" not in s:
        s = ("https://" if https else "http://") + s
    u = urlparse(s)
    if not https and u.port is None and u.scheme == "http":
        s = f"{u.scheme}://{u.hostname}:{default_port}"
    return s


def is_public_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        # Tailscale's 100.64.0.0/10 is CGNAT space — private for our purposes
        return not (ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10"))
    except ValueError:
        return "." in host and not host.endswith((".local", ".lan", ".home", ".internal", ".ts.net"))


def validate(c: Choices) -> list[str]:
    """Problems that block Continue. Empty list = OK."""
    errs = []
    if c.option == "connect":
        # A token is OPTIONAL here, and that is the whole point of this mode. The other options
        # install the server, so they set the token and can insist on one. We install nothing:
        # the server was started by the operator, possibly with no WAVEFLOW_TOKEN at all, and
        # demanding a token we cannot create would lock him out of his own machine.
        # (Found 2026-09-15: the operator's own LAN box runs tokenless.)
        if c.token and len(c.token) < 16:
            errs.append("That token looks too short. Copy the server's WAVEFLOW_TOKEN exactly, "
                        "or clear the box if the server has no token.")
        if not c.token:
            host = urlparse(normalize_url(c.address)).hostname or ""
            if is_public_host(host):
                # No token on a private LAN or Tailscale address is the operator's business. On a
                # PUBLIC address it means anyone who finds the port can send audio to the engine,
                # and that is not a warning, it is a refusal.
                errs.append("A server on a public address must have a token — without one, anyone "
                            "who finds it can use it. Start it with WAVEFLOW_TOKEN set.")
    elif c.option in TOKEN_OPTIONS and len(c.token) < 16:
        errs.append("A token of at least 16 characters is required.")
    if c.option in REMOTE_OPTIONS and not c.address.strip():
        errs.append("Enter your domain." if c.option == "vps" else "Enter the server address.")
    if c.option == "vps" and c.address.strip().lower().startswith("http://"):
        errs.append("A VPS must use https:// — audio would cross the internet unencrypted.")
    if c.option == "onsite" and c.method == "venv" and c.engine == "nemo":
        errs.append("NeMo needs Docker. Choose Docker, or an ONNX engine.")
    if not (THREADS_MIN <= int(c.threads) <= THREADS_MAX):
        errs.append(f"CPU threads must be {THREADS_MIN}–{THREADS_MAX}.")
    return errs


def warnings(c: Choices) -> list[str]:
    w = []
    if c.option in ("onsite", "connect") and c.address:
        u = urlparse(normalize_url(c.address))
        if u.scheme == "http" and is_public_host(u.hostname or ""):
            w.append("This looks like a public address. Plain http sends audio unencrypted — "
                     "put the server behind https, or reach it over Tailscale or a VPN.")
    if c.option == "connect":
        if not c.token and c.address:
            w.append("This server has no token, so anything that can reach that address can use "
                     "it. Fine on a private LAN or Tailscale; set WAVEFLOW_TOKEN if that changes.")
        return w        # no engine or thread advice: those belong to the server, not to us
    if c.engine == "onnx-cpu" and not c.auto_threads:
        w.append("More threads than performance cores is usually slower, not faster.")
    return w


# ---------------------------------------------------------------- test connection
@dataclass
class Check:
    name: str
    ok: bool | None          # None = not run
    detail: str = ""


def _resolves(host: str, timeout: float = 6.0) -> bool:
    """Can this machine turn `host` into an address within `timeout`?

    getaddrinfo cannot be interrupted, so it is run on a daemon thread and simply abandoned if it
    overruns. The thread dies with the process; the point is that the CALLER gets an answer and
    the UI never freezes waiting for a name that is never coming.
    """
    import socket
    try:
        ipaddress.ip_address(host)
        return True                      # a literal address needs no lookup
    except ValueError:
        pass
    done: list[bool] = []

    def lookup():
        try:
            socket.getaddrinfo(host, None)
            done.append(True)
        except Exception:
            done.append(False)

    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    return bool(done and done[0])


def run_checks(url: str, token: str, sample: Path = SAMPLE_WAV, timeout: float = 6.0,
               require_token: bool = False) -> tuple[list[Check], str]:
    """The same 4 checks for every option. Returns (checks, one-line verdict)."""
    checks = [Check("Server answers", None), Check("Engine ready", None),
              Check("Token accepted", None), Check("Sample clip transcribed", None)]
    hdr = {"Authorization": f"Bearer {token}"} if token else {}
    # Resolve the name FIRST, with a timeout of its own.
    #
    # requests' `timeout` covers connect and read — it does NOT cover name resolution, because
    # getaddrinfo() is a blocking C call that no Python timeout can interrupt. So a name that
    # never resolves (a .local host on a network without mDNS, a Tailscale name with Tailscale
    # not running here) hangs the whole check with no message and no way out. Reported from a
    # real Mac, 2026-09-15: "the test is hanging".
    host = urlparse(url).hostname or ""
    if host and not _resolves(host, timeout):
        checks[0] = Check("Server answers", False, "name not found")
        return checks, (f"Can't find “{host}” from this machine. Check the spelling, and "
                        f"that this machine can reach it: a .local name needs the server on the "
                        f"same network, and a Tailscale name needs Tailscale running here too.")
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{url}/health", timeout=timeout)
        ms = int((time.perf_counter() - t0) * 1000)
        checks[0] = Check("Server answers", True, f"{ms} ms")
        h = r.json()
    except requests.exceptions.SSLError:
        checks[0] = Check("Server answers", False, "certificate")
        return checks, "Certificate problem. Check the domain's HTTPS certificate (Caddy needs ports 80 and 443 open)."
    except Exception:
        checks[0] = Check("Server answers", False, "no reply")
        return checks, f"Can't reach {url}. Is the server running, and is the address right?"
    if h.get("status") != "ok":
        checks[1] = Check("Engine ready", False, "loading")
        return checks, "The engine is still loading (the first start downloads the model). Wait, then test again."
    checks[1] = Check("Engine ready", True, str(h.get("model", "")))
    auth = bool(h.get("auth"))
    if require_token and not auth:
        checks[2] = Check("Token accepted", False, "server has no token")
        return checks, "The server accepts anyone. Start it with WAVEFLOW_TOKEN set."
    try:
        wav = Path(sample).read_bytes()
        t0 = time.perf_counter()
        r = requests.post(f"{url}/v1/audio/transcriptions", headers=hdr, timeout=max(timeout, 30),
                          files={"file": ("sample.wav", wav, "audio/wav")}, data={"model": "default"})
        ms = int((time.perf_counter() - t0) * 1000)
    except Exception as e:
        checks[2] = Check("Token accepted", False, "error")
        return checks, f"Request failed: {type(e).__name__}."
    if r.status_code == 401:
        checks[2] = Check("Token accepted", False, "401")
        return checks, "Token rejected. Copy the token again into the server, restart it, and test again."
    checks[2] = Check("Token accepted", True, "ok" if auth else "local only — no token")
    if r.status_code != 200:
        checks[3] = Check("Sample clip transcribed", False, str(r.status_code))
        return checks, f"The server returned an error ({r.status_code})."
    text = (r.json().get("text") or "").strip()
    heard = "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").strip()
    if SAMPLE_TEXT not in heard:
        checks[3] = Check("Sample clip transcribed", False, f"heard “{text}”")
        return checks, "The server answered, but did not hear the sample correctly."
    checks[3] = Check("Sample clip transcribed", True, f"{ms / 1000:.2f} s")
    verdict = f"Heard “{text.rstrip('.')}”."
    if ms > 1500:
        verdict += " It is slow — live mode may lag. Try the GPU engine or check CPU threads."
    return checks, verdict
