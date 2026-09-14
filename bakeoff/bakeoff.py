"""Phase-0 STT bake-off — runs on the GPU server. Keep-if-better, one fixed judge.

Scores each candidate model directly (no server needed) against the fixed
fixture set, on the actual deployment GPU. The verdict rule is hard:
a challenger is KEEP only if it beats the incumbent on BOTH mean WER and
silence-hallucination, without a latency regression >20%. Otherwise REJECT —
"staying put" is a legitimate outcome.

Also answers the open device question: reports which physical GPU cuda:0
maps to before anything is scored.

Run (from this directory, in a venv with faster-whisper):
    python3 bakeoff.py [--device cuda] [--gpu-index 0]
Parakeet needs NeMo (pip install -U 'nemo_toolkit[asr]'); if absent it is
SKIPPED and the report says so explicitly — an unscored candidate is never
counted as a loss or a win.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from judge import norm, wer  # the SAME fixed judge as the local dev-machine harness

FIX = Path(__file__).parent / "fixtures"
INCUMBENT = "distil-medium.en"
CANDIDATES_FW = [INCUMBENT, "large-v3-turbo"]  # faster-whisper names, int8
PARAKEET = "nvidia/parakeet-tdt-0.6b-v2"


def gpu_report() -> dict:
    try:
        import torch
        n = torch.cuda.device_count()
        return {"cuda_available": torch.cuda.is_available(),
                "devices": {i: torch.cuda.get_device_name(i) for i in range(n)},
                "cuda0_is": torch.cuda.get_device_name(0) if n else None}
    except Exception as e:
        return {"error": f"torch probe failed: {e}"}


def score_transcripts(get_text) -> dict:
    """get_text(wav_path) -> transcript. Returns judge metrics."""
    manifest = json.loads((FIX / "manifest.json").read_text())
    speech, halluc, lat = [], [], []
    per_file = {}
    for fname, ref in sorted(manifest.items()):
        t0 = time.perf_counter()
        text = get_text(FIX / fname)
        ms = int((time.perf_counter() - t0) * 1000)
        lat.append(ms)
        if ref:
            w = wer(ref, text)
            speech.append(w)
            per_file[fname] = {"wer": round(w, 3), "ms": ms, "text": text}
        else:
            inv = len(norm(text))
            halluc.append(inv)
            per_file[fname] = {"invented_words": inv, "ms": ms, "text": text}
    return {"mean_wer": round(sum(speech) / len(speech), 4),
            "hallucinated_words_on_silence": sum(halluc),
            "silence_files_clean": sum(1 for h in halluc if h == 0),
            "mean_latency_ms": int(sum(lat) / len(lat)),
            "per_file": per_file}


def run_faster_whisper(name: str, device: str, gpu_index: int) -> dict:
    from faster_whisper import WhisperModel
    print(f"  loading {name} ({device} int8) ...", flush=True)
    m = WhisperModel(name, device=device, device_index=gpu_index, compute_type="int8")

    def get_text(wav: Path) -> str:
        segs, _ = m.transcribe(str(wav), beam_size=5, vad_filter=False)
        return " ".join(s.text.strip() for s in segs).strip()

    r = score_transcripts(get_text)
    del m
    return r


def run_parakeet(device: str) -> dict | None:
    try:
        import nemo.collections.asr as nemo_asr
    except ImportError:
        return None
    print(f"  loading {PARAKEET} ...", flush=True)
    m = nemo_asr.models.ASRModel.from_pretrained(model_name=PARAKEET)
    if device == "cuda":
        m = m.cuda()
    m.eval()

    def get_text(wav: Path) -> str:
        out = m.transcribe([str(wav)])
        first = out[0]
        return (first.text if hasattr(first, "text") else str(first)).strip()

    return score_transcripts(get_text)


def verdict(incumbent: dict, challenger: dict) -> str:
    better_wer = challenger["mean_wer"] <= incumbent["mean_wer"]
    better_halluc = (challenger["hallucinated_words_on_silence"]
                     < incumbent["hallucinated_words_on_silence"]
                     or incumbent["hallucinated_words_on_silence"] == 0
                     and challenger["hallucinated_words_on_silence"] == 0)
    lat_ok = challenger["mean_latency_ms"] <= incumbent["mean_latency_ms"] * 1.2
    if better_wer and better_halluc and lat_ok:
        return "KEEP (beats incumbent on WER + hallucination, latency within bound)"
    reasons = []
    if not better_wer:
        reasons.append("WER not better")
    if not better_halluc:
        reasons.append("hallucination not better")
    if not lat_ok:
        reasons.append("latency regression >20%")
    return "REJECT (" + ", ".join(reasons) + ")"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--gpu-index", type=int, default=0)
    args = ap.parse_args()

    report = {"gpu": gpu_report(), "device": args.device,
              "gpu_index": args.gpu_index, "results": {}, "verdicts": {}}
    print("GPU map:", json.dumps(report["gpu"]))

    for name in CANDIDATES_FW:
        print(f"[{name}]", flush=True)
        report["results"][name] = run_faster_whisper(name, args.device, args.gpu_index)

    print(f"[parakeet]", flush=True)
    pk = run_parakeet(args.device)
    if pk is None:
        report["results"]["parakeet-SKIPPED"] = (
            "NeMo not installed — run: pip install -U 'nemo_toolkit[asr]' "
            "then re-run. Skipped, NOT scored.")
        print("  SKIPPED: NeMo not installed (report marks this explicitly)")
    else:
        report["results"][PARAKEET] = pk

    inc = report["results"][INCUMBENT]
    for name, res in report["results"].items():
        if name == INCUMBENT or isinstance(res, str):
            continue
        report["verdicts"][name] = verdict(inc, res)

    import socket
    out = Path(__file__).parent / f"report_{socket.gethostname().lower()}_{int(time.time())}.json"
    out.write_text(json.dumps(report, indent=2))
    print("\n=== VERDICTS (keep-if-better vs distil-medium.en) ===")
    for k, v in report["verdicts"].items():
        print(f"  {k}: {v}")
    print(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
