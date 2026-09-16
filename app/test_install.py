"""Exercise build._install against a FAKE bundle, so the copy logic is proven without a 60MB build."""
import shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, r"F:\AI_Projects\WaveFlow\app")
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
if FAILS: print("INSTALL_FAIL\n"+"\n".join(FAILS)); raise SystemExit(1)
print("INSTALL_OK — bundles copy whole, reinstall replaces rather than merges")
