"""Parakeet STT server — NVIDIA Parakeet TDT (600M) via NeMo: accurate, ~zero
hallucination on silence. Serves TWO consumers over ONE contract:

  POST /v1/audio/transcriptions   OpenAI-shape batch (unchanged).
  WS   /v1/audio/stream           SHORT-BURST commits. Client sends binary PCM16
                                  mono 16k (any framing) + the text frame "reset".

WS wire contract (append-only — commits NEVER revise):
  ->  b"<pcm16le>"        audio in
  ->  "reset"             start a fresh segment
  <-  {"reset": true, "text": ""}                                  reset ack
  <-  {"text": <cumulative>, "delta": <new burst>, "commit": true,
       "ms": <n>, "final": false}                                  burst commit

  `text` stays CUMULATIVE for drop-in compatibility, but — unlike the
  FastConformer streaming server it replaces — it only ever GROWS. It is never
  rewritten, so the revise/edit-clobber bug class is gone by construction.
  `delta` is the newly committed burst for append-only consumers.

Why bursts and not word-by-word: cache-aware streaming needs the 114M
FastConformer, whose accuracy was rejected. Parakeet (600M) can't emit
incremental tokens, so we cut the audio at natural pauses (or a hard cap) and
transcribe each burst ONCE — accuracy of the big model, text still visibly
flowing every few words, no re-transcription and therefore no jitter/rewrites.

Run: python3 parakeet_server.py [--port 8756] [--model nvidia/parakeet-tdt-0.6b-v2]
     [--burst-max-s 2.5] [--burst-gap-s 0.35]
"""
import argparse
import asyncio
import hmac
import ipaddress
import json
import os
import re
import tempfile
import time

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

try:
    from itn import apply_itn            # spoken numbers -> digits (dependency-free, idempotent)
except Exception:
    def apply_itn(t):                    # never let a missing/broken ITN take down the STT
        return t
try:
    from vocab import apply_vocab, starts_with_proper_noun  # terms Parakeet's LM never saw
except Exception:
    def apply_vocab(t):
        return t

    def starts_with_proper_noun(t):
        return False

# Real VAD, not an energy threshold. RMS gates were shipped three times here and failed
# every time: they cannot separate quiet speech from loud noise, because loudness is not
# what makes something speech. webrtcvad classifies on speech SPECTRA, so it hears a
# mutter and ignores a bang. Another client reached this same conclusion independently
# after its own two static-threshold misses — its webrtcvad gate is why it heard
# speech when this server did not. Import failure is LOUD (not a soft fallback): a
# silent downgrade to RMS would reintroduce exactly the bug this replaces.
try:
    import webrtcvad
    _HAVE_WEBRTCVAD = True
except Exception as _e:
    _HAVE_WEBRTCVAD = False
    print(f"*** webrtcvad MISSING ({_e}) — falling back to the RMS gate that has failed "
          f"three times. Install webrtcvad in the image. ***", flush=True)


def polish(text: str) -> str:
    """The full post-STT text pass, in order. Both consumers and both endpoints use THIS —
    so batch and streaming can never drift apart. Idempotent end to end."""
    return apply_vocab(apply_itn(text))

