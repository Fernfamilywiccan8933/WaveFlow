"""Setup wizard rules — no Qt, no network. Run: venv/Scripts/python.exe app/test_wizard.py -> WIZARD_OK

Covers contract C1.4 / C6.4 / C6.5: every install option lists all 3 engines (unavailable ones
kept with a reason), CPU threads are editable and clamped, unsafe setups are refused, the plan
writes what the preview shows, and Test connection gives one distinct message per failure.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_logic as S  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


ALL = S.Hardware(perf_cores=8, all_cores=24, directml=True, cuda=False, docker=True, nvidia=True)
BARE = S.Hardware(perf_cores=4, all_cores=4)

# --- every option lists all three engines, none hidden --------------------------------
# "connect" is excluded on purpose and has its own block at the end of this file: it installs
# nothing, so it offers no engine — the server already running has already chosen one.
for hw in (ALL, BARE):
    for opt in S.INSTALL_OPTIONS:
        for method in ("docker", "venv"):
            got = [e.engine for e in S.engines_for(opt, method, hw)]
            check(f"{opt}/{method} lists all engines", got, S.ENGINES)
            for e in S.engines_for(opt, method, hw):
                if not e.available and not e.reason:
                    FAILS.append(f"  {opt}/{method}/{e.engine} unavailable with no reason")

loc = {e.engine: e for e in S.engines_for("local", "", BARE)}
check("local: ONNX CPU available", loc["onnx-cpu"].available, True)
check("local: ONNX GPU offered", loc["onnx-gpu"].available, True)
check("local: ONNX GPU without DirectML says what to install", "onnxruntime-directml" in loc["onnx-gpu"].reason, True)
check("local: NeMo off, points to Docker", (loc["nemo"].available, "Docker" in loc["nemo"].reason), (False, True))
dk = {e.engine: e for e in S.engines_for("docker", "", BARE)}
check("docker w/o Docker: all off", [e.available for e in dk.values()], [False, False, False])
dk = {e.engine: e for e in S.engines_for("docker", "", ALL)}
check("docker with NVIDIA: all on", [e.available for e in dk.values()], [True, True, True])
on = {e.engine: e for e in S.engines_for("onsite", "venv", BARE)}
check("onsite venv: NeMo needs Docker", (on["nemo"].available, on["onnx-gpu"].available), (False, True))

# --- threads -------------------------------------------------------------------------------
check("perf cores on hybrid 8P+16E", S.count_performance([1] * 8 + [0] * 16), 8)
check("perf cores on non-hybrid", S.count_performance([0] * 4), 4)
check("perf cores empty", S.count_performance([]), 1)
check("clamp low", S.clamp_threads(0), 1)
check("clamp high", S.clamp_threads(500), 64)
check("clamp junk", S.clamp_threads("x"), 4)
p, a = S.performance_cores()
check("this machine reports sane cores", 1 <= p <= a <= 256, True)

c = S.Choices(option="local", engine="onnx-cpu", threads=6, auto_threads=False)
check("threads reach the local command", "--threads 6" in S.build_plan(c).commands[0], True)
check("threads saved to config", S.build_plan(c).config["engine"]["threads"], 6)
c = S.Choices(option="docker", engine="onnx-cpu", threads=12, token="t" * 20)
check("threads reach docker .env", "THREADS=12" in S.build_plan(c).env_text, True)
for opt, addr in (("onsite", "server.local"), ("vps", "stt.example.com")):
    c = S.Choices(option=opt, engine="onnx-cpu", threads=3, token="t" * 20, address=addr)
    pl = S.build_plan(c)
    check(f"{opt}: threads in plan", "THREADS=3" in pl.env_text or "--threads 3" in " ".join(pl.commands), True)
c = S.Choices(option="onsite", method="venv", engine="onnx-cpu", threads=5, token="t" * 20, address="10.0.0.5")
check("onsite venv: threads in command", "--threads 5" in S.build_plan(c).commands[-1], True)

# --- plans ---------------------------------------------------------------------------------
pl = S.build_plan(S.Choices(option="local", engine="onnx-gpu"))
check("local GPU = DirectML fp32 on loopback",
      all(x in pl.commands[0] for x in ("--device dml", "--onnx-quant fp32", "--host 127.0.0.1")), True)
check("local has no token", (pl.token, pl.url), ("", "http://127.0.0.1:8756"))
for eng, port, svc in (("onnx-cpu", 8756, "waveflow"), ("onnx-gpu", 8759, "waveflow-onnx-gpu"),
                       ("nemo", 8757, "waveflow-nemo")):
    pl = S.build_plan(S.Choices(option="docker", engine=eng, token="t" * 20))
    check(f"docker {eng}: url port", pl.url, f"http://127.0.0.1:{port}")
    check(f"docker {eng}: service named", pl.commands[0].endswith(f" {svc}"), True)
    check(f"docker {eng}: loopback bind by default", "HOST_BIND=127.0.0.1" in pl.env_text, True)
pl = S.build_plan(S.Choices(option="docker", engine="onnx-cpu", token="t" * 20, lan=True))
check("docker 'my network' binds 0.0.0.0", "HOST_BIND=0.0.0.0" in pl.env_text, True)
pl = S.build_plan(S.Choices(option="vps", engine="nemo", token="t" * 20, address="stt.example.com"))
check("vps: https url", pl.url, "https://stt.example.com")
check("vps GPU engine adds GPU compose", "compose.gpu.yml" in pl.commands[0], True)
check("vps picks the engine's Dockerfile", "WAVEFLOW_DOCKERFILE=docker/nemo/Dockerfile" in pl.env_text, True)
check("onsite default port follows engine",
      S.build_plan(S.Choices(option="onsite", engine="nemo", token="t" * 20, address="box")).url, "http://box:8757")
check("token never in local config", S.build_plan(S.Choices(option="local")).config["token"], "")

# --- validation ----------------------------------------------------------------------------
check("local needs nothing", S.validate(S.Choices(option="local")), [])
check("docker needs a token", bool(S.validate(S.Choices(option="docker", token=""))), True)
check("vps refuses http", any("https" in e for e in S.validate(
    S.Choices(option="vps", token="t" * 20, address="http://stt.example.com"))), True)
check("vps needs a domain", bool(S.validate(S.Choices(option="vps", token="t" * 20))), True)
check("onsite venv + nemo refused", bool(S.validate(
    S.Choices(option="onsite", method="venv", engine="nemo", token="t" * 20, address="box"))), True)
check("threads out of range refused", bool(S.validate(S.Choices(option="local", threads=99))), True)
check("public http onsite warns", bool(S.warnings(S.Choices(option="onsite", address="8.8.8.8"))), True)
check("tailscale IP is private", S.is_public_host("100.101.102.103"), False)
check("LAN is private", S.is_public_host("192.168.1.20"), False)
check(".local name is private", S.is_public_host("server.local"), False)
check("ts.net name is private", S.is_public_host("server.tailnet.ts.net"), False)
check("new token is long", len(S.new_token()) >= 32, True)


# --- test connection: one distinct message per failure ---------------------------------------
class R:
    def __init__(self, code=200, js=None):
        self.status_code, self._js = code, js or {}

    def json(self):
        return self._js


def checks_with(get=None, post=None, **kw):
    import requests
    with mock.patch.object(requests, "get", side_effect=get if callable(get) else (lambda *a, **k: get)), \
         mock.patch.object(requests, "post", side_effect=post if callable(post) else (lambda *a, **k: post)):
        # A LITERAL ADDRESS, not a name. run_checks now resolves the host before anything else,
        # because getaddrinfo cannot be interrupted by requests' timeout and an unresolvable name
        # hung the UI forever (real Mac, 2026-09-15). These tests mock HTTP but not DNS, so the
        # old placeholder "x" made every one of them short-circuit on the lookup. An IP literal
        # skips resolution entirely, which keeps these tests about HTTP, which is their subject.
        return S.run_checks("http://127.0.0.1:8756", kw.pop("token", "tok"), **kw)


def raise_(e):
    def f(*a, **k):
        raise e
    return f


import requests  # noqa: E402

ok_h = R(200, {"status": "ok", "model": "m", "auth": True})
chk, v = checks_with(ok_h, R(200, {"text": "Send it."}))
check("all pass", [c.ok for c in chk], [True, True, True, True])
chk, v = checks_with(raise_(requests.exceptions.ConnectionError()))
check("down -> can't reach", ([c.ok for c in chk][0], "Can't reach" in v), (False, True))
chk, v = checks_with(raise_(requests.exceptions.SSLError()))
check("bad cert", "Certificate" in v, True)
chk, v = checks_with(R(200, {"status": "loading"}))
check("loading", (chk[1].ok, "loading" in v), (False, True))
chk, v = checks_with(ok_h, R(401))
check("bad token", (chk[2].ok, "Token rejected" in v), (False, True))
chk, v = checks_with(R(200, {"status": "ok", "auth": False}), R(200, {"text": "Send it."}), require_token=True)
check("remote server with no token refused", (chk[2].ok, "accepts anyone" in v), (False, True))
chk, v = checks_with(ok_h, R(200, {"text": "banana"}))
check("wrong text caught", (chk[3].ok, "did not hear" in v), (False, True))
chk, v = checks_with(ok_h, R(500))
check("server error", (chk[3].ok, "500" in v), (False, True))
messages = set()
for args in [(raise_(requests.exceptions.ConnectionError()),), (raise_(requests.exceptions.SSLError()),),
             (R(200, {"status": "loading"}),), (ok_h, R(401)), (ok_h, R(200, {"text": "x"}))]:
    messages.add(checks_with(*args)[1])
check("5 failures -> 5 distinct messages", len(messages), 5)
check("sample clip is bundled", S.SAMPLE_WAV.exists(), True)

# --- remote install over SSH (mock wizard-onsite-ssh-v1 A) ----------------------------------
import io  # noqa: E402
import tarfile  # noqa: E402

import remote_install as RI  # noqa: E402

check("host from address", [RI.host_of(a) for a in ("server", "server:8759", "http://10.0.0.5:8756",
                                                     "https://stt.example.com")],
      ["server", "server", "10.0.0.5", "stt.example.com"])
check("ssh never prompts for a password", "BatchMode=yes" in RI.ssh_cmd("u", "h", "true"), True)
check("target validation", (RI.validate_target("bob", "server", "~/waveflow"),
                            bool(RI.validate_target("bob; rm", "server", "~/wf")),
                            bool(RI.validate_target("bob", "server", "~/wf && rm -rf /")),
                            bool(RI.validate_target("bob", "server", "../etc"))), ([], True, True, True))
check("ssh key rejected -> key help", (RI.classify_ssh_error("bob@h: Permission denied (publickey).")[0],
                                      RI.key_help("bob", "server")[1]), ("auth", "ssh-copy-id bob@server"))
check("ssh unreachable", RI.classify_ssh_error("ssh: connect to host h port 22: Connection timed out")[0],
      "unreachable")
names = tarfile.open(fileobj=io.BytesIO(RI.server_tar()), mode="r:gz").getnames()
check("ships server + docker", ("server/parakeet_server.py" in names, "docker/compose.yml" in names), (True, True))
check("never ships personal vocab, .env or caches",
      [n for n in names if n.endswith(("vocab.user.json", "/.env", ".pyc")) or "__pycache__" in n], [])
c = S.Choices(option="onsite", engine="onnx-gpu", method="docker", address="server", token="t" * 20)
cmd = RI.compose_command(S.build_plan(c))
check("compose reads the written .env", ("--env-file docker/.env" in cmd, "waveflow-onnx-gpu" in cmd),
      (True, True))
check("token never on a command line", "t" * 20 in cmd, False)
check("build log streams without a terminal", "--progress plain" in cmd, True)
# Operator's real run 2026-09-15: an apt Docker upgrade restarted dockerd mid-build.
check("daemon restart mid-build -> plain 'try again'",
      "Try again" in RI.explain_build_failure(
          ["failed to receive status: rpc error: code = Unavailable desc = error reading from server: EOF"]), True)
check("disk full explained", "disk" in RI.explain_build_failure(["write /x: no space left on device"]), True)
check("step names fit next to their detail", max(len(s) for s in RI.STEPS) <= 27, True)


def fake_probe(stdout, rc=0, stderr=""):
    res = mock.Mock(returncode=rc, stdout=stdout.encode(), stderr=stderr.encode())
    with mock.patch.object(RI, "_ssh", lambda *a, **k: res):
        return RI.probe("bob", "server", "onnx-gpu")


good = "DOCKER=27.3.1\nRUNTIMES=/usr/bin/nvidia-ctkio.containerd.runc.v2 runc\nGPUS=GTX 1080;GTX 1060\nFREE_KB=53000000\n"
check("probe ok (CDI toolkit, no nvidia runtime entry)", (fake_probe(good).ok, fake_probe(good).gpus),
      (True, ["GTX 1080", "GTX 1060"]))
check("probe ok (legacy nvidia runtime)", fake_probe(good.replace("/usr/bin/nvidia-ctk", "nvidia ")).ok, True)
check("probe: key rejected", fake_probe("", 255, "Permission denied (publickey)").kind, "auth")
check("probe: docker group", fake_probe(good.replace("27.3.1", "permission denied while trying to connect")).kind,
      "docker-permission")
check("probe: no gpu", fake_probe(good.replace("GTX 1080;GTX 1060", "")).kind, "no-gpu")
check("probe: no toolkit", fake_probe(good.replace("/usr/bin/nvidia-ctk", "")).kind, "no-gpu")
check("probe: disk", fake_probe(good.replace("53000000", "2000000")).kind, "disk")

# --- "connect": the server is already running, so setup installs NOTHING ------------------
# The bug this block exists to prevent: treating connect as a fifth flavour of install. Any
# command, .env line or engine choice leaking in here would run a second container on a port that
# is already serving, or write a guessed engine name into config.json.
TOK = "k" * 24
cn = S.Choices(option="connect", address="server.example.com:8756", token=TOK)

check("connect is an option", "connect" in S.OPTIONS, True)
check("connect is NOT an install option", "connect" in S.INSTALL_OPTIONS, False)
check("connect has a name", bool(S.OPTION_NAMES.get("connect")), True)
check("connect offers no engine", S.engines_for("connect", "docker", ALL), [])

pl = S.build_plan(cn)
check("connect runs no commands", pl.commands, [])
check("connect writes no .env", (pl.env_path, pl.env_text), ("", ""))
check("connect never runs anything here", pl.run_here, False)
check("connect says nothing is installed", pl.where, "Nothing is installed")
check("connect keeps the token", pl.token, TOK)
check("connect adds the default port", pl.url, "http://server.example.com:8756")
check("connect config mode", pl.config["engine"], {"mode": "connect"})
check("connect config has no engine name", "engine" in pl.config["engine"], False)
check("connect config has no thread count", "threads" in pl.config["engine"], False)

# a bare host gets http:// and the default port; an explicit https:// is left alone
check("connect bare host", S.build_plan(S.Choices(option="connect", address="10.0.0.9", token=TOK)).url,
      "http://10.0.0.9:8756")
check("connect keeps https", S.build_plan(S.Choices(option="connect", address="https://stt.example.com", token=TOK)).url,
      "https://stt.example.com")

# validate
check("connect needs an address", S.validate(S.Choices(option="connect", address="", token=TOK)),
      ["Enter the server address."])
check("connect with both is valid", S.validate(cn), [])

# --- a TOKENLESS server is a legitimate setup, and this is the operator's own -------------
# A real user's own box often runs with no WAVEFLOW_TOKEN at all. The first cut of this mode
# demanded a token of 16+ chars and would have refused exactly the setup it was built for.
# 100.64.0.0/10 is CGNAT, which is what Tailscale hands out — private, so no token is fine.
LAN_BOX = "http://100.100.100.100:8756"        # Tailscale-style CGNAT, i.e. private
check("connect accepts the operator's tokenless Tailscale box",
      S.validate(S.Choices(option="connect", address=LAN_BOX, token="")), [])
check("connect accepts a tokenless LAN box",
      S.validate(S.Choices(option="connect", address="192.168.1.5:8756", token="")), [])
check("connect accepts a tokenless .local box",
      S.validate(S.Choices(option="connect", address="server.local:8756", token="")), [])
# but it still says so out loud
check("tokenless connect warns",
      any("no token" in w for w in S.warnings(S.Choices(option="connect", address=LAN_BOX, token=""))), True)
check("tokenless connect warning names Tailscale as fine",
      any("Tailscale" in w for w in S.warnings(S.Choices(option="connect", address=LAN_BOX, token=""))), True)
check("a token that IS set removes the warning",
      S.warnings(S.Choices(option="connect", address=LAN_BOX, token=TOK)), [])

# --- tokenless on a PUBLIC address is refused, not warned ---------------------------------
pub = S.validate(S.Choices(option="connect", address="http://stt.example.com", token=""))
check("tokenless public address is an error", len(pub), 1)
check("and it says why", "anyone" in pub[0], True)
check("a token makes the public address acceptable",
      S.validate(S.Choices(option="connect", address="http://stt.example.com", token=TOK)), [])

# --- a short token is still a typo, whatever the address ----------------------------------
short = S.validate(S.Choices(option="connect", address=LAN_BOX, token="abc"))
check("a short token is rejected as a typo", len(short), 1)
check("and offers the way out", "clear the box" in short[0], True)

# the plan must survive a tokenless connect unchanged
pl0 = S.build_plan(S.Choices(option="connect", address=LAN_BOX, token=""))
check("tokenless connect still installs nothing", pl0.commands, [])
check("tokenless connect saves an empty token", pl0.config["token"], "")
check("tokenless connect keeps the url", pl0.url, LAN_BOX)
check("tokenless connect round-trips", S.choices_from_config(pl0.config).option, "connect")
# connect must NOT inherit the VPS https rule — a LAN box on plain http is the normal case
check("connect allows plain http on a private address",
      S.validate(S.Choices(option="connect", address="http://192.168.1.5:8756", token=TOK)), [])

# warnings
check("connect warns on public http",
      any("unencrypted" in w for w in S.warnings(S.Choices(option="connect", address="http://stt.example.com", token=TOK))),
      True)
check("connect quiet on a Tailscale address",
      S.warnings(S.Choices(option="connect", address="http://server.tailnet.ts.net:8756", token=TOK)), [])
check("connect quiet on a LAN address",
      S.warnings(S.Choices(option="connect", address="http://192.168.1.5:8756", token=TOK)), [])
check("connect gives no thread advice",
      S.warnings(S.Choices(option="connect", address="10.0.0.9", token=TOK, engine="onnx-cpu", auto_threads=False)), [])

# a saved connect config rebuilds the same Choices (Settings depends on this)
saved = pl.config
back = S.choices_from_config(saved)
check("connect round-trips the mode", back.option, "connect")
check("connect round-trips the address", back.address, pl.url)
check("connect round-trips the token", back.token, TOK)
check("connect round-trip rebuilds the same plan", S.build_plan(back).config, saved)

# rotating the token: we did not install that server, so we never claim to restart it
rot = S.rotation_plan(saved, "n" * 24)
check("connect rotation happens on the server", rot.kind, "server")
check("connect rotation writes no env", (rot.env_path, rot.env_text), ("", ""))
check("connect rotation names the new token", any("n" * 24 in c for c in rot.commands), True)
check("connect rotation invents no compose command", any("docker" in c for c in rot.commands), False)


# --- the hang fix itself: a name that never resolves must FAIL FAST, not block ------------
# requests' timeout covers connect and read, never getaddrinfo. Before this, a .local name on a
# network without mDNS froze the wizard with no message and no way out.
import time as _t
_t0 = _t.time()
chk, verdict = S.run_checks("http://no-such-host-waveflow-test.invalid:8756", "tok", timeout=2.0)
_el = _t.time() - _t0
check("unresolvable host fails instead of hanging", chk[0].ok, False)
check("and says the name was not found", chk[0].detail, "name not found")
check("and returns within the timeout", _el < 8, True)
check("and the message names the host", "no-such-host-waveflow-test.invalid" in verdict, True)
check("an IP literal needs no lookup", S._resolves("127.0.0.1", 0.01), True)
check("localhost resolves", S._resolves("localhost", 5.0), True)

if FAILS:
    print("WIZARD_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("WIZARD_OK")
