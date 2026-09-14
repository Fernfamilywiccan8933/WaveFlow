"""Microphone capture — device-aware, native-rate, resampled to 16k mono for STT.

Why this exists: the mic (e.g. a Yeti) runs at 44100 Hz stereo; Whisper wants
16000 Hz mono. v1 force-opened the stream at 16k mono, which is fragile per
device and can hand back silence. This captures at the device's NATIVE rate,
mono-mixes, and resamples to 16k with numpy (no scipy needed).
"""
import io
import wave

import numpy as np
import sounddevice as sd

STT_SR = 16000


def list_input_devices() -> list[tuple[int, str, int, int]]:
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append((i, d["name"], d["max_input_channels"],
                        int(d["default_samplerate"])))
    return out


def default_input_index() -> int:
    return sd.default.device[0]


def resolve_device_name(name: str | None):
    """Windows reshuffles device INDEXES between sessions — persist the NAME and
    resolve it to today's index at startup. Returns (index, actual_name)."""
    devs, default = clean_input_devices()
    if name:
        for i, n in devs:
            if n == name or n.startswith(name[:28]):  # names get truncated by some APIs
                return i, n
    for i, n in devs:
        if i == default:
            return i, n
    return (devs[0] if devs else (default, ""))


def clean_input_devices() -> tuple[list[tuple[int, str]], int]:
    """One entry per physical mic, robust to Windows host-API shuffling (the same
    mic hops between WASAPI/WDM-KS/MME between runs). Enumerates ALL input devices,
    dedups by FULL name (so 'Microphone (Yeti)' and 'Microphone (BlackShark)' stay
    distinct — keying on the text before '(' collapsed them all into one), and
    drops non-mic inputs (stereo mix, loopback, speakers). Default = system default
    input if present, else the first real mic."""
    EXCLUDE = ("stereo mix", "speakers", "what u hear", "loopback",
               "output", "wave out", "sound mapper", "primary sound capture")
    raw = sd.query_devices()
    seen, out = set(), []
    for i, d in enumerate(raw):
        if d["max_input_channels"] <= 0:
            continue
        name = d["name"].strip()
        if any(x in name.lower() for x in EXCLUDE):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append((i, name))
    if not out:  # nothing survived -> any input device, unfiltered
        out = [(i, d["name"]) for i, d in enumerate(raw) if d["max_input_channels"] > 0]
    try:
        di = default_input_index()
        default = di if any(i == di for i, _ in out) else (out[0][0] if out else -1)
    except Exception:
        default = out[0][0] if out else -1
    return out, default


def _native(device: int | None) -> tuple[int, int]:
    info = sd.query_devices(device if device is not None else default_input_index(),
                            "input")
    return int(info["default_samplerate"]), min(2, int(info["max_input_channels"]))


def resample_mono_16k(x: np.ndarray, src_sr: int) -> np.ndarray:
    """x: float32 mono at src_sr -> float32 mono at 16k (linear interp)."""
    if src_sr == STT_SR or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * STT_SR / src_sr))
    xp = np.arange(len(x))
    xq = np.linspace(0, len(x) - 1, n_out)
    return np.interp(xq, xp, x).astype(np.float32)


