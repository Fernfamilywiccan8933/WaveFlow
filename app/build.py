"""Package WaveFlow into ONE clean app.

Windows: dist/WaveFlow.exe  (--onefile, no loose folders)
macOS:   dist/WaveFlow.app  (a bundle. --onefile is WRONG here: an onefile binary unpacks to a
         new temp folder on every launch, and macOS ties Accessibility and Input Monitoring to
         the path it sees — so the permissions would have to be granted again on every start.)

Run: venv/Scripts/python.exe app/build.py     (Windows)
     venv/bin/python app/build.py             (macOS)
"""
import argparse
import os
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
    ap.add_argument("--install", nargs="?", const="__default__", default=None,
                    metavar="FOLDER",
                    help="after building, put the app where you can actually launch it. "
                         "macOS default /Applications, Windows default %%LOCALAPPDATA%%\\WaveFlow. "
                         "Give a folder to choose your own.")
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
        # --- the ENGINE, so the app can run it itself (`WaveFlow --serve …`) ---------------
        # Without these the frozen build had no engine at all, and "This PC — background app"
        # — the setup the README calls easiest — refused to start in the build people download.
        # The server is imported only in serve mode, so PyInstaller's scan never saw any of it.
        "--paths", str(ROOT / "server"),
        # Project hooks override PyInstaller's stock ones. Needed because webrtcvad is installed
        # as the `webrtcvad-wheels` distribution, and the stock hook's metadata lookup by the
        # name `webrtcvad` crashed the build. See app/pyinstaller_hooks/.
        "--additional-hooks-dir", str(HERE / "pyinstaller_hooks"),
        *[a for m in ("parakeet_server", "itn", "vocab", "webrtcvad", "onnx_asr")
          for a in ("--hidden-import", m)],
        "--collect-all", "onnx_asr",       # ships its own model configs and tokenizer data
        "--collect-all", "onnxruntime",    # native providers are not found by the import scan
        "--collect-submodules", "uvicorn", # picks its loop/protocol implementations by name
        "--collect-submodules", "fastapi",
        # No vocab file is bundled: the engine ships no built-in terms, and a user's own
        # vocab.user.json is personal. serve_main() points the engine at the app data folder.
        # NeMo and torch are several GB and only ever used through Docker. Excluded explicitly so
        # that installing them into the build venv later cannot silently balloon the app.
        *[a for m in ("torch", "nemo", "nemo_toolkit", "pytorch_lightning", "lightning")
          for a in ("--exclude-module", m)],
    ]
    if IS_MAC:
        # The usage strings this bundle cannot record without are in MAC_PLIST, applied by
        # _mac_plist() after the build: PyInstaller accepts plist keys only through a .spec file,
        # never the command line.
        args += [
            "--osx-bundle-identifier", "com.waveflow.client",
            "--collect-all", "objc",
            *[a for m in ("Quartz", "AppKit", "ApplicationServices", "Foundation")
              for a in ("--collect-all", m)],
        ]
        # FAIL without it. It used to be skipped silently, and the bundle shipped PyInstaller's
        # placeholder icon in Finder and in the Privacy & Security lists the user has to search.
        icns = HERE / "assets" / "waveflow.icns"
        if not icns.exists():
            print(f"BUILD FAILED: {icns} is missing. Make it with: venv/bin/python app/icons.py")
            return 1
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
        # plist BEFORE signing: the signature must cover the final Info.plist, or macOS treats
        # the bundle as modified after signing and refuses it.
        _mac_plist(out)
        _mac_after(out)
    if opts.install is not None:
        _install(out, opts.install)
    elif IS_MAC:
        print("\nTo put it somewhere you can launch it from Launchpad or Spotlight:")
        print("  venv/bin/python app/build.py --install")
        print("  venv/bin/python app/build.py --install ~/Apps      # or anywhere you like")
    return 0


def default_install_dir() -> Path:
    """Where an app belongs on this OS, if the user does not say."""
    if IS_MAC:
        return Path("/Applications")
    # Windows: this user's own folder. Program Files would need admin rights, and WaveFlow has
    # never asked for any.
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "WaveFlow"