app = FastAPI(title="waveflow-parakeet-stt")
model = None
model_name = "?"
# ENGINE: "nemo" = NVIDIA NeMo (PyTorch, the Docker container). "onnx" = the same Parakeet
# exported to ONNX via onnx-asr: no PyTorch, runs on CPU or GPU. Both speak one contract.
ENGINE = "nemo"
_TX_EMA_S = 0.0        # rolling measured seconds per transcribe; paces live re-reads
SR = 16000
BURST_MAX_S = 2.5      # hard cap -> ~5-7 words; bounds latency AND buffer size
BURST_GAP_S = 0.35     # trailing silence that ends a burst at a NATURAL boundary
# SUSTAINED speech required to open a burst. This is the discriminator that neither
# energy nor spectra provides: on this mic webrtcvad says vad=True on BREATH (rms 0-8),
# and a throat-clear is LOUDER than a whisper — so no threshold can separate them. What
# separates them is DURATION: breath/throat/cough is a transient (~250-450ms then it
# stops); speech sustains. The old BURST_MIN_S=0.40 sat right at breath length, and it
# counted TOTAL speech frames rather than a run, so two breaths summed past it — that was
# the leak that let Parakeet hallucinate words out of breathing.
# (My "a false trigger only costs a wasted burst" claim was wrong: Parakeet returns "" for
# SILENCE, but breath is audible signal, so it gets transcribed into words. Silence was
# never the risk case.) 0.60s matches another client's setting, validated on real
# frames from this server: rejects breath/throat/cough, still fires on a whisper.
SUSTAIN_S = 0.60
BURST_MIN_S = 0.40     # (legacy total-speech floor; SUSTAIN_S is the real gate)
# A pause >= this reads as a SENTENCE end; anything shorter is a breath mid-sentence.
# Each burst is transcribed standalone, so Parakeet terminates EVERY one with a period —
# cutting at a 0.35s breath yielded "I have to restart the app. because it doesn't like
# that." Bursts cut below this threshold get their trailing period stripped and the next
# burst de-capitalised, so a sentence spoken across several bursts reads as one sentence.
SENTENCE_GAP_S = 0.80
# --- mode=live: no blind cuts (2026-09-12) ---
# BURST_MAX_S exists to bound LATENCY, and at 2.5s it does that by slicing the audio
# wherever the clock lands — middle of a word is fine by it. Both halves of a split word
# then decode as whole words, so "an AI brain" typed as "an A" + "aI brain" and "VRAM" as
# "for V" + "rAM usage". Measured on real speech: ~3 such defects per 63s,
# and they accumulate with how long the user talks. That is the whole "does not capture long
# conversations well" complaint.
#
# Raising the cap is the WRONG axis — 2.5s was chosen deliberately so text prints
# in real time. So: stop cutting mid-speech at all. Let the utterance buffer
# GROW until a real pause, and re-transcribe the whole thing every LIVE_EMIT_S. Parakeet
# does 2.5s in ~60ms and 34s in ~720ms, so a re-transcribe of a growing buffer is cheap
# enough to do 1.4x/second — which prints text SOONER than 2.5s bursts did, not later.
#
# The known hazard is the one that killed the first live typer (2026-07-13): re-transcribing
# the same audio returns slightly different words, so the text jittered and looped. The fix
# is that the SERVER decides what is settled, not the consumer: a word is STABLE only once
# two consecutive transcribes agree on it, stable text never shrinks, and only the unstable
# TAIL is ever allowed to change. Committed words are never revisited.
LIVE_EMIT_S = 0.70     # re-transcribe the growing utterance this often while speech is live
LIVE_CEILING_S = 22.0  # absolute buffer cap; cut at the QUIETEST frame, never mid-word
LIVE_TAIL_MAX = 48     # chars of tail a consumer may ever be asked to redraw
# Audio kept while waiting for sustained speech, and carried across an utterance boundary, so
# a soft onset survives. Must exceed onset + a short gap + SUSTAIN_S: at 1.0s a soft "This",
# a beat, then "is looking..." had already scrolled out by the time 0.6s of sustained speech
# was recognised — live it came out "is looking way better." (replay of the same WAV kept "This").
LIVE_ONSET_KEEP_S = 2.5
# While an utterance's first word is a FILLER, nothing settles until this much speech is in the
# buffer (or the final arrives). A soft onset needs RIGHT context to decode: on 2026-09-13 143159
# every window ending under ~5s after "I'm" heard "Um"; with 9s it heard "I'm", from any start
# offset (measured). Settling early locked in "Um talking", and the client strips "Um", so the
# word was simply lost. Cost: utterances that really start with "um" type up to this much later.
LIVE_FILLER_HOLD_S = 5.0
# --- speech floor: ADAPTIVE, because a fixed number cannot be right ---
# A static threshold was shipped twice and failed both times: 0.010 deafened normal-volume
# speech, and any single value is wrong for a voice that moves between a mutter and a peak.
# The floor now tracks the QUIETEST recent audio (10th percentile of a rolling ~9s window)
# and the threshold scales off it, so it follows the mic, the room, and the speaker's tone.
#
# Why aggressive sensitivity is safe: this VAD's real job is finding PAUSE BOUNDARIES, not
# gating speech — Parakeet returns "" on silence, so a false trigger costs one wasted burst
# and never a hallucination. The clamps are the safety net: the ceiling means it can never
# deafen a consumer (another client pre-VADs, so its "floor" is measured over inter-word gaps and
# would otherwise drift high), the floor stops it chasing a digitally-silent stream to zero.
VAD_MIN = 0.0015       # most sensitive it may become (near-silent room)
VAD_MAX = 0.0100       # least sensitive it may become — the anti-deafening clamp
VAD_K = 3.0            # threshold = noise_floor * K
VAD_COLD = 0.0025      # used until the window has enough history to estimate
VAD_FIXED = 0.0        # >0 forces a fixed RMS threshold (escape hatch: --vad-rms)
VAD_MODE = 1           # webrtcvad aggressiveness 0..3; LOW = more sensitive to quiet speech


# --- auth: one shared token, checked on EVERY route except /health ---
# Off only when the server is reachable from this machine alone. main() refuses a
# non-loopback bind without a token, so a LAN/VPS server can never be open by accident.
TOKEN = ""


def _token_ok(presented: str) -> bool:
    return not TOKEN or hmac.compare_digest(presented.encode(), TOKEN.encode())


def _bearer(headers) -> str:
    h = headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


@app.middleware("http")
async def _require_token(request: Request, call_next):
    if request.url.path != "/health" and not _token_ok(_bearer(request.headers)):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok" if model is not None else "loading", "model": model_name,
            "auth": bool(TOKEN)}


@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...), model_param: str = Form("", alias="model"),
                     beam: int = Form(1)):
    t0 = time.perf_counter()
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        if ENGINE == "onnx":
            text = (model.recognize(path) or "").strip()
        else:
            out = model.transcribe([path])
            h = out[0]
            text = (h.text if hasattr(h, "text") else str(h)).strip()
        text = polish(text)             # same pass as streaming — the two can't drift
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return {"text": text, "x_latency_ms": int((time.perf_counter() - t0) * 1000),
            "x_model": model_name}


# ---------- short-burst streaming ----------
_TX_QUIET = True          # does this NeMo build accept verbose=? probed once, then cached
# NOTE: volume-normalising a burst before STT was TESTED against the real model at
# low levels (rms 0.006-0.018 on real speech) and made ZERO difference —
# Parakeet transcribes clean quiet speech perfectly (NeMo normalises features itself).
# So "hallucinates when I speak low" is NOT a signal-level problem; it's SNR — real
# background noise sits proportionally closer to speech when the speaker is quiet, and gets
# transcribed into words. Normalising would AMPLIFY that noise, so it's deliberately
# absent. The real lever is webrtcvad aggressiveness (--vad-mode), which trims noisier
# audio before it reaches the model.


def _transcribe_pcm(x: np.ndarray) -> str:
    """Transcribe ONE burst of float32 mono 16k audio with Parakeet, ONCE.

    Passes the array straight to NeMo (transcribe() takes numpy/tensors when the audio is
    already 16k mono, which ours is by contract) — the old path wrote a temp WAV per burst
    and made NeMo re-open and decode it, pure I/O for data already in RAM.

    BLOCKING + GPU-bound: callers MUST run this off the event loop.
    """
    global _TX_EMA_S
    audio = np.ascontiguousarray(np.clip(x, -1.0, 1.0), dtype=np.float32)
    t0 = time.perf_counter()
    try:
        return _transcribe_array(audio)
    finally:
        dt = time.perf_counter() - t0
        _TX_EMA_S = dt if _TX_EMA_S == 0.0 else 0.7 * _TX_EMA_S + 0.3 * dt


