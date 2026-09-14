"""Add a real user's voice to the bake-off fixture set.

Synthetic SAPI voices are the floor; real-voice fixtures make the judge match
reality. Run, read the sentence aloud, and the WAV + ground-truth manifest
entry are added — the judge picks them up automatically.

Run: venv/Scripts/python.exe bakeoff/record_voice.py --text "what you will say" [--seconds 8]
"""
import argparse
import io
import json
import sys
import time
import wave
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
SR = 16000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="the exact words you will speak")
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()

    import sounddevice as sd
    frames = []

    def cb(indata, n, t, status):
        frames.append(bytes(indata))

    n_existing = len(list(FIX.glob("v*_voice*.wav")))
    name = f"v{n_existing + 1:02d}_voice.wav"
    print(f"recording {args.seconds}s — speak now: {args.text!r}")
    with sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", callback=cb):
        time.sleep(args.seconds)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(frames))
    (FIX / name).write_bytes(buf.getvalue())

    manifest_path = FIX / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[name] = args.text
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"added {name} ({args.seconds}s) + manifest entry — judge will score it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
