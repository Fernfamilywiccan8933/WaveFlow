"""Unit tests for LiveSession's settled/tail split — the jitter guard.

Re-transcribing a growing buffer returns SLIGHTLY DIFFERENT words each pass. That is what
made the July 2026 live typer loop and churn, and it is the one risk this mode reintroduces.
These cases replay that behaviour against the settle rule, using the real mis-hearings from
a 2026-09-12 session.

The invariants, all of which the July code broke:
  1. stable NEVER shrinks and is never rewritten;
  2. a word settles only when two consecutive hypotheses agree on it;
  3. settling happens on WORD boundaries, never mid-word ("rAM" must not settle from "rA");
  4. if the model contradicts committed text, we keep ours and emit no tail.

Run: venv/Scripts/python.exe server/test_live_settle.py
"""
import parakeet_server as P

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}\n    got  {got!r}\n    want {want!r}")


def new():
    return P.LiveSession(gap_s=0.35)


# --- 1. a word settles only after TWO passes agree -------------------------------
s = new()
check("first pass settles nothing", s.settle("the quick brown"), ("", "the quick brown"))
check("second pass settles the agreed words",
      s.settle("the quick brown fox"), ("the quick brown", "fox"))
check("third pass extends", s.settle("the quick brown fox jumps"),
      ("the quick brown fox", "jumps"))

# --- 2. stable never shrinks when the tail churns --------------------------------
s = new()
s.settle("we are working on")
s.settle("we are working on a")            # settles "we are working on"
before = s.stable
s.settle("we are working on the")          # tail changed; stable must hold
check("stable holds through tail churn", s.stable, before)
check("stable is exactly the agreed prefix", s.stable, "we are working on")

# --- 3. WORD boundary, not character boundary ------------------------------------
# The exact defect this mode exists to kill: "VRAM" split as "V" + "rAM".
s = new()
s.settle("check nvidia smi for V")
stable, tail = s.settle("check nvidia smi for VRAM usage")
check("half-word never settles", stable, "check nvidia smi for")
check("the whole word stays in the revisable tail", tail, "VRAM usage")
assert "V" not in stable.split(), "a bare 'V' settled — the half-word bug is back"

# --- 4. the model contradicting committed text must NOT rewrite it ---------------
s = new()
s.settle("kind of like an AI")
s.settle("kind of like an AI brain")        # commits "kind of like an AI"
committed = s.stable
stable, tail = s.settle("kind of like a AI brain")   # disagrees with a committed word
check("contradiction does not rewrite committed text", stable, committed)
check("stable still intact after contradiction", s.stable, "kind of like an AI")
# No tail on a contradicting pass. The disagreement is at word 3 ("an" -> "a"), so every
# later position may be shifted too; emitting hw[5:] would risk typing the WRONG word at
# the seam. Hold, and let the next pass — which usually agrees again — supply the tail.
check("contradiction emits no tail rather than risk a shifted word", tail, "")

# --- 5. finish(): committed words kept verbatim, the rest becomes the tail --------
s = new()
s.settle("send the notes to the")
s.settle("send the notes to the whole")     # commits "send the notes to the"
stable, tail = s.finish("send the notes to the whole team.")
check("finish keeps commit", stable, "send the notes to the")
check("finish appends the rest", tail, "whole team.")
check("utterance counter advances", s.utt, 1)
check("stable resets for the next utterance", s.stable, "")

# --- 5b. REGRESSION (2026-09-12): the full-context pass re-punctuates -------------
# First cut compared with str.startswith, so "All right, this is" did not "start with"
# the committed "all right this is" — and the ENTIRE remainder of the utterance was
# dropped. Real probe output was literally: "All Or the original WaveFlow...".
s = new()
s.settle("all right this is")
s.settle("all right this is just me")       # commits "all right this is"
stable, tail = s.finish("All right, this is just me giving it a test.")
check("re-punctuated final keeps the commit", stable, "all right this is")
check("re-punctuated final does NOT drop the remainder",
      tail, "just me giving it a test.")

