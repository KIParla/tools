#!/usr/bin/env python3
"""
whisperx_align.py — align a WhisperX transcription with a KIParla .vert.tsv
using Needleman-Wunsch, add the WhisperX word timestamps, and compute the WER.

Adapted from intonation-cues-whisper/src/align_nw.py (same normalization,
same output layout), extended to use WhisperX's word-level timestamps and to
report the WER.

Only 'linguistic' rows of the TSV are aligned. Normalization for comparison:
lowercase, accent-strip, punctuation-strip (so `l~` matches `l`). Every other
row (shortpause, nonverbal, unknown, ... and rows typed 'linguistic' whose form
has no letter or digit) is written as `skipped` and ignored by the WER.

Timestamps: WhisperX word `start`/`end` (needs the forced-alignment step, i.e.
not `--no_align`). The TSV splits elisions (`l'` `ho`) that WhisperX keeps in
one word (`l'ho`): WhisperX words are split after the apostrophe and the time
span is shared out by character length. Words WhisperX could not time (numbers,
"%", "€"...) are interpolated between their timed neighbours.
A plain Whisper JSON (segments without `words`) is also accepted: timestamps
are then interpolated linearly across each segment, as in align_nw.py.

Output TSV, one row per TSV row (in the TSV's order) plus the WhisperX words
that matched nothing:
  token_id, tu_id, speaker, form
                            copied from the TSV ("_" on whisper_only rows)
  tsv_begin, tsv_end        Begin=/End= of the TSV's `align` column: only the
                            first / last token of a unit carries them, "_" otherwise
  whisper_segment           index of the WhisperX segment of the aligned word
  whisper_speaker           speaker label predicted by WhisperX diarization of the
                            aligned word (SPEAKER_00...), "_" if there is none
  whisper_speaker_mapped    the same speaker as a KIParla speaker code (see below),
                            "_" where WhisperX has no speaker or no code could be assigned
  whisper_text              the aligned WhisperX word (lowercase, no punctuation)
  whisper_begin             start (s) of the aligned WhisperX word
  whisper_end               end (s) of the aligned WhisperX word
  alignment                 match | mismatch | whisper_only | tsv_only | skipped
  whisper_error_text        `whisper_text` on mismatch and whisper_only rows only, "_" on the
                            others: what WhisperX heard where it disagrees with the TSV
  whisper_score             WhisperX confidence of the aligned word (the `score` of its forced
                            alignment, 0-1); "_" where WhisperX gave none (words timed by
                            interpolation) and on rows with no WhisperX word
  whisper_avg_logprob       `avg_logprob` of the WhisperX segment of the aligned word: the average
                            log-probability of the transcription (<= 0, closer to 0 is more
                            confident). One value per segment, shared by its words; "_" if the
                            JSON has none
and, unless disabled (`--no-correction`), the columns of the timestamp correction below.

Timestamp correction: the WhisperX times drift against the TSV's (shifts and stretches of
seconds, irregularly along a recording), while the TSV times exist only at unit boundaries
(`tsv_begin` on the first token of a unit, `tsv_end` on the last). Each unit (`tu_id`) is
corrected locally, anchored on its boundary tokens. A boundary is reliable when the unit's first
(last) token is a `match` with a WhisperX time and a `tsv_begin` (`tsv_end`).
  whisper_begin_adj, whisper_end_adj
                            rule 1: both boundaries reliable and at least MIN_MATCH_SHARE of the
                            unit's tokens are `match`. A unit of MIN_STRETCH_TOKENS tokens or more is
                            shifted and stretched so that begin and end equal `tsv_begin` and
                            `tsv_end`; a shorter one is only shifted, by the mean of the two
                            boundary offsets
  whisper_begin_partial, whisper_end_partial
                            rule 2: exactly one boundary reliable; the unit is only shifted by that
                            boundary's offset
  adj_rule                  both_stretch | both_shift | first | last | "_" (unit not corrected)
  gap_even_begin, gap_even_end, gap_chars_begin, gap_chars_end
                            tokens with no WhisperX time (`tsv_only`, and the `skipped` ones) of a
                            corrected unit, placed in the gap between their corrected neighbours (at
                            the unit's edges, bounded by its `tsv_begin` / `tsv_end` if it has one).
                            Words (tsv_only, x, @..., truncated forms) share what is left of the
                            gap, in equal shares (`even`) or in proportion to the length of the form
                            (`chars`). A pause `(.)` lasts PAUSE_MIN seconds (the whole gap, at least
                            PAUSE_MIN each, if the run holds no words). Events `((ride))` get no time
Nothing is interpolated for the other tokens: they get "_". Tokens, the original columns and
the WER are not affected.

Speaker mapping (per conversation): the WhisperX labels (SPEAKER_00...) mean nothing
across conversations, so each is assigned to one TSV speaker. The `match` tokens
give a confusion table (WhisperX label x TSV speaker); the one-to-one assignment
with the most agreeing tokens wins (all permutations up to a size limit, greedy
beyond it). Unknown speakers (`???`) are left out. Extra WhisperX labels stay
unmapped. The share of agreeing tokens is the mapping's quality: low values flag
a bad diarization, a speaker count that differs from the metadata, or heavy overlap.

WER, with the TSV linguistic tokens as reference and WhisperX as hypothesis:
  (substitutions + deletions + insertions) / reference tokens
  substitutions = mismatch, deletions = tsv_only, insertions = whisper_only,
  reference tokens = match + mismatch + tsv_only.

Usage:
    python whisperx_align.py <whisperx.json> <corpus.vert.tsv> [-o output.tsv]
"""

