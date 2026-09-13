"""
data.py — TranscriptionUnit and Transcript data structures.

Pipeline coverage:
    Step 2  — TranscriptionUnit.__post_init__  (preprocess / normalize)
    Step 3  — Transcript.sort
    Step 3b — Transcript.stretch_missing_overlaps  (optional, config-gated)
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


def _is_nvb_or_pause_token(t: Token) -> bool:
    """True for NVB and shortpause tokens — the two types whose participation
    in overlaps is gated together by ``overlaps.nvb_participates_in_overlaps``
    (shortpause is assimilated to NVB's behavior; see check_overlaps)."""
    return (df.tokentype.nonverbalbehavior in t.token_type
            or df.tokentype.shortpause in t.token_type)


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
            self.non_ita = df.languagevariation.unspecified
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
        # Update TU-level non_ita based on token-level flags. `unspecified`
        # (explicit TU-level "# " prefix, set in step 2b) is never downgraded
        # here — it must survive independently of per-token marks so
        # vert2eaf can tell it apart from `yes` (see vert_to_linear_rows).
        has_non_ita = any(t.non_ita for t in self.tokens)
        all_non_ita = bool(self.tokens) and all(t.non_ita for t in self.tokens)
        if all_non_ita:
            self.non_ita = df.languagevariation.all
        elif self.non_ita == df.languagevariation.unspecified:
            pass
        elif has_non_ita:
            self.non_ita = df.languagevariation.yes

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

                # NVB/shortpause tokens are invisible to the pass above: every
                # character of `(.)`/`((...))` is punctuation under
                # token_at/form_idx (dots and brackets are stripped as
                # non-form markers), so they never appear in `covered` even
                # when they sit inside the overlap span. Detect them directly
                # via their own character span instead. Unlike ordinary
                # tokens -- which can straddle an overlap boundary mid-word,
                # hence the letters-only sub-range above -- NVB/shortpause can
                # never be interrupted by `[`/`]` (that would be a malformed
                # annotation): they're always entirely inside or entirely
                # outside a span, so instead of a char range they get the "X"
                # sentinel (same convention as their `upos` value) meaning
                # "whole token", rather than a sub-range that would be
                # meaningless here.
                for tok in self.tokens:
                    if _is_nvb_or_pause_token(tok):
                        ts, te = tok.span
                        if ts < b and te > a:
                            tok.overlaps[match_id] = "X"

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
    # Step 3b — Stretch boundaries for annotated-but-missing overlaps
    # ------------------------------------------------------------------

    def stretch_missing_overlaps(self, stretch_threshold: float):
        """Nudge TU boundaries to create a real time overlap where one is
        annotated (`[...]` in the source) but missing purely because of a
        tiny gap between two TUs — e.g. an annotator set matching instead of
        overlapping timestamps. Without this, such a TU has no time-based
        overlap at all and ends up as an unresolvable OVERLAPS:MISSING_TIME
        error (see check_overlaps).

        Disabled by default (stretch_threshold <= 0 is a no-op) — this
        edits actual start/end timing, not just how it's interpreted, so
        it's opt-in per module via overlaps.stretch_threshold.

        Only acts when a TU with an annotated overlap span has *exactly
        one* immediate neighbor (by sorted start time) within
        stretch_threshold seconds and not already touching/overlapping.
        If both neighbors are in range, or the only nearby one is further
        than the threshold, nothing is changed — picking a partner among
        multiple candidates would be a guess, and is left for a human
        (still surfaces as OVERLAPS:MISSING_TIME / MISMATCHING_OVERLAPS).

        Must run after sort() and before find_overlaps(), so the stretched
        boundaries are picked up as a genuine time-based overlap.
        """
        if stretch_threshold <= 0:
            return

        units = self.transcription_units
        for i, tu in enumerate(units):
            if not tu.overlapping_spans:
                continue

            candidates = []
            if i > 0:
                prev = units[i - 1]
                gap = tu.start - prev.end
                if 0 <= gap <= stretch_threshold:
                    candidates.append(("prev", prev, gap))
            if i + 1 < len(units):
                nxt = units[i + 1]
                gap = nxt.start - tu.end
                if 0 <= gap <= stretch_threshold:
                    candidates.append(("next", nxt, gap))

            if len(candidates) != 1:
                continue

            direction, neighbor, gap = candidates[0]
            # Split the gap so the new overlap is symmetric; if the gap was
            # exactly 0 (touching, not overlapping), nudge by half the
            # configured threshold instead so a real overlap actually forms,
            # still bounded by what the module considers acceptable.
            half = gap / 2 if gap > 0 else stretch_threshold / 2
            if direction == "prev":
                tu.start -= half
                neighbor.end += half
            else:
                tu.end += half
                neighbor.start -= half
            tu.warnings["STRETCHED_BOUNDARIES"] += 1
            neighbor.warnings["STRETCHED_BOUNDARIES"] += 1

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

        # 6a. Remove manually ignored pairs.
        for u, v in relations_to_ignore:
            if self.time_based_overlaps.has_edge(u, v):
                logger.warning("Removing ignored edge %s-%s", u, v)
                self.time_based_overlaps.remove_edge(u, v)

        # 6b. Remove short unannotated overlaps; nudge TU boundaries.
        #
        # Note: NVB/shortpause-only TUs are *not* pruned from the graph here
        # (or anywhere before cliques are built) -- a genuinely annotated
        # `[...]` overlap span on such a TU always gets matched to its real
        # time-based partner and assigned a proper feature (see 6d), no
        # matter what nvb_participates_in_overlaps is set to. That flag only
        # decides, per overlap *event*, whether a clique that's entirely
        # NVB/shortpause counts as removable noise when there's no annotated
        # span to back it up (6d).
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

        # 6c. Cliques → overlap events.
        cliques = sorted(
            (c for c in nx.find_cliques(self.time_based_overlaps) if len(c) > 1),
            key=len,
        )
        self.overlap_events = {}

        for clique_id, clique in enumerate(cliques):
            starts = [self._tu_by_id[n].start for n in clique]
            ends   = [self._tu_by_id[n].end   for n in clique]
            nvb_or_pause_in_clique = any(
                any(_is_nvb_or_pause_token(t) for t in self._tu_by_id[n].tokens)
                for n in clique
            )
            overlap_start = max(starts)
            overlap_end   = min(ends)
            self.overlap_events[clique_id] = (overlap_start, overlap_end)

            for node in clique:
                partners = tuple(n for n in clique if n != node)
                self._tu_by_id[node].overlapping_times[partners] = (
                    overlap_start, overlap_end, clique_id, nvb_or_pause_in_clique
                )

        # 6d. Match annotated spans to overlap events.
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
                for el, (os, oe, cid, nvb_or_pause) in times.items():
                    tu.overlap_duration["+".join(str(x) for x in el)] = oe - os
                    if (nvb_or_pause and not nvb_participates) or (oe - os < duration_threshold):
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
                # More time-based overlap events than annotated spans: try to
                # reconcile by dropping the smallest-duration removable
                # events first (short/nvb-or-pause-ineligible ones are the
                # most likely to be incidental boundary touches rather than a
                # real annotated overlap), down to exactly as many events as
                # there are annotated spans.
                diff = n_times - n_spans
                removable = [
                    (oe - os, cid)
                    for el, (os, oe, cid, nvb_or_pause) in times.items()
                    if (oe - os < duration_threshold) or (nvb_or_pause and not nvb_participates)
                ]

                if len(removable) >= diff:
                    drop_ids = {cid for _, cid in sorted(removable)[:diff]}
                    sorted_times = sorted(times.items(), key=lambda kv: kv[1][0])
                    keep_ids = [kv[1][2] for kv in sorted_times
                                if kv[1][2] not in drop_ids]
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