def _transcribe_array(audio: np.ndarray) -> str:
    global _TX_QUIET
    if ENGINE == "onnx":
        return (model.recognize(audio, sample_rate=SR) or "").strip()
    if _TX_QUIET:
        try:
            out = model.transcribe([audio], batch_size=1, verbose=False)
        except TypeError:          # older/newer signature without verbose= — probe once, remember
            _TX_QUIET = False
            out = model.transcribe([audio], batch_size=1)
    else:
        out = model.transcribe([audio], batch_size=1)
    h = out[0]
    return (h.text if hasattr(h, "text") else str(h)).strip()


class BurstSession:
    """Buffers PCM and cuts it into bursts at natural pauses (or a hard cap).
    Each burst is transcribed exactly once and APPENDED — never re-transcribed,
    so nothing a consumer has already committed can be revised."""

    def __init__(self, max_s: float, gap_s: float):
        self.max_s, self.gap_s = max_s, gap_s
        self.buf = np.zeros(0, dtype=np.float32)
        self.cumulative = ""
        self._continues = False   # did the previous burst end mid-sentence?
        from collections import deque
        self._floor = deque(maxlen=300)   # ~9s of 30ms frame RMS -> RMS-fallback floor
        # aggressiveness 0..3; LOW = more sensitive. Sensitivity is cheap here because
        # Parakeet returns "" on silence, so a false positive costs one wasted burst.
        self._vad = webrtcvad.Vad(VAD_MODE) if _HAVE_WEBRTCVAD else None

    def thresh(self) -> float:
        """Adaptive speech threshold: the noise floor, scaled, clamped. Follows the
        speaker's tone and the room instead of assuming one static level."""
        if VAD_FIXED > 0:
            return VAD_FIXED
        if len(self._floor) < 20:
            return VAD_COLD
        floor = float(np.percentile(np.asarray(self._floor), 10))
        return float(np.clip(floor * VAD_K, VAD_MIN, VAD_MAX))

    def _frames(self):
        """Per-30ms-frame speech decisions (bool array) — webrtcvad if available,
        else the adaptive RMS gate. 30ms @16k = exactly 480 samples, which is one
        of webrtcvad's three legal frame sizes and already our framing."""
        fl = 480                                  # 30ms @ 16k — webrtcvad-legal
        n = len(self.buf) // fl
        if n == 0:
            return np.zeros(0, dtype=bool)
        block = self.buf[:n * fl]
        if self._vad is not None:
            i16 = (np.clip(block, -1.0, 1.0) * 32767).astype("<i2")
            raw = i16.tobytes()
            out = np.zeros(n, dtype=bool)
            for k in range(n):
                try:
                    out[k] = self._vad.is_speech(raw[k * fl * 2:(k + 1) * fl * 2], SR)
                except Exception:
                    out[k] = False
            return out
        rms = np.sqrt((block.reshape(n, fl) ** 2).mean(axis=1))
        return rms > self.thresh()

    def _speech_s(self, flags):
        return float(flags.sum()) * 0.03

    def _sustained_s(self, flags):
        """LONGEST CONSECUTIVE run of speech frames. Total-speech was the wrong
        measure: two 300ms breaths sum to 600ms of 'speech' while never sustaining
        for 600ms. A transient stops; speech keeps going."""
        best = run = 0
        for v in flags:
            run = run + 1 if v else 0
            if run > best:
                best = run
        return best * 0.03

    def _trailing_silence_s(self, flags):
        k = 0
        for v in flags[::-1]:
            if v:
                break
            k += 1
        return k * 0.03

    def add(self, samples: np.ndarray):
        """Buffer samples and return the AUDIO bursts that are ready to transcribe.

        Pure buffer arithmetic — deliberately does NO transcription, so the caller can run the
        GPU work off the event loop. Loops so a bulk send can't produce one oversized burst:
        the cap bounds burst length regardless of the client's framing.
        """
        # Feed the noise-floor estimator from the INCOMING audio only — once per
        # sample. (self.buf gets re-scanned every loop pass; counting it there would
        # weight the estimate by how often we happen to iterate.)
        fl = int(SR * 0.03)
        nf = len(samples) // fl
        if nf:
            self._floor.extend(
                np.sqrt((samples[:nf * fl].reshape(nf, fl) ** 2).mean(axis=1)).tolist())
        self.buf = np.concatenate([self.buf, samples])
        out = []
        while True:
            rms = self._frames()
            if len(rms) == 0:
                break
            speech = self._speech_s(rms)
            dur = len(self.buf) / SR
            trail = self._trailing_silence_s(rms)
            if self._sustained_s(rms) < SUSTAIN_S:
                # Nothing has sustained long enough to be speech yet. If a whole
                # window went by without a sustained run, whatever is in here is a
                # TRANSIENT — breath, throat-clear, cough, a door — so drop it
                # rather than let it reach Parakeet and come back as invented words.
                # (Also fixes a real leak: the old branch only cleared on speech==0,
                # so a buffer holding sub-threshold speech grew without bound.)
                if dur >= self.max_s:
                    self.buf = np.zeros(0, dtype=np.float32)
                break
            if trail >= self.gap_s:
                # natural pause -> clean boundary, take the whole buffer.
                # Long enough to be a SENTENCE end, or just a breath?
                ends_sentence = trail >= SENTENCE_GAP_S
                audio, self.buf = self.buf, np.zeros(0, dtype=np.float32)
            elif dur >= self.max_s:
                # hard cap -> bound the burst, keep the remainder (may clip a
                # word mid-cut; rare, and the price of a bounded burst). A cap
                # cut is mid-speech by definition, so never a sentence end.
                n = int(self.max_s * SR)
                audio, self.buf = self.buf[:n], self.buf[n:]
                ends_sentence = False
            else:
                break
            out.append((audio, ends_sentence))
        return out

    def flush(self):
        """Return the remaining speech as one final burst (segment end), or None."""
        rms = self._frames()
        if len(rms) and self._speech_s(rms) >= 0.20:
            audio, self.buf = self.buf, np.zeros(0, dtype=np.float32)
            return audio
        self.buf = np.zeros(0, dtype=np.float32)
        return None

    def commit(self, text: str, ends_sentence: bool = True) -> str:
        """Append committed text. APPEND-ONLY — cumulative grows, never rewrites.

        Also repairs burst-boundary punctuation: every burst is transcribed
        standalone, so Parakeet ends each one with a period even when the cut was
        a mid-sentence breath. A burst cut below SENTENCE_GAP_S loses its trailing
        period, and the burst that continues it is de-capitalised — so one spoken
        sentence split across bursts reads as one sentence.
        """
        if not text:
            return ""
        if self._continues and text[:1].isupper() and not starts_with_proper_noun(text):
            # this burst continues the previous one -> don't start a new sentence.
            # "I"/"I'm" stay capitalised; a proper noun landing exactly on a burst
            # start is the known (rare) cost of not having whole-utterance context.
            if not (text == "I" or text.startswith("I ") or text.startswith("I'")):
                text = text[0].lower() + text[1:]
        if not ends_sentence:
            stripped = text.rstrip()
            if stripped.endswith("."):        # only '.', never '?'/'!' — those are
                text = stripped[:-1]          # deliberate and rare mid-burst
        # A short cut does NOT continue a sentence if the burst still ends in real
        # terminal punctuation: "Did it work?" finishes the thought even when the
        # pause after it was brief, so the next burst must stay capitalised.
        self._continues = not ends_sentence and not text.rstrip().endswith(("?", "!"))
        self.cumulative = (self.cumulative + " " + text).strip() if self.cumulative else text
        return text


