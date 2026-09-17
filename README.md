<p align="center">
  <img src="docs/banner.png" alt="WaveFlow — private, local voice dictation" width="880">
</p>

<p align="center">
  <strong>Private voice dictation for Windows and macOS.</strong><br>
  Hold a hotkey, speak, and words type live into whatever text field has focus.<br>
  Speech recognition runs on <em>your</em> hardware — this machine, a home GPU server, or your VPS.
</p>

<p align="center">
  <a href="#get-it">Get WaveFlow</a> ·
  <a href="#why-waveflow">Why WaveFlow</a> ·
  <a href="#what-it-looks-like">Screenshots</a> ·
  <a href="#pick-a-setup">Setup Choices</a> ·
  <a href="#engines">Performance</a> ·
  <a href="#uninstall">Uninstall</a>
</p>

> **Status: v1.** Powered by NVIDIA **Parakeet TDT 0.6B v2** (English) via ONNX Runtime (default) or NVIDIA NeMo. The Windows client is tested and in daily use. The **macOS client is new** — it builds and functions. Reports welcome.

---

## Get it

<table>
<tr>
<th width="50%">🪟 &nbsp; Windows</th>
<th width="50%">🍎 &nbsp; macOS</th>
</tr>
<tr valign="top">
<td>

**Download the app**

[**WaveFlow.exe**](../../releases/latest) → double-click. Nothing else to install.

The speech engine is inside the app (109 MB). No Python, no Docker. The first start downloads the model once (~660 MB).

**Or install from PowerShell**

```powershell
$r = "MrAbookah/WaveFlow"
$u = "[https://github.com/$r/releases/latest/download/WaveFlow.exe](https://github.com/$r/releases/latest/download/WaveFlow.exe)"
iwr $u -OutFile WaveFlow.exe
.\WaveFlow.exe
```

**Or run from source**

```powershell
git clone [https://github.com/MrAbookah/WaveFlow.git](https://github.com/MrAbookah/WaveFlow.git)
cd WaveFlow
py -3.12 -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python app\waveflow.py
```

Verify download integrity:

```powershell
Get-FileHash .\WaveFlow.exe -Algorithm SHA256
```
*(Compare with `WaveFlow.exe.sha256` on the release page)*

</td>
<td>

**Download the app**

No prebuilt `.app` yet. A Mac bundle must be built on a Mac. The command below builds and installs it in ~1 minute.

**Install from Terminal**

```bash
git clone [https://github.com/MrAbookah/WaveFlow.git](https://github.com/MrAbookah/WaveFlow.git)
cd WaveFlow
python3 -m venv venv
venv/bin/pip install -r requirements.txt \
  pyobjc-framework-Cocoa \
  pyobjc-framework-Quartz \
  pyobjc-framework-ApplicationServices
venv/bin/python app/build.py --install
open /Applications/WaveFlow.app
```

This builds `WaveFlow.app`, moves it to **/Applications**, and opens it. Reopen later from Launchpad or Spotlight. Custom path: `--install ~/Apps`.

Model, settings, and logs live in `~/Library/Application Support/WaveFlow` and `~/Library/Logs/WaveFlow` so rebuilding keeps your config intact.

If macOS blocks the first run: right-click the app in Finder and select **Open**.

</td>
</tr>
</table>

The setup wizard opens on first launch. It automatically detects your OS and offers only supported engines.

### macOS permissions

macOS requires three system permissions for system-wide dictation:

| Permission | Purpose |
|---|---|
| Microphone | To hear your speech |
| Accessibility | To inject transcribed text into your focused text field |
| Input Monitoring | To capture your global hotkey while other apps are active |

The wizard's final page and **Settings → Hotkey & look** display live permission states. Click **Allow** on each item to open the native OS prompt. 

* **Automatic Code Signing:** `app/build.py` creates a local `WaveFlow Local Signing` certificate in your keychain. Rebuilds share this identity so permissions persist. If macOS asks whether `codesign` may use the key, choose **Always Allow**.
* **Permissions Reset:** If permissions remain red after granting, press **Reset and ask again** in the panel to refresh OS entitlements.

### Install via AI Assistant

Paste this block directly into Claude Code, Cursor, or your AI terminal tool:

