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
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_WAV = Path(__file__).resolve().parent / "assets" / "sample.wav"
SAMPLE_TEXT = "send it"

OPTIONS = ["local", "docker", "onsite", "vps"]
OPTION_NAMES = {"local": "This PC — background app", "docker": "This PC — Docker",
                "onsite": "Onsite server", "vps": "Offsite VPS"}
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
    if sys.platform == "win32":
        try:
            return _win_cores()
        except Exception:
            pass
    n = os.cpu_count() or 4
    return max(1, n // 2), max(1, n // 2)       # assume SMT; never count hyperthreads


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
    the wizard's "Install GPU support" button could then not replace it."""
    try:
        r = subprocess.run([sys.executable, "-c",
                            "import onnxruntime as o; print(','.join(o.get_available_providers()))"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [p for p in r.stdout.strip().split(",") if p] if r.returncode == 0 else []
    except Exception:
        return []


def directml_install_commands() -> list[list[str]]:
    """Swap the CPU build of onnxruntime for the DirectML build (they cannot be installed together)."""
    py = sys.executable
    return [[py, "-m", "pip", "uninstall", "-y", "onnxruntime"],
            [py, "-m", "pip", "install", "onnxruntime-directml"]]


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
    directml: bool = False
    cuda: bool = False
    docker: bool = False
    nvidia: bool = False


def detect() -> Hardware:
    p, a = performance_cores()
    prov = onnx_providers()
    return Hardware(perf_cores=p, all_cores=a, directml="DmlExecutionProvider" in prov,
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
    """All three engines for an install option. None is ever omitted."""
    out = []
    if option == "local":
        out.append(EngineChoice("onnx-cpu", ENGINE_NAMES["onnx-cpu"],
                                f"int8 · 660 MB · {hw.perf_cores} performance cores", True))
        out.append(EngineChoice("onnx-gpu", ENGINE_NAMES["onnx-gpu"],
                                "fp32 · 2.4 GB · DirectML (any DirectX 12 GPU)", True,
                                "" if hw.directml else
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
                                "fp32 · CUDA (Linux, NVIDIA) or DirectML (Windows)", True))
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
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "WaveFlow"


def server_args(c: Choices, host: str = "127.0.0.1", port: int = 8756) -> list[str]:
    """parakeet_server.py arguments for an ONNX engine (local app or venv)."""
    if c.engine == "onnx-gpu":
        dev = "dml" if (c.option == "local" or sys.platform == "win32") else "cuda"
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
    if c.option == "local":
        url = "http://127.0.0.1:8756"
        cmd = "parakeet_server.py " + " ".join(server_args(c))
        engine_cfg.update({"device": "dml" if c.engine == "onnx-gpu" else "cpu"})
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
    if c.option in ("docker", "onsite", "vps") and len(c.token) < 16:
        errs.append("A token of at least 16 characters is required.")
    if c.option in ("onsite", "vps") and not c.address.strip():
        errs.append("Enter the server address." if c.option == "onsite" else "Enter your domain.")
    if c.option == "vps" and c.address.strip().lower().startswith("http://"):
        errs.append("A VPS must use https:// — audio would cross the internet unencrypted.")
    if c.option == "onsite" and c.method == "venv" and c.engine == "nemo":
        errs.append("NeMo needs Docker. Choose Docker, or an ONNX engine.")
    if not (THREADS_MIN <= int(c.threads) <= THREADS_MAX):
        errs.append(f"CPU threads must be {THREADS_MIN}–{THREADS_MAX}.")
    return errs


def warnings(c: Choices) -> list[str]:
    w = []
    if c.option == "onsite" and c.address:
        host = urlparse(normalize_url(c.address)).hostname or ""
        if urlparse(normalize_url(c.address)).scheme == "http" and is_public_host(host):
            w.append("This looks like a public address. Plain http sends audio unencrypted — use the VPS option.")
    if c.engine == "onnx-cpu" and not c.auto_threads:
        w.append("More threads than performance cores is usually slower, not faster.")
    return w


# ---------------------------------------------------------------- test connection
@dataclass
class Check:
    name: str
    ok: bool | None          # None = not run
    detail: str = ""


def run_checks(url: str, token: str, sample: Path = SAMPLE_WAV, timeout: float = 6.0,
               require_token: bool = False) -> tuple[list[Check], str]:
    """The same 4 checks for every option. Returns (checks, one-line verdict)."""
    import requests
    checks = [Check("Server answers", None), Check("Engine ready", None),
              Check("Token accepted", None), Check("Sample clip transcribed", None)]
    hdr = {"Authorization": f"Bearer {token}"} if token else {}
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
