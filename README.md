# WaveFlow

Private, local voice dictation for Windows. Press a hotkey, talk, and the words type live into
whatever text box has focus. Speech recognition runs on **your** hardware — this PC, a server
in your house, or your own VPS. Nothing goes to a cloud service.

Engine: NVIDIA **Parakeet TDT 0.6B v2** (English), via ONNX Runtime (default) or NVIDIA NeMo.

> Status: v1. Windows client only. English only.

---

## Pick a setup

| Where the engine runs | Section | Needs | Token |
|---|---|---|---|
| **This PC — background app** (easiest) | [A](#a-this-pc--background-app-onnx) | Any 4+ core CPU, or a DirectX 12 GPU | not needed (local only) |
| **This PC — Docker** | [B](#b-this-pc-or-onsite-server--docker) | Docker Desktop | required |
| **Onsite server** (LAN, Tailscale, VPN) — Docker | [B](#b-this-pc-or-onsite-server--docker) | A box with Docker | required |
| **Onsite server** — Python venv | [C](#c-onsite-server--python-venv) | Python 3.12 | required |
| **Offsite VPS** — Docker + HTTPS | [D](#d-offsite-vps--docker--https) | A VPS and a domain name | required |

The server listens on `127.0.0.1` (this machine only) by default. It **refuses to start** on any
other address without a token.

## Engines

| Engine | Device | Model size | Seconds to re-read 5 / 10 / 20 s of speech | Memory |
|---|---|---|---|---|
| ONNX int8 (default) | CPU — Intel Core Ultra 7 270K, 4 threads | ~660 MB | 0.14 / 0.31 / 0.64 | ~1.5 GB RAM |
| ONNX int8 | CPU — Intel i7-7700K (2017), 4 threads | ~660 MB | 0.33 / 0.59 / 1.17 | ~1.5 GB RAM |
| ONNX fp32 | GPU — DirectML on Windows (RTX 5070 Ti) | ~2.4 GB | 0.20 / 0.21 / 0.25 | ~2.5 GB VRAM |
| ONNX fp32 | GPU — CUDA on Linux | ~2.4 GB | not measured | ~2.5 GB VRAM |
| NeMo fp16 ("max quality") | NVIDIA GPU, Docker only (GTX 1060) | ~1.2 GB | 0.11 / 0.15 / 0.24 | ~1.6 GB VRAM, ~7 GB image |

Live mode re-reads the current sentence about every 0.7 s. On a slower machine it slows that
pace down by itself. An old 4-core CPU keeps up well for sentences up to ~10 s.

**CPU threads:** set `--threads` to your number of **physical performance cores**. Do not count
hyperthreads or efficiency cores. On a hybrid Intel CPU, all 24 threads ran ~4x slower than 4.

**Old NVIDIA GPUs (Pascal, GTX 10xx) with ONNX CUDA:** use Python 3.12,
`onnxruntime-gpu==1.23.2` and `nvidia-cudnn-cu12==9.1.0.70`. Newer cuDNN fails on Pascal. The
NeMo Docker image already works on Pascal.

---

## A. This PC — background app (ONNX)

```powershell
git clone <this repo> WaveFlow
cd WaveFlow
py -3.12 -m venv venv
venv\Scripts\pip install -r requirements.txt
# CPU:
venv\Scripts\python server\parakeet_server.py --engine onnx --onnx-quant int8 --device cpu --threads 4
# or GPU (DirectML):
venv\Scripts\pip uninstall -y onnxruntime
venv\Scripts\pip install onnxruntime-directml
venv\Scripts\python server\parakeet_server.py --engine onnx --onnx-quant fp32 --device dml
```

The model downloads on first start. Then, in a second window, start the client:

```powershell
venv\Scripts\python app\waveflow.py
```

Default hotkey: `Ctrl+Alt+W`. Change the hotkey, mic and skin from the ⚙ menu.

## B. This PC or onsite server — Docker

```bash
cd docker
python -c "import secrets; print('WAVEFLOW_TOKEN=' + secrets.token_urlsafe(32))" > .env
echo "HOST_BIND=127.0.0.1" >> .env     # 0.0.0.0 = reachable from your network
echo "THREADS=4" >> .env
docker compose -f compose.yml up -d --build                  # ONNX on CPU, port 8756
docker compose -f compose.yml --profile nemo up -d --build   # + NeMo on NVIDIA GPU, port 8757
```

NeMo needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)
and ~20 GB free disk while it builds.

In the client `app/config.json`: `"url": "http://<server>:8756"` and `"token": "<your token>"`.

## C. Onsite server — Python venv

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements-server.txt
export WAVEFLOW_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
venv/bin/python server/parakeet_server.py --engine onnx --host 0.0.0.0 --threads 4
```

Use a private network (LAN, Tailscale, WireGuard). Plain HTTP sends audio unencrypted. For
anything outside your own network, use D.

## D. Offsite VPS — Docker + HTTPS

1. Point a DNS name at the VPS (for example `stt.example.com`).
2. Create `docker/.env`:
   ```
   WAVEFLOW_DOMAIN=stt.example.com
   WAVEFLOW_TOKEN=<long random string>
   ```
3. `docker compose -f docker/compose.vps.yml up -d --build`
4. In the client: `"url": "https://stt.example.com"` and the same token.

Caddy gets the HTTPS certificate by itself. Only ports 80 and 443 are open. The engine port is not.

---

## Your own words (vocabulary)

The model does not know your names and jargon. Copy `server/vocab.example.json` to
`server/vocab.user.json` (or set `WAVEFLOW_VOCAB` to a path), then add how the model
mis-hears each word. Restart the server. `vocab.user.json` is git-ignored.

## Privacy and security

- Audio goes only to the server URL you set. No telemetry.
- Server default: `127.0.0.1`. Any other address needs `--token` or `WAVEFLOW_TOKEN`.
  `--allow-no-token` turns that off. Use it only on a network you fully trust.
- The client sends the token as `Authorization: Bearer <token>`. It is never written to logs.
- Recording is **off**. `--record <dir>` (or `"record"` in `config.json`) saves session audio,
  for debugging only.

## Build the .exe

```powershell
venv\Scripts\pip install pyinstaller
venv\Scripts\python app\build.py      # -> dist\WaveFlow.exe
```

## Tests

```bash
cd server
python test_live_settle.py   # LIVE_SETTLE_OK
python test_auth.py          # AUTH_OK
python vocab.py              # VOCAB_OK
```

## Licences

WaveFlow code: [MIT](LICENSE). Third-party parts: [NOTICE.md](NOTICE.md).
The Parakeet model is **CC-BY-4.0** (NVIDIA; ONNX conversion by istupakov). Credit them if you
redistribute it.