def _install(built: Path, where: str) -> None:
    """Copy the built app to a folder the user can launch it from.

    This exists because a build alone leaves the app inside the source tree, reachable only by
    typing a path — the operator's words, 2026-09-16: "once i close the terminal window the app
    goes away and i cant reopen it". An app you cannot find again is not installed.
    """
    import shutil

    target_dir = default_install_dir() if where == "__default__" else Path(where).expanduser()
    target = target_dir / built.name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Replacing a .app means removing the old bundle first: copytree will not merge two
            # directories, and a half-merged bundle is one macOS refuses to open.
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        if built.is_dir():
            shutil.copytree(built, target, symlinks=True)     # symlinks: a bundle is full of them
        else:
            shutil.copy2(built, target)
    except PermissionError:
        print(f"\nCould not write to {target_dir} — no permission.")
        print(f"Either pick a folder you own:  --install ~/Apps")
        print(f"or move it by hand:            mv '{built}' '{target_dir}/'")
        return
    except OSError as e:
        print(f"\nCould not install to {target_dir}: {e}")
        return

    print(f"\nInstalled -> {target}")
    if IS_MAC:
        print("Open it from Launchpad or Spotlight (Cmd+Space, type WaveFlow).")
        print("FIRST TIME: right-click it in Finder and choose Open — Gatekeeper blocks a")
        print("double-click on an app that is not signed by a paid Apple account.")
        print("If Accessibility or Input Monitoring show WaveFlow as ON but it still can't type or")
        print("hear the hotkey: remove it from that list (− button) and add it again.")


MAC_PLIST = {
    # Without this key macOS refuses the microphone request OUTRIGHT — it will not even show a
    # prompt — and CoreAudio then blocks forever when the stream opens. A bundle built without it
    # can never record, and fails in the most confusing way available: no error, no dialog, just
    # a hang. Found on real hardware 2026-09-16.
    #
    # The text is what the user READS in the prompt, so it says what WaveFlow does and when,
    # rather than "this app needs access".
    "NSMicrophoneUsageDescription":
        "WaveFlow listens while you hold your hotkey, so it can type what you say.",
    "NSAppleEventsUsageDescription":
        "WaveFlow types the words into whichever app you are using.",
    # A menu-bar app: no Dock icon and nothing in the app switcher. The pill is the whole UI.
    "LSUIElement": True,
}


def _mac_plist(app: Path) -> None:
    """Write the usage strings into the built bundle's Info.plist.

    Done AFTER the build because PyInstaller accepts plist additions only through a .spec file,
    and this project builds from the command line. Signing happens after this, which matters: the
    signature must cover the final plist or macOS treats the bundle as tampered with.
    """
    import plistlib

    path = app / "Contents" / "Info.plist"
    try:
        data = plistlib.loads(path.read_bytes())
    except Exception as e:
        print(f"could not read {path}: {e} — the app will NOT be able to record.")
        return
    data.update(MAC_PLIST)
    # Where this app was built from, so its first launch can pick up a setup already finished in
    # that checkout (setup_logic.migrate_mac_settings). Local builds only; nothing is published.
    data["WaveFlowSourceCheckout"] = str(ROOT)
    try:
        path.write_bytes(plistlib.dumps(data))
    except OSError as e:
        print(f"could not write {path}: {e} — the app will NOT be able to record.")
        return
    print(f"Info.plist: added {', '.join(MAC_PLIST)}")


SIGN_NAME = "WaveFlow Local Signing"


def _sign_identity() -> str:
    """The certificate to sign with: $WAVEFLOW_SIGN_IDENTITY, else a keychain certificate named
    SIGN_NAME, else "-" (ad-hoc).

    Why it matters: an ad-hoc signature's designated requirement is the build's cdhash, which is
    different on EVERY build. macOS stores that requirement with the Accessibility and Input
    Monitoring grants, so after a rebuild the switch in System Settings still shows ON but belongs
    to the old build — the running app is refused (Mac, 2026-09-16: `codesign -dr -` showed
    `cdhash H"63ff…"`). A certificate, even a free self-signed one, makes the requirement
    `identifier "com.waveflow.client" and certificate leaf = …`, which survives rebuilds.

    `find-identity` WITHOUT -v: a self-signed certificate is "not trusted" as a CA, which -v hides,
    but codesign signs with it perfectly well.
    """
    want = os.environ.get("WAVEFLOW_SIGN_IDENTITY", "").strip()
    if want:
        return want
    return SIGN_NAME if _have_identity(SIGN_NAME) else "-"


