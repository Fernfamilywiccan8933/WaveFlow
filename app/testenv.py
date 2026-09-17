"""Import FIRST in every test: point WaveFlow's settings, data and log at a throwaway folder.

Since a Mac source run shares ~/Library/Application Support/WaveFlow and ~/Library/Logs/WaveFlow
with the installed app, a test that imports waveflow wrote its SIMULATED failures into the user's
real log — "local engine not ready after 120s" thirteen seconds after launch, indistinguishable
from a real fault (Mac, 2026-09-16). WAVEFLOW_DATA redirects config, data and (on a Mac) the log.

On exit it checks that nothing resolved under the real ~/Library, and fails the run if it did.
"""
import atexit
import os
import sys
import tempfile
from pathlib import Path

os.environ["WAVEFLOW_DATA"] = tempfile.mkdtemp(prefix="wf-test-")
_REAL_LIBRARY = Path.home() / "Library"


def _under_real_library(p) -> bool:
    try:
        p = Path(p).resolve()
        lib = _REAL_LIBRARY.resolve()
        return p == lib or lib in p.parents
    except Exception:
        return False


def _guard():
    S = sys.modules.get("setup_logic")
    if S is None:
        return
    bad = [f"{name}() -> {fn()}" for name, fn in (("app_dir", S.app_dir), ("log_dir", S.log_dir),
                                                  ("app_data", S.app_data))
           if _under_real_library(fn())]
    if bad:
        print("TESTENV_FAIL — a test resolved the user's real WaveFlow folders:\n  " + "\n  ".join(bad))
        sys.stdout.flush()
        os._exit(1)


atexit.register(_guard)