_NORM_RX = re.compile(r"[^a-z0-9']+")


def _norm(w: str) -> str:
    """Compare words ignoring case and punctuation.

    Everything here must align a PARTIAL hypothesis against a FULL-CONTEXT one, and those
    two differ in exactly those two ways: the full pass capitalises and punctuates
    ("all right this is" -> "All right, this is"). A first cut compared with str.startswith
    and so decided the final disagreed with every committed word, then dropped the entire
    remainder of the utterance — the transcript came back as "All" and nothing else.
    """
    return _NORM_RX.sub("", w.lower())


_DOUBLE_RX = re.compile(r"(.)\1+")
_FILLER_RX = re.compile(r"^(?:um+|uh+|uhm+|erm+|hmm+)$")


def _stream(words: list, blank_fillers: bool = True):
    """Words -> (letter stream, cumulative word-end offsets).

    Alignment runs on LETTERS, not words, because Parakeet re-segments the same audio between
    passes: "All right," <-> "Alright,", "block brain" <-> "blockbrain". Compared word by
    word those never match, and on a real session the first utterance held 27
    of 31 passes — nothing printed for 22 seconds. Spaces vanish in the stream and doubled
    letters collapse ("allright" == "alright"). Committed TEXT is never rewritten; this
    only decides where two passes line up.
    """
    # FILLERS count as nothing. The model adds or drops "um"/"uh" between passes over the
    # same audio, and one inside the anchor ("better. Uh it operates") hid the join point:
    # every pass held, and the final re-typed "operates better," (ONNX, 2026-09-13).
    # Same filler set the client strips before typing (app/stt.py strip_fillers).
    # Blanking is only for FINDING the join point. Deciding that a word is safe to type
    # (_agree_words) keeps fillers: on 2026-09-13 143159 the clipped onset "I'm" was heard as
    # "Uh"/"Um" on early passes; blanking let those passes agree and type "talking" first.
    keys = ["" if blank_fillers and _FILLER_RX.match(_norm(w))
            else _DOUBLE_RX.sub(r"\1", _norm(w)) for w in words]
    ends, pos = [], 0
    for k in keys:
        pos += len(k)
        ends.append(pos)
    return "".join(keys), ends


def _agree_words(a: list, b: list) -> int:
    """How many leading words of `a` agree with `b`.

    Agreement ends at the last point that is a WORD BOUNDARY IN BOTH — so "is" never
    settles against "isn't" just because the letters run on.
    """
    sa, ea = _stream(a, blank_fillers=False)
    sb, eb = _stream(b, blank_fillers=False)
    c = 0
    for x, y in zip(sa, sb):
        if x != y:
            break
        c += 1
    common_ends = set(ea) & (set(eb) | ({len(sb)} if c == len(sb) else set()))
    best = max((e for e in common_ends if e <= c), default=0)
    return sum(1 for e in ea if e <= best)


