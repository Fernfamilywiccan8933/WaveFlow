"""Exercise build._install against a FAKE bundle, so the copy logic is proven without a 60MB build."""
import shutil, sys, tempfile
from pathlib import Path

# Its own parent, not a hardcoded absolute path — that resolved only by luck of the working
# directory, and on a Mac it does not exist at all (found 2026-09-16).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build

FAILS=[]
def check(n,g,w):
    if g!=w: FAILS.append(f"  {n}\n    got  {g!r}\n    want {w!r}")

tmp=Path(tempfile.mkdtemp())
# a .app is a DIRECTORY with nested files — the shape that breaks naive copies
src=tmp/"WaveFlow.app"; (src/"Contents"/"MacOS").mkdir(parents=True)
(src/"Contents"/"Info.plist").write_text("<plist/>")
(src/"Contents"/"MacOS"/"WaveFlow").write_text("binary")
dest=tmp/"Applications"

build._install(src, str(dest))
out=dest/"WaveFlow.app"
check("bundle copied", out.is_dir(), True)
check("nested file came too", (out/"Contents"/"MacOS"/"WaveFlow").read_text(), "binary")
check("plist came too", (out/"Contents"/"Info.plist").exists(), True)

# installing twice must REPLACE, not merge: a merged bundle is one macOS refuses to open
(out/"Contents"/"STALE").write_text("left over from the old version")
build._install(src, str(dest))
check("reinstall removes stale files", (out/"Contents"/"STALE").exists(), False)
check("reinstall still has the real ones", (out/"Contents"/"MacOS"/"WaveFlow").exists(), True)

# a single file (the Windows .exe) must work through the same path
exe=tmp/"WaveFlow.exe"; exe.write_text("exe")
d2=tmp/"win"; build._install(exe, str(d2))
check("single file copied", (d2/"WaveFlow.exe").read_text(), "exe")
build._install(exe, str(d2))
check("single file re-installs over itself", (d2/"WaveFlow.exe").read_text(), "exe")

# ~ must expand, or the user's --install ~/Apps makes a literal "~" folder in the repo
check("tilde expands", str(Path("~/Apps").expanduser()).startswith(str(Path.home())), True)
check("default dir is absolute", build.default_install_dir().is_absolute(), True)

shutil.rmtree(tmp, ignore_errors=True)

# --- signing sets itself up (operator requirement 2026-09-16: no Keychain Access steps) ----------
import os, subprocess
from unittest import mock

real_run = subprocess.run
calls, keychain = [], {"has": False}
seen_tmp = []

def fake_run(cmd, **kw):
    calls.append(cmd)
    if cmd[0] == "security" and cmd[1] == "find-identity":
        out = f'  1) ABC "{build.SIGN_NAME}"\n' if keychain["has"] else "  0 identities found\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    if cmd[0] == "security" and cmd[1] == "import":
        p12 = Path(cmd[2]); seen_tmp.append(p12.parent)
        ok = p12.exists() and p12.stat().st_size > 0
        keychain["has"] = ok
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "" if ok else "no p12")
    if cmd[0] == "codesign":
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if cmd[0] == "openssl" and HAVE_OPENSSL:
        return real_run(cmd, **kw)                 # the REAL openssl makes the real key + p12
    if cmd[0] == "openssl":                          # no openssl here: fake the files it would write
        out = Path(cmd[cmd.index("-out") + 1]); out.write_bytes(b"x")
        if "-keyout" in cmd: Path(cmd[cmd.index("-keyout") + 1]).write_bytes(b"k")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    raise AssertionError(f"unexpected command {cmd}")

HAVE_OPENSSL = bool(shutil.which("openssl"))
which = lambda n: "/usr/bin/" + n
with mock.patch.object(build.subprocess, "run", fake_run), mock.patch("shutil.which", which), \
     mock.patch.dict(os.environ, {"WAVEFLOW_SIGN_IDENTITY": ""}):
    app = Path(tempfile.mkdtemp()) / "WaveFlow.app"; app.mkdir()
    build._mac_after(app)
    signs = [c for c in calls if c[0] == "codesign"]
    check("first build: certificate created", keychain["has"], True)
    check("first build: signed with it, not ad-hoc", signs[-1][signs[-1].index("--sign") + 1], build.SIGN_NAME)
    order = [c[0] + " " + c[1] for c in calls if c[0] in ("openssl", "security")]
    check("created with openssl then imported", order[:4],
          ["security find-identity", "openssl req", "openssl pkcs12", "security import"])
    imp = next(c for c in calls if c[:2] == ["security", "import"])
    check("codesign may use the key without a prompt each build", imp[imp.index("-T") + 1], "/usr/bin/codesign")
    check("the private key never outlives the build", all(not d.exists() for d in seen_tmp), True)
    req = next(c for c in calls if c[:2] == ["openssl", "req"])
    check("10-year certificate", req[req.index("-days") + 1], "3650")
    calls.clear()
    build._mac_after(app)
    check("second build: reuses it, creates nothing", [c[:2] for c in calls if c[0] == "openssl"], [])
    check("second build: still signed with it",
          [c[c.index("--sign") + 1] for c in calls if c[0] == "codesign"], [build.SIGN_NAME])
    # a failed import must fall back to ad-hoc, and still delete the key
    keychain["has"] = False; calls.clear(); seen_tmp.clear()
    def broken(cmd, **kw):
        if cmd[:2] == ["security", "import"]:
            seen_tmp.append(Path(cmd[2]).parent)
            return subprocess.CompletedProcess(cmd, 1, "", "denied")
        return fake_run(cmd, **kw)
    with mock.patch.object(build.subprocess, "run", broken):
        build._mac_after(app)
    check("failed import: ad-hoc fallback", [c[c.index("--sign") + 1] for c in calls if c[0] == "codesign"], ["-"])
    check("failed import: key deleted anyway", all(not d.exists() for d in seen_tmp), True)
    shutil.rmtree(app.parent, ignore_errors=True)
check("config names the codeSigning extended key usage", "extendedKeyUsage = critical,codeSigning" in build._CERT_CONFIG, True)
print(f"  (openssl steps ran for real: {HAVE_OPENSSL})")

if FAILS: print("INSTALL_FAIL\n"+"\n".join(FAILS)); raise SystemExit(1)
print("INSTALL_OK — bundles copy whole, reinstall replaces rather than merges")
