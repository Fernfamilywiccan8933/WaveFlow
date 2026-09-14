"""Inverse text normalization (ITN) — spoken numbers -> digits, dependency-free.

"twenty three" -> "23", "one hundred and five" -> "105", "three thousand" -> "3000",
"nineteen eighty four" -> "1984" (year/paired groups concatenate; scaled numbers accumulate).
Pure string tokenization (no regex, no libraries): zero server-side installs, and IDEMPOTENT
(digits pass through unchanged), so it is safe to run on every cumulative streaming partial.
Conservative: a lone "one" stays a word, bare scale words are left alone, and an ambiguous run is
left as words rather than emitting a wrong number.
"""

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}
_NUM = set(_UNITS) | set(_TENS) | set(_SCALES) | {"and"}
_PUNCT = ",.?!;:\""

def _accumulate(words):
    """Cardinal accumulator for runs that contain a scale word (hundred/thousand/...)."""
    total = 0
    cur = 0
    for w in words:
        if w in _UNITS:
            cur += _UNITS[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in _SCALES:
            total += (cur or 1) * _SCALES[w]
            cur = 0
    return total + cur

def _groups(words):
    """Split a scale-free run into <100 values ('nineteen eighty four' -> [19, 84])."""
    out = []
    cur = None
    for w in words:
        if w == "and":
            continue
        if w in _TENS:
            if cur is not None:
                out.append(cur)
            cur = _TENS[w]
        elif w in _UNITS:
            if cur is not None and cur in _TENS.values() and _UNITS[w] < 10:
                out.append(cur + _UNITS[w]); cur = None
            else:
                if cur is not None:
                    out.append(cur)
                cur = _UNITS[w]
    if cur is not None:
        out.append(cur)
    return out

def _subwords(core):
    return core.lower().replace("-", " ").split() if core else []

def apply_itn(text):
    if not text:
        return text
    tokens = text.split(" ")
    out = []
    i = 0
    n = len(tokens)
    while i < n:
        core = tokens[i].strip(_PUNCT)
        sw = _subwords(core)
        if sw and all(x in _NUM for x in sw) and any(x != "and" for x in sw):
            run = list(sw)
            trail = tokens[i][len(core):]
            j = i + 1
            while j < n:
                c2 = tokens[j].strip(_PUNCT)
                s2 = _subwords(c2)
                if s2 and all(x in _NUM for x in s2):
                    run += s2
                    trail = tokens[j][len(c2):]
                    j += 1
                    if trail:
                        break
                else:
                    break
            while run and run[-1] == "and":
                run.pop()
            has_count = any(x in _UNITS or x in _TENS for x in run)
            if run and has_count and run != ["one"]:
                if any(x in _SCALES for x in run):
                    digits = str(_accumulate(run))
                else:
                    g = _groups(run)
                    digits = str(g[0]) if len(g) == 1 else "".join(str(x) for x in g)
                out.append(digits + trail)
                i = j
                continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)
