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
  whisper_text              the aligned WhisperX word (lowercase, no punctuation)
  whisper_begin             start (s) of the aligned WhisperX word
  whisper_end               end (s) of the aligned WhisperX word
  alignment                 match | mismatch | whisper_only | tsv_only | skipped

WER, with the TSV linguistic tokens as reference and WhisperX as hypothesis:
  (substitutions + deletions + insertions) / reference tokens
  substitutions = mismatch, deletions = tsv_only, insertions = whisper_only,
  reference tokens = match + mismatch + tsv_only.

Usage:
    python whisperx_align.py <whisperx.json> <corpus.vert.tsv> [-o output.tsv]
"""

from __future__ import annotations

import argparse
import csv
import json
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
    "whisper_segment", "whisper_speaker", "whisper_text",
    "whisper_begin", "whisper_end", "alignment",
]

_ALIGN_TIME = re.compile(r"\b(Begin|End)=([0-9.]+)")

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


def _split_word(raw: str, start, end, score, speaker, segment) -> list[WhisperWord]:
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
                               p_start, p_end, score, speaker))
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
                    w.get("score"), w.get("speaker", seg.get("speaker")), segment))
        else:
            # plain Whisper: spread the segment's span evenly over its words
            seg_words = seg.get("text", "").split()
            n = len(seg_words)
            duration = seg["end"] - seg["start"]
            for i, w in enumerate(seg_words):
                words.extend(_split_word(
                    w, seg["start"] + i * duration / n,
                    seg["start"] + (i + 1) * duration / n,
                    None, seg.get("speaker"), segment))
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

    def tsv_row(t, segment, speaker, text, begin, end, aln):
        t_begin, t_end = tsv_times(t.row.get("align", ""))
        return [t.row.get("token_id", ""), t.row.get("tu_id", ""), t.row.get("speaker", ""),
                t.row.get("form", ""), t_begin, t_end, segment, speaker, text, begin, end, aln]

    for a, b in pairs:
        if b is not None:  # emit the skipped rows that precede this token
            while pending and pending[0].index < aligned[b].index:
                rows.append(tsv_row(pending.pop(0), EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, "skipped"))
        if a is not None and b is not None:
            w, t = whisper_words[a], aligned[b]
            aln = "match" if w.norm == t.norm else "mismatch"
            rows.append(tsv_row(t, w.segment, w.speaker or EMPTY, w.display or EMPTY,
                                _time(w.start), _time(w.end), aln))
        elif a is not None:
            w = whisper_words[a]
            aln = "whisper_only"
            rows.append([EMPTY] * 6 + [w.segment, w.speaker or EMPTY, w.display or EMPTY,
                                  _time(w.start), _time(w.end), aln])
        else:
            aln = "tsv_only"
            rows.append(tsv_row(aligned[b], EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, aln))
        stats[aln] += 1
    for t in pending:
        rows.append(tsv_row(t, EMPTY, EMPTY, EMPTY, EMPTY, EMPTY, "skipped"))
    return rows, stats


def align_files(json_path: str, tsv_path: str, output: Optional[str] = None,
                verbose: bool = True) -> dict:
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

    if output:
        out_path = output
    else:
        p = pathlib.Path(tsv_path)
        stem = p.name.removesuffix(".vert.tsv").removesuffix(".tsv")
        out_path = str(p.with_name(f"{stem}.aligned.tsv"))

    with open(out_path, "w", encoding=ENCODING_WRITE, newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
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
    args = parser.parse_args(argv)
    align_files(args.json_path, args.tsv_path, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