from __future__ import annotations

import argparse
import collections
import csv
import itertools
import json
import math
import pathlib
import sys
import unicodedata
from dataclasses import dataclass
from typing import Optional

import regex as re
from sequence_align.pairwise import needleman_wunsch

from textutil import ENCODING_READ, ENCODING_WRITE, nfc

MATCH = 2
MISMATCH = -1
GAP = -1
GAP_SYMBOL = "<GAP>"  # normalized forms are alphanumeric only: no collision

EMPTY = "_"
OUTPUT_COLUMNS = [
    "token_id", "tu_id", "speaker", "form", "tsv_begin", "tsv_end",
    "whisper_segment", "whisper_speaker", "whisper_speaker_mapped", "whisper_text",
    "whisper_begin", "whisper_end", "alignment", "whisper_error_text",
    "whisper_score", "whisper_avg_logprob",
]
ERROR_ALIGNMENTS = ("mismatch", "whisper_only")

CORRECTION_COLUMNS = [
    "whisper_begin_adj", "whisper_end_adj", "whisper_begin_partial", "whisper_end_partial",
    "adj_rule", "gap_even_begin", "gap_even_end", "gap_chars_begin", "gap_chars_end",
]
MIN_MATCH_SHARE = 0.5   # rule 1: share of `match` tokens a unit needs
MIN_STRETCH_TOKENS = 6  # shorter units are shifted, not stretched
PAUSE_MIN = 0.1         # seconds a `(.)` lasts at least

_ALIGN_TIME = re.compile(r"\b(Begin|End)=([0-9.]+)")
_UNKNOWN_SPEAKER = re.compile(r"\?+")
_COL = {name: i for i, name in enumerate(OUTPUT_COLUMNS)}
_MAX_PERMUTATIONS = 200_000  # beyond this the assignment is greedy

# l'ho -> l' ho  (split after an apostrophe followed by a letter)
_ELISION_SPLIT = re.compile(r"(?<=\p{L}['’])(?=\p{L})")


@dataclass
class WhisperWord:
    display: str   # lowercase, all punctuation stripped
    norm: str      # for NW comparison: also accent-stripped
    segment: str   # index of the Whisper segment this word belongs to
    start: Optional[float]
    end: Optional[float]
    score: Optional[float] = None
    speaker: Optional[str] = None
    avg_logprob: Optional[float] = None


@dataclass
class TSVToken:
    row: dict        # full original row
    norm: str        # for NW comparison
    index: int       # position in the TSV
    alignable: bool  # linguistic, and with something to compare


def normalize(text: str) -> str:
    """Normalize for NW comparison: lowercase, accent-strip, punctuation-strip."""
    text = unicodedata.normalize("NFD", nfc(text).lower().strip())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\p{L}\p{N}]", "", text)


def whisper_display(raw: str) -> str:
    """Lowercase, punctuation stripped."""
    return re.sub(r"[^\p{L}\p{N}]", "", nfc(raw).lower())


