"""
tsv2chat.py — Convert pipeline vert.tsv to CHAT format (ISO 24624).

Public API:
    vert2chat(input_path, output_path, media_file=None)  → writes .cha file
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

# CLAN timestamp delimiter (U+0015 NAK, displayed as bullet in CLAN)
BULLET = "\x15"


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------

def _ms(seconds_str: str) -> int:
    """Convert a float seconds string to integer milliseconds."""
    try:
        return int(float(seconds_str) * 1000)
    except (ValueError, TypeError):
        return 0


def _parse_kv(field: str) -> dict[str, str]:
    """Parse a pipe-separated key=value field (jefferson_feats, align, etc.)."""
    if not field or field == "_":
        return {}
    result = {}
    for part in field.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v
    return result


def _pace_flags(pace_field: str) -> tuple[bool, bool]:
    """Return (is_slow, is_fast) from the vert.tsv pace column."""
    if not pace_field or pace_field == "_":
        return False, False
    slow = any(p.startswith("Slow=") for p in pace_field.split("|"))
    fast = any(p.startswith("Fast=") for p in pace_field.split("|"))
    return slow, fast


def _clique_ids(overlaps_field: str) -> list[str]:
    """Extract overlap clique IDs from the overlaps column."""
    if not overlaps_field or overlaps_field == "_":
        return []
    return re.findall(r"\((\d+)\)", overlaps_field)


def _intonation_terminator(jf: dict[str, str]) -> str | None:
    return {"weakly_rising": ",", "rising": "?", "falling": "."}.get(
        jf.get("Intonation", "")
    )


def _with_prolongations(form: str, prolongations_field: str) -> str:
    """
    Re-insert prolongation colons into a stripped form.
    prolongations_field format: "pos1xcount1,pos2xcount2"
    where pos is the 0-based char index in `form` after which colons are inserted.
    """
    if not prolongations_field or prolongations_field == "_":
        return form
    insertions: dict[int, str] = {}
    for part in prolongations_field.split(","):
        if "x" in part:
            pos_str, count_str = part.split("x", 1)
            try:
                insertions[int(pos_str)] = ":" * int(count_str)
            except ValueError:
                pass
    if not insertions:
        return form
    result = []
    for i, ch in enumerate(form):
        result.append(ch)
        if i in insertions:
            result.append(insertions[i])
    return "".join(result)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _units_from_vert(fobj) -> list[tuple[str, list[dict]]]:
    """Group vert.tsv rows by tu_id, preserving file order."""
    units: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in csv.DictReader(fobj, delimiter="\t"):
        uid = row["tu_id"]
        if uid not in units:
            units[uid] = []
            order.append(uid)
        units[uid].append(row)
    return [(uid, units[uid]) for uid in order]


def _first_clique_tu(units: list[tuple[str, list[dict]]]) -> dict[str, str]:
    """Map each overlap clique ID to the first TU (file order) that contains it."""
    seen: dict[str, str] = {}
    for uid, tokens in units:
        for tok in tokens:
            for cid in _clique_ids(tok.get("overlaps", "_")):
                seen.setdefault(cid, uid)
    return seen


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _word_form(tok: dict) -> str:
    """
    Return the CHAT word form: normalized lowercase form with prolongation
    colons restored from the prolongations field.
    """
    return _with_prolongations(
        tok.get("form", "_"),
        tok.get("prolongations", "_"),
    )


def _render_tu(
    unit_id: str,
    tokens: list[dict],
    first_clique: dict[str, str],
) -> str | None:
    if not tokens:
        return None

    speaker = tokens[0].get("speaker", "UNK")
    begin_ms: int | None = None
    end_ms: int | None = None
    terminator = "."

    # Build per-token overlap clique map (first clique ID per token)
    tok_clique: dict[int, str] = {}
    for i, tok in enumerate(tokens):
        cids = _clique_ids(tok.get("overlaps", "_"))
        if cids:
            tok_clique[i] = cids[0]

    # Collect timestamps from align field
    for tok in tokens:
        align = _parse_kv(tok.get("align", "_"))
        if "Begin" in align:
            begin_ms = _ms(align["Begin"])
        if "End" in align:
            end_ms = _ms(align["End"])

    words: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        tok_type = tok.get("type", "linguistic")
        jf = _parse_kv(tok.get("jefferson_feats", "_"))

        # Update terminator from this token's intonation
        t = _intonation_terminator(jf)
        if t:
            terminator = t

        # --- Special token types ---

        if tok_type == "shortpause":
            words.append("(.)")
            i += 1
            continue

        if tok_type == "nonverbalbehavior":
            nvb = tok.get("form", tok.get("span", "nvb")).strip("{}")
            words.append(f"[^ {nvb}]")
            i += 1
            continue

        if tok_type == "anonymized":
            words.append("xxx")
            i += 1
            continue

        # --- Overlapping token(s) ---

        cid = tok_clique.get(i)
        if cid is not None:
            # Collect all consecutive tokens in this clique
            j = i + 1
            while j < len(tokens) and tok_clique.get(j) == cid:
                j += 1

            overlap_words: list[str] = []
            for k in range(i, j):
                t2 = tokens[k]
                t2_type = t2.get("type", "linguistic")
                t2jf = _parse_kv(t2.get("jefferson_feats", "_"))
                t2_inton = _intonation_terminator(t2jf)
                if t2_inton:
                    terminator = t2_inton

                if t2_type == "shortpause":
                    overlap_words.append("(.)")
                elif t2_type == "nonverbalbehavior":
                    t2nvb = t2.get("form", t2.get("span", "nvb")).strip("{}")
                    overlap_words.append(f"[^ {t2nvb}]")
                elif t2_type == "anonymized":
                    overlap_words.append("xxx")
                else:
                    f = _word_form(t2)
                    if t2jf.get("Truncated") == "Yes":
                        f = f + "-"
                    overlap_words.append(f)

            direction = "[>]" if first_clique.get(cid) == unit_id else "[<]"
            words.append(f"<{' '.join(overlap_words)}> {direction}")
            i = j
            continue

        # --- Regular linguistic / error token ---

        form = _word_form(tok)

        if jf.get("Truncated") == "Yes":
            form = form + "-"

        lang = jf.get("Language")
        if lang and lang != "ita":
            form = f"{form}@l:{lang}"

        post_codes: list[str] = []
        vol = jf.get("Volume")
        if vol == "low":
            post_codes.append("[=! soft]")
        elif vol == "high":
            post_codes.append("[=! loud]")

        is_slow, is_fast = _pace_flags(tok.get("pace", "_"))
        if is_slow:
            post_codes.append("[=! slow]")
        elif is_fast:
            post_codes.append("[=! fast]")

        if tok.get("guesses", "_") != "_":
            post_codes.append("[?]")

        if post_codes:
            words.append(f"<{form}> {' '.join(post_codes)}")
        else:
            words.append(form)

        if jf.get("PauseAfter") == "Yes":
            words.append("(.)")

        i += 1

    if not words:
        return None

    text = " ".join(words)
    line = f"*{speaker}:\t{text} {terminator}"
    if begin_ms is not None and end_ms is not None:
        line += f" {BULLET}{begin_ms}_{end_ms}{BULLET}"
    return line


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def vert2chat(
    input_path: Path,
    output_path: Path,
    media_file: str | None = None,
) -> None:
    """Write a CHAT (.cha) file from a pipeline vert.tsv."""
    with input_path.open(encoding="utf-8") as fin:
        units = _units_from_vert(fin)

    speakers: list[str] = []
    for _, tokens in units:
        for tok in tokens:
            sp = tok.get("speaker", "")
            if sp and sp not in speakers:
                speakers.append(sp)

    first_clique = _first_clique_tu(units)
    corpus_id = re.sub(r"\.vert$", "", input_path.stem)
    media = media_file or corpus_id

    with output_path.open("w", encoding="utf-8") as fout:
        print("@Begin", file=fout)
        print("@Languages:\tita", file=fout)
        print(
            f"@Participants:\t{' , '.join(f'{s} {s} Adult' for s in speakers)}",
            file=fout,
        )
        for sp in speakers:
            print(f"@ID:\tita|KIParla|{sp}|||||Adult|||", file=fout)
        print(f"@Media:\t{media}, audio", file=fout)
        print(f"@Comment:\tConverted from {input_path.name}", file=fout)
        print("", file=fout)

        for unit_id, tokens in units:
            line = _render_tu(unit_id, tokens, first_clique)
            if line:
                print(line, file=fout)

        print("", file=fout)
        print("@End", file=fout)
