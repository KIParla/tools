"""
data.py — TranscriptionUnit and Transcript data structures.

Pipeline coverage:
    Step 2  — TranscriptionUnit.__post_init__  (preprocess / normalize)
    Step 3  — Transcript.sort
    Step 4  — Transcript.find_overlaps
    Step 5  — TranscriptionUnit.tokenize  (delegates to tokens.tokenize_tu)
    Step 6  — Transcript.check_overlaps
    Step 7  — TranscriptionUnit.add_token_features
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx
import regex as re

import dataflags as df
from normalize import validate_and_normalize, _mask_non_guess_parens, is_reduction_candidate_span as _is_reduction_candidate_span
from tokens import Token, tokenize_tu

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TranscriptionUnit  (steps 2, 5, 7)
# ---------------------------------------------------------------------------

@dataclass
class TranscriptionUnit:
    tu_id: int
    speaker: str
    start: float
    end: float
    duration: float
    annotation: str
    parent_tu_id: Optional[int] = None
    # Full pipeline config dict; used for normalization and tokenization.
    cfg: dict = field(default_factory=dict, repr=False)

    # Computed in __post_init__
    orig_annotation: str = field(init=False, default="")
    include: bool = field(init=False, default=True)
    non_ita: df.languagevariation = field(init=False, default=df.languagevariation.none)

    # Span positions (char offsets into the normalized annotation), step 2f.
    overlapping_spans: list[tuple[int, int]] = field(init=False, default_factory=list)
    slow_pace_spans:   list[tuple[int, int]] = field(init=False, default_factory=list)
    fast_pace_spans:   list[tuple[int, int]] = field(init=False, default_factory=list)
    low_volume_spans:  list[tuple[int, int]] = field(init=False, default_factory=list)
    high_volume_spans: list[tuple[int, int]] = field(init=False, default_factory=list)
    # Genuine "hard to understand" spans, e.g. (non lo so).
    guessing_spans:    list[tuple[int, int]] = field(init=False, default_factory=list)
    # Word-internal spans marking a phonetically reduced sound, e.g. c(io)è —
    # letters immediately before and after the parens, no whitespace inside.
    reduction_spans:   list[tuple[int, int]] = field(init=False, default_factory=list)

    # Populated by Transcript.check_overlaps (step 6).
    overlapping_times:   dict = field(init=False, default_factory=dict)
    overlapping_matches: dict = field(init=False, default_factory=dict)
    overlap_duration:    dict = field(init=False, default_factory=dict)

    warnings: dict[str, int]  = field(init=False, default_factory=lambda: collections.defaultdict(int))
    errors:   dict[str, bool] = field(init=False, default_factory=lambda: collections.defaultdict(bool))

    tokens: list[Token] = field(init=False, default_factory=list)

    # ------------------------------------------------------------------
    # Step 2 — Preprocess
    # ------------------------------------------------------------------

    def __post_init__(self):
        self.orig_annotation = self.annotation

        # 2a. Empty / exclude check.
        if not self.annotation or not self.annotation.strip():
            logger.info("TU %s: empty annotation, excluding", self.tu_id)
            self.include = False
            return

        self.annotation = self.annotation.strip()

        # 2b. TU-level language variation markers.
        if self.annotation.startswith("#_"):
            self.non_ita = df.languagevariation.all
            self.annotation = self.annotation[2:].strip()
            # Skip normalization for entirely non-Italian TUs.
            return

        if self.annotation.startswith("# "):
            self.non_ita = df.languagevariation.some
            self.annotation = self.annotation[1:].strip()

        # 2c–2g. Normalize, error-check, conditional fixes, symbol corrections.
        # validate_and_normalize covers: warning rules (SYMBOL_NOT_ALLOWED, META_TAGS,
        # UNEVEN_SPACES, TRIM_PAUSES, TRIM_PROSODICLINKS, OVERLAP_PROLONGATION,
        # MULTIPLE_SPACES, ACCENTS, NUMBERS, check_spaces_dots, check_spaces_angular,
        # SWITCHES, remove_empty_spans, flag_empty_unit) and error rules
        # (UNBALANCED_DOTS, UNBALANCED_PACE, UNBALANCED_GUESS, UNBALANCED_OVERLAP).
        norm_cfg = self.cfg.get("normalization", {})
        normalized, warnings, errors = validate_and_normalize(self.annotation, norm_cfg)

        for key, count in warnings.items():
            self.warnings[key] += count
        for key, has_error in errors.items():
            if has_error:
                self.errors[key] = True

        # 2h. All-symbol exclusion: flag_empty_unit returns "" when nothing remains.
        if not normalized:
            logger.info("TU %s: only symbols after normalization, excluding", self.tu_id)
            self.include = False
            return

        self.annotation = normalized

        # 2f. Span position extraction on the normalized annotation.
        if "<" in self.annotation and not self.errors.get("UNBALANCED_PACE"):
            self.slow_pace_spans = [
                (m.start(), m.end())
                for m in re.finditer(r"<[^<>]*>", self.annotation)
            ]
            self.fast_pace_spans = [
                (m.start(), m.end())
                for m in re.finditer(r">[^<>]*<", self.annotation)
            ]

        if "°" in self.annotation and not self.errors.get("UNBALANCED_DOTS"):
            self.low_volume_spans = [
                (m.start(), m.end())
                for m in re.finditer(r"°[^°]+°", self.annotation)
            ]

        hv_matches = list(re.finditer(
            r"\b[A-ZÀÈÉÌÒÓÙ]+(?:\s+[A-ZÀÈÉÌÒÓÙ]+)*\b", self.annotation
        ))
        if hv_matches:
            self.high_volume_spans = [(m.start(), m.end()) for m in hv_matches]

        if "[" in self.annotation and not self.errors.get("UNBALANCED_OVERLAP"):
            self.overlapping_spans = [
                (m.start(), m.end())
                for m in re.finditer(r"\[[^\]]+\]", self.annotation)
            ]

        if "(" in self.annotation and not self.errors.get("UNBALANCED_GUESS"):
            masked = _mask_non_guess_parens(self.annotation)
            for m in re.finditer(r"\([^)]+\)", masked):
                start, end = m.start(), m.end()
                if _is_reduction_candidate_span(self.annotation, start, end):
                    self.reduction_spans.append((start, end))
                else:
                    self.guessing_spans.append((start, end))

    # ------------------------------------------------------------------
    # Step 5 — Tokenize
    # ------------------------------------------------------------------

    def tokenize(self, cfg: dict | None = None):
        """Tokenize the normalized annotation."""
        if not self.include:
            return
        if cfg is None:
            cfg = self.cfg
        self.tokens = tokenize_tu(
            self.annotation,
            tu_id=self.tu_id,
            variation_context=self.non_ita,
            cfg_variation=cfg.get("variation_markers", {}),
        )
        # Update TU-level non_ita based on token-level flags.
        has_non_ita = any(t.non_ita for t in self.tokens)
        all_non_ita = bool(self.tokens) and all(t.non_ita for t in self.tokens)
        if all_non_ita:
            self.non_ita = df.languagevariation.all
        elif has_non_ita:
            self.non_ita = df.languagevariation.some

    # ------------------------------------------------------------------
    # Step 7 — Map span features to tokens
    # ------------------------------------------------------------------

    def add_token_features(self):
        """Map TU-level span positions to individual tokens."""
        if not self.tokens:
            return

        # Build a character-level index over all token orig_text values.
        # For annotation char position i:
        #   token_at[i] = list index of the owning token
        #                 (-1 = colon/punctuation, -2 = bracket marker, -3 = inter-token)
        #   form_idx[i] = form-char index within that token (-1 for non-form chars)
        token_at: list[int] = []
        form_idx: list[int] = []

        for tok_i, tok in enumerate(self.tokens):
            fi = 0
            for ch in tok.orig_text:
                if ch in ":.,?":
                    token_at.append(-1)
                    form_idx.append(-1)
                elif ch in "[]()<>°":
                    token_at.append(-2)
                    form_idx.append(-2)
                else:
                    token_at.append(tok_i)
                    form_idx.append(fi)
                    fi += 1
            # Sentinel between tokens.
            token_at.append(-3)
            form_idx.append(-3)

        def _char_ranges_for(a: int, b: int) -> dict[int, tuple[int, int]]:
            """Map a (start, end) char span in the annotation to per-token
            (cs, ce) form-char ranges, for each token the span touches."""
            pairs = list(zip(token_at[a:b], form_idx[a:b]))
            covered = {ti for ti, _ in pairs if ti >= 0}
            char_ranges: dict[int, list[int]] = {ti: [] for ti in covered}
            for ti, fi in pairs:
                if ti in char_ranges:
                    char_ranges[ti].append(fi)
            return {ti: (min(pos), max(pos) + 1) for ti, pos in char_ranges.items()}

        def _apply(feature: str, spans: list[tuple[int, int]]):
            for span_id, (a, b) in enumerate(spans):
                for ti, (cs, ce) in _char_ranges_for(a, b).items():
                    tok = self.tokens[ti]
                    if feature == "slow_pace":
                        tok.slow_pace[span_id] = (cs, ce)
                    elif feature == "fast_pace":
                        tok.fast_pace[span_id] = (cs, ce)
                    elif feature == "low_volume":
                        tok.low_volume[span_id] = (cs, ce)
                        tok.volume = df.volume.low
                    elif feature == "guesses":
                        tok.guesses[span_id] = (cs, ce)

        _apply("slow_pace",  self.slow_pace_spans)
        _apply("fast_pace",  self.fast_pace_spans)
        _apply("low_volume", self.low_volume_spans)
        # high_volume is detected per-token in Token._classify; no dict on Token.
        _apply("guesses",    self.guessing_spans)

        # Reduction candidates (e.g. c(io)è, or a multi-token contraction like
        # m(e l)o — "me lo" reduced across a word boundary) are only genuine
        # phonetic reduction if the reconstructed word/phrase is on the
        # module's configured reduction_words whitelist; otherwise they fall
        # back to ordinary guess spans on each touched token.
        reduction_words = {w.lower() for w in self.cfg.get("reduction_words", [])}
        for i, (a, b) in enumerate(self.reduction_spans):
            ranges = _char_ranges_for(a, b)
            touched = sorted(ranges)  # token order, not set-iteration order
            phrase = " ".join(self.tokens[ti].form for ti in touched)
            # Fallback span ids continue past guessing_spans' numbering so they
            # can't collide with a real guess span id on the same token.
            fallback_span_id = len(self.guessing_spans) + i
            if phrase.lower() in reduction_words:
                for ti in touched:
                    self.tokens[ti].reduced = True
            else:
                for ti in touched:
                    self.tokens[ti].guesses[fallback_span_id] = ranges[ti]

        # Overlaps use match_id (clique id) as the key, not span index.
        if self.overlapping_matches:
            for span, match_id in self.overlapping_matches.items():
                a, b = span
                pairs = list(zip(token_at[a:b], form_idx[a:b]))
                covered = {ti for ti, _ in pairs if ti >= 0}
                char_ranges = {ti: [] for ti in covered}
                for ti, fi in pairs:
                    if ti in char_ranges:
                        char_ranges[ti].append(fi)
                for ti, positions in char_ranges.items():
                    self.tokens[ti].overlaps[match_id] = (min(positions), max(positions) + 1)

        # Position flags: first and last token of TU.
        self.tokens[0].set_position(df.position.start)
        self.tokens[-1].set_position(df.position.end)


# ---------------------------------------------------------------------------
# Transcript  (steps 1, 3, 4, 6)
# ---------------------------------------------------------------------------

@dataclass
class Transcript:
    tr_id: str
    speakers: dict[str, int] = field(default_factory=dict)
    _tu_by_id: dict[int, TranscriptionUnit] = field(default_factory=dict, repr=False)
    transcription_units: list[TranscriptionUnit] = field(default_factory=list)
    tot_length: float = 0.0
    time_based_overlaps: nx.Graph = field(default_factory=nx.Graph)
    overlap_events: dict[int, tuple[float, float]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Step 1 — Add TUs
    # ------------------------------------------------------------------

    def add(self, tu: TranscriptionUnit):
        if tu.speaker not in self.speakers:
            self.speakers[tu.speaker] = 0
        if tu.include:
            self.speakers[tu.speaker] += 1
        self._tu_by_id[tu.tu_id] = tu

    # ------------------------------------------------------------------
    # Step 3 — Sort
    # ------------------------------------------------------------------

    def sort(self):
        self.transcription_units = sorted(
            self._tu_by_id.values(), key=lambda tu: tu.start
        )
        if self.transcription_units:
            self.tot_length = self.transcription_units[-1].end

    # ------------------------------------------------------------------
    # Step 4 — Find time-based overlaps
    # ------------------------------------------------------------------

    def find_overlaps(self, duration_threshold: float = 0.0):
        G = nx.Graph()
        tus = [tu for tu in self.transcription_units if tu.include]

        for i, tu1 in enumerate(tus):
            for tu2 in tus[i + 1:]:
                if tu1.end > tu2.start and tu2.end > tu1.start:
                    if tu1.tu_id not in G:
                        G.add_node(tu1.tu_id, speaker=tu1.speaker,
                                   overlaps=tu1.overlapping_spans)
                    if tu2.tu_id not in G:
                        G.add_node(tu2.tu_id, speaker=tu2.speaker,
                                   overlaps=tu2.overlapping_spans)
                    start = max(tu1.start, tu2.start)
                    end   = min(tu1.end,   tu2.end)
                    G.add_edge(tu1.tu_id, tu2.tu_id,
                               start=start, end=end, duration=end - start)

        self.time_based_overlaps = G

    # ------------------------------------------------------------------
    # Step 6 — Resolve overlaps
    # ------------------------------------------------------------------

    def check_overlaps(
        self,
        duration_threshold: float,
        relations_to_ignore: list[tuple] | None = None,
        nvb_participates: bool = False,
    ):
        if relations_to_ignore is None:
            relations_to_ignore = []

        # 6a. Remove NVB-only edges (unless nvb_participates is True).
        if not nvb_participates:
            to_remove = [
                (u, v)
                for u, v in self.time_based_overlaps.edges()
                if (all(df.tokentype.nonverbalbehavior in t.token_type
                        for t in self._tu_by_id[u].tokens) or
                    all(df.tokentype.nonverbalbehavior in t.token_type
                        for t in self._tu_by_id[v].tokens))
            ]
            for u, v in to_remove:
                logger.warning("Removing NVB edge %s-%s", u, v)
                self.time_based_overlaps.remove_edge(u, v)

        # 6b. Remove manually ignored pairs.
        for u, v in relations_to_ignore:
            if self.time_based_overlaps.has_edge(u, v):
                logger.warning("Removing ignored edge %s-%s", u, v)
                self.time_based_overlaps.remove_edge(u, v)

        # 6c. Remove short unannotated overlaps; nudge TU boundaries.
        to_remove = []
        for u, v in list(self.time_based_overlaps.edges()):
            edge = self.time_based_overlaps[u][v]
            tu_u = self._tu_by_id[u]
            tu_v = self._tu_by_id[v]
            if (edge["duration"] < duration_threshold and
                    not tu_u.overlapping_spans and not tu_v.overlapping_spans):
                half = edge["duration"] / 2
                min_tu, max_tu = sorted([tu_u, tu_v], key=lambda t: t.tu_id)
                min_tu.end   -= half
                max_tu.start += half
                min_tu.warnings["MOVED_BOUNDARIES"] += 1
                max_tu.warnings["MOVED_BOUNDARIES"] += 1
                to_remove.append((u, v))
        for u, v in to_remove:
            logger.warning("Removing short unannotated overlap %s-%s", u, v)
            self.time_based_overlaps.remove_edge(u, v)

        # 6d. Cliques → overlap events.
        cliques = sorted(
            (c for c in nx.find_cliques(self.time_based_overlaps) if len(c) > 1),
            key=len,
        )
        self.overlap_events = {}

        for clique_id, clique in enumerate(cliques):
            starts = [self._tu_by_id[n].start for n in clique]
            ends   = [self._tu_by_id[n].end   for n in clique]
            nvb_in_clique = any(
                any(df.tokentype.nonverbalbehavior in t.token_type
                    for t in self._tu_by_id[n].tokens)
                for n in clique
            )
            overlap_start = max(starts)
            overlap_end   = min(ends)
            self.overlap_events[clique_id] = (overlap_start, overlap_end)

            for node in clique:
                partners = tuple(n for n in clique if n != node)
                self._tu_by_id[node].overlapping_times[partners] = (
                    overlap_start, overlap_end, clique_id, nvb_in_clique
                )

        # 6e. Match annotated spans to overlap events.
        for tu in self._tu_by_id.values():
            spans   = tu.overlapping_spans
            times   = tu.overlapping_times
            n_spans = len(spans)
            n_times = len(times)

            if n_spans == n_times:
                sorted_times = sorted(times.items(), key=lambda kv: kv[1][0])
                tu.overlapping_matches = dict(
                    zip(spans, (kv[1][2] for kv in sorted_times))
                )

            elif n_spans == 0:
                # Record durations and check which events are removable.
                removable_ids: set[int] = set()
                for el, (os, oe, cid, nvb) in times.items():
                    tu.overlap_duration["+".join(str(x) for x in el)] = oe - os
                    if (nvb and not nvb_participates) or (oe - os < duration_threshold):
                        removable_ids.add(cid)

                all_clique_ids = {v[2] for v in times.values()}
                if removable_ids >= all_clique_ids:
                    tu.warnings["MISMATCHING_OVERLAPS"] = True
                else:
                    tu.errors["OVERLAPS:MISSING_ANNOTATION"] = True

            elif n_times == 0:
                tu.errors["OVERLAPS:MISSING_TIME"] = True
                tu.overlapping_matches = {span: "?" for span in spans}

            elif n_times > n_spans:
                diff = n_times - n_spans
                removable_ids = set()
                for el, (os, oe, cid, nvb) in times.items():
                    if (oe - os < duration_threshold) or (nvb and not nvb_participates):
                        removable_ids.add(cid)

                if len(removable_ids) == diff:
                    sorted_times = sorted(times.items(), key=lambda kv: kv[1][0])
                    keep_ids = [kv[1][2] for kv in sorted_times
                                if kv[1][2] not in removable_ids]
                    tu.overlapping_matches = dict(zip(spans, keep_ids))
                    tu.warnings["MISMATCHING_OVERLAPS"] = True
                else:
                    tu.errors["MISMATCHING_OVERLAPS"] = True
                    tu.overlapping_matches = {span: "?" for span in spans}
                    for el, (os, oe, _, _) in times.items():
                        tu.overlap_duration["+".join(str(x) for x in el)] = oe - os

            else:
                tu.errors["MISMATCHING_OVERLAPS"] = True
                tu.overlapping_matches = {span: "?" for span in spans}
                for el, (os, oe, _, _) in times.items():
                    tu.overlap_duration["+".join(str(x) for x in el)] = oe - os

    def __iter__(self):
        return iter(self.transcription_units)