def _split_word(raw: str, start, end, score, speaker, segment, avg_logprob=None) -> list[WhisperWord]:
    """One WhisperX word -> one WhisperWord per elision piece."""
    pieces = [p for p in _ELISION_SPLIT.split(nfc(raw)) if p]
    pieces = [(p, normalize(p)) for p in pieces]
    pieces = [(p, n) for p, n in pieces if n]
    total = sum(len(n) for _, n in pieces)
    out, offset = [], 0
    for piece, norm in pieces:
        if start is not None and end is not None:
            p_start = start + (end - start) * offset / total
            p_end = start + (end - start) * (offset + len(norm)) / total
        else:
            p_start = p_end = None
        offset += len(norm)
        out.append(WhisperWord(whisper_display(piece), norm, segment,
                               p_start, p_end, score, speaker, avg_logprob))
    return out


def _interpolate_untimed(words: list[WhisperWord]) -> None:
    """Give timestamps to words WhisperX left untimed, from their neighbours."""
    i, n = 0, len(words)
    while i < n:
        if words[i].start is not None:
            i += 1
            continue
        j = i
        while j < n and words[j].start is None:
            j += 1
        prev_end = words[i - 1].end if i > 0 else None
        next_start = words[j].start if j < n else None
        if prev_end is None:
            prev_end = next_start
        if next_start is None:
            next_start = prev_end
        if prev_end is not None:
            step = (next_start - prev_end) / (j - i)
            for k in range(i, j):
                words[k].start = prev_end + step * (k - i)
                words[k].end = prev_end + step * (k - i + 1)
                words[k].score = None
        i = j


def load_whisper(path: str) -> list[WhisperWord]:
    with open(path, encoding=ENCODING_READ) as f:
        data = json.load(f)
    segments = data["segments"] if isinstance(data, dict) else data
    words: list[WhisperWord] = []
    for idx, seg in enumerate(segments):
        segment = str(seg.get("id", idx))
        if seg.get("words"):
            for w in seg["words"]:
                words.extend(_split_word(
                    w["word"], w.get("start"), w.get("end"),
                    w.get("score"), w.get("speaker", seg.get("speaker")), segment,
                    seg.get("avg_logprob")))
        else:
            # plain Whisper: spread the segment's span evenly over its words
            seg_words = seg.get("text", "").split()
            n = len(seg_words)
            duration = seg["end"] - seg["start"]
            for i, w in enumerate(seg_words):
                words.extend(_split_word(
                    w, seg["start"] + i * duration / n,
                    seg["start"] + (i + 1) * duration / n,
                    None, seg.get("speaker"), segment, seg.get("avg_logprob")))
    _interpolate_untimed(words)
    return words


def load_tsv(path: str) -> list[TSVToken]:
    tokens = []
    with open(path, encoding=ENCODING_READ, newline="") as f:
        for i, row in enumerate(csv.DictReader(f, delimiter="\t")):
            norm = normalize(row["form"]) if row["type"] == "linguistic" else ""
            tokens.append(TSVToken(row=dict(row), norm=norm, index=i, alignable=bool(norm)))
    return tokens


def run_needleman_wunsch(seq1, seq2) -> list[tuple[Optional[int], Optional[int]]]:
    """Align two norm sequences; return index pairs (None = gap)."""
    if not seq1 or not seq2:
        return ([(i, None) for i in range(len(seq1))]
                + [(None, j) for j in range(len(seq2))])
    print(f"Running Needleman-Wunsch (n={len(seq1)}, m={len(seq2)})...",
          file=sys.stderr, flush=True)
    al1, al2 = needleman_wunsch(
        seq1, seq2, match_score=MATCH, mismatch_score=MISMATCH,
        indel_score=GAP, gap=GAP_SYMBOL)
    pairs, i1, i2 = [], 0, 0
    for x, y in zip(al1, al2):
        a = None if x == GAP_SYMBOL else i1
        b = None if y == GAP_SYMBOL else i2
        i1 += a is not None
        i2 += b is not None
        pairs.append((a, b))
    return pairs


def compute_wer(stats: dict) -> float:
    """WER from alignment counts; the TSV is the reference."""
    ref = stats["match"] + stats["mismatch"] + stats["tsv_only"]
    errors = stats["mismatch"] + stats["tsv_only"] + stats["whisper_only"]
    return errors / ref if ref else 0.0


def _time(value: Optional[float]) -> str:
    return f"{value:.3f}" if value is not None else EMPTY


def tsv_times(align: str) -> tuple[str, str]:
    """(begin, end) from an `align` cell: "_", "Begin=1.2", "End=3.4" or "Begin=1.2|End=3.4"."""
    found = dict(_ALIGN_TIME.findall(align or ""))
    return found.get("Begin", EMPTY), found.get("End", EMPTY)