s = new()
s.settle("the order total is")
s.settle("the order total is 47")           # commits "the order total is"
stable, tail = s.finish("The order total is $47.95 for 12 items.")
check("ITN'd final keeps the commit", stable, "the order total is")
check("ITN'd final still yields its tail", tail, "$47.95 for 12 items.")

# --- 5c. REGRESSION (2026-09-12): committed words must be APPEND-ONLY ------------
# Caught on real audio. Parakeet re-segments between passes, so the agreed
# word COUNT can grow while earlier words silently change under it. Measured: committed
# ['All','right,'] came back as ['Alright,','uh']; 'kind of like uh' became 'kinda like a'.
# Growing the count is not enough — already-typed words must keep their exact text.
s = new()
s.settle("All right this")
s.settle("All right this is")               # commits "All right this"
committed = s.stable
st, tail = s.settle("Alright, uh this is just")   # re-segmented: "All right"->"Alright, uh"
# Since fillers stopped hiding the anchor (case 8), this pass may also settle "is" — the two
# passes agree on it. The RULE is that the committed text survives verbatim, not that we hold.
check("re-segmentation must NOT rewrite committed words", st.startswith(committed), True)
check("stable words unchanged after re-segmentation", s.stable, "All right this is")

# once it agrees again, growth resumes and old words keep their ORIGINAL text
s.settle("All right this is just me")
st, _ = s.settle("All right this is just me giving")
assert st.startswith("All right this"), f"committed prefix lost: {st!r}"
check("growth resumes append-only", st, "All right this is just me")

# a filler appearing mid-utterance shifts positions — must not corrupt the commit
s = new()
s.settle("we are working on")
s.settle("we are working on token")          # commits "we are working on"
st, _ = s.settle("we are uh working on token optimization")
check("inserted filler does not rewrite the commit", st, "we are working on token")

# --- 5e. REGRESSION (2026-09-13): re-segmentation must not STALL printing ----------
# On a real 63s session the first utterance held 27 of 31 passes because
# Parakeet flips "All right," <-> "Alright," between passes; word-by-word matching never
# lined them up, so nothing printed for 22 seconds. Alignment now runs on letters.
s = new()
s.settle("All right, this is")
s.settle("All right, this is just")              # commits "All right, this is"
st, tail = s.settle("Alright, this is just me giving")
# "just" appeared in the previous pass too, so it settles; the point is that the pass is NOT
# held — a held pass returns an empty tail
check("re-segmented anchor still found (no stall)", tail, "me giving")
check("and committed text keeps its original spelling", st, "All right, this is just")
s.settle("Alright, this is just me giving it")
st, _ = s.settle("All right, this is just me giving it a")
check("growth continues across flips, original text kept",
      st, "All right, this is just me giving it")

s = new()
s.settle("working on a block brain white")
s.settle("working on a block brain white paper")   # commits "... block brain white"
st, tail = s.settle("working on a blockbrain white paper idea")
check("joined compound still aligns (not held)", tail, "idea")
check("and 'paper' settled across the join", st, "working on a block brain white paper")

# letters running on must NOT settle a different word
s = new()
s.settle("this is")
st, tail = s.settle("this isn't")
check("'is' never settles against 'isn't'", st, "this")

# --- 5d. word-for-word: nothing committed is ever altered, over a whole utterance --
s = new()
seen = []
for hyp in ["all right this", "all right this is", "Alright, uh this is just",
            "all right this is just me", "all right this is just me giving",
            "Alright this is just me giving it", "all right this is just me giving it a"]:
    st, _ = s.settle(hyp)
    seen.append(st.split())
for older, newer in zip(seen, seen[1:]):
    if newer[:len(older)] != older:
        FAILS.append(f"  COMMITTED TEXT ALTERED\n    {older!r}\n    -> {newer[:len(older)]!r}")

