"""Functions to handle sequence alignment between transcripts."""
from __future__ import annotations

import collections
import csv
import re
import pathlib
from typing import List

from sequence_align.pairwise import needleman_wunsch

from data import Token, Transcript


def align_transcripts(transcript_a: Transcript, transcript_b: Transcript):
    """Align two transcripts by tokens up to the shorter one's length."""
    min_length = min(transcript_a.tot_length, transcript_b.tot_length)

    tokens_a = [
        token
        for tu in transcript_a.transcription_units_dict.values()
        if tu.end <= min_length
        for token in tu.tokens.values()
    ]
    tokens_b = [
        token
        for tu in transcript_b.transcription_units_dict.values()
        if tu.end <= min_length
        for token in tu.tokens.values()
    ]

    aligned_seq_a, aligned_seq_b, _, _ = align(
        [t.text for t in tokens_a],
        [t.text for t in tokens_b],
    )

    def _map(aligned_seq, source):
        result = []
        i = 0
        for tok in aligned_seq:
            if tok == "_":
                result.append(None)
            else:
                result.append(source[i])
                i += 1
        return result

    return _map(aligned_seq_a, tokens_a), _map(aligned_seq_b, tokens_b)


def align(seq_a, seq_b, match_score=1.0, mismatch_score=-1.0, indel_score=-1.0):
    """Run Needleman-Wunsch and return (aligned_a, aligned_b, score_seq, tot_score)."""
    aligned_seq_a, aligned_seq_b = needleman_wunsch(
        seq_a, seq_b,
        match_score=match_score,
        mismatch_score=mismatch_score,
        indel_score=indel_score,
        gap="_",
    )

    score_seq = []
    for x, y in zip(aligned_seq_a, aligned_seq_b):
        if x == y:
            score_seq.append(0)
        elif x == "_" or y == "_":
            score_seq.append(0.5)
        else:
            score_seq.append(1)

    tot_score = sum(score_seq) / len(score_seq) if score_seq else 0.0
    return aligned_seq_a, aligned_seq_b, score_seq, tot_score


def compute_wer(file_path):
    """Compute WER from an alignment TSV (match/token_A/token_B columns)."""
    substitutions = insertions = deletions = N = 0
    with open(file_path, encoding="utf-8") as f:
        next(f)
        for line in f:
            match, _, token_A, _, token_B = line.strip().split("\t")
            if match == "0":
                N += 1
            elif match == "1":
                if token_A == "_":
                    insertions += 1
                elif token_B == "_":
                    deletions += 1
                    N += 1
            elif match == "2":
                substitutions += 1
                N += 1
    return (substitutions + deletions + insertions) / N if N > 0 else 0.0


if __name__ == "__main__":
    import sys
    alignment_dir = pathlib.Path(sys.argv[1])
    for file in alignment_dir.glob("*.tsv"):
        wer = compute_wer(file)
        print(f"{file.name}: WER = {wer:.2%}")