class LiveSession(BurstSession):
    """Utterance buffer that is NEVER cut mid-speech, plus a settled/tail split.

    Differences from BurstSession, which it reuses wholesale for VAD:
      * the buffer grows until a real pause (or LIVE_CEILING_S), so Parakeet always sees a
        whole phrase and can never be handed half a word;
      * it re-transcribes that whole buffer every LIVE_EMIT_S instead of once per cut;
      * it reports `stable` (agreed by two consecutive transcribes, monotonically growing,
        never rewritten) and `tail` (may still change). This is the settled-words policy
        that was correct all along but was attached to the wrong architecture in July.
    """

    def __init__(self, gap_s: float, ceiling_s: float = LIVE_CEILING_S):
        super().__init__(ceiling_s, gap_s)
        self.stable = ""        # committed text for THIS utterance; append-only
        self._stable_words = []  # the SAME text as words — the append-only unit
        self._prev_rest = []    # last pass's UNCOMMITTED words, for the two-in-a-row test
        self._last_emit = 0.0   # monotonic seconds
        self.utt = 0
        self._cut_kind = "gap"  # how the pending final was cut: "gap" | "ceiling" | "flush"
        # Words already TYPED whose audio a ceiling cut carried into THIS utterance. They
        # will be heard again at its start and must not be typed twice.
        self._carry = []
        # Speech seconds in the buffer the pending partial was cut from. Unit tests call
        # settle() directly, with no audio, so the default never blocks a vote.
        self._speech_s_at_emit = float("inf")

    def _frame_rms(self):
        fl = 480
        n = len(self.buf) // fl
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        return np.sqrt((self.buf[:n * fl].reshape(n, fl) ** 2).mean(axis=1))

    def plan(self, samples: np.ndarray, now: float):
        """Feed audio; return a list of ('partial'|'final', audio) to transcribe.

        Pure buffer arithmetic — no GPU work here, so the caller keeps the event loop free.
        """
        fl = int(SR * 0.03)
        nf = len(samples) // fl
        if nf:
            self._floor.extend(
                np.sqrt((samples[:nf * fl].reshape(nf, fl) ** 2).mean(axis=1)).tolist())
        self.buf = np.concatenate([self.buf, samples])

        flags = self._frames()
        if len(flags) == 0:
            return []
        # Nothing has sustained long enough to be speech: drop transients (breath, a
        # door) rather than let them reach the model and come back as invented words.
        if self._sustained_s(flags) < SUSTAIN_S:
            # Nothing sustained yet: keep only the last ~1s, so a quiet ONSET survives into
            # the utterance it begins, without silence piling up for 22s and slowing the
            # first transcribe of the next phrase.
            keep = int(LIVE_ONSET_KEEP_S * SR)
            if len(self.buf) > keep * 1.2:
                self.buf = self.buf[-keep:]
            return []

        trail = self._trailing_silence_s(flags)
        if trail >= self.gap_s:
            # Close the utterance at the START of its trailing silence (plus a short tail),
            # and CARRY the rest forward. Taking the whole buffer swallowed the first sliver
            # of the next word whenever that word began before webrtcvad called it speech:
            # a spoken "Lastly, the border shell..." came back as "astly, ...".
            cut = len(self.buf) - int(trail / 0.03) * 480
            tail = int(0.15 * SR)
            audio = self.buf[:min(len(self.buf), cut + tail)]
            self.buf = self.buf[max(0, cut):][-int(LIVE_ONSET_KEEP_S * SR):]
            self._cut_kind = "gap"
            return [("final", audio)]

        if len(self.buf) / SR >= self.max_s:
            # Ceiling reached while STILL speaking. Cut at the quietest 30ms frame in the
            # last 2s rather than at the clock index — a constant cut point is what put a
            # boundary through the middle of "AI". This is the same defect class as a fixed
            # VAD threshold: pick the point by MEASURING the signal, never by arithmetic.
            rms = self._frame_rms()
            look = min(len(rms), int(2.0 / 0.03))
            if look > 4:
                start = len(rms) - look
                cut_f = start + int(np.argmin(rms[start:]))
            else:
                cut_f = len(rms) - 1
            n = max(1, cut_f) * 480
            # OVERLAP, don't butt-join. In fast speech the quietest frame can still sit
            # between two words with no gap, and the word at the seam decoded on NEITHER side
            # (live 2026-09-14 000134: "a couple of | But over time" — "files" lost) or on
            # BOTH ("couple of of files" on replay). The next utterance restarts ~0.5-1.5s
            # earlier, at the quietest frame there, and _skip_carry drops the words it
            # re-hears that were already sent.
            lo = max(0, cut_f - int(1.5 / 0.03))
            hi = max(0, cut_f - int(0.5 / 0.03))
            ov_f = lo + int(np.argmin(rms[lo:hi])) if hi - lo > 1 else cut_f
            audio, self.buf = self.buf[:n], self.buf[ov_f * 480:]
            self._cut_kind = "ceiling"
            return [("final", audio)]

        # PACE TO THE MACHINE. A fixed 0.70s only works while one transcribe is faster than
        # 0.70s. Measured 2026-09-13 on an i7-7700K CPU: a 20s buffer takes 1.17s, so a fixed
        # cadence queues a new re-read before the last one ends and live text falls further
        # behind the voice. Wait at least as long as re-reads are actually taking.
        if now - self._last_emit >= max(LIVE_EMIT_S, 1.25 * _TX_EMA_S):
            self._last_emit = now
            self._speech_s_at_emit = self._speech_s(flags)
            return [("partial", self.buf.copy())]   # copy: the buffer keeps growing
        return []

    def flush(self):
        """Close the utterance on request. Returns the audio to transcribe as a final, or None.

        The burst rule (>=0.20s TOTAL speech frames) let trailing breath and room noise through,
        and Parakeet turned it into words ("Reviewer's demand", probe 2026-09-14). Use the SAME
        gate plan() uses — a sustained run of speech — and cut the trailing silence the way a
        gap cut does, so the model never hears a long noise tail after the last word.
        """
        flags = self._frames()
        if len(flags) == 0 or self._sustained_s(flags) < SUSTAIN_S:
            self.buf = np.zeros(0, dtype=np.float32)
            return None
        trail = self._trailing_silence_s(flags)
        cut = len(self.buf) - int(trail / 0.03) * 480
        audio = self.buf[:min(len(self.buf), cut + int(0.15 * SR))]
        self.buf = np.zeros(0, dtype=np.float32)
        self._cut_kind = "flush"
        return audio

    def settle(self, hyp: str):
        """Fold a fresh hypothesis in. Returns (stable, tail).

        A word joins `stable` only when two consecutive transcribes agree on it, and the
        committed word COUNT never shrinks — so anything a consumer has already typed stays
        typed. Everything is positional on words, so punctuation drift between passes can
        never desync the two sides.
        """
        hyp = (hyp or "").strip()
        if not hyp:
            return self.stable, ""
        hw = self._skip_carry(hyp.split())
        if not hw:
            return self.stable, ""      # still inside words a ceiling cut already typed
        pos = self._resume_at(hw)
        if pos is None:
            return self.stable, ""      # cannot locate our commit in this pass; hold
        rest = hw[pos:]
        if (not self._stable_words and rest and _FILLER_RX.match(_norm(rest[0]))
                and self._speech_s_at_emit < LIVE_FILLER_HOLD_S):
            # A filler onset may be a clipped real word ("I'm" -> "Um"). Show it as tail and
            # let later context — or the final — decide before anything is committed.
            self._prev_rest = []
            return self.stable, " ".join(rest)
        prev_rest, self._prev_rest = self._prev_rest, rest
        # A word settles once two consecutive passes agree on it. Everything is measured on
        # the UNCOMMITTED remainder, so re-segmentation earlier in the utterance cannot stall
        # it. Comparing whole hypotheses instead stalled 27 of 30 passes on real audio: the
        # commit froze at two words for a 22s utterance and the rest piled into the tail.
        agreed = _agree_words(rest, prev_rest)
        if agreed:
            # APPEND ONLY — words already committed keep the exact text they were typed with.
            self._stable_words = self._stable_words + rest[:agreed]
            self.stable = " ".join(self._stable_words)
            self._prev_rest = rest[agreed:]
        return self.stable, " ".join(rest[agreed:])

    def _skip_carry(self, hw: list, final: bool = False):
        """Drop, from the head of this pass, the words the previous utterance already sent.

        After a ceiling cut this utterance's audio starts in OVERLAP with the last one, so
        its first words re-hear the END of what was sent — some suffix of `_carry`, of
        unknown length. The longest suffix of `_carry` that matches the head of this pass
        (letter stream, ending on a word boundary) is removed. Returns the remaining words,
        or [] when the whole pass lies inside already-sent words.
        """
        c = self._carry
        if not c or not hw:
            return hw
        sh, eh = _stream(hw)
        ends = set(eh)
        could_grow = False
        for k in range(len(c), 0, -1):
            sc, _ = _stream(c[-k:])
            if not sc:
                continue
            if sh.startswith(sc) and len(sc) in ends:
                return hw[sum(1 for e in eh if e <= len(sc)):]
            if len(sh) < len(sc) and sc.startswith(sh):
                could_grow = True
        # Whole pass sits inside already-sent words: nothing new yet (a partial waits; a
        # final has nothing to add — typing it would repeat the seam).
        return [] if could_grow else hw

    def _resume_at(self, hw: list, words: list = None):
        """Index in `hw` just past our committed text, or None if it cannot be found.

        Anchoring on the LAST few committed words — rather than requiring the whole commit to
        match the head — is what makes this survive Parakeet re-segmenting a growing buffer
        ("All right," -> "Alright, uh", a filler appearing or vanishing). Only the join point
        has to be recognisable, not the entire history.
        """
        # Anchor on REAL words only. Fillers blank out in the letter stream, so a commit that is
        # nothing but "Uh" gave an empty anchor and every pass held — a whole 22s utterance
        # printed nothing on replay. With no real word committed yet, resume past any fillers
        # the new pass starts with.
        sw = [w for w in (self._stable_words if words is None else words)
              if not _FILLER_RX.match(_norm(w))]
        if not sw:
            if not self._stable_words:
                return 0
            i = 0
            while i < len(hw) and _FILLER_RX.match(_norm(hw[i])):
                i += 1
            return i
        k = min(4, len(sw))
        anchor, _ = _stream(sw[-k:])
        if not anchor:
            return None
        sh, eh = _stream(hw)
        # Letter-stream match so a re-segmented anchor ("All right," vs "Alright,") is still
        # found. Only the END must fall on a word boundary — that is the resume point. A
        # first cut also demanded a boundary at the START, and the anchor's first word is
        # exactly the one most likely to have merged with its neighbour ("right" inside
        # "Alright"), so the match was rejected and printing stalled again. Search backwards:
        # the buffer grows forward, so the LATEST occurrence is the join when a phrase repeats.
        ends = set(eh)
        at = sh.rfind(anchor)
        while at != -1:
            end = at + len(anchor)
            if end in ends:
                return sum(1 for e in eh if e <= end)
            at = sh.rfind(anchor, 0, end - 1)
        return None

    def finish(self, text: str):
        """Close the utterance. Committed words are kept verbatim; only the tail is new.

        Positional by word: whatever the full-context pass produced beyond the number of
        words already committed is the tail. Never rewrites a committed word, and never
        drops the remainder just because the final pass punctuated it differently.
        """
        text = (text or "").strip()
        st = self.stable
        carry = []
        if text:
            tw = self._skip_carry(text.split(), final=True)
            # Find the join point by ANCHOR, not by word count. The full-context pass
            # re-segments, so it can hold a different number of words before our commit
            # point; slicing at len(committed) then re-emitted a word we had already typed
            # ("...can be shipped shipped and installed"). Word count is only the fallback.
            pos = self._resume_at(tw)
            if pos is None and self._cut_kind == "ceiling":
                # A CEILING cut can land BEHIND words already committed from the growing
                # buffer; their audio went into the next utterance. Find how much of our
                # commit this final does cover, and hand the rest to the next utterance to
                # skip. Replay 2026-09-12 111418: "Or the original Or the original".
                sw = self._stable_words
                for j in range(len(sw) - 1, 0, -1):
                    p = self._resume_at(tw, sw[:j])
                    if p is not None:
                        pos = p
                        break
            if pos is None:
                # Fallback: skip as many REAL words as we committed. Fillers the final pass
                # added are not words we typed, so counting them shifted the cut backwards.
                real = sum(1 for w in self._stable_words if not _FILLER_RX.match(_norm(w)))
                pos, seen = len(tw), 0
                for i, w in enumerate(tw):
                    if seen == real:
                        pos = i
                        break
                    if not _FILLER_RX.match(_norm(w)):
                        seen += 1
            tail = " ".join(tw[pos:])
        else:
            tail = ""
        if self._cut_kind == "ceiling":
            # The next utterance re-hears the end of what this one SENT (overlap audio, and
            # any words committed past the cut). Hand it the last few sent words to skip.
            sent = (st + " " + tail).split()
            carry = [w for w in sent if not _FILLER_RX.match(_norm(w))][-8:]
        self.stable, self._stable_words = "", []
        self._prev_rest, self.utt = [], self.utt + 1
        self._carry, self._cut_kind = carry, "gap"
        return st, tail


