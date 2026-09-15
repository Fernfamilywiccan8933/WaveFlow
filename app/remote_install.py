"""Install the engine on an Onsite server or VPS over SSH — design/mocks/wizard-onsite-ssh-v1.html (A).

The wizard never asks for or stores a password: ssh runs with BatchMode, so it uses the user's own
SSH key or fails with a clear "add your key" message. The token reaches the server on ssh's stdin
(written into docker/.env), never on a command line where `ps` or a log could show it.

Steps (the checklist the wizard draws, in order):
  0 Connect over SSH · 1 Check Docker and GPU · 2 Copy server files · 3 Write .env with the token
  4 Build and start the container · 5 Wait for the engine
"""
from __future__ import annotations

import io
import re
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import setup_logic as S

STEPS = ["Connect over SSH", "Check Docker and GPU", "Copy server files", "Write .env with the token",
         "Build and start the container", "Wait for the engine"]
NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SAFE_FOLDER = re.compile(r"^~?[A-Za-z0-9_./-]+$")
SAFE_USER = re.compile(r"^[A-Za-z0-9_.-]+$")
DISK_GB = {"onnx-cpu": 4, "onnx-gpu": 8, "nemo": 20}     # image + model, with headroom
# Only what the server needs. Never the user's own vocabulary, a local .env, or caches.
SHIP = ["server", "docker", "requirements-server.txt", "LICENSE", "NOTICE.md"]
SKIP_NAMES = {"__pycache__", ".env", "vocab.user.json"}


@dataclass
class Probe:
    ok: bool
    kind: str = ""                 # "" | auth | unreachable | docker-missing | docker-permission | no-gpu | disk
    message: str = ""
    docker: str = ""
    gpus: list[str] = field(default_factory=list)
    nvidia_runtime: bool = False
    free_gb: float = 0.0


def host_of(address: str) -> str:
    a = address.strip()
    if "://" not in a:
        a = "http://" + a
    return urlparse(a).hostname or ""


def ssh_cmd(user: str, host: str, remote: str) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
            f"{user}@{host}", remote]


def _ssh(user, host, remote, stdin: bytes | None = None, timeout=60):
    return subprocess.run(ssh_cmd(user, host, remote), input=stdin, capture_output=True, timeout=timeout,
                          creationflags=NOWINDOW)


def classify_ssh_error(stderr: str) -> tuple[str, str]:
    e = stderr.lower()
    if "permission denied" in e or "publickey" in e:
        return "auth", "The server did not accept an SSH key. Add yours once, then press Try again."
    if "could not resolve" in e or "name or service not known" in e:
        return "unreachable", "That server name was not found. Check the name, or use its IP address."
    if "timed out" in e or "no route" in e or "refused" in e:
        return "unreachable", "Could not reach the server on SSH (port 22). Is it on and on your network?"
    if "host key verification failed" in e or "remote host identification has changed" in e:
        return "unreachable", "The server's SSH identity changed. Check ~/.ssh/known_hosts before continuing."
    return "unreachable", "SSH failed: " + stderr.strip()[-160:]


def key_help(user: str, host: str) -> list[str]:
    return ["ssh-keygen -t ed25519", f"ssh-copy-id {user}@{host}"]


def validate_target(user: str, host: str, folder: str) -> list[str]:
    errs = []
    if not host:
        errs.append("Enter the server address.")
    if not SAFE_USER.match(user or ""):
        errs.append("Enter the SSH user (letters, numbers, . _ -).")
    if not SAFE_FOLDER.match(folder or "") or ".." in folder:
        errs.append("Install folder: use a simple path like ~/waveflow.")
    return errs


def probe(user: str, host: str, engine: str) -> Probe:
    """Connect and look: Docker usable by this user, NVIDIA GPUs + Docker's nvidia runtime, free disk."""
    script = ("echo DOCKER=$(docker version --format '{{.Server.Version}}' 2>&1 | head -1); "
              # NVIDIA Container Toolkit: the binaries (current CDI setups list no "nvidia" runtime), or the
              # legacy runtime name. Measured on a working GPU host: no runtime entry, toolkit present.
              "echo RUNTIMES=$(command -v nvidia-ctk nvidia-container-toolkit 2>/dev/null | head -1)"
              "$(docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null); "
              "echo GPUS=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | paste -sd ';'); "
              "echo FREE_KB=$(df -Pk ~ | awk 'NR==2{print $4}')")
    try:
        r = _ssh(user, host, script, timeout=40)
    except subprocess.TimeoutExpired:
        return Probe(False, "unreachable", "The server did not answer within 40 seconds.")
    except FileNotFoundError:
        return Probe(False, "unreachable", "ssh was not found on this PC (Windows: Settings > Optional features > OpenSSH Client).")
    if r.returncode == 255:
        kind, msg = classify_ssh_error(r.stderr.decode(errors="replace"))
        return Probe(False, kind, msg)
    out = dict(line.split("=", 1) for line in r.stdout.decode(errors="replace").splitlines() if "=" in line)
    p = Probe(True, docker=out.get("DOCKER", "").strip(), nvidia_runtime="nvidia" in out.get("RUNTIMES", ""),
              gpus=[g for g in out.get("GPUS", "").split(";") if g.strip()])
    try:
        p.free_gb = int(out.get("FREE_KB", "0").strip() or 0) / 1024 / 1024
    except ValueError:
        p.free_gb = 0.0
    d = p.docker.lower()
    if not p.docker or "not found" in d:
        return _fail(p, "docker-missing", "Docker is not installed on the server. Install Docker Engine, then Try again.")
    if "permission denied" in d:
        return _fail(p, "docker-permission", f"{user} cannot use Docker. On the server run: "
                                             f"sudo usermod -aG docker {user}  (then log in again).")
    if not re.match(r"^\d", p.docker):
        return _fail(p, "docker-missing", "Docker is not running on the server: " + p.docker[:120])
    if engine in ("onnx-gpu", "nemo") and not p.gpus:
        return _fail(p, "no-gpu", "No NVIDIA GPU was found on the server. Choose ONNX · CPU.")
    if engine in ("onnx-gpu", "nemo") and not p.nvidia_runtime:
        return _fail(p, "no-gpu", "Docker on the server cannot use the GPU yet. Install the NVIDIA Container Toolkit.")
    if p.free_gb < DISK_GB[engine]:
        return _fail(p, "disk", f"The server has {p.free_gb:.0f} GB free; this engine needs about {DISK_GB[engine]} GB.")
    return p


