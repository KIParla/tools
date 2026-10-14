#!/usr/bin/env python3
"""
reconcile.py - Merge a freshly generated vert.tsv into the one already in the module.

When an .eaf is edited and reprocessed (`sync.py --from-eaf`), `serialize.process`
builds the vert.tsv from scratch: `tu_id` and `token_id` are renumbered, and every
column the .eaf cannot carry (lemma, upos, xpos, feats, deprel, meta_label, the
language labels, ...) is empty again. This module reconciles the new file with the
existing one so that

  * the `tu_id` of a unit and the `token_id` of a token never change as long as the
    unit / the token still exists in the .eaf;
  * annotations that live only in the tsv survive an .eaf edit;
  * units and tokens added in the .eaf get fresh ids (never reusing an old one),
    and units/tokens removed from the .eaf disappear.

The tsv stays the master: where the transcription of a token did not change, its row is
kept as it is. Only what depends on the .eaf (speaker, begin/end times and the overlaps
between units) is taken from the new file.

Matching
--------
Units of the two files are paired (same speaker, same transcription, same times; then
progressively looser: transcription edited but same times, times edited but same
transcription, tier renamed). Inside a paired unit, the tokens are aligned on their
`span` with difflib:

  equal     -> old row kept (new speaker / align / overlaps)
  replace   -> pairwise: old `token_id`, new transcription-derived columns; lemma, upos,
               xpos, feats, deprel, meta_label kept only if `form` is unchanged;
               `Language=` of code_variation / jefferson_feats kept
  insert    -> new row with a fresh `token_id`
  delete    -> dropped
  (old tokens with form `_` are not representable in the .eaf: they are never dropped)

Usage:
    python reconcile.py <old.vert.tsv> <new.vert.tsv> [-o OUT.vert.tsv]
"""

import argparse
import collections
import csv
import difflib
import json
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from textutil import ENCODING_READ, ENCODING_WRITE  # noqa: E402

TOLERANCE = 0.002  # seconds: align keeps three decimals, ELAN works in milliseconds

# columns that exist only in the tsv (the .eaf cannot carry them)
ANNOTATION_COLUMNS = ("lemma", "upos", "xpos", "feats", "deprel", "meta_label")
# `Key=` entries of the pipe-separated columns that are not derived from the span
KEPT_FEATURES = ("Language", "Lang")
FEATURE_COLUMNS = ("code_variation", "jefferson_feats")
# columns the new file always provides, even for unchanged tokens
FROM_NEW = ("speaker", "align", "overlaps")


# ---------------------------------------------------------------------------
# Reading / writing
# ---------------------------------------------------------------------------

def read_vert(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, encoding=ENCODING_READ, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader.fieldnames), list(reader)


