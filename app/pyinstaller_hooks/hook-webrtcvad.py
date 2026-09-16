"""Override PyInstaller's stock hook for webrtcvad.

The stock hook calls `copy_metadata('webrtcvad')`. This project installs the module from the
`webrtcvad-wheels` distribution — the one that ships prebuilt wheels, since the original
`webrtcvad` needs a C compiler to install — and that distribution's metadata is registered under
its own name. So the stock hook raised PackageNotFoundError and the whole build failed, the moment
the engine was bundled into the app (found 2026-09-16).

A hook in a directory given to --additional-hooks-dir takes precedence over the stock one.
"""
from PyInstaller.utils.hooks import copy_metadata

datas = []
for _dist in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas += copy_metadata(_dist)
        break
    except Exception:
        # Neither is required at runtime — nothing in WaveFlow reads webrtcvad's metadata. It is
        # copied only because the stock hook did; missing it must never fail a build.
        continue