def build_rows(pairs, whisper_words, all_tokens):
    """Return (output rows as lists, stats). Skipped rows keep the TSV order."""
    aligned = [t for t in all_tokens if t.alignable]
    pending = [t for t in all_tokens if not t.alignable]  # TSV order
    stats = {"match": 0, "mismatch": 0, "whisper_only": 0, "tsv_only": 0,
             "skipped": len(pending)}
    rows = []

    def score_cell(w):
        return f"{w.score:.3f}" if w.score is not None else EMPTY

    def logprob_cell(w):
        return f"{w.avg_logprob:.4f}" if w.avg_logprob is not None else EMPTY

    def tsv_row(t, segment, speaker, text, begin, end, aln, score=EMPTY, logprob=EMPTY):
        t_begin, t_end = tsv_times(t.row.get("align", ""))
        return [t.row.get("token_id", ""), t.row.get("tu_id", ""), t.row.get("speaker", ""),
                t.row.get("form", ""), t_begin, t_end, segment, speaker, EMPTY, text, begin, end, aln,
                text if aln in ERROR_ALIGNMENTS else EMPTY, score, logprob]

    for a, b in pairs:
        if b is not None:  # emit the skipped rows that precede this token
            while pending and pending[0].index < aligned[b].index:
                rows.append(tsv_row(pending.pop(0), EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, "skipped"))
        if a is not None and b is not None:
            w, t = whisper_words[a], aligned[b]
            aln = "match" if w.norm == t.norm else "mismatch"
            rows.append(tsv_row(t, w.segment, w.speaker or EMPTY, w.display or EMPTY,
                                _time(w.start), _time(w.end), aln, score_cell(w), logprob_cell(w)))
        elif a is not None:
            w = whisper_words[a]
            aln = "whisper_only"
            rows.append([EMPTY] * 6 + [w.segment, w.speaker or EMPTY, EMPTY, w.display or EMPTY,
                                  _time(w.start), _time(w.end), aln, w.display or EMPTY, score_cell(w), logprob_cell(w)])
        else:
            aln = "tsv_only"
            rows.append(tsv_row(aligned[b], EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, aln))
        stats[aln] += 1
    for t in pending:
        rows.append(tsv_row(t, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, "skipped"))
    return rows, stats


def map_speakers(rows: list[list[str]]) -> tuple[dict[str, str], int, int]:
    """Map WhisperX speaker labels to TSV speakers using the `match` rows.

    Returns (mapping, agreeing_tokens, tokens_used).
    """
    confusion: collections.Counter = collections.Counter()
    for r in rows:
        w, t = r[_COL["whisper_speaker"]], r[_COL["speaker"]]
        if (r[_COL["alignment"]] == "match" and w != EMPTY
                and t not in (EMPTY, "") and not _UNKNOWN_SPEAKER.fullmatch(t)):
            confusion[(w, t)] += 1
    used = sum(confusion.values())
    if not used:
        return {}, 0, 0

    wspk = sorted({w for w, _ in confusion})
    tspk = sorted({t for _, t in confusion})
    best: dict[str, str] = {}
    if math.perm(max(len(wspk), len(tspk)), min(len(wspk), len(tspk))) <= _MAX_PERMUTATIONS:
        best_score = -1
        if len(wspk) <= len(tspk):
            for perm in itertools.permutations(tspk, len(wspk)):
                score = sum(confusion[(w, t)] for w, t in zip(wspk, perm))
                if score > best_score:
                    best_score, best = score, dict(zip(wspk, perm))
        else:
            for perm in itertools.permutations(wspk, len(tspk)):
                score = sum(confusion[(w, t)] for w, t in zip(perm, tspk))
                if score > best_score:
                    best_score, best = score, {w: t for w, t in zip(perm, tspk)}
    else:  # greedy: biggest cell first
        taken_w, taken_t = set(), set()
        for (w, t), _n in confusion.most_common():
            if w not in taken_w and t not in taken_t:
                best[w] = t
                taken_w.add(w)
                taken_t.add(t)
    agreeing = sum(confusion[(w, t)] for w, t in best.items())
    return best, agreeing, used