```text
Install WaveFlow from [https://github.com/MrAbookah/WaveFlow](https://github.com/MrAbookah/WaveFlow) on this machine.
Read its README first and follow the section for my operating system.
Create the virtual environment inside the cloned folder, never system-wide.
On macOS also install the three pyobjc frameworks the README names, then build and
install the app with app/build.py --install and open /Applications/WaveFlow.app.
Do not run app/waveflow.py directly on macOS. Tell me that the app's own Allow
buttons ask for the permissions - you cannot grant them for me. Do not change any
of my existing settings and do not install anything globally.
When the setup wizard opens, stop and hand it back to me.
```

---

## Why WaveFlow

| Feature | Typical Local Dictation App | WaveFlow |
|---|---|---|
| **Engine Location** | Host machine only | Local PC, home server (LAN/Tailscale), or VPS |
| **Server Provisioning** | Manual Docker / CLI setup | Setup wizard provisions server **over SSH** with your key |
| **Network Security** | Varies | Binds to loopback by default; token mandatory for remote binds |
| **Typing Behavior** | Transcribes after silence | Types live as you speak; **never rewrites previous words** |
| **Uninstaller** | Leaves configs/models behind | Dedicated uninstaller lists and purges all containers, models, & files |
| **Benchmarks** | Vague estimates | Measured re-read latency with reproducible CLI commands |

---

## What it looks like

<p align="center"><img src="docs/shot_overlay.png" width="720" alt="WaveFlow Live Overlay"></p>

| Setup Wizard: Engine Selection | Wizard: Engine Configuration |
|---|---|
| <img src="docs/shot_wizard_where.png" width="430"> | <img src="docs/shot_wizard_configure.png" width="430"> |

| Settings: Hotkey & Look | Settings: Connection & Token |
|---|---|
| <img src="docs/shot_wizard_last.png" width="430"> | <img src="docs/shot_settings_connection.png" width="430"> |

| Settings: Microphone Level | Native Clean Uninstaller |
|---|---|
| <img src="docs/shot_settings_microphone.png" width="430"> | <img src="docs/shot_settings_uninstall.png" width="430"> |

---

## Pick a setup

| Route | Target Machine | Dependencies | Token | Setup Wizard Action | Tested Status |
|---|---|---|---|---|---|
| **This PC — background app** | Local desktop | 4+ core CPU or DX12 GPU | Optional | Starts & manages background engine | ✅ CPU & DirectML GPU |
| **This PC — Docker** | Local desktop | Docker Desktop | Required | Builds and launches local container | ⚠️ Untested |
| **Onsite Server — Docker** | Home LAN / GPU box | Linux + Docker + SSH access | Required | **Installs container over SSH** | ✅ NeMo on GTX 1060/1080 |
| **Onsite Server — Python venv** | Local network | Python 3.12 | Required | Generates CLI run commands | ✅ ONNX |
| **Offsite VPS — Docker + HTTPS** | Remote cloud VPS | VPS + Domain name | Required | **Deploys Caddy HTTPS stack via SSH** | ⚠️ Untested |

---

## Engines & Performance

Live mode re-evaluates active audio buffers every ~0.7 seconds. Realized processing latency across speech buffer durations:

| Engine | Execution Hardware | Model Size | 5s Buffer | 10s Buffer | 20s Buffer | Memory Footprint |
|---|---|---|---:|---:|---:|---|
| **ONNX int8 (Default)** | Intel Core Ultra 7 270K (4 threads) | ~660 MB | 0.14 s | 0.31 s | 0.64 s | ~1.5 GB RAM |
| **ONNX int8** | Intel i7-7700K (4 threads) | ~660 MB | 0.33 s | 0.59 s | 1.17 s | ~1.5 GB RAM |
| **ONNX fp32 DirectML** | NVIDIA RTX 5070 Ti | ~2.4 GB | 0.20 s | 0.21 s | 0.25 s | ~2.5 GB VRAM |
| **NeMo fp16 (Max Quality)**| NVIDIA GTX 1060 (Docker) | ~1.2 GB | 0.11 s | 0.15 s | 0.24 s | ~1.6 GB VRAM |

### CPU Thread Optimization
Set `--threads` equal to physical **performance cores**. On hybrid CPU architectures (e.g., Intel 14th/15th Gen), allocating efficiency cores or hyperthreads degrades throughput. In testing, 8 performance cores yielded 4x lower latency than utilizing all 24 available logical threads.

---

## A. This PC — background app (ONNX)

```powershell
git clone [https://github.com/MrAbookah/WaveFlow.git](https://github.com/MrAbookah/WaveFlow.git)
cd WaveFlow
py -3.12 -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python app\waveflow.py
```