def _have_identity(name: str) -> bool:
    try:
        out = subprocess.run(["security", "find-identity", "-p", "codesigning"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    return f'"{name}"' in (out or "")


# Code signing only: digitalSignature + the codeSigning extended key usage. Without the EKU,
# codesign refuses the certificate.
_CERT_CONFIG = """[req]
distinguished_name = dn
prompt = no
x509_extensions = ext
[dn]
CN = {name}
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
"""


def _create_identity(name: str = SIGN_NAME) -> bool:
    """Make a self-signed code-signing certificate and put it in the login keychain. Once.

    The operator's requirement (2026-09-16): signing sets itself up during install — no Keychain
    Access steps. Everything used here ships with macOS: `openssl` makes the key and certificate,
    `security` imports them.

    * The private key exists on disk only inside a private temp folder, for the seconds between
      `openssl` and `security import`, and the folder is deleted whatever happens.
    * `-T /usr/bin/codesign` lets codesign use the key. macOS may still show ONE keychain dialog on
      the first signing; "Always Allow" ends it.
    * The .p12 is written with SHA1/3DES on purpose: `security import` cannot read the AES
      default of newer openssl builds, and the file lives for seconds under a throwaway password.
    * 10 years, so it never expires under a working install.
    """
    import secrets
    import shutil
    import tempfile

    if not shutil.which("openssl") or not shutil.which("security"):
        return False
    tmp = Path(tempfile.mkdtemp(prefix="wf-sign-"))
    try:
        os.chmod(tmp, 0o700)
        cfg, key, crt, p12 = tmp / "c.cnf", tmp / "k.pem", tmp / "c.pem", tmp / "i.p12"
        cfg.write_text(_CERT_CONFIG.format(name=name))
        pw = secrets.token_hex(16)
        steps = [
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
             "-config", str(cfg), "-keyout", str(key), "-out", str(crt)],
            ["openssl", "pkcs12", "-export", "-inkey", str(key), "-in", str(crt), "-out", str(p12),
             "-name", name, "-passout", f"pass:{pw}",
             "-keypbe", "PBE-SHA1-3DES", "-certpbe", "PBE-SHA1-3DES", "-macalg", "sha1"],
            ["security", "import", str(p12), "-P", pw, "-T", "/usr/bin/codesign"],
        ]
        for cmd in steps:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                print(f"could not create a signing certificate ({cmd[0]} {cmd[1]}): "
                      f"{(r.stderr or r.stdout).strip()[-200:]}")
                return False
        return _have_identity(name)
    except Exception as e:
        print(f"could not create a signing certificate: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mac_after(app: Path) -> None:
    """Sign, then say the one thing that would otherwise look like a bug."""
    ident = _sign_identity()
    if ident == "-" and not os.environ.get("WAVEFLOW_SIGN_IDENTITY"):
        print(f"No '{SIGN_NAME}' certificate yet — creating one (once, in your login keychain).")
        if _create_identity():
            ident = SIGN_NAME
            print("created. macOS may ask once whether codesign may use it: choose Always Allow.")
    try:
        # No --identifier: with --deep it would stamp every nested library too. The bundle's own
        # identifier comes from CFBundleIdentifier (--osx-bundle-identifier above).
        r = subprocess.run(["codesign", "--force", "--deep", "--sign", ident, str(app)],
                           check=False, timeout=300)
        if r.returncode != 0 and ident != "-":
            print(f"signing with '{ident}' failed — falling back to ad-hoc.")
            ident = "-"
            subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                           check=False, timeout=300)
        print("ad-hoc signed." if ident == "-" else f"signed with '{ident}'.")
    except Exception as e:
        print(f"could not sign ({e}) — macOS will complain harder on first launch.")
    print("\nFirst launch on macOS:")
    print("  1. Right-click the app -> Open. Gatekeeper blocks a double-click on an unsigned app.")
    print("  2. Grant Microphone, then Accessibility, then Input Monitoring.")
    if ident == "-":
        print("  3. WARNING: ad-hoc signed. After EVERY rebuild, REMOVE WaveFlow from Accessibility")
        print("     and Input Monitoring (the − button) and add it again. Switching the old entry")
        print("     on is not enough: it belongs to the previous build.")
        print(f"     The automatic '{SIGN_NAME}' certificate could not be made — see the message above.")


if __name__ == "__main__":
    sys.exit(main())