def apply_speaker_mapping(rows: list[list[str]]) -> dict:
    """Fill `whisper_speaker_mapped` in place; return the mapping summary."""
    mapping, agreeing, used = map_speakers(rows)
    for r in rows:
        r[_COL["whisper_speaker_mapped"]] = mapping.get(r[_COL["whisper_speaker"]], EMPTY)
    return {
        "speaker_map": ";".join(f"{w}={t}" for w, t in sorted(mapping.items())),
        "speaker_tokens": used,
        "speaker_agreement": agreeing / used if used else None,
    }


def _gap_role(rec: dict) -> Optional[str]:
    """How an untimed token takes part in a gap: word, pause, event (no time) or None."""
    if rec["alignment"] == "tsv_only":
        return "word"
    if rec["alignment"] != "skipped":
        return None
    form = rec["form"]
    if form == "(.)":
        return "pause"
    if form.startswith("(("):
        return "event"  # ((ride)) etc.: duration unknown, left without a time
    return "word"  # tsv_only-like: x, xxx, @nomeluogo, dialect or truncated forms


def _fill_gaps(toks: list[dict]) -> None:
    """Place the untimed tokens of one corrected unit in the gap_* columns (see module doc)."""
    def corrected(t, which):
        v = t[f"whisper_{which}_adj"]
        return t[f"whisper_{which}_partial"] if v == EMPTY else v

    i = 0
    while i < len(toks):
        if _gap_role(toks[i]) is None or corrected(toks[i], "begin") != EMPTY:
            i += 1
            continue
        j = i
        while j < len(toks) and _gap_role(toks[j]) is not None and corrected(toks[j], "begin") == EMPTY:
            j += 1
        lo = corrected(toks[i - 1], "end") if i else toks[0]["tsv_begin"]
        hi = corrected(toks[j], "begin") if j < len(toks) else toks[-1]["tsv_end"]
        run = [t for t in toks[i:j] if _gap_role(t) != "event"]
        i = j
        if lo == EMPTY or hi == EMPTY or not run:
            continue
        lo, hi = float(lo), float(hi)
        gap = max(0.0, hi - lo)
        words = [t for t in run if _gap_role(t) == "word"]
        pauses = [t for t in run if _gap_role(t) == "pause"]
        if words:
            pause_dur = PAUSE_MIN
            room = max(0.0, gap - PAUSE_MIN * len(pauses))
        else:
            pause_dur = max(gap, PAUSE_MIN * len(pauses)) / len(pauses)
            room = 0.0
        for name in ("even", "chars"):
            def weight(t):
                return 1 if name == "even" else max(1, len(t["form"]))
            total = sum(weight(t) for t in words)
            cursor = lo
            for t in run:
                dur = pause_dur if _gap_role(t) == "pause" else room * weight(t) / total
                t[f"gap_{name}_begin"] = f"{cursor:.3f}"
                cursor += dur
                t[f"gap_{name}_end"] = f"{cursor:.3f}"


def correct_timestamps(rows: list[list[str]]) -> dict:
    """Append the CORRECTION_COLUMNS to every row (see module doc); return unit counts."""
    recs = [dict(zip(OUTPUT_COLUMNS, r), **{c: EMPTY for c in CORRECTION_COLUMNS}) for r in rows]
    units: dict[str, list[dict]] = {}
    for rec in recs:
        if rec["tu_id"] != EMPTY:  # whisper_only rows belong to no unit
            units.setdefault(rec["tu_id"], []).append(rec)

    def reliable(tok, whisper, tsv):
        return tok["alignment"] == "match" and tok[whisper] != EMPTY and tok[tsv] != EMPTY

    n_full = n_partial = 0
    for toks in units.values():
        first, last = toks[0], toks[-1]
        has_first = reliable(first, "whisper_begin", "tsv_begin")
        has_last = reliable(last, "whisper_end", "tsv_end")
        if has_first:
            wb, tb = float(first["whisper_begin"]), float(first["tsv_begin"])
        if has_last:
            we, te = float(last["whisper_end"]), float(last["tsv_end"])
        share = sum(t["alignment"] == "match" for t in toks) / len(toks)
        if has_first and has_last and share >= MIN_MATCH_SHARE and we > wb and te > tb:
            stretch = len(toks) >= MIN_STRETCH_TOKENS
            shift = (tb - wb + te - we) / 2
            rule = "both_stretch" if stretch else "both_shift"
            n_full += 1
        elif has_first != has_last:  # exactly one reliable boundary
            shift = (tb - wb) if has_first else (te - we)
            stretch, rule = False, ("first" if has_first else "last")
            n_partial += 1
        else:
            continue
        suffix = "_adj" if rule.startswith("both") else "_partial"
        for t in toks:
            t["adj_rule"] = rule
            for col in ("whisper_begin", "whisper_end"):
                if t[col] == EMPTY:
                    continue
                w = float(t[col])
                v = tb + (w - wb) * (te - tb) / (we - wb) if stretch else w + shift
                t[col + suffix] = f"{v:.3f}"
        _fill_gaps(toks)

    for r, rec in zip(rows, recs):
        r.extend(rec[c] for c in CORRECTION_COLUMNS)
    return {"units": len(units), "units_corrected_full": n_full, "units_corrected_partial": n_partial}