# --- 6. empty / degenerate input never crashes or emits junk ----------------------
s = new()
check("empty hypothesis is inert", s.settle(""), ("", ""))
check("whitespace hypothesis is inert", s.settle("   "), ("", ""))
s2 = new()
check("finish with no audio text", s2.finish(""), ("", ""))

# --- 7. a long utterance settles monotonically, never going backwards -------------
s = new()
words = ("all right this is just me giving it a test right now we are working on "
         "waveflow debugging it").split()
prev_len = 0
for i in range(2, len(words) + 1):
    st, _ = s.settle(" ".join(words[:i]))
    if len(st) < prev_len:
        FAILS.append(f"  stable SHRANK at word {i}: {len(st)} < {prev_len}")
    prev_len = len(st)
check("long utterance settles all but the last word",
      s.stable, " ".join(words[:-1]))

# --- 8. a FILLER appears inside committed text (real ONNX hyps, 2026-09-13 18:52 session) --
# The committed "...much better. It operates better," was re-heard as "...much better. Uh it
# operates better,". The anchor could not be found, every pass held, and the final fell back
# to a word count that the two fillers had shifted — typing "operates better," twice.
s = new()
s.settle("And also, this is looking much better. It operates.")
s.settle("And also this is looking much better. It operates better.")
s.settle("And also, this is looking much better. It operates better, the skins look better.")
check("filler inside the anchor does not stall settling",
      s.settle("And also um this is looking much better. Uh it operates better, the skins look beautiful."),
      ("And also this is looking much better. It operates better, the skins look", "beautiful."))
st, tail = s.finish("And also, um this is looking much better. Uh it operates better, the skins look beautiful. Um")
check("final after a filler shift types nothing twice", (st + " " + tail).split(),
      "And also this is looking much better. It operates better, the skins look beautiful. Um".split())

s = new()
s.settle("we are working on the")
s.settle("we are working on the dashboard")
check("final anchor ignores fillers",
      s.finish("uh we are um working on thee dash board today"),
      ("we are working on the", "dash board today"))

s = new()
s.settle("we are working on the")
s.settle("we are working on the dashboard")
check("no anchor at all: fallback word count ignores fillers",
      s.finish("uh we are um walking in a dashboard"),
      ("we are working on the", "dashboard"))

# --- 9. commit that is ONLY a filler must not stall the utterance (replay 2026-09-13) ----
# "Uh" settled first; with fillers blanked the anchor was empty, and every pass held for 22s.
s = new()
s.settle("Uh")
s.settle("Uh all")
s.settle("Uh alright this is just")
st, _ = s.settle("Alright, uh this is just me")
check("leading filler never blocks printing", st.startswith("Uh Alright,"), True)

# --- 9b. an onset heard as a filler must not let the NEXT word jump ahead (143159 replay) --
s = new()
s.settle("Um")
s.settle("Uh talking.")
s.settle("Um talking ended.")
s.settle("I'm talking and it's not typing.")
st, _ = s.settle("I'm talking and it's not typing anything.")
check("'I'm' misheard as a filler is not dropped", st, "I'm talking and it's not typing")

s = new()
s._stable_words, s.stable = ["Uh"], "Uh"         # the exact stuck state from the replay
s._prev_rest = ["alright", "this", "is", "just"]
st, _ = s.settle("Uh alright this is just me")
check("filler-only commit keeps printing", st, "Uh alright this is just")

# --- 10. CEILING CUT behind committed words (ONNX replay of 2026-09-12 111418) ------------
# Partials of the growing 22s buffer committed "Or the original"; the ceiling then cut at the
# quietest frame BEFORE those words, so they were in the carried audio and typed again:
# "put out. Um Or the original Or the original WaveFlow".
s = new()
s.settle("that Jack Roberts put out. Um or the")
s.settle("that Jack Roberts put out. Um Or the original")
s.settle("that Jack Roberts put out. Um Or the original WaveFlow")
s._cut_kind = "ceiling"
st, tail = s.finish("that Jack Roberts put out. Um")
check("ceiling final keeps what was typed", (st, tail), ("that Jack Roberts put out. Um Or the original", ""))
check("next utterance holds while it has not yet passed the carried words",
      s.settle("or the"), ("", ""))
