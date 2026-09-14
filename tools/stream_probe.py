"""Probe the STREAMING endpoint end-to-end with a real WAV: streams PCM16
frames at real-time pace and prints each incremental hypothesis. This is the
staging DONE check — run it against the CPU staging container:

  venv/Scripts/python.exe tools/stream_probe.py --url "ws://127.0.0.1:8756/v1/audio/stream?mode=live" --wav <16k-mono.wav>

PASS = hypotheses grow monotonically word-by-word (stable prefixes, no loops).
"""
import argparse
import json
import threading
import time
import wave

import websocket


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--chunk-ms", type=int, default=100)
    ap.add_argument("--latency", type=int, default=None)
    ap.add_argument("--flush", action="store_true",
                    help="send 'flush' at end of audio, as a consumer closing a session would")
    args = ap.parse_args()

    with wave.open(args.wav, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, "need 16k mono"
        pcm = w.readframes(w.getnframes())

    url = args.url + (("&" if "?" in args.url else "?")+f"latency={args.latency}" if args.latency is not None else "")
    ws = websocket.create_connection(url, timeout=30)
    got, done = [], threading.Event()

    def rx():
        try:
            while True:
                m = json.loads(ws.recv())
                # mode=live speaks stable/tail instead of cumulative text. Normalise to one
                # string so the stability check below applies unchanged to both protocols.
                if "stable" in m:
                    m["text"] = (m["stable"] + " " + m.get("tail", "")).strip()
                    mark = "FINAL" if m.get("final") else "  ..."
                    print(f"  [{m.get('ms','?'):>4}ms] {mark} stable={m['stable']!r} "
                          f"tail={m.get('tail','')!r}")
                else:
                    print(f"  [{m.get('ms','?'):>4}ms] {m.get('text','')!r}")
                got.append(m)
        except Exception:
            done.set()

    threading.Thread(target=rx, daemon=True).start()
    n = 16000 * 2 * args.chunk_ms // 1000  # bytes per chunk
    t0 = time.time()
    for i in range(0, len(pcm), n):
        ws.send_binary(pcm[i:i + n])
        time.sleep(args.chunk_ms / 1000)   # real-time pace
    if args.flush:
        ws.send("flush")
    time.sleep(2.0)
    ws.close()
    done.wait(timeout=5)
    dur = time.time() - t0
    if got and "stable" in got[0]:
        # mode=live reports per-UTTERANCE, not cumulatively: rebuild the whole transcript
        # the way a consumer would, by appending each final and then the live tail.
        parts = [(m.get("stable", "") + " " + m.get("tail", "")).strip()
                 for m in got if m.get("final")]
        if got and not got[-1].get("final"):
            parts.append((got[-1].get("stable", "") + " " + got[-1].get("tail", "")).strip())
        final = " ".join(x for x in parts if x)
    else:
        final = got[-1]["text"] if got else ""
    print(f"\n{len(got)} hypotheses over {dur:.1f}s")
    print(f"FINAL: {final!r}")
    if got and "stable" in got[0]:
        # mode=live: the contract is that COMMITTED text never shrinks or changes WITHIN an
        # utterance. Comparing raw strings across utterances counts every new sentence as a
        # regression, which says nothing about whether a consumer would have to backspace.
        bad, pairs = [], 0
        for a, b in zip(got, got[1:]):
            if a.get("utt") != b.get("utt") or a.get("final"):
                continue                      # different utterance: no relationship to check
            pairs += 1
            aw, bw = a.get("stable", "").split(), b.get("stable", "").split()
            if len(bw) < len(aw):
                bad.append(f"stable SHRANK {len(aw)}->{len(bw)} words")
            elif [w.lower() for w in bw[:len(aw)]] != [w.lower() for w in aw]:
                bad.append(f"committed word CHANGED: {aw!r} -> {bw[:len(aw)]!r}")
        print(f"committed-text stability: {pairs-len(bad)}/{pairs} in-utterance transitions "
              f"clean ({'PASS' if not bad else 'FAIL'})")
        for m in bad[:5]:
            print(f"    {m}")
        print(f"utterances: {1 + max((m.get('utt', 0) for m in got), default=0)}  "
              f"max tail redraw: {max((len(m.get('tail','')) for m in got), default=0)} chars")
    else:
        unstable = sum(1 for a, b in zip(got, got[1:])
                       if not b["text"].startswith(a["text"][:max(0, len(a["text"]) - 20)]))
        print(f"stability: {len(got)-unstable}/{len(got)} prefix-stable "
              f"({'PASS' if unstable == 0 else f'{unstable} regressions'})")


if __name__ == "__main__":
    main()