def float_to_wav16k(mono_16k: np.ndarray) -> bytes:
    pcm = (np.clip(mono_16k, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(STT_SR)
        w.writeframes(pcm)
    return buf.getvalue()


class MicStream:
    """Continuous capture at native rate; mono float32 chunks accumulate.
    .rms() = amplitude of the latest chunk (0..~1) for the waveform.
    .wav16k() = everything captured so far as a 16k-mono WAV for STT."""

    def __init__(self, device: int | None = None):
        self.device = device if device is not None else default_input_index()
        self.sr, self.ch = _native(self.device)
        self.chunks: list[np.ndarray] = []
        self._last_rms = 0.0
        from collections import deque
        self._rms_hist = deque(maxlen=600)
        self._viz = deque(maxlen=48)     # ~0.75s of recent audio for the waveform ONLY
        self._stream: sd.InputStream | None = None

    def _cb(self, indata, frames, t, status):
        mono = indata.mean(axis=1) if indata.ndim > 1 and indata.shape[1] > 1 \
            else indata.reshape(-1)
        self.chunks.append(mono.copy())
        # SEPARATE ring for the waveform: drain_pcm16k()/take_segment_wav16k()
        # empty `chunks` to ship audio, which starved the visualizer and made a
        # perfectly-capturing mic look dead on screen. Viz must not share the
        # buffer that gets consumed.
        self._viz.append(mono.copy())
        self._last_rms = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
        self._rms_hist.append(self._last_rms)

    def speech_thresh(self) -> float:
        """ADAPTIVE speech threshold: 3x this mic's own noise floor (20th
        percentile of recent frame RMS). A fixed threshold read quiet mics'
        speech as silence — cutting the user off mid-sentence."""
        if len(self._rms_hist) < 10:
            return 0.008
        floor = float(np.percentile(np.asarray(self._rms_hist), 20))
        return float(np.clip(floor * 3.0, 0.004, 0.06))

    def start(self):
        self.chunks = []
        self._stream = sd.InputStream(samplerate=self.sr, channels=self.ch,
                                      dtype="float32", device=self.device,
                                      callback=self._cb)
        self._stream.start()

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def rms(self) -> float:
        return self._last_rms

    def recent_samples(self, n: int) -> np.ndarray:
        """Last n mono samples (native rate) for live spectrum analysis. Reads the
        VIZ ring, not `chunks` — chunks get drained/consumed by the STT path."""
        if not self._viz:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(list(self._viz))[-n:]

    def _full_mono(self) -> np.ndarray:
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, "float32")

    def wav16k(self, last_seconds: float | None = None) -> bytes:
        x = self._full_mono()
        if last_seconds is not None:
            keep = int(self.sr * last_seconds)
            x = x[-keep:]
        return float_to_wav16k(resample_mono_16k(x, self.sr))

    def peak_rms_16k(self) -> tuple[int, float]:
        """Diagnostic: peak sample and mean RMS of the full capture at 16k."""
        x = resample_mono_16k(self._full_mono(), self.sr)
        i16 = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        return (int(np.max(np.abs(i16))) if len(i16) else 0,
                float(np.sqrt(np.mean(i16.astype(np.float64) ** 2))) if len(i16) else 0.0)

    def speech_ratio(self, thresh: float = 0.012) -> float:
        """Fraction of 20ms frames whose RMS exceeds a speech threshold. Low =
        mostly silence/breathing (guard against Whisper hallucinating on it)."""
        x = self._full_mono()
        fl = int(self.sr * 0.02)
        if len(x) < fl:
            return 0.0
        n = len(x) // fl
        frames = x[:n * fl].reshape(n, fl)
        rms = np.sqrt((frames ** 2).mean(axis=1))
        return float((rms > thresh).mean())

    def speech_seconds(self, thresh: float | None = None) -> float:
        """ABSOLUTE seconds of speech-level audio. The right commit gate:
        a ratio gate discards 10s of real speech followed by 50s of silence."""
        if thresh is None:
            thresh = self.speech_thresh()
        x = self._full_mono()
        fl = int(self.sr * 0.02)
        if len(x) < fl:
            return 0.0
        n = len(x) // fl
        frames = x[:n * fl].reshape(n, fl)
        rms = np.sqrt((frames ** 2).mean(axis=1))
        return float((rms > thresh).sum()) * 0.02

    def take_segment_wav16k(self) -> tuple[bytes, int]:
        """Atomically grab-and-CLEAR the audio captured since the last call, as a
        16k-mono WAV. For phrase-at-a-time dictation: each phrase is transcribed
        from its own fresh audio exactly once (no overlapping re-transcription =
        no duplication/jitter). Returns (wav_bytes, n_samples)."""
        chunks, self.chunks = self.chunks, []   # atomic swap under the GIL
        x = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return float_to_wav16k(resample_mono_16k(x, self.sr)), len(x)

    def drain_pcm16k(self) -> bytes:
        """Atomically grab-and-clear captured audio as RAW PCM16 @16k (no WAV
        header) — the streaming websocket's wire format."""
        chunks, self.chunks = self.chunks, []
        if not chunks:
            return b""
        x = resample_mono_16k(np.concatenate(chunks), self.sr)
        return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    def wav16k_trimmed(self, pad_s: float = 0.3) -> bytes:
        """Full capture with leading/trailing SILENCE trimmed (+pad) — the final
        transcribe shouldn't chew a minute of dead air after 10s of speech."""
        x = self._full_mono()
        fl = int(self.sr * 0.02)
        if len(x) < fl:
            return float_to_wav16k(resample_mono_16k(x, self.sr))
        n = len(x) // fl
        rms = np.sqrt((x[:n * fl].reshape(n, fl) ** 2).mean(axis=1))
        idx = np.where(rms > self.speech_thresh())[0]
        if len(idx) == 0:
            return float_to_wav16k(resample_mono_16k(x, self.sr))
        pad = int(pad_s / 0.02)
        a = max(0, (idx[0] - pad)) * fl
        b = min(n, (idx[-1] + pad + 1)) * fl
        return float_to_wav16k(resample_mono_16k(x[a:b], self.sr))


class ReplayMic(MicStream):
    """Drop-in MicStream that REPLAYS a real WAV as if it were the live mic —
    real sample rate (48k), real 1024-frame callback cadence, real timing. Lets
    the ENTIRE widget pipeline (worker loop, locks, finalize, inject) be exercised
    with known audio when no human is at the mic. This is how the real path gets
    tested, not by calling transcribe() on a file directly (which skips all of it)."""

    def __init__(self, wav_path: str, device=None, realtime: bool = True):
        import threading
        import wave
        self.device = -1
        self.sr, self.ch = 48000, 1
        self.chunks = []
        self._last_rms = 0.0
        from collections import deque
        self._rms_hist = deque(maxlen=600)
        self._viz = deque(maxlen=48)     # same viz contract as MicStream
        self._stream = None
        self.realtime = realtime
        self._stop = threading.Event()
        self.finished = threading.Event()
        with wave.open(wav_path, "rb") as w:
            src_sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if w.getnchannels() > 1:
                pcm = pcm.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
        mono = pcm.astype(np.float32) / 32767
        self._samples = resample_mono_16k(mono, src_sr) if src_sr == 48000 else \
            np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * 48000 / src_sr)),
                      np.arange(len(mono)), mono).astype(np.float32)

    def start(self):
        import threading
        self.chunks = []
        self._stop.clear()
        self.finished.clear()
        threading.Thread(target=self._feed, daemon=True).start()

    def _feed(self):
        blk = 1024
        period = blk / self.sr
        for i in range(0, len(self._samples), blk):
            if self._stop.is_set():
                return
            ch = self._samples[i:i + blk].copy()
            self.chunks.append(ch)
            self._viz.append(ch)
            self._last_rms = float(np.sqrt(np.mean(ch ** 2))) if len(ch) else 0.0
            self._rms_hist.append(self._last_rms)
            if self.realtime:
                time.sleep(period)
        self.finished.set()

    def stop(self):
        self._stop.set()


import time  # noqa: E402  (used by ReplayMic._feed)
