"""Package WaveFlow into ONE clean app — a single exe, no loose folders.

Runs PyInstaller --onefile over waveflow.py, bundling audio.py + stt.py and
the PySide6/sounddevice runtime. Output: dist/WaveFlow.exe.

Run: venv/Scripts/python.exe app/build.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    # ensure PyInstaller present
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "pyinstaller"], check=True)
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "WaveFlow",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(HERE),
        "--collect-submodules", "sounddevice",
        "--collect-data", "sounddevice",
        # UIA caret needs comtypes' runtime-generated modules + uiautomation
        "--collect-all", "comtypes",
        "--collect-all", "uiautomation",
        "--hidden-import", "audio",
        "--hidden-import", "websocket",
        "--hidden-import", "stt",
        str(HERE / "waveflow.py"),
    ]
    print("running PyInstaller...")
    r = subprocess.run(args)
    exe = ROOT / "dist" / "WaveFlow.exe"
    if r.returncode == 0 and exe.exists():
        print(f"\nOK -> {exe}  ({exe.stat().st_size // (1024*1024)} MB)")
        return 0
    print("BUILD FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
