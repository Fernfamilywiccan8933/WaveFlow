"""Build the fixed bake-off audio set with KNOWN ground truth.

Fixtures = SAPI-synthesized speech (known text), pure silence (the hallucination
probe), and low-level noise. The same set ships to the GPU server for Phase 0 so both
boxes are judged against identical audio. A real user's voice gets ADDED later
(record_voice.py) — synthetic voices are the floor, not the whole set.

Run: venv/Scripts/python.exe bakeoff/make_fixtures.py
"""
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
FIX.mkdir(exist_ok=True)

SENTENCES = {
    "s01_plain": "The quick brown fox jumps over the lazy dog near the riverbank.",
    "s02_dictation": "Please schedule the meeting for Tuesday at three thirty and send the notes to the whole team.",
    "s03_jargon": "Restart the docker container, check nvidia smi for VRAM usage, and tail the pipeline log.",
    "s04_numbers": "The order total is forty seven dollars and ninety five cents for twelve items.",
    "s05_short": "Send it.",
}

def sapi_tts(text: str, out_wav: Path, rate: int = 0) -> None:
    """Windows SAPI text-to-speech straight to a 16kHz mono WAV."""
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = {rate}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('{out_wav}', $fmt)
$s.Speak(@'
{text}
'@)
$s.Dispose()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True, timeout=60)

def write_wav(path: Path, samples: list[int], sr: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))

def main() -> None:
    manifest = {}
    for name, text in SENTENCES.items():
        out = FIX / f"{name}.wav"
        sapi_tts(text, out)
        manifest[out.name] = text
        print(f"  {out.name}: {out.stat().st_size} bytes")

    # THE hallucination probe: pure digital silence, 6 seconds
    write_wav(FIX / "x01_silence.wav", [0] * (16000 * 6))
    manifest["x01_silence.wav"] = ""

    # near-silence with faint broadband noise (dead-air mic floor)
    import random
    rng = random.Random(313)
    write_wav(FIX / "x02_noisefloor.wav",
              [int(rng.gauss(0, 40)) for _ in range(16000 * 6)])
    manifest["x02_noisefloor.wav"] = ""

    # faint hum (60Hz) — another classic dead-air signature
    write_wav(FIX / "x03_hum.wav",
              [int(120 * math.sin(2 * math.pi * 60 * t / 16000))
               for t in range(16000 * 6)])
    manifest["x03_hum.wav"] = ""

    (FIX / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"manifest: {len(manifest)} fixtures")

if __name__ == "__main__":
    sys.exit(main())