Run manual headless server execution:
```powershell
# CPU Mode:
venv\Scripts\python server\parakeet_server.py --engine onnx --onnx-quant int8 --device cpu --threads 4

# DirectML GPU Mode:
venv\Scripts\pip uninstall -y onnxruntime
venv\Scripts\pip install onnxruntime-directml
venv\Scripts\python server\parakeet_server.py --engine onnx --onnx-quant fp32 --device dml
```

Default hotkey: `Ctrl+Alt+W`.

---

## B. Onsite Server — Docker

**Automated Setup:** Select *Onsite server* in the setup wizard, enter host SSH credentials, and click **Install**. The wizard uses your existing SSH key (`~/.ssh/id_rsa`), validates remote CUDA drivers/disk space, generates `.env` security tokens, and provisions the container automatically.

**Manual Setup:**
```bash
cd docker
python -c "import secrets; print('WAVEFLOW_TOKEN=' + secrets.token_urlsafe(32))" > .env
echo "HOST_BIND=0.0.0.0" >> .env
echo "THREADS=4" >> .env

# Launch ONNX Engine (Port 8756)
docker compose -f compose.yml up -d --build 

# Launch NeMo Engine (Port 8757, requires NVIDIA Container Toolkit)
docker compose -f compose.yml --profile nemo up -d --build
```

---

## C. Onsite Server — Python venv

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements-server.txt
export WAVEFLOW_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
venv/bin/python server/parakeet_server.py --engine onnx --host 0.0.0.0 --threads 4
```

---

## D. Offsite VPS — Docker + HTTPS

1. Direct A-record DNS entry (`stt.yourdomain.com`) to your VPS IP.
2. Generate environment configuration:
   ```bash
   echo "WAVEFLOW_DOMAIN=stt.yourdomain.com" > docker/.env
   echo "WAVEFLOW_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> docker/.env
   ```
3. Deploy reverse-proxy container:
   ```bash
   docker compose -f docker/compose.vps.yml up -d --build
   ```
Caddy handles ACME TLS certificate provisioning automatically. External port access is restricted to 80/443.

---

## Your own words (Vocabulary)

To seed custom names, technical jargon, or acronyms:
1. Copy `server/vocab.example.json` to `server/vocab.user.json`.
2. Map phonetic misinterpretations to your desired text output:
   ```json
   {
     "Abookah": ["a book ah", "uh booker"],
     "Parakeet": ["pair a keet"]
   }
   ```
3. Restart the server. (`vocab.user.json` is git-ignored).

---

## Privacy and security

- Audio streams transmit exclusively to your specified server endpoint. Zero external telemetry.
- Binding default is restricted to `127.0.0.1`. Remote interfaces (`0.0.0.0`) mandate authentication via Bearer token (`Authorization: Bearer <token>`).
- Audio logging defaults to disabled. Local debugging audio capture requires explicit activation via `--record <dir>`.

---

## Uninstall

Access **Settings → Uninstall** to trigger guided resource cleanup. WaveFlow tracks created resources and removes:
- Application binaries, cached model weights, local `.json` configs, and logs.
- Registered OS startup keys and desktop/start menu shortcuts.
- Docker containers, images, and volumes initialized by the setup wizard.
- Remote server Docker installations provisioned via SSH.

Manual dry-run CLI verification:
```bash
python app/uninstall.py --dry-run
```

---

## Build from source

```powershell
# Windows EXE build:
venv\Scripts\pip install pyinstaller
venv\Scripts\python app\build.py    # Output: dist\WaveFlow.exe
```

```bash
# macOS APP Bundle build:
venv/bin/pip install pyinstaller
venv/bin/python app/build.py        # Output: dist/WaveFlow.app
```

---

## Tests

Execute test suites:
```bash
python server/test_live_settle.py   # Verify live typing stability
python server/test_auth.py          # Validate Bearer token rejection
python server/vocab.py              # Validate phonetic replacement rules
python app/test_wizard.py           # Verify SSH & deployment rules
python app/test_settings.py         # Verify configuration persistence
python app/test_uninstall.py        # Validate resource tracking
```

---

## Licences

- WaveFlow Codebase: **[GNU GPL v3](LICENSE)**
- NVIDIA Parakeet Model Weights: **CC-BY-4.0** (NVIDIA Corporation; ONNX conversion by istupakov).
- Third-Party Dependencies: See [NOTICE.md](NOTICE.md).