# ONE transcribe at a time. The 1060 shares its ~2GB of headroom with the memory service, which
# is not enough for two concurrent decodes if both consumers happen to speak at once. Queueing
# costs a few hundred ms; an OOM takes the STT down mid-sentence.
_GPU = asyncio.Lock()


async def _emit_burst(ws: WebSocket, sess: "BurstSession", audio: np.ndarray, t0: float,
                      ends_sentence: bool = True):
    """Transcribe one burst OFF the event loop, then emit an append-only commit.

    The transcribe is blocking and GPU-bound (~100ms+). Running it inline in this coroutine —
    as the first cut did — stalls the whole server for its duration: no audio received, no
    /health, no batch endpoint. to_thread hands it to a worker so the loop keeps serving.
    """
    async with _GPU:
        raw = await asyncio.to_thread(_transcribe_pcm, audio)
    text = sess.commit(polish(raw), ends_sentence)
    if text:
        await ws.send_text(json.dumps(
            {"text": sess.cumulative, "delta": text, "commit": True, "final": False,
             "ms": int((time.perf_counter() - t0) * 1000)}))


async def _live_emit(ws: WebSocket, sess: LiveSession, kind: str, audio, t0: float):
    """Transcribe one whole-utterance snapshot off the event loop and emit stable/tail."""
    async with _GPU:
        raw = await asyncio.to_thread(_transcribe_pcm, audio)
    text = polish(raw)
    final = kind == "final"
    # Read the id BEFORE finish(), which increments it. Labelling an utterance's own final
    # with the NEXT id made a consumer treat it as a new utterance and type the opening
    # words twice ("All right, All right, this is...").
    utt = sess.utt
    stable, tail = sess.finish(text) if final else sess.settle(text)
    if not (stable or tail):
        return
    # NEVER truncate a final. LIVE_TAIL_MAX bounds how much a consumer is asked to REDRAW
    # while speech is still in flight; a final is the last word on this utterance and the
    # consumer keeps it. Applying the cap to both chopped a real transcript mid-word —
    # "...Um right now w" — which is precisely the half-word defect this mode exists to end.
    await ws.send_text(json.dumps({
        "stable": stable, "tail": tail if final else tail[:LIVE_TAIL_MAX], "final": final,
        "utt": utt, "ms": int((time.perf_counter() - t0) * 1000)}))


