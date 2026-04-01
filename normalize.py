"""
Validation and normalization pipeline for KIParla transcription units.

Rules are registered in WARNING_RULES and ERROR_RULES. To add a new rule:
  1. Write the check/fix function in this module (or import it).
  2. Append a ValidationRule to the appropriate registry.
  3. Add it to the default YAML config schema (Step 4).

Warning rule functions must have signature:  (str) -> tuple[int, str]
  returning (substitution_count, new_annotation).

Error rule functions must have signature:    (str) -> bool
  returning True if the annotation is valid, False if an error is detected.
"""

from dataclasses import dataclass
from typing import Callable
import regex as re
import num2words as _num2words


@dataclass
class ValidationRule:
    name: str
    function: Callable
    enabled_by_default: bool = True


# Warning rules: applied in order to the annotation string; order is load-bearing.
WARNING_RULES: list[ValidationRule] = []

# Error rules: applied after all warnings; each checks the final annotation.
ERROR_RULES: list[ValidationRule] = []


def _is_enabled(rule: ValidationRule, config: dict[str, bool]) -> bool:
    return config.get(rule.name, rule.enabled_by_default)


def validate_and_normalize(
    annotation: str,
    config: dict[str, bool] | None = None,
) -> tuple[str, dict[str, int], dict[str, bool]]:
    """
    Apply all enabled rules to *annotation*.

    Args:
        annotation: raw transcription unit text.
        config:     optional mapping of rule name -> enabled. Missing keys
                    fall back to each rule's ``enabled_by_default``.

    Returns:
        normalized  -- the (possibly modified) annotation string
        warnings    -- {rule_name: total_substitution_count} for rules that fired
        errors      -- {rule_name: True} for error rules that detected a problem
    """
    if config is None:
        config = {}

    warnings: dict[str, int] = {}
    errors: dict[str, bool] = {}
    normalized = annotation

    for rule in WARNING_RULES:
        if not _is_enabled(rule, config):
            continue
        count, normalized = rule.function(normalized)
        if count > 0:
            warnings[rule.name] = warnings.get(rule.name, 0) + count

    for rule in ERROR_RULES:
        if not _is_enabled(rule, config):
            continue
        if not rule.function(normalized):
            errors[rule.name] = True

    return normalized, warnings, errors


# ---------------------------------------------------------------------------
# Warning functions  (str) -> tuple[int, str]
# ---------------------------------------------------------------------------

def remove_spaces(annotation: str) -> tuple[int, str]:
    """Collapse tabs, newlines, and repeated spaces into a single space."""
    total = 0
    for pattern, replacement in [(r"\t+", " "), (r"\n+", " "), (r"\s\s+", " ")]:
        annotation, n = re.subn(pattern, replacement, annotation)
        total += n
    return total, annotation.strip()


def _replace_spaces_in_braces(match: re.Match) -> str:
    return "{" + match.group(1).replace(" ", "_") + "}"


def meta_tag(annotation: str) -> tuple[int, str]:
    """Convert Jefferson double-parenthesis notation: (( → {, )) → }, (.) → {P}."""
    subs_map = {"((": "{", "))": "}", "(.)": "{P}"}
    total = 0
    for old, new in subs_map.items():
        annotation, n = re.subn(re.escape(old), new, annotation)
        total += n
    annotation = re.sub(r"\{([\w ]+)\}", _replace_spaces_in_braces, annotation)
    return total, annotation


def check_spaces(annotation: str) -> tuple[int, str]:
    """Fix spacing errors around brackets and punctuation."""
    total = 0
    rules = [
        # Space is on the wrong side of an opening bracket: move it outside.
        # e.g. "bla[ ciao]" → "bla [ciao]"
        (r"([^ \[\(<>°])([\[\(]) ([^ ])",   r"\1 \2\3"),
        # Space after opening bracket when nothing precedes it (or preceded by space).
        # e.g. "[ ciao]" → "[ciao]"
        (r"([\[\(]) ([^ ])",                 r"\1\2"),
        # Space is on the wrong side of a closing bracket: move it outside.
        # e.g. "[ciao ]bla" → "[ciao] bla"
        (r"([^ ]) ([\)\]])([^ ])",           r"\1\2 \3"),
        # Space before closing bracket when nothing follows it (or followed by space).
        # e.g. "[ciao ]" → "[ciao]"
        (r"([^ ]) ([\)\]])",                 r"\1\2"),
        # Space before punctuation with a word following: move space to after.
        # e.g. "ciao ,bla" → "ciao, bla"
        (r"([^ ]) ([.,:?])([^ ])",           r"\1\2 \3"),
        # Space before punctuation at end (or before another space).
        # e.g. "ciao ," → "ciao,"
        (r"([^ ]) ([.,:?])",                 r"\1\2"),
        # Missing space before NVB tag.
        (r"([^ \[\(<>°])(\{[^}]+\})",        r"\1 \2"),
        # Missing space after NVB tag.
        (r"(\{[^}]+\})([^ \]\)<>°])",        r"\1 \2"),
    ]
    for pattern, replacement in rules:
        annotation, n = re.subn(pattern, replacement, annotation)
        total += n
    return total, annotation.strip()