s.settle("or the original WaveFlow even.")
st, tail = s.settle("Or the original WaveFlow, even you know")
check("next utterance skips the words already typed", (st + " " + tail).split()[0], "WaveFlow,")
st, tail = s.finish("Or the original WaveFlow, even. You know this is based off of an actual idea.")
check("next utterance final skips them too", (st + " " + tail).lower().count("original"), 0)

# --- 11. OVERLAP after a ceiling cut (live 2026-09-14 000134) ------------------------------
# Sent "...they'll only have a couple of"; the true text is "a couple of files, but over time".
# The next utterance starts in overlap, so its head re-hears SOME suffix of what was sent.
def after_ceiling(sent):
    s = new()
    s._stable_words, s.stable = sent.split(), sent
    s._cut_kind = "ceiling"
    s.finish(sent)
    return s

s = after_ceiling("Obviously some people will only have a couple of")
check("overlap re-hearing 3 sent words drops them",
      s.finish("a couple of files, but over time obviously databases will grow."),
      ("", "files, but over time obviously databases will grow."))
s = after_ceiling("Obviously some people will only have a couple of")
check("overlap re-hearing 1 sent word drops it",
      s.finish("of files, but over time."), ("", "files, but over time."))
s = after_ceiling("Obviously some people will only have a couple of")
check("no overlap words heard: nothing dropped",
      s.finish("files, but over time."), ("", "files, but over time."))
s = after_ceiling("Obviously some people will only have a couple of")
check("partial still inside the overlap holds", s.settle("a couple"), ("", ""))
check("final wholly inside the overlap adds nothing", s.finish("a couple"), ("", ""))

# a GAP cut never carries: everything committed was in the final audio
s = new()
s.settle("hello there")
s.settle("hello there friend")
s._cut_kind = "gap"
s.finish("hello there")
check("gap cut carries nothing", s.settle("friend of mine"), ("", "friend of mine"))

# --- 12. FLUSH must not hand trailing noise to the model (probe 2026-09-14) -----------------
# The burst rule counted TOTAL speech frames (>=0.20s), so breath/room noise after the last
# word was transcribed on flush and came back as invented words ("Reviewer's demand").
import numpy as np


def flushed(flags):
    s = new()
    s.buf = np.ones(len(flags) * 480, dtype=np.float32) * 0.01
    s._frames = lambda: np.asarray(flags, dtype=bool)
    return s, s.flush()


s, audio = flushed([True, False] * 20)                 # 0.6s of scattered noise, never sustained
check("flush drops unsustained noise", audio is None, True)
check("flush clears the buffer", len(s.buf), 0)
s, audio = flushed([True] * 30 + [False] * 50)          # 0.9s speech then 1.5s silence
check("flush keeps sustained speech", audio is not None, True)
check("flush cuts the trailing silence (0.9s + 0.15s tail)", len(audio), 30 * 480 + int(0.15 * 16000))
check("flush marks the cut kind", s._cut_kind, "flush")
s, audio = flushed([])
check("flush on an empty buffer is inert", audio, None)

# --- 13. a FILLER onset holds settling (replay 2026-09-13 143159: "I'm" heard as "Um") ------
s = new()
s._speech_s_at_emit = 2.0
check("filler onset shows as tail", s.settle("Um talking."), ("", "Um talking."))
check("filler onset still holds", s.settle("Um talking and it"), ("", "Um talking and it"))
check("final decides the onset", s.finish("I'm talking and it's not typing."),
      ("", "I'm talking and it's not typing."))
s = new()
s._speech_s_at_emit = 2.0
s.settle("Um talking and")
s.settle("I'm talking and it's")
check("real onset settles once heard", s.settle("I'm talking and it's not"),
      ("I'm talking and it's", "not"))