def write_vert(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding=ENCODING_WRITE, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t",
                                restval="_", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def group_by_tu(rows: list[dict]) -> dict[int, list[dict]]:
    """tu_id -> its rows, in file order."""
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(int(row["tu_id"]), []).append(row)
    return groups


# ---------------------------------------------------------------------------
# Unit matching
# ---------------------------------------------------------------------------

def _times(rows: list[dict]) -> tuple[float | None, float | None]:
    begin = end = None
    for row in rows:
        for part in row.get("align", "_").split("|"):
            key, _, value = part.partition("=")
            try:
                number = float(value)
            except ValueError:
                continue
            if key == "Begin" and begin is None:
                begin = number
            elif key == "End":
                end = number
    return begin, end


class _Unit:
    def __init__(self, tu_id: int, rows: list[dict]):
        self.tu_id = tu_id
        self.rows = rows
        self.speaker = rows[0]["speaker"]
        self.spans = tuple(r["span"] for r in rows)
        self.begin, self.end = _times(rows)

    def same_times(self, other: "_Unit") -> bool:
        if None in (self.begin, self.end, other.begin, other.end):
            return False
        return (abs(self.begin - other.begin) <= TOLERANCE
                and abs(self.end - other.end) <= TOLERANCE)

    def overlap(self, other: "_Unit") -> float:
        """Fraction of the shorter unit covered by the other."""
        if None in (self.begin, self.end, other.begin, other.end):
            return 0.0
        shared = min(self.end, other.end) - max(self.begin, other.begin)
        shorter = min(self.end - self.begin, other.end - other.begin)
        return shared / shorter if shorter > 0 and shared > 0 else 0.0

    def distance(self, other: "_Unit") -> float:
        if self.begin is None or other.begin is None:
            return 0.0
        return abs(self.begin - other.begin)


def match_units(old: dict[int, list[dict]], new: dict[int, list[dict]],
                hints: dict[int, int] | None = None) -> dict[int, int]:
    """Pair new units with old units. Returns {new tu_id: old tu_id}.

    `hints` ({new tu_id: old tu_id}, from the unit ids stored in a layered .eaf) are taken
    as they are, as long as the old unit exists and is not claimed twice; the remaining
    units are paired by what they contain.
    """
    old_units = [_Unit(k, v) for k, v in old.items()]
    new_units = [_Unit(k, v) for k, v in new.items()]
    matched: dict[int, int] = {}
    used_old: set[int] = set()
    for n_id, o_id in sorted((hints or {}).items()):
        if n_id in new and o_id in old and o_id not in used_old:
            matched[n_id] = o_id
            used_old.add(o_id)

    def stage(accept, score):
        # greedy: best candidate pairs first; only units still unmatched take part
        pairs = []
        for n in new_units:
            if n.tu_id in matched:
                continue
            for o in old_units:
                if o.tu_id in used_old or not accept(o, n):
                    continue
                pairs.append((score(o, n), n.tu_id, o.tu_id))
        for _, n_id, o_id in sorted(pairs):
            if n_id not in matched and o_id not in used_old:
                matched[n_id] = o_id
                used_old.add(o_id)

    same = lambda o, n: o.speaker == n.speaker and o.spans == n.spans  # noqa: E731
    stage(lambda o, n: same(o, n) and o.same_times(n), lambda o, n: o.distance(n))
    stage(same, lambda o, n: o.distance(n))
    stage(lambda o, n: o.speaker == n.speaker and o.same_times(n), lambda o, n: o.distance(n))
    stage(lambda o, n: o.speaker == n.speaker and o.overlap(n) >= 0.5,
          lambda o, n: (-o.overlap(n), o.distance(n)))
    stage(lambda o, n: o.spans == n.spans and o.same_times(n), lambda o, n: o.distance(n))
    return matched


# ---------------------------------------------------------------------------
# Token merging
# ---------------------------------------------------------------------------

def _feature_dict(value: str) -> dict[str, str]:
    if value in ("", "_"):
        return {}
    return dict(part.partition("=")[::2] for part in value.split("|"))


def _merge_features(old_value: str, new_value: str) -> str:
    """New value, plus the `Language=` style entries only the old one had."""
    new = _feature_dict(new_value)
    extra = {k: v for k, v in _feature_dict(old_value).items()
             if k in KEPT_FEATURES and k not in new}
    if not extra:
        return new_value
    merged = {**new, **extra}
    return "|".join(f"{k}={v}" for k, v in merged.items())


def _same_time(old_align: str, new_align: str) -> bool:
    """True if two align values are the same up to rounding."""
    if old_align == new_align:
        return True
    a, b = old_align.split("|"), new_align.split("|")
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        kx, _, vx = x.partition("=")
        ky, _, vy = y.partition("=")
        try:
            if kx != ky or abs(float(vx) - float(vy)) > TOLERANCE:
                return False
        except ValueError:
            return False
    return True


def _align_value(old_align: str, new_align: str, keep: set[str]) -> str:
    """`Begin=`/`End=` of a token: the new ones, except the edges listed in `keep`, which
    stay as the old file had them (their time was not edited in the .eaf)."""
    if _same_time(old_align, new_align):
        return old_align
    old = dict(p.partition("=")[::2] for p in old_align.split("|") if "=" in p)
    new = [p.partition("=")[::2] for p in new_align.split("|") if "=" in p]
    if not new:
        return new_align
    return "|".join(f"{k}={old[k] if k in keep and k in old else v}" for k, v in new)


def _keep_old(old_row: dict, new_row: dict, tu_id: int, keep: set[str] = frozenset()) -> dict:
    row = dict(old_row)
    for col in FROM_NEW:
        if col == "align":
            row[col] = _align_value(old_row.get("align", "_"), new_row.get("align", "_"), keep)
        else:
            row[col] = new_row.get(col, "_")
    row["tu_id"] = str(tu_id)
    return row


def _hidden_row(old_row: dict, tu_id: int) -> dict:
    """Old token without form: kept untouched (marked, so its overlap ids are not remapped)."""
    row = dict(old_row)
    row["tu_id"] = str(tu_id)
    row["_hidden"] = True
    return row


def _replace(old_row: dict, new_row: dict, tu_id: int, keep: set[str] = frozenset()) -> dict:
    row = dict(new_row)
    row["align"] = _align_value(old_row.get("align", "_"), new_row.get("align", "_"), keep)
    row["tu_id"] = str(tu_id)
    row["token_id"] = old_row["token_id"]
    if old_row.get("form") == new_row.get("form"):
        for col in ANNOTATION_COLUMNS:
            row[col] = old_row.get(col, "_")
    for col in FEATURE_COLUMNS:
        row[col] = _merge_features(old_row.get(col, "_"), new_row.get(col, "_"))
    return row


_SID = re.compile(r"\((\d+)\)")


def _overlap_ids(value: str) -> list[str]:
    return _SID.findall(value) if value not in ("", "_") else []


def unchanged_edges(old_rows: list[dict], raw_times: tuple[float, float] | None) -> set[str]:
    """Which of `Begin` / `End` of a unit were not touched in the .eaf.

    `raw_times` are the start and end of the unit as written in the .eaf. The pipeline
    adjusts the times it reads (overlaps), so comparing its output with the old file would
    mistake those adjustments for edits: the raw times are compared instead.
    """
    if raw_times is None:
        return set()
    begin, end = _times(old_rows)
    keep = set()
    if begin is not None and abs(begin - raw_times[0]) <= TOLERANCE:
        keep.add("Begin")
    if end is not None and abs(end - raw_times[1]) <= TOLERANCE:
        keep.add("End")
    return keep


def merge_unit(old_rows: list[dict], new_rows: list[dict], tu_id: int, stats: dict,
               sid_votes: collections.Counter | None = None,
               keep_times: set[str] = frozenset()) -> list[dict]:
    """Token rows of one paired unit.

    Old tokens whose `form` is `_` (no word form) cannot be written in the .eaf, so they
    are not part of the alignment: they stay where they were.
    """
    suffixes = [int(r["token_id"].split("-")[1]) for r in old_rows
                if r["token_id"].split("-")[1].isdigit()]
    next_suffix = max(suffixes, default=-1) + 1

    aligned = [(i, r) for i, r in enumerate(old_rows) if r.get("form") != "_"]
    hidden = [(i, r) for i, r in enumerate(old_rows) if r.get("form") == "_"]

    out: list[tuple[int | None, dict]] = []  # (position in old_rows, row)
    matcher = difflib.SequenceMatcher(
        None, [r["span"] for _, r in aligned], [r["span"] for r in new_rows], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for (pos, o), n in zip(aligned[i1:i2], new_rows[j1:j2]):
                out.append((pos, _keep_old(o, n, tu_id, keep_times)))
                if sid_votes is not None:
                    old_ids, new_ids = _overlap_ids(o["overlaps"]), _overlap_ids(n["overlaps"])
                    if len(old_ids) == len(new_ids):
                        sid_votes.update(zip(new_ids, old_ids))
            stats["tokens_kept"] += i2 - i1
            continue
        paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for (pos, o), n in zip(aligned[i1:i1 + paired], new_rows[j1:j1 + paired]):
            out.append((pos, _replace(o, n, tu_id, keep_times)))
        stats["tokens_changed"] += paired
        for n in new_rows[j1 + paired:j2]:
            row = dict(n)
            row["tu_id"] = str(tu_id)
            row["token_id"] = f"{tu_id}-{next_suffix}"
            next_suffix += 1
            out.append((None, row))
            stats["tokens_added"] += 1
        stats["tokens_removed"] += (i2 - i1) - paired

    result: list[dict] = []
    pending = list(hidden)
    for pos, row in out:
        while pos is not None and pending and pending[0][0] < pos:
            hidden_pos, hidden_row = pending.pop(0)
            result.append(_hidden_row(hidden_row, tu_id))
            stats["tokens_kept"] += 1
        result.append(row)
    for _, hidden_row in pending:
        result.append(_hidden_row(hidden_row, tu_id))
        stats["tokens_kept"] += 1
    return result


# ---------------------------------------------------------------------------
# Whole file
# ---------------------------------------------------------------------------

def _keep_overlap_ids(old_rows: list[dict], merged: dict[int, list[dict]],
                      sid_votes: collections.Counter) -> None:
    """Give the overlaps of the new file the group numbers they had in the old one.

    The number in `overlaps` (`0-5(895)`) only says which tokens overlap each other. The
    pipeline numbers the groups again from scratch, so a group is mapped to the old number
    most of its tokens had; groups that did not exist get numbers above the old maximum.
    """
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for (new_sid, old_sid), _ in sid_votes.most_common():
        if new_sid not in mapping and old_sid not in taken:
            mapping[new_sid] = old_sid
            taken.add(old_sid)
    top = max((int(s) for r in old_rows for s in _overlap_ids(r["overlaps"])), default=0)
    for rows in merged.values():
        for row in rows:
            for sid in ([] if row.get("_hidden") else _overlap_ids(row["overlaps"])):
                if sid not in mapping:
                    top += 1
                    mapping[sid] = str(top)
    for rows in merged.values():
        for row in rows:
            if row.pop("_hidden", False):
                continue
            if row["overlaps"] not in ("", "_"):
                row["overlaps"] = _SID.sub(lambda m: f"({mapping[m.group(1)]})", row["overlaps"])


def reconcile_rows(old_rows: list[dict], new_rows: list[dict],
                   hints: dict[int, int] | None = None,
                   raw_times: dict[int, tuple[float, float]] | None = None) -> tuple[list[dict], dict, dict]:
    """Reconcile two vert.tsv row lists.

    `raw_times` ({new tu_id: (start, end)} as written in the .eaf) lets a unit keep the
    `Begin=`/`End=` it had when its time was not edited.

    Returns (rows, mapping, stats): `mapping` is {tu_id in the new file: tu_id in the
    result}, also for units that did not exist before.
    """
    old = group_by_tu(old_rows)
    new = group_by_tu(new_rows)
    matched = match_units(old, new, hints)
    stats = {"tus_kept": len(matched), "tus_added": 0, "tus_removed": 0,
             "tokens_kept": 0, "tokens_changed": 0, "tokens_added": 0, "tokens_removed": 0}

    mapping = dict(matched)
    next_tu = max(old, default=0) + 1
    for n_id in new:
        if n_id not in mapping:
            mapping[n_id] = next_tu
            next_tu += 1
            stats["tus_added"] += 1
    stats["tus_removed"] = len(set(old) - set(matched.values()))

    merged: dict[int, list[dict]] = {}
    sid_votes: collections.Counter = collections.Counter()
    for n_id, rows in new.items():
        final = mapping[n_id]
        if n_id in matched:
            merged[final] = merge_unit(
                old[matched[n_id]], rows, final, stats, sid_votes,
                unchanged_edges(old[matched[n_id]], (raw_times or {}).get(n_id)))
        else:
            fresh = []
            for idx, n in enumerate(rows):
                row = dict(n)
                row["tu_id"] = str(final)
                row["token_id"] = f"{final}-{idx}"
                fresh.append(row)
            merged[final] = fresh
            stats["tokens_added"] += len(rows)

    # order: surviving old units keep their place, new units go after the unit that
    # precedes them in the new file
    after: dict[int | None, list[int]] = {}
    anchor = None
    for n_id in new:
        if n_id in matched:
            anchor = mapping[n_id]
        else:
            after.setdefault(anchor, []).append(mapping[n_id])
    order = list(after.get(None, []))
    for o_id in old:
        if o_id in merged:
            order.append(o_id)
            order.extend(after.get(o_id, []))

    _keep_overlap_ids(old_rows, merged, sid_votes)

    rows_out = []
    for tu_id in order:
        for idx, row in enumerate(merged[tu_id]):
            row["id"] = str(idx)
            rows_out.append(row)
    return rows_out, mapping, stats


def reconcile_vert(old_path: Path | None, new_path: Path, out_path: Path,
                   hints: dict[int, int] | None = None,
                   raw_times: dict[int, tuple[float, float]] | None = None) -> tuple[dict, dict]:
    """Write the reconciled vert.tsv. Without an old file, the new one is copied as is."""
    fieldnames, new_rows = read_vert(new_path)
    if old_path is None or not Path(old_path).is_file():
        write_vert(out_path, fieldnames, new_rows)
        return {t: t for t in group_by_tu(new_rows)}, {"tus_added": len(group_by_tu(new_rows))}
    _, old_rows = read_vert(old_path)
    rows, mapping, stats = reconcile_rows(old_rows, new_rows, hints, raw_times)
    write_vert(out_path, fieldnames, rows)
    return mapping, stats


def reconcile_translations(old_path: Path | None, new_rows: list[dict],
                           mapping: dict[int, int], used_tu_ids: set[int],
                           old_unit_ids: set[int] | None = None) -> list[dict]:
    """Renumber translation rows with the tu_id mapping of `reconcile_rows`.

    `parent_tu_id` follows its parent. The row's own `tu_id` is kept from the old file
    when that unit already had a translation, otherwise a fresh one.

    A translation of the old file whose parent is not a unit of the old tsv (a translation
    tier with no unit to hang from) cannot be written to the .eaf, so it can never come
    back from it: it is kept as it was instead of being lost.
    """
    old_by_key: dict[tuple, str] = {}
    old_rows_by_key: dict[tuple, dict] = {}
    orphans: list[dict] = []
    if old_path is not None and Path(old_path).is_file():
        with open(old_path, encoding=ENCODING_READ) as f:
            for row in json.load(f):
                old_by_key[str(row["parent_tu_id"])] = str(row["tu_id"])
                old_rows_by_key[str(row["parent_tu_id"])] = row
                parent = str(row["parent_tu_id"])
                if old_unit_ids is not None and not (parent.isdigit() and int(parent) in old_unit_ids):
                    if (row.get("text") or "").strip():
                        orphans.append(row)

    taken = set(used_tu_ids) | {int(v) for v in old_by_key.values() if str(v).isdigit()}
    next_id = max(taken, default=0) + 1
    out = []
    for row in new_rows:
        row = dict(row)
        parent = str(row.get("parent_tu_id", "_"))
        if parent.isdigit() and int(parent) in mapping:
            row["parent_tu_id"] = str(mapping[int(parent)])
        key = str(row["parent_tu_id"])   # one translation per unit
        if key in old_by_key:
            old_row = old_rows_by_key[key]
            if (old_row.get("text") or "").strip() == (row.get("text") or "").strip():
                row = dict(old_row)   # unchanged: keep it exactly (its own times included)
            row["tu_id"] = old_by_key[key]
        else:
            row["tu_id"] = str(next_id)
            next_id += 1
        out.append(row)
    kept = {(r["speaker"], str(r["parent_tu_id"]), str(r["tu_id"])) for r in out}
    out.extend(r for r in orphans
               if (r["speaker"], str(r["parent_tu_id"]), str(r["tu_id"])) not in kept)
    return out


def format_stats(stats: dict) -> str:
    return ("units: {tus_kept} kept, {tus_added} added, {tus_removed} removed; "
            "tokens: {tokens_kept} kept, {tokens_changed} changed, "
            "{tokens_added} added, {tokens_removed} removed").format(**{
                k: stats.get(k, 0) for k in (
                    "tus_kept", "tus_added", "tus_removed", "tokens_kept",
                    "tokens_changed", "tokens_added", "tokens_removed")})


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    _, stats = reconcile_vert(args.old, args.new, args.output)
    print(format_stats(stats))


if __name__ == "__main__":
    main()
