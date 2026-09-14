"""The fixed judge — one scoring function for every STT candidate, every box.

Scores a candidate STT endpoint (OpenAI-shape) against the fixed fixture set:
  - WER on the known-text fixtures (word error rate, lower better)
  - HALLUCINATION on the silence/noise fixtures (any non-trivial text = a hit)
  - LATENCY per request (wall clock, includes server inference)

This file is the SAME judge locally (the dev-machine harness) and on the GPU server
(Phase 0 bake-off). It must not be edited per-candidate — hardened means fixed.

Run: python judge.py --url http://127.0.0.1:8756 [--fixtures bakeoff/fixtures]
Exit code 0 = judged (report printed); nonzero = harness failure.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests


def norm(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r)][len(h)] / len(r)


def judge(url: str, fixtures: Path) -> dict:
    manifest = json.loads((fixtures / "manifest.json").read_text())
    speech, halluc, latencies = [], [], []
    per_file = {}
    for fname, ref in sorted(manifest.items()):
        wav = fixtures / fname
        t0 = time.perf_counter()
        resp = requests.post(f"{url}/v1/audio/transcriptions",
                             files={"file": (fname, wav.read_bytes(), "audio/wav")},
                             data={"model": "whatever"}, timeout=300)
        resp.raise_for_status()
        ms = int((time.perf_counter() - t0) * 1000)
        text = resp.json().get("text", "")
        latencies.append(ms)
        if ref:  # speech fixture -> WER
            w = wer(ref, text)
            speech.append(w)
            per_file[fname] = {"wer": round(w, 3), "ms": ms, "text": text}
        else:    # silence/noise fixture -> hallucination probe
            invented = len(norm(text))
            halluc.append(invented)
            per_file[fname] = {"invented_words": invented, "ms": ms, "text": text}
    return {
        "endpoint": url,
        "mean_wer": round(sum(speech) / len(speech), 4) if speech else None,
        "hallucinated_words_on_silence": sum(halluc),
        "silence_files_clean": sum(1 for h in halluc if h == 0),
        "silence_files_total": len(halluc),
        "mean_latency_ms": int(sum(latencies) / len(latencies)),
        "p_worst_latency_ms": max(latencies),
        "per_file": per_file,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--fixtures", default=str(Path(__file__).parent / "fixtures"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    report = judge(args.url.rstrip("/"), Path(args.fixtures))
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