@app.websocket("/v1/audio/stream")
async def stream(ws: WebSocket):
    # HTTP middleware does not see websockets. Header first; ?token= for clients that
    # cannot set headers. Reject BEFORE accept so no audio is ever read.
    if not _token_ok(_bearer(ws.headers) or ws.query_params.get("token", "")):
        await ws.close(code=1008)
        return
    await ws.accept()
    # mode=live is OPT-IN. The default path is byte-for-byte the burst protocol a
    # burst-protocol client already speaks; adding a second consumer's needs must never
    # change the first's wire format. WaveFlow asks for live, that client does not,
    # neither can break the other.
    if ws.query_params.get("mode") == "live":
        sess = LiveSession(app.state.burst_gap_s)
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    if msg["text"] == "reset":
                        sess = LiveSession(app.state.burst_gap_s)
                        await ws.send_text(json.dumps(
                            {"stable": "", "tail": "", "reset": True, "final": False}))
                    elif msg["text"] == "flush":
                        t0 = time.perf_counter()
                        audio = sess.flush()
                        if audio is not None:
                            await _live_emit(ws, sess, "final", audio, t0)
                        elif sess.stable:
                            # Only noise left, but words were committed: still close the
                            # utterance, or the next one anchors on this one's words.
                            utt = sess.utt
                            st, _ = sess.finish("")
                            await ws.send_text(json.dumps(
                                {"stable": st, "tail": "", "final": True, "utt": utt, "ms": 0}))
                    continue
                data = msg.get("bytes")
                if not data:
                    continue
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767.0
                t0 = time.perf_counter()
                for kind, audio in sess.plan(samples, time.monotonic()):
                    await _live_emit(ws, sess, kind, audio, t0)
        except WebSocketDisconnect:
            pass
        return

    sess = BurstSession(app.state.burst_max_s, app.state.burst_gap_s)
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                if msg["text"] == "reset":
                    # drop in-flight audio + cumulative; ack so the consumer can
                    # gate stale partials (another client relies on this handshake)
                    sess = BurstSession(app.state.burst_max_s, app.state.burst_gap_s)
                    await ws.send_text(json.dumps({"text": "", "reset": True}))
                elif msg["text"] == "flush":
                    t0 = time.perf_counter()
                    audio = sess.flush()
                    if audio is not None:
                        await _emit_burst(ws, sess, audio, t0, True)
                continue
            data = msg.get("bytes")
            if not data:
                continue
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767.0
            t0 = time.perf_counter()
            for audio, ends_sentence in sess.add(samples):
                await _emit_burst(ws, sess, audio, t0, ends_sentence)
    except WebSocketDisconnect:
        pass


