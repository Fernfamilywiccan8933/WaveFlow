"""Import every WaveFlow module as if this were a Mac.

Three things have to be true at once or the check is worthless:
  1. sys.platform must say "darwin" BEFORE anything imports, or every `IS_WINDOWS` constant is
     captured as True and the code happily takes the Windows branch. An earlier version of this
     harness missed this and reported a false pass.
  2. ctypes.wintypes and ctypes.windll must be gone, and BOTH import spellings blocked —
     `import ctypes.wintypes` and `from ctypes import wintypes` (the fromlist form).
  3. Third-party packages are stubbed, not blocked. sounddevice's WINDOWS build imports wintypes
     internally; its macOS build does not. Failing on that would be testing sounddevice, not us.
"""
import builtins, ctypes, os, sys, types
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, r"F:\AI_Projects\WaveFlow\app")

sys.platform = "darwin"                       # (1) before any app module is imported
sys.modules.pop("ctypes.wintypes", None)      # (2)
for attr in ("wintypes", "windll"):
    if hasattr(ctypes, attr):
        delattr(ctypes, attr)

# (3) stand-ins for packages that exist on macOS but whose Windows builds are not portable
# _scproxy is a macOS-ONLY CPython builtin that urllib imports when sys.platform is darwin, to
# read the system proxy settings. Windows Python does not ship it, so its absence here is an
# artifact of the simulation, not a defect in WaveFlow — on a real Mac it is present.
_sc = types.ModuleType("_scproxy")
_sc._get_proxy_settings = lambda: {}
_sc._get_proxies = lambda: {}
sys.modules["_scproxy"] = _sc

for name in ("sounddevice", "pyperclip"):
    m = types.ModuleType(name)
    m.__getattr__ = lambda n, _m=name: (_ for _ in ()).throw(
        AttributeError(f"{_m}.{n} not stubbed"))
    sys.modules.setdefault(name, m)

_real = builtins.__import__
BLOCKED = {"winreg", "uiautomation", "comtypes", "comtypes.client", "win32api", "win32com"}
def _fake(name, g=None, l=None, fromlist=(), level=0):
    if name == "ctypes.wintypes" or (name == "ctypes" and fromlist and "wintypes" in fromlist):
        raise ValueError("_type_ 'v' not supported")
    if name in BLOCKED:
        raise ImportError(f"No module named '{name}'")
    return _real(name, g, l, fromlist, level)
builtins.__import__ = _fake

MODULES = ("osbridge", "osbridge.mac", "osbridge.posix", "audio", "icons", "stt", "setup_logic",
           "remote_install", "local_engine", "wizard_ui", "panels", "uninstall", "settings",
           "wizard", "waveflow")
FAIL = []
for mod in MODULES:
    try:
        __import__(mod)
        print(f"  {mod:16} imports OK")
    except Exception as e:
        FAIL.append(mod)
        print(f"  {mod:16} FAILS -> {type(e).__name__}: {str(e)[:78]}")

if FAIL:
    print("\nAS_MAC_FAIL:", ", ".join(FAIL))
    raise SystemExit(1)

# The backend actually selected must be the Mac one, not a Windows fallback.
import osbridge
print(f"\n  backend selected : {osbridge.NAME}")
print(f"  IS_MAC           : {osbridge.IS_MAC}")
print(f"  data dir         : {osbridge.app_data_dir()}")
print(f"  os_theme()       : {osbridge.os_theme()}")
print(f"  permissions      : {[n for n, _ in osbridge.missing_permissions()]}")
assert osbridge.NAME == "macos", f"wrong backend: {osbridge.NAME}"
# and the degrade-don't-raise contract holds with no pyobjc present
assert osbridge.type_text("hi") == 0
assert osbridge.paste_text("hi") is False
assert osbridge.caret_rect() is None
assert osbridge.inject_text("hi") == "failed"
print("\nAS_MAC_OK — every module imports, the macOS backend is selected, nothing raises")
