"""Domain vocabulary correction — the deterministic half of STT accuracy.

Parakeet is a general-English model. Its language model has never seen your domain terms
(jargon, product and project names), so it emits the nearest common-English neighbour:
"Kubernetes" -> "cooper netties". No decoder tuning fixes
that; the words are not in its vocabulary. A correction table is the standard, honest fix for
proper nouns and domain jargon.

Applied AFTER itn.apply_itn(), per burst. Two invariants, both tested in _self_test():
  * IDEMPOTENT — the canonical form must survive re-application unchanged, because bursts and
    cumulative text can both be passed through.
  * NEVER inside a word — corrections match on word boundaries only.

To teach it a new term: add one row to YOUR vocab file — `vocab.user.json` next to this file,
or the path in env WAVEFLOW_VOCAB. Format: {"Canonical Spelling": ["regex", ...]}. Key = the
canonical spelling; value = the mis-hearings actually observed in transcripts. Keep patterns
anchored and specific; a loose pattern that fires on ordinary speech is worse than the
mis-hearing it fixes. See vocab.example.json. No terms ship built in.
"""
import json
import os
import re
from pathlib import Path

USER_VOCAB = Path(os.environ.get("WAVEFLOW_VOCAB") or Path(__file__).with_name("vocab.user.json"))


def load_vocab(path: Path = USER_VOCAB) -> dict:
    """Read a user vocab file. Missing = no terms. Broken = no terms, said out loud."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"vocab: ignoring {path} ({e})", flush=True)
        return {}
    out = {}
    for canon, pats in data.items():
        if canon.startswith("_"):
            continue                      # "_comment" keys
        pats = [pats] if isinstance(pats, str) else list(pats)
        try:
            [re.compile(p) for p in pats]
        except re.error as e:
            print(f"vocab: skipping {canon!r} — bad pattern ({e})", flush=True)
            continue
        out[canon] = pats
    return out


def _compile(vocab: dict):
    return [(canon, re.compile(r"(?<![A-Za-z])(?:" + "|".join(pats) + r")(?![A-Za-z])",
                               re.IGNORECASE))
            for canon, pats in vocab.items()]


VOCAB = load_vocab()
_COMPILED = _compile(VOCAB)

# Terms used only by _self_test(), so the matcher is tested without shipping anyone's words.
_TEST_VOCAB = {
    # multi-word mis-hearing of one jargon word
    "Kubernetes": [r"(?:cooper|kuber|cuber)\s*(?:netties|nettys|neatees|netes)"],
    # a split name; live mode also joins it and picks its own casing ("Waveflow")
    "WaveFlow": [r"whispers?\s+flows?", r"waveflow"],
    # anchored to the TWO-word phrase, so the ordinary word "post" alone never fires
    "PostgreSQL": [r"post\s*(?:gress|gres|gray)\s*(?:q\s*l|sequel)"],
}


def apply_vocab(text: str, compiled=None) -> str:
    """Correct known domain terms. Idempotent; safe on partial bursts."""
    if not text:
        return text
    for canon, rx in (_COMPILED if compiled is None else compiled):
        text = rx.sub(canon, text)
    return text


def starts_with_proper_noun(text: str, vocab=None) -> bool:
    """Does `text` begin with one of the canonical terms above?

    The burst-boundary de-capitaliser lowercases the first letter of any burst that
    continues a sentence, so one spoken sentence reads as one sentence. That rule has
    no idea what a proper noun is: a burst starting on a term this table just fixed
    came out "waveFlow" — observed in a 2026-09-12 session, AFTER the
    vocab entry was already working. The existing "I"/"I'm" carve-out is the same
    idea; this generalises it to every term we deliberately capitalised.
    """
    if not text:
        return False
    low = text.lstrip().lower()
    return any(low.startswith(canon.lower()) for canon in (VOCAB if vocab is None else vocab))


def _self_test():
    compiled = _compile(_TEST_VOCAB)
    bad = []
    # the loader: missing file, broken JSON, bad regex, a string instead of a list
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "v.json"
        if load_vocab(p) != {}:
            bad.append("  missing file should load as {}")
        p.write_text("{not json", encoding="utf-8")
        if load_vocab(p) != {}:
            bad.append("  broken JSON should load as {}")
        p.write_text(json.dumps({"_comment": "x", "Good": "go+d", "Bad": ["("]}), encoding="utf-8")
        if load_vocab(p) != {"Good": ["go+d"]}:
            bad.append(f"  loader filter wrong: {load_vocab(p)!r}")
    if not starts_with_proper_noun("WaveFlow is up", _TEST_VOCAB):
        bad.append("  starts_with_proper_noun missed a canonical term")
    cases = [
        ("deploy it on cooper netties", "deploy it on Kubernetes"),
        ("a kubernettys cluster", "a Kubernetes cluster"),
        ("Kubernetes", "Kubernetes"),                # canonical survives (idempotence)
        ("working on whisper flow debugging it", "working on WaveFlow debugging it"),
        ("the original Whisper Flow even", "the original WaveFlow even"),
        ("working on Waveflow, debugging it", "working on WaveFlow, debugging it"),
        ("WaveFlow", "WaveFlow"),
        ("store it in post gress q l", "store it in PostgreSQL"),
        ("PostgreSQL", "PostgreSQL"),
        # must NOT fire on ordinary speech
        ("a cooper came by", "a cooper came by"),
        ("post the letter", "post the letter"),
        ("a whisper in the dark", "a whisper in the dark"),
        ("", ""),
    ]
    for src, want in cases:
        got = apply_vocab(src, compiled)
        if got != want:
            bad.append(f"  {src!r} -> {got!r} (want {want!r})")
        twice = apply_vocab(got, compiled)
        if twice != got:
            bad.append(f"  NOT IDEMPOTENT: {src!r} -> {got!r} -> {twice!r}")
    if bad:
        print("VOCAB_FAIL\n" + "\n".join(bad))
        return False
    print("VOCAB_OK")
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)