def _fail(p: Probe, kind: str, msg: str) -> Probe:
    p.ok, p.kind, p.message = False, kind, msg
    return p


def server_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in SHIP:
            src = S.ROOT / name
            if src.exists():
                tar.add(str(src), arcname=name,
                        filter=lambda ti: None if any(part in SKIP_NAMES for part in ti.name.split("/"))
                        or ti.name.endswith(".pyc") else ti)
    return buf.getvalue()


def compose_command(plan: S.Plan) -> str:
    """The plan's compose command, told where the .env is (the wizard writes it next to the compose file)."""
    return plan.commands[0].replace("docker compose ", "docker compose --env-file docker/.env ", 1)


def install(c: S.Choices, user: str, folder: str, emit, wait_s: float = 1800) -> tuple[bool, str]:
    """Run every step. emit("step", i, state, detail) with state None=running True=done False=failed;
    emit("log", line). Returns (ok, message)."""
    plan = S.build_plan(c)
    host = host_of(c.address)

    def step(i, state, detail=""):
        emit("step", i, state, detail)

    step(0, None, f"{user}@{host}")
    emit("log", f"$ ssh {user}@{host}")
    step(1, None, "docker · nvidia-smi · df")
    p = probe(user, host, c.engine)
    if not p.ok and p.kind in ("auth", "unreachable"):
        step(0, False, p.message)
        if p.kind == "auth":
            emit("log", "Permission denied (publickey). Add your key once, in a terminal:")
            for k in key_help(user, host):
                emit("log", "  " + k)
        else:
            emit("log", p.message)
        return False, p.message
    step(0, True, f"{user}@{host}")
    if not p.ok:
        step(1, False, p.message)
        emit("log", p.message)
        return False, p.message
    gpus = " · ".join(p.gpus) if p.gpus else "no NVIDIA GPU"
    step(1, True, f"Docker {p.docker} · {len(p.gpus)} GPU · {p.free_gb:.0f} GB")
    emit("log", f"docker {p.docker} · {gpus} · {p.free_gb:.0f} GB free")

    step(2, None, folder)
    data = server_tar()
    r = _ssh(user, host, f"mkdir -p {folder} && tar -xz -C {folder}", stdin=data, timeout=120)
    if r.returncode != 0:
        msg = "Copy failed: " + r.stderr.decode(errors="replace").strip()[-160:]
        step(2, False, msg)
        return False, msg
    step(2, True, f"{folder} · {len(data) // 1024} KB")
    emit("log", f"copied server files -> {folder}")

    step(3, None, "token hidden")
    r = _ssh(user, host, f"umask 077 && cat > {folder}/docker/.env", stdin=plan.env_text.encode(), timeout=30)
    if r.returncode != 0:
        msg = "Writing .env failed: " + r.stderr.decode(errors="replace").strip()[-160:]
        step(3, False, msg)
        return False, msg
    step(3, True, "docker/.env · token hidden")
    emit("log", "wrote docker/.env (token hidden)")

    cmd = compose_command(plan)
    step(4, None, "docker compose up")
    emit("log", f"$ cd {folder} && {cmd}")
    proc = subprocess.Popen(ssh_cmd(user, host, f"cd {folder} && {cmd} 2>&1"), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=NOWINDOW)
    tail = []
    for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            tail = (tail + [line])[-5:]
            emit("log", line[-160:])
    if proc.wait() != 0:
        msg = "The container did not start: " + (tail[-1] if tail else "see the log")
        step(4, False, msg[-160:])
        return False, msg
    step(4, True, "container started")

    step(5, None, "first start downloads the model (~660 MB)")
    from local_engine import _health
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if _health(plan.url):
            step(5, True, f"ready at {plan.url}")
            emit("log", f"engine ready at {plan.url}")
            return True, "Installed. Running Test connection…"
        time.sleep(2)
    msg = (f"The container runs, but {plan.url} did not answer. Check the server's firewall allows the port, "
           f"or run on the server: docker compose -f {folder}/docker/compose.yml logs")
    step(5, False, msg)
    return False, msg
