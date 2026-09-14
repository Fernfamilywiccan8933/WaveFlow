"""Token + bind checks for parakeet_server. No model is loaded.

Run: venv/Scripts/python.exe server/test_auth.py   -> prints AUTH_OK
"""
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import parakeet_server as P

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}: got {got!r}, want {want!r}")


P.app.state.burst_max_s, P.app.state.burst_gap_s = P.BURST_MAX_S, P.BURST_GAP_S   # main() sets these
c = TestClient(P.app)
TX = "/v1/audio/transcriptions"   # with no file, an AUTHORISED call is 422 (validation), not 401

# --- token set ---
P.TOKEN = "s3cret"
check("health open without token", c.get("/health").status_code, 200)
check("health reports auth on", c.get("/health").json().get("auth"), True)
check("no token -> 401", c.post(TX).status_code, 401)
check("wrong token -> 401", c.post(TX, headers={"Authorization": "Bearer nope"}).status_code, 401)
check("right token passes auth", c.post(TX, headers={"Authorization": "Bearer s3cret"}).status_code, 422)
check("token in query is NOT accepted on HTTP", c.post(TX + "?token=s3cret").status_code, 401)


def ws_opens(url, headers=None):
    try:
        with c.websocket_connect(url, headers=headers or {}) as ws:
            ws.send_text("reset")
            ws.receive_text()
            return True
    except WebSocketDisconnect:
        return False


check("ws without token refused", ws_opens("/v1/audio/stream"), False)
check("ws wrong token refused", ws_opens("/v1/audio/stream?token=nope"), False)
check("ws header token opens", ws_opens("/v1/audio/stream", {"Authorization": "Bearer s3cret"}), True)
check("ws query token opens", ws_opens("/v1/audio/stream?mode=live&token=s3cret"), True)

# --- no token (loopback use) ---
P.TOKEN = ""
check("no token configured -> open", c.post(TX).status_code, 422)
check("ws open with no token configured", ws_opens("/v1/audio/stream"), True)

# --- bind rules ---
check("127.0.0.1 is loopback", P._is_loopback("127.0.0.1"), True)
check("localhost is loopback", P._is_loopback("localhost"), True)
check("::1 is loopback", P._is_loopback("::1"), True)
check("0.0.0.0 is not loopback", P._is_loopback("0.0.0.0"), False)
check("LAN is not loopback", P._is_loopback("192.168.1.10"), False)

srv = str(Path(__file__).with_name("parakeet_server.py"))
r = subprocess.run([sys.executable, srv, "--host", "0.0.0.0", "--engine", "onnx"],
                   capture_output=True, text=True, timeout=60, env={**__import__("os").environ,
                                                                    "WAVEFLOW_TOKEN": ""})
check("open bind without token exits non-zero", r.returncode != 0, True)
check("and says why", "without a token" in (r.stderr + r.stdout), True)

if FAILS:
    print("AUTH_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("AUTH_OK")
