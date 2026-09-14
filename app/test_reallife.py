"""Real-life test protocol (steering rows 34-35): REAL internet speech, stress
duration, repeated cycling, timing budgets asserted. Run before any 'done'.

Usage: venv/Scripts/python.exe app/test_reallife.py --wav <real_speech.wav> [--url ...]
Exit 0 = all pass.
"""
import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from audio import float_to_wav16k, resample_mono_16k  # noqa: E402
from stt import transcribe  # noqa: E402

# timing budgets (CPU base.en stand-in server; a GPU server must beat these hard)
BUDGET_PARTIAL_MS = 3000      # a 12s-window partial must return under 3s
BUDGET_FINAL_FACTOR = 0.6     # final transcribe <= 0.6x audio duration
BUDGET_CYCLE_MS = 4000        # each short-utterance round trip under 4s


def load_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            pcm = pcm.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
    return resample_mono_16k(pcm.astype(np.float32) / 32767, sr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8756")
    args = ap.parse_args()
    audio = load_16k(Path(args.wav))
    dur = len(audio) / 16000
    print(f"real speech loaded: {dur:.1f}s @16k")
    results = []

    # 1) PARTIAL path: 12s window, beam=1 — the every-0.6s request
    win = audio[: 12 * 16000]
    t0 = time.perf_counter()
    text1, _ = transcribe(args.url, float_to_wav16k(win), beam=1)
    ms = int((time.perf_counter() - t0) * 1000)
    ok = ms <= BUDGET_PARTIAL_MS and len(text1.split()) >= 5
    results.append(ok)
    print(f"[partial-12s beam1] {ms}ms (budget {BUDGET_PARTIAL_MS}) "
          f"words={len(text1.split())} -> {'PASS' if ok else 'FAIL'}")
    print(f"   text: {text1[:100]!r}")

    # 2) FINAL path on the full real clip, beam=5
    t0 = time.perf_counter()
    text2, _ = transcribe(args.url, float_to_wav16k(audio), beam=5)
    ms = int((time.perf_counter() - t0) * 1000)
    ok = ms <= dur * 1000 * BUDGET_FINAL_FACTOR and len(text2.split()) >= 30
    results.append(ok)
    print(f"[final-{dur:.0f}s beam5] {ms}ms (budget {int(dur*1000*BUDGET_FINAL_FACTOR)}) "
          f"words={len(text2.split())} -> {'PASS' if ok else 'FAIL'}")

    # 3) STRESS: ~120s of speech (looped real audio), final path — the
    #    'minutes-slow' reproduction check, now with the 90s cap it must obey
    stress = np.tile(audio, int(np.ceil(120 / dur)))[: 120 * 16000]
    capped = stress[: 90 * 16000]          # app caps utterances at 90s
    t0 = time.perf_counter()
    text3, _ = transcribe(args.url, float_to_wav16k(capped), beam=5)
    ms = int((time.perf_counter() - t0) * 1000)
    ok = ms <= 90 * 1000 * BUDGET_FINAL_FACTOR
    results.append(ok)
    print(f"[stress-90s-cap beam5] {ms}ms (budget {int(90*1000*BUDGET_FINAL_FACTOR)}) "
          f"words={len(text3.split())} -> {'PASS' if ok else 'FAIL'}")

    # 4) CYCLE: 8 rapid short utterances (3.5s each) — repeated real use
    clip = audio[: int(3.5 * 16000)]
    worst = 0
    for i in range(8):
        t0 = time.perf_counter()
        transcribe(args.url, float_to_wav16k(clip), beam=5)
        worst = max(worst, int((time.perf_counter() - t0) * 1000))
    ok = worst <= BUDGET_CYCLE_MS
    results.append(ok)
    print(f"[cycle-8x-3.5s] worst {worst}ms (budget {BUDGET_CYCLE_MS}) -> "
          f"{'PASS' if ok else 'FAIL'}")

    print("ALL PASS" if all(results) else "FAILURES PRESENT")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