def main():
    global model, model_name, VAD_FIXED, VAD_MODE, ENGINE, _TX_EMA_S
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--model", default="nvidia/parakeet-tdt-0.6b-v2")
    ap.add_argument("--engine", choices=["nemo", "onnx"], default="nemo")
    ap.add_argument("--onnx-dir", default="",
                    help="folder with the istupakov/parakeet-tdt-0.6b-v2-onnx files")
    ap.add_argument("--onnx-quant", choices=["int8", "fp32"], default="int8",
                    help="int8 = small, for CPU; fp32 = full model, for GPU")
    ap.add_argument("--device", choices=["cpu", "cuda", "dml"], default="cpu",
                    help="onnx only: cuda = NVIDIA (onnxruntime-gpu), dml = DirectML")
    ap.add_argument("--gpu-mem-mb", type=int, default=2048,
                    help="onnx cuda: cap on the GPU memory arena (context is extra, ~0.4GB)")
    ap.add_argument("--threads", type=int, default=4,
                    help="onnx cpu: intra-op threads. Set to PHYSICAL performance cores — "
                         "all 24 threads on a hybrid Ultra 7 ran ~4x slower than 4")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this machine only. Any other address needs --token.")
    ap.add_argument("--token", default=os.environ.get("WAVEFLOW_TOKEN", ""),
                    help="shared secret clients send as 'Authorization: Bearer <token>' "
                         "(env WAVEFLOW_TOKEN)")
    ap.add_argument("--allow-no-token", action="store_true",
                    help="serve a non-loopback address WITHOUT a token (trusted network only)")
    ap.add_argument("--burst-max-s", type=float, default=BURST_MAX_S)
    ap.add_argument("--burst-gap-s", type=float, default=BURST_GAP_S)
    ap.add_argument("--vad-rms", type=float, default=0.0,
                    help="force a FIXED RMS floor (escape hatch); default 0 = adaptive")
    ap.add_argument("--vad-mode", type=int, default=VAD_MODE,
                    help="webrtcvad aggressiveness 0..3; lower = hears quieter speech")
    args = ap.parse_args()
    global TOKEN
    TOKEN = args.token
    if not TOKEN and not args.allow_no_token and not _is_loopback(args.host):
        raise SystemExit(f"refusing to serve {args.host} without a token: other machines could "
                         f"use this server. Pass --token (or WAVEFLOW_TOKEN), or "
                         f"--allow-no-token on a network you trust.")
    app.state.burst_max_s = args.burst_max_s
    app.state.burst_gap_s = args.burst_gap_s
    VAD_FIXED = args.vad_rms
    VAD_MODE = args.vad_mode
    print(f"vad={'webrtcvad mode='+str(VAD_MODE) if _HAVE_WEBRTCVAD else 'RMS-FALLBACK'} "
      f"burst_max={args.burst_max_s}s gap={args.burst_gap_s}s", flush=True)
    ENGINE = args.engine
    if ENGINE == "onnx":
        import onnx_asr
        import onnxruntime as ort
        if args.device == "cuda" and hasattr(ort, "preload_dlls"):
            # CUDA/cuDNN installed as pip wheels (onnxruntime-gpu[cuda,cudnn]) are not on the
            # library path; ORT >= 1.21 loads them from site-packages when asked.
            ort.preload_dlls()
        so = ort.SessionOptions()
        so.intra_op_num_threads = args.threads
        so.inter_op_num_threads = 1
        if args.device == "dml":
            # DirectML requires both (onnxruntime DirectML EP docs).
            so.enable_mem_pattern = False
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # CUDA: grow the memory arena only as far as asked, capped. The default arena grabbed
        # 2.5GB on a GTX 1060 shared with an assistant (measured 2026-09-14); capped at 2GB the
        # same model ran at the same speed. Pascal also needs a cuDNN 9.1-era runtime.
        cuda_opts = {"arena_extend_strategy": "kSameAsRequested",
                     "cudnn_conv_algo_search": "HEURISTIC",
                     "gpu_mem_limit": args.gpu_mem_mb * 1024 * 1024}
        providers = {"cpu": ["CPUExecutionProvider"],
                     "cuda": [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"],
                     "dml": ["DmlExecutionProvider", "CPUExecutionProvider"]}[args.device]
        model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", args.onnx_dir or None,
                                    quantization=None if args.onnx_quant == "fp32" else "int8",
                                    sess_options=so, providers=providers)
        model_name = f"parakeet-tdt-0.6b-v2-onnx-{args.onnx_quant}-{args.device}"
        print(f"loaded {model_name} threads={args.threads} "
              f"available={ort.get_available_providers()}", flush=True)
    else:
        model = _load_nemo(args)
    # Warm the model so the FIRST real burst isn't the slow cold path — and warm it through the
    # SAME numpy path the bursts use, not a temp WAV. Warming a path we no longer take would
    # leave the first real burst cold, which is exactly the latency this is here to prevent.
    try:
        t0 = time.perf_counter()
        _transcribe_pcm(np.zeros(16000, dtype=np.float32))
        print(f"warm ({int((time.perf_counter() - t0) * 1000)}ms, engine={ENGINE}, "
              f"verbose_kw={_TX_QUIET})", flush=True)
    except Exception as e:
        print(f"WARMUP FAILED — the array path is broken, not just cold: {e}", flush=True)
        raise
    _TX_EMA_S = 0.0        # the cold warm-up is not a real pace; start measuring from speech
    _return_freed_ram()
    print("ready", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _return_freed_ram():
    """Hand memory Python already freed back to the OS.

    NeMo loads the fp32 weights in RAM (5.7GB RSS), halves them, and moves them to the GPU. The
    CPU copies are freed, but glibc keeps the pages, so the server sat at 4.8GB RSS forever.
    gc + malloc_trim after load measured 4.8GB -> 1.7GB on a Linux GPU box (2026-09-14), with the 63s
    transcript word-for-word identical. Linux/glibc only; a no-op elsewhere.
    """
    import ctypes
    import gc
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


def _load_nemo(args):
    global model_name
    model_name = args.model.split("/")[-1]
    print(f"loading {args.model} (fp16) ...", flush=True)
    import nemo.collections.asr as nemo_asr
    # CPU-load -> halve to ~1.2GB -> GPU: fits the 1060 alongside the assistant
    # without OOM (fp32-on-GPU load is 2.85GB and won't fit). Pascal fp16 is fast
    # here: ~0.4s warm for 34s audio, and ZERO hallucination on silence.
    m = nemo_asr.models.ASRModel.from_pretrained(model_name=args.model,
                                                 map_location="cpu")
    m = m.half()
    try:
        m = m.cuda()
    except Exception as e:
        print(f"cuda() failed ({e}); running on CPU", flush=True)
    m.eval()
    return m


if __name__ == "__main__":
    main()