def remove_pauses(annotation: str) -> tuple[int, str]:
    """Strip leading and trailing short-pause markers {P}."""
    annotation, n = re.subn(
        r"^([\[\]()<>°]?)\s*\{P\}\s*|\s*\{P\}\s*([\[\]()<>°]?)$",
        r"\1\2",
        annotation,
    )
    return n, annotation.strip()


def remove_prosodiclinks(annotation: str) -> tuple[int, str]:
    """Strip leading and trailing prosodic-link markers =."""
    annotation, n = re.subn(
        r"^([\[\]()<>°]?)\s*=\s*|\s*=\s*([\[\]()<>°]?)$",
        r"\1\2",
        annotation,
    )
    return n, annotation.strip()


def space_prosodiclink(annotation: str) -> tuple[int, str]:
    """Remove stray spaces immediately before or after = markers."""
    annotation, n = re.subn(r" =|= ", "=", annotation)
    return n, annotation.strip()


def overlap_prolongations(annotation: str) -> tuple[int, str]:
    """Fix malformed overlap+prolongation sequences: word:[: → [word::."""
    annotation, n = re.subn(r"(\w:*)\[:", r"[\1:", annotation)
    return n, annotation


def clean_non_jefferson_symbols(annotation: str) -> tuple[int, str]:
    """Remove characters that are not part of the Jefferson transcription system."""
    annotation, n = re.subn(
        r"[^{}_,\?.:=°><\[\]\(\)\w\s'\-~$#@]",
        "",
        annotation,
    )
    return n, annotation.strip()


# Accent substitution tables — defined as module-level constants so they can
# be replaced or extended by a YAML config in a later step.

# Words where -chè should become -ché (the regex allows Jefferson markers
# interspersed between letters, e.g. per[chè → per[ché).
ACCENT_CHE_MAP: dict[str, str] = {
    "perchè":   "perché",
    "benchè":   "benché",
    "finchè":   "finché",
    "poichè":   "poiché",
    "anzichè":  "anziché",
    "dopodichè":"dopodiché",
    "granchè":  "granché",
    "fuorchè":  "fuorché",
    "affinchè": "affinché",
    "pressochè":"pressoché",
    "nè":       "né",
}

# Words where a trailing apostrophe-accent should become the proper accent.
ACCENT_PERO_MAP: dict[str, str] = {
    "pero'":   "però",
    "perche'": "perché",
    "puo'":    "può",
}


def replace_che(annotation: str, accent_map: dict[str, str] | None = None) -> tuple[int, str]:
    """Replace common Italian accent errors of the -chè/-né family."""
    if accent_map is None:
        accent_map = ACCENT_CHE_MAP
    total = 0
    for word in accent_map:
        # Build a pattern that tolerates Jefferson markers between letters.
        pattern = r"\b" + "".join(f"([^ =']*){ch}" for ch in word) + r"\b"
        back_refs = "".join(f"\\{i+1}{ch}" for i, ch in enumerate(word))
        replacement = back_refs[:-1] + "é"  # swap last char with é
        annotation, n = re.subn(re.compile(pattern), replacement, annotation)
        total += n
    return total, annotation


def replace_po(annotation: str) -> tuple[int, str]:
    """Replace pò (and prolonged variants like p:ò) with po'."""
    annotation, n = re.subn(r"\bp([^ =\p{L}]*)ò\b", r"p\1o'", annotation)
    return n, annotation.strip()


def replace_pero(annotation: str, accent_map: dict[str, str] | None = None) -> tuple[int, str]:
    """Replace apostrophe-accent words (pero', puo', perche') with accented forms."""
    if accent_map is None:
        accent_map = ACCENT_PERO_MAP
    total = 0
    for word, substitute in accent_map.items():
        pattern = r"\b" + word[0]
        back_refs = word[0]
        for i, ch in enumerate(word[1:-1]):
            pattern += f"([^ =]*){ch}"
            back_refs += f"\\{i+1}{ch}"
        pattern += f"([^ =]*){word[-1]}"
        back_refs = back_refs[:-1] + substitute[-1]
        annotation, n = re.subn(re.compile(pattern), back_refs, annotation)
        total += n
    return total, annotation


def check_numbers(annotation: str) -> tuple[int, str]:
    """Convert digit sequences to Italian words (2 → due)."""
    matches = list(re.finditer(r"\b[0-9]+\b", annotation))
    if not matches:
        return 0, annotation

    parts = []
    prev = 0
    for m in matches:
        start, end = m.span()
        parts.append(annotation[prev:start])
        word = _num2words.num2words(m.group(0), lang="it")
        if word.endswith("tre") and len(word) > 3:
            word = word[:-1] + "é"
        parts.append(word)
        prev = end
    parts.append(annotation[prev:])

    return len(matches), "".join(parts)


