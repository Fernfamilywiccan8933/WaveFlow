"""Package WaveFlow into ONE clean app.

Windows: dist/WaveFlow.exe  (--onefile, no loose folders)
macOS:   dist/WaveFlow.app  (a bundle. --onefile is WRONG here: an onefile binary unpacks to a
         new temp folder on every launch, and macOS ties Accessibility and Input Monitoring to
         the path it sees — so the permissions would have to be granted again on every start.)

Run: venv/Scripts/python.exe app/build.py     (Windows)
     venv/bin/python app/build.py             (macOS)
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IS_MAC = sys.platform == "darwin"
# PyInstaller's --add-data separator is os.pathsep: ";" on Windows, ":" everywhere else.
SEP = ";" if sys.platform == "win32" else ":"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # dist/ holds the RELEASED binary and its published .sha256. A test build must not overwrite
    # them, or the checksum people downloaded stops matching the file it names.
    ap.add_argument("--dist", default=str(ROOT / "dist"),
                    help="output folder (default: dist/, which is the RELEASE)")
    opts = ap.parse_args()
    dist = Path(opts.dist)
    # ensure PyInstaller present
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "pyinstaller"], check=True)
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", "WaveFlow",
        "--distpath", str(dist),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(HERE),
        "--collect-submodules", "sounddevice",
        "--collect-data", "sounddevice",
        "--hidden-import", "audio",
        "--hidden-import", "websocket",
        "--hidden-import", "stt",
        # imported lazily (Setup / Settings / Uninstall), so PyInstaller cannot see them
        *[a for m in ("icons", "settings", "wizard", "wizard_ui", "panels", "setup_logic", "uninstall",
                      "local_engine", "remote_install") for a in ("--hidden-import", m)],
        # osbridge is imported INSIDE a function (waveflow.resolve_theme), which PyInstaller's
        # static scan cannot see. Without these the frozen build silently falls back to a dark
        # pill with no platform integration — a failure that only appears AFTER packaging, which
        # is the worst time to find it.
        *[a for m in ("osbridge", "osbridge.win", "osbridge.mac", "osbridge.posix")
          for a in ("--hidden-import", m)],
        "--add-data", f"{HERE / 'assets'}{SEP}assets",
    ]
    if IS_MAC:
        args += [
            "--osx-bundle-identifier", "com.waveflow.client",
            "--collect-all", "objc",
            *[a for m in ("Quartz", "AppKit", "ApplicationServices", "Foundation")
              for a in ("--collect-all", m)],
        ]
        icns = HERE / "assets" / "waveflow.icns"
        if icns.exists():
            args += ["--icon", str(icns)]
    else:
        args += [
            "--onefile",
            # UIA caret needs comtypes' runtime-generated modules + uiautomation
            "--collect-all", "comtypes",
            "--collect-all", "uiautomation",
            "--icon", str(HERE / "assets" / "waveflow.ico"),
        ]
    args.append(str(HERE / "waveflow.py"))
    print("running PyInstaller...")
    r = subprocess.run(args)
    out = dist / ("WaveFlow.app" if IS_MAC else "WaveFlow.exe")
    if r.returncode != 0 or not out.exists():
        print("BUILD FAILED")
        return 1
    size = (sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
            if out.is_dir() else out.stat().st_size)
    print(f"\nOK -> {out}  ({size // (1024 * 1024)} MB)")
    if IS_MAC:
        _mac_after(out)
    return 0


def _mac_after(app: Path) -> None:
    """Ad-hoc sign, then say the one thing that would otherwise look like a bug.

    Ad-hoc (`codesign -s -`) gives the bundle an identity for THIS build, so macOS will offer the
    permission dialogs at all. It is not a Developer ID signature: the identity changes whenever
    the binary changes, so macOS drops Accessibility and Input Monitoring on every rebuild. That
    is expected, this script cannot fix it, and the operator has chosen not to buy an account.
    """
    try:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                       check=False, timeout=180)
        print("ad-hoc signed.")
    except Exception as e:
        print(f"could not ad-hoc sign ({e}) — macOS will complain harder on first launch.")
    print("\nFirst launch on macOS:")
    print("  1. Right-click the app -> Open. Gatekeeper blocks a double-click on an unsigned app.")
    print("  2. Grant Microphone, then Accessibility, then Input Monitoring.")
    print("  3. After EVERY rebuild, re-tick Accessibility and Input Monitoring: macOS ties them")
    print("     to the exact app file, and this build is not signed by a paid account.")


if __name__ == "__main__":
    sys.exit(main())