s = new()
s._speech_s_at_emit = 6.0
s.settle("Um so the plan")
check("past the hold, a real 'um' settles as before", s.settle("Um so the plan is"),
      ("Um so the plan", "is"))

# --- 14. mic sensitivity presets (per connection) ----------------------------------------------
check("no preset = server defaults",
      (lambda s: (s.vad_mode, s.sustain_s, s.k))(P.LiveSession(0.35)), (P.VAD_MODE, P.SUSTAIN_S, P.VAD_K))
check("balanced preset == server defaults (clients that send it change nothing)",
      (lambda s: (s.vad_mode, s.sustain_s, s.k))(P.LiveSession(0.35, sensitivity="balanced")),
      (P.VAD_MODE, P.SUSTAIN_S, P.VAD_K))
check("unknown preset falls back to defaults", P.LiveSession(0.35, sensitivity="loud").sustain_s, P.SUSTAIN_S)
hi, lo = P.LiveSession(0.35, sensitivity="high"), P.LiveSession(0.35, sensitivity="low")
check("high is more permissive than low", (hi.vad_mode < lo.vad_mode, hi.sustain_s < lo.sustain_s, hi.k < lo.k),
      (True, True, True))
for name, want in (("high", True), ("balanced", False), ("low", False)):   # a 0.51s run of speech
    s = P.LiveSession(0.35, sensitivity=name)
    s.buf = np.ones(40 * 480, dtype=np.float32) * 0.01
    s._frames = lambda: np.asarray([False] * 10 + [True] * 17 + [False] * 13, dtype=bool)
    check(f"0.5s speech with {name}", s.flush() is not None, want)

# --- 15. REGRESSION (2026-09-15, 5-min CPU replay): a SHORT commit anchors to the WRONG repeat ----
# Only "The" had settled. The anchor search took the LAST "the" in each new pass, so everything
# before it counted as already typed: live tails read "water.", "lazy dog.", "river." and the
# final typed "The riverbank." — "quick brown fox jumps over the lazy dog near the" was lost.
# Exact message sequence from tools/stream_probe.py against a 4-thread CPU engine.
s = new()
s.settle("The")
check("short commit: first word settles", s.settle("The quick brown fog")[0], "The")
st, tail = s.settle("The quick brown fox jumps over the water.")
check("short commit: live text keeps the middle of the sentence", " ".join((st + " " + tail).split()),
      "The quick brown fox jumps over the water.")
st, tail = s.finish("The quick brown fox jumps over the lazy dog near the riverbank.")
check("short commit: final keeps the whole sentence", " ".join((st + " " + tail).split()),
      "The quick brown fox jumps over the lazy dog near the riverbank.")
# why LAST-occurrence existed must still hold: a phrase that repeats later is not the join
s = new()
s.settle("so I said go")
s.settle("so I said go and then")
st, tail = s.settle("so I said go and then I said go again")
check("repeated phrase: join stays at the commit point", " ".join((st + " " + tail).split()),
      "so I said go and then I said go again")

# --- 16. REGRESSION (2026-09-15): the model glues a currency sign to the previous word ------------
# Real ONNX output: "The order total is$47.95 for 12 items." — with "is" committed, the glued word
# never matched and the final dropped "$47.95". polish() splits it, and polish is idempotent.
check("glued currency split", P.polish("The order total is$47.95 for 12 items."),
      "The order total is $47.95 for 12 items.")
check("polish idempotent on it", P.polish(P.polish("is$47.95")), "is $47.95")
check("normal prices untouched", (P.polish("costs $5"), P.polish("US$5 fee")), ("costs $5", "US $5 fee"))
s = new()
s.settle(P.polish("The order total"))
s.settle(P.polish("The order total is 47."))
s.settle(P.polish("The order total is$47"))
st, tail = s.finish(P.polish("The order total is$47.95 for 12 items."))
check("price survives a live utterance", " ".join((st + " " + tail).split()), "The order total is $47.95 for 12 items.")

if FAILS:
    print("LIVE_SETTLE_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("LIVE_SETTLE_OK")