def align_files(json_path: str, tsv_path: str, output: Optional[str] = None,
                verbose: bool = True, correction: bool = True) -> dict:
    """Align one WhisperX JSON with one .vert.tsv and write the output TSV.

    Returns the alignment counts plus ``wer``, ``reference_tokens`` and
    ``output`` (the path written).
    """
    whisper_words = load_whisper(json_path)
    all_tokens = load_tsv(tsv_path)
    tsv_tokens = [t for t in all_tokens if t.alignable]
    if verbose:
        print(f"{len(whisper_words)} WhisperX words, {len(tsv_tokens)} linguistic tokens "
              f"({len(all_tokens) - len(tsv_tokens)} other rows skipped)", file=sys.stderr)

    pairs = run_needleman_wunsch([w.norm for w in whisper_words],
                                 [t.norm for t in tsv_tokens])
    rows, stats = build_rows(pairs, whisper_words, all_tokens)
    stats.update(apply_speaker_mapping(rows))
    if correction:
        stats.update(correct_timestamps(rows))

    if output:
        out_path = output
    else:
        p = pathlib.Path(tsv_path)
        stem = p.name.removesuffix(".vert.tsv").removesuffix(".tsv")
        out_path = str(p.with_name(f"{stem}.aligned.tsv"))

    with open(out_path, "w", encoding=ENCODING_WRITE, newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS + (CORRECTION_COLUMNS if correction else []))
        for r in rows:
            writer.writerow([EMPTY if c == "" else c for c in r])

    stats["reference_tokens"] = stats["match"] + stats["mismatch"] + stats["tsv_only"]
    stats["wer"] = compute_wer(stats)
    stats["output"] = out_path
    if verbose:
        print("Alignment stats:", file=sys.stderr)
        print(f"  matches:       {stats['match']}", file=sys.stderr)
        print(f"  mismatches:    {stats['mismatch']}  (substitutions)", file=sys.stderr)
        print(f"  tsv_only:      {stats['tsv_only']}  (deletions)", file=sys.stderr)
        print(f"  whisper_only:  {stats['whisper_only']}  (insertions)", file=sys.stderr)
        print(f"  WER (linguistic tokens only, {stats['reference_tokens']} reference "
              f"tokens): {stats['wer']:.2%}", file=sys.stderr)
        if stats["speaker_tokens"]:
            print(f"  speakers: {stats['speaker_map']} "
                  f"({stats['speaker_agreement']:.1%} of {stats['speaker_tokens']} "
                  f"matched tokens agree)", file=sys.stderr)
        else:
            print("  speakers: no WhisperX speaker labels to map", file=sys.stderr)
        if correction:
            print(f"  timestamp correction: {stats['units_corrected_full']} units on both boundaries, "
                  f"{stats['units_corrected_partial']} on one, of {stats['units']}", file=sys.stderr)
        print(f"Output written to {out_path}", file=sys.stderr)
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Align a WhisperX JSON with a KIParla vert.tsv using "
                    "Needleman-Wunsch; add timestamps and compute the WER."
    )
    parser.add_argument("json_path", help="WhisperX transcription JSON")
    parser.add_argument("tsv_path", help="KIParla vert.tsv file")
    parser.add_argument("-o", "--output", default=None, help="Output TSV path "
                        "(default: <name>.aligned.tsv next to the input TSV)")
    parser.add_argument("--no-correction", action="store_true",
                        help="do not add the timestamp-correction columns")
    args = parser.parse_args(argv)
    align_files(args.json_path, args.tsv_path, args.output, correction=not args.no_correction)
    return 0


if __name__ == "__main__":
    sys.exit(main())