def switch_symbols(annotation: str) -> tuple[int, str]:
    """Move intonation markers before prosodic/interruption symbols: .,? must follow :, -, ~."""
    annotation, n = re.subn(r"([.,?])([:-~])", r"\2\1", annotation)
    return n, annotation.strip()


def switch_NVB(annotation: str) -> tuple[int, str]:
    """Move NVB tags outside bracket spans of any kind.

    NVB tags found immediately after an opening bracket, or immediately before
    a closing bracket, are relocated to just outside the span.

    Exception: {P} immediately after [ or immediately before ] is left in
    place — a pause inside an overlap span is transcriptionally valid.
    """
    total = 0
    # Opening [ : move any NVB except {P} to before the bracket.
    annotation, n = re.subn(r"(\[)(\{(?!P\})\w+\})", r"\2 \1", annotation)
    total += n
    # Opening ( < > ° : move any NVB (including {P}) to before the bracket.
    annotation, n = re.subn(r"([(<>°])(\{\w+\})", r"\2 \1", annotation)
    total += n
    # Closing ] : move any NVB except {P} to after the bracket.
    annotation, n = re.subn(r"(\{(?!P\})\w+\})(])", r"\2 \1", annotation)
    total += n
    # Closing ) > < ° : move any NVB (including {P}) to after the bracket.
    annotation, n = re.subn(r"(\{\w+\})([)><°])", r"\2 \1", annotation)
    total += n
    return total, annotation.strip()


def check_spaces_dots(annotation: str) -> tuple[int, str]:
    """Remove stray spaces immediately inside °...° low-volume markers."""
    segments = re.split(r"(°[^°]+°)", annotation)
    subs = 0
    for i, seg in enumerate(segments):
        if not seg.startswith("°"):
            continue
        if seg[1] == " ":
            seg = seg[0] + seg[2:]
            subs += 1
        if seg[-2] == " ":
            seg = seg[:-2] + seg[-1]
            subs += 1
        segments[i] = seg
    return subs, "".join(segments).strip()


def check_spaces_angular(annotation: str) -> tuple[int, str]:
    """Remove stray spaces immediately inside <...> and >...< pace markers."""
    segments = _split_angular(annotation)
    subs = 0
    for i, seg in enumerate(segments):
        if not seg or seg[0] not in ("<", ">"):
            continue
        if len(seg) > 1 and seg[1] == " ":
            seg = seg[0] + seg[2:]
            subs += 1
        if len(seg) > 1 and seg[-2] == " ":
            seg = seg[:-2] + seg[-1]
            subs += 1
        segments[i] = seg
    return subs, "".join(segments).strip()


def _split_angular(annotation: str) -> list[str]:
    """Split annotation into segments delimited by angular-bracket pace markers."""
    fast = False   # >...<
    slow = False   # <...>
    cur: list[str] = []
    segs: list[list[str]] = []
    for ch in annotation:
        if ch == "<":
            if fast:
                cur.append(ch)
                segs.append(cur)
                cur = []
                fast = False
            elif not slow:
                segs.append(cur)
                cur = [ch]
                slow = True
            else:
                cur.append(ch)
        elif ch == ">":
            if slow:
                cur.append(ch)
                segs.append(cur)
                cur = []
                slow = False
            elif not fast:
                segs.append(cur)
                cur = [ch]
                fast = True
            else:
                cur.append(ch)
        else:
            cur.append(ch)
    if cur:
        segs.append(cur)
    return ["".join(s) for s in segs if s]


# ---------------------------------------------------------------------------
# Error functions  (str) -> bool   (True = valid, False = error detected)
# ---------------------------------------------------------------------------

def check_even_dots(annotation: str) -> bool:
    """Return True if the number of ° characters is even (i.e. all pairs are closed)."""
    return annotation.count("°") % 2 == 0


def check_normal_parentheses(annotation: str, open_char: str, close_char: str) -> bool:
    """Return True if open_char/close_char pairs are balanced and non-nested."""
    is_open = False
    for ch in annotation:
        if ch == open_char:
            if is_open:
                return False
            is_open = True
        elif ch == close_char:
            if not is_open:
                return False
            is_open = False
    return not is_open


def check_angular_parentheses(annotation: str) -> bool:
    """Return True if <...> (slow) and >...< (fast) pace markers are balanced."""
    fast = False
    slow = False
    for ch in annotation:
        if ch == "<":
            if fast:
                fast = False
            elif not slow:
                slow = True
            else:
                return False  # nested slow
        elif ch == ">":
            if slow:
                slow = False
            elif not fast:
                fast = True
            else:
                return False  # nested fast
    return not fast and not slow
