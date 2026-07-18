"""
tokenize.py — Token data structure and TranscriptionUnit tokenization.

Public API:
    Token               — single token with all extracted features
    tokenize_tu         — split a normalized annotation into a list of Tokens
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import regex as re

import dataflags as df


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

@dataclass
class Token:
    """One token produced by tokenizing a TranscriptionUnit annotation."""

    # Text as it appears in the annotation (before feature extraction).
    orig_text: str

    # Character span of orig_text within the TU annotation string.
    span: tuple[int, int] = field(default_factory=lambda: (0, 0))

    # Normalized form: lowercase, no prolongations, no intonation punctuation.
    form: str = field(init=False)

    token_type: df.tokentype = field(init=False)
    variation:  df.tokenvariation = field(init=False, default=df.tokenvariation.none)

    # Feature flags
    intonation:    df.intonation  = field(init=False, default=df.intonation.plain)
    interruption:  bool           = field(init=False, default=False)
    truncation:    bool           = field(init=False, default=False)
    prosodiclink:  bool           = field(init=False, default=False)
    spaceafter:    bool           = field(init=False, default=True)
    pauseafter:    bool           = field(init=False, default=False)
    volume:        Optional[df.volume] = field(init=False, default=None)
    non_ita:       bool           = field(init=False, default=False)
    iso_code:      str            = field(init=False, default="ita")
    non_ortho:     bool           = field(init=False, default=False)

    # {char_index: colon_count}  — colons stripped from form
    prolongations: dict[int, int] = field(init=False, default_factory=dict)

    # Span features set by add_token_features() — {span_id: (char_start, char_end)}
    overlaps:   dict[int, tuple[int, int]] = field(init=False, default_factory=dict)
    slow_pace:  dict[int, tuple[int, int]] = field(init=False, default_factory=dict)
    fast_pace:  dict[int, tuple[int, int]] = field(init=False, default_factory=dict)
    low_volume: dict[int, tuple[int, int]] = field(init=False, default_factory=dict)
    guesses:    dict[int, tuple[int, int]] = field(init=False, default_factory=dict)
    # True when a word-internal guess span (e.g. c(io)è) matched a word on
    # the module's configured reduction_words whitelist.
    reduced:    bool = field(init=False, default=False)

    # Position of this token within its TU (start/end flags set after tokenization).
    position_in_tu: df.position = field(init=False, default=df.position.inner)

    # Syllable count for unknown tokens.
    syllables: Optional[int] = field(init=False, default=None)

    # Config controlling which variation markers are active.
    # Passed in at construction; not serialized as a "feature".
    _cfg_variation: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.form = self.orig_text
        self._classify()

    # ------------------------------------------------------------------
    # Classification (step 5c)
    # ------------------------------------------------------------------

    def _classify(self):
        text = self.form

        # 0. Shortpause / NVB — checked before the generic bracket strip below,
        # since (.) and ((...)) use the same parens as guess/pace/volume spans.
        if text == "(.)":
            self.token_type = df.tokentype.shortpause
            return

        if text.startswith("((") and text.endswith("))"):
            self.token_type = df.tokentype.nonverbalbehavior
            return

        # Strip Jefferson span markers — they are position markers only.
        for ch in "[]()<>°":
            text = text.replace(ch, "")

        if not text.strip():
            self.form = ""
            self.token_type = df.tokentype.error
            return

        # 1. Anonymized
        if text.startswith("@"):
            self.form = text
            self.token_type = df.tokentype.anonymized
            self._extract_features()
            return

        # 2. Short pause
        if text == "{P}":
            self.form = text
            self.token_type = df.tokentype.shortpause
            return

        # 3. Non-verbal behavior
        if text.startswith("{"):
            self.form = text
            self.token_type = df.tokentype.nonverbalbehavior
            return

        # 4. Unknown (all x)
        if text and all(c == "x" for c in text):
            self.form = text          # keep as-is
            self.syllables = len(text)
            self.token_type = df.tokentype.unknown
            self._extract_features()
            return

        # 5. Doubtful variation (#*word) — checked before plain #
        if self._cfg_variation.get("hash_doubtful") and text.startswith("#*"):
            self.variation = df.tokenvariation.doubtful
            self.non_ita = True
            self.iso_code = "NO_ISO_CODE"
            text = text[2:]

        # 6. Token variation (#word)
        elif self._cfg_variation.get("hash_token") and text.startswith("#"):
            self.variation = df.tokenvariation.token
            self.non_ita = True
            self.iso_code = "NO_ISO_CODE"
            text = text[1:]

        # 7. Emerging variation ($word)
        elif self._cfg_variation.get("dollar") and text.startswith("$"):
            self.variation = df.tokenvariation.emerging
            self.non_ortho = True
            text = text[1:]

        # 8. Word regex
        in_variation_context = self.variation != df.tokenvariation.none

        word_re = re.compile(r"['~-]?(\p{L}+:*[-]?)*\p{L}+:*[-'~]?[.,?]?")
        po_re   = re.compile(r"po':*[.,?]?")

        if po_re.fullmatch(text):
            # po' variant: move the apostrophe after any trailing colons
            text, _ = re.subn(r"'(:*)", r"\1'", text)
            self.form = text
            self.token_type = df.tokentype.linguistic
            self._extract_features()
            return

        if word_re.fullmatch(text):
            self.form = text
            self.token_type = df.tokentype.linguistic
            self._extract_features()
            return

        # Fallthrough — error (or downgraded to warning if in variation context)
        self.form = text
        if in_variation_context:
            self.token_type = df.tokentype.warning
        else:
            self.token_type = df.tokentype.error

    # ------------------------------------------------------------------
    # Feature extraction (step 5d)
    # ------------------------------------------------------------------

    def _extract_features(self):
        """Extract prosodic and orthographic features from self.form.

        Applies to linguistic, unknown, and anonymized tokens.
        """
        text = self.form

        if self.token_type == df.tokentype.unknown:
            # Unknown tokens: only high-volume check; form stays as-is
            if any(c.isupper() for c in text):
                self.volume = df.volume.high
            self.form = text.lower()
            return

        # Intonation — consume final punctuation
        if text.endswith("."):
            self.intonation = df.intonation.falling
            text = text[:-1]
        elif text.endswith(","):
            self.intonation = df.intonation.weakly_rising
            text = text[:-1]
        elif text.endswith("?"):
            self.intonation = df.intonation.rising
            text = text[:-1]

        # Interruption — leading or trailing - / ~
        if text and (text[0] in "-~" or text[-1] in "-~"):
            self.interruption = True

        # Truncation — leading or trailing apostrophe (but not po')
        if text and (text[0] == "'" or text[-1] == "'"):
            stem_letters = "".join(c for c in text if c.isalpha())
            if stem_letters != "po":
                self.truncation = True

        # High volume — uppercase presence; then lowercase
        if any(c.isupper() for c in text):
            self.volume = df.volume.high
        text = text.lower()

        # Prolongations — record {char_index: colon_count}, strip colons
        if ":" in text:
            # Build a map from string position → form character index
            # (ignoring apostrophe, dash, tilde which are not form chars)
            _NON_FORM = set("'-~")
            form_idx = 0
            str_to_form: list[int] = []
            for ch in text:
                if ch == ":":
                    str_to_form.append(-1)
                elif ch in _NON_FORM:
                    str_to_form.append(-1)
                else:
                    str_to_form.append(form_idx)
                    form_idx += 1

            for m in re.finditer(r":+", text):
                start = m.start()
                # Find the form char immediately before this colon run
                char_idx = start - 1
                while char_idx >= 0 and str_to_form[char_idx] < 0:
                    char_idx -= 1
                if char_idx >= 0:
                    cidx = str_to_form[char_idx]
                    self.prolongations[cidx] = len(m.group())

            text = re.sub(r":+", "", text)

        self.form = text

    # ------------------------------------------------------------------
    # Feature setters called after tokenization
    # ------------------------------------------------------------------

    def set_prosodic_link(self):
        self.prosodiclink = True

    def set_space_after_no(self):
        self.spaceafter = False
        # apostrophe at end of l' etc. is not truncation
        if self.truncation:
            self.truncation = False

    def set_pause_after(self):
        self.pauseafter = True

    def set_language(self, iso_code: str):
        self.non_ita = True
        self.iso_code = iso_code

    def set_position(self, pos: df.position):
        self.position_in_tu = self.position_in_tu | pos


# ---------------------------------------------------------------------------
# Tokenization (step 5)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"['~-]?(\p{L}+:*[-]?)*\p{L}+:*[-'~]?[.,?]?")


def tokenize_tu(
    annotation: str,
    tu_id: int,
    variation_context: df.languagevariation = df.languagevariation.none,
    cfg_variation: dict | None = None,
) -> list[Token]:
    """Split a normalized TU annotation into tokens.

    Args:
        annotation:        normalized annotation string (output of preprocess step).
        tu_id:             TU identifier, used for debug context only.
        variation_context: TU-level variation flag (for post-tokenize language pass).
        cfg_variation:     variation_markers config dict.

    Returns:
        Ordered list of Token objects.
    """
    if cfg_variation is None:
        cfg_variation = {}

    # 5a. Split on spaces and prosodic links; keep delimiters.
    parts = re.split(r"( |=)", annotation)

    tokens: list[Token] = []
    char_pos = 0

    for part in parts:
        end_pos = char_pos + len(part)

        if part == " ":
            char_pos = end_pos
            continue

        if part == "=":
            if tokens:
                tokens[-1].set_prosodic_link()
            char_pos = end_pos
            continue

        # Apostrophe split: only when letters appear on both sides (Italian elision).
        apos_idx = part.find("'")
        if apos_idx != -1:
            prefix = part[:apos_idx]
            suffix = part[apos_idx + 1:]
            letter_before = any(c.isalpha() for c in prefix)
            letter_after  = any(c.isalpha() for c in suffix)

            if letter_before and letter_after:
                tok1_text = part[:apos_idx + 1]  # e.g. "l'"
                tok2_text = part[apos_idx + 1:]  # e.g. "albero"

                tok1 = Token(tok1_text, span=(char_pos, char_pos + len(tok1_text)),
                             _cfg_variation=cfg_variation)
                tok1.set_space_after_no()
                tokens.append(tok1)

                tok2 = Token(tok2_text, span=(char_pos + len(tok1_text), end_pos),
                             _cfg_variation=cfg_variation)
                tokens.append(tok2)

                char_pos = end_pos
                continue

        tok = Token(part, span=(char_pos, end_pos), _cfg_variation=cfg_variation)
        tokens.append(tok)
        char_pos = end_pos

    # 5e. Post-tokenize: language variation for #_-marked TUs
    if df.languagevariation.all in variation_context:
        for tok in tokens:
            tok.token_type = df.tokentype.linguistic
            tok.set_language("NO_ISO_CODE")

    # PauseAfter pass: mark tokens immediately before a shortpause
    for i, tok in enumerate(tokens):
        if tok.token_type == df.tokentype.shortpause and i > 0:
            tokens[i - 1].set_pause_after()

    # Position flags: first and last tokens
    if tokens:
        tokens[0].set_position(df.position.start)
        tokens[-1].set_position(df.position.end)

    return tokens
