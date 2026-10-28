#!/usr/bin/env python3
"""
wip2tsv.py — Put the lemma and upos of a lemmatization-project CSV into a vert.tsv.

Usage:
    python wip2tsv.py <wip/MODULE/CODE.csv> <MODULE/tsv/CODE.vert.tsv> [-o OUT]
                      [--conllu <CODE.conllu>] [--force]

The CSV is the lemmatization work file (one row per transcription token, plus one row per
syntactic word of a multiword token: ``5-7`` sulla, ``5-7a`` su, ``5-7b`` la). The TSV keeps
its transcription untouched; this adds the annotation, matching rows by ``token_id``:

  * a token with no syntactic words gets the ``lemma`` and ``upos`` of its CSV row;
  * a multiword token gets them rebuilt from its syntactic words, joined by ``+``
    (``della``: ``di+il``, ``ADP+DET``), and each syntactic word is written as its own row
    under it: ``token_id`` ``5-7a``, ``type`` ``syntactic``, ``speaker`` and ``tu_id`` of
    the surface token, ``form``/``lemma``/``upos`` of the word, ``id``, ``span`` and every
    other column ``_``. Those rows are not part of the transcription: ``variety.iter_vert_rows``
    leaves them out unless asked, so the linear files, the eaf, NoSketch and the counts
    never see them (see ``is_syntactic``);
  * the lemma of a dialect token is not transferred (the Italian lemma has no column yet):
    a token whose conllu row has ``Dialect=Yes`` or ``Lang=und`` (``--conllu``), or whose
    ``code_variation`` says it is in another variety (``Code=Other``), keeps ``lemma`` ``_``.
    Its ``upos`` is transferred.

Running it again replaces the syntactic rows and the lemma/upos with the CSV's: it is
idempotent. Only transcription-derived rows that exist in both files are annotated; if the
two files disagree on the tokens (or on a form) nothing is written, unless ``--force``
(apply the patch of the CSV first, see make_patch.py).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from textutil import ENCODING_READ, ENCODING_WRITE  # noqa: E402
from variety import SYNTACTIC, feats_dict, is_syntactic  # noqa: E402

_SUBTOKEN = re.compile(r"(\d+-\d+)([A-Za-z])")
MISSING = "_"
JOIN = "+"


def _missing(value: str | None) -> bool:
    return value is None or value.strip() in ("", MISSING)


def read_wip(path: Path) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """(surface rows by token_id, syntactic-word rows by their surface token_id)."""
    rows: dict[str, dict] = {}
    words: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {k: (v or "").strip() for k, v in row.items()}
            m = _SUBTOKEN.fullmatch(row["token_id"])
            if m:
                words.setdefault(m.group(1), []).append(row)
            else:
                rows[row["token_id"]] = row
    return rows, words


def read_dialect_kids(path: Path) -> set[str]:
    """KIDs of the conllu words marked ``Dialect=Yes`` or ``Lang=und``."""
    kids: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) != 10 or not re.fullmatch(r"\d+", cols[0]):
                continue
            misc = dict(p.split("=", 1) for p in cols[9].split("|") if "=" in p)
            if misc.get("Dialect") == "Yes" or misc.get("Lang") == "und":
                if "KID" in misc:
                    kids.add(misc["KID"])
    return kids


def _other_variety(row: dict) -> bool:
    return feats_dict(row.get("code_variation")).get("Code") == "Other"


def annotate(wip_path: Path, tsv_path: Path, out_path: Path, conllu: Path | None = None,
             force: bool = False) -> dict:
    surface, words = read_wip(wip_path)
    dialect = read_dialect_kids(conllu) if conllu else set()

    with open(tsv_path, encoding=ENCODING_READ, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = list(reader.fieldnames)
        tsv_rows = [r for r in reader if not is_syntactic(r)]

    tsv_ids = [r["token_id"] for r in tsv_rows]
    problems = []
    for tid in tsv_ids:
        if tid not in surface:
            problems.append(f"{tid}: in the tsv, not in the csv")
    for tid in surface:
        if tid not in set(tsv_ids):
            problems.append(f"{tid}: in the csv, not in the tsv")
    for r in tsv_rows:
        c = surface.get(r["token_id"])
        if c and r["type"] == "linguistic" and c["form"] != r["form"]:
            problems.append(f"{r['token_id']}: form '{r['form']}' in the tsv, '{c['form']}' in the csv")
    stats = {"annotated": 0, "multiword": 0, "syntactic_rows": 0, "dialect_lemma_skipped": 0,
             "incomplete_multiword": 0, "problems": problems}
    if problems and not force:
        return stats

    out_rows = []
    for r in tsv_rows:
        out_rows.append(r)
        tid = r["token_id"]
        c = surface.get(tid)
        if c is None or r["type"] != "linguistic":
            continue
        kids = words.get(tid, [])
        is_dialect = _other_variety(r) or tid in dialect or any(
            k["token_id"] in dialect for k in kids)
        if kids:
            stats["multiword"] += 1
            lemmas = [k["lemma"] for k in kids]
            uposes = [k["upos"] for k in kids]
            complete = not any(_missing(v) for v in lemmas + uposes)
            if not complete:
                stats["incomplete_multiword"] += 1
            r["lemma"] = JOIN.join(lemmas) if complete and not is_dialect else MISSING
            r["upos"] = JOIN.join(uposes) if complete else MISSING
            for k in kids:
                child = dict.fromkeys(fieldnames, MISSING)
                child.update(token_id=k["token_id"], speaker=r["speaker"], tu_id=r["tu_id"],
                             form=k["form"], type=SYNTACTIC,
                             lemma=MISSING if is_dialect or _missing(k["lemma"]) else k["lemma"],
                             upos=MISSING if _missing(k["upos"]) else k["upos"])
                out_rows.append(child)
                stats["syntactic_rows"] += 1
        else:
            r["lemma"] = MISSING if is_dialect or _missing(c["lemma"]) else c["lemma"]
            r["upos"] = MISSING if _missing(c["upos"]) else c["upos"]
        had_lemma = any(not _missing(k["lemma"]) for k in kids) if kids else not _missing(c["lemma"])
        if is_dialect and had_lemma:
            stats["dialect_lemma_skipped"] += 1
        stats["annotated"] += 1

    with open(out_path, "w", encoding=ENCODING_WRITE, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t",
                                restval=MISSING, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("tsv", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="default: rewrite the tsv in place")
    parser.add_argument("--conllu", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    stats = annotate(args.csv, args.tsv, args.output or args.tsv, args.conllu, args.force)
    for p in stats["problems"][:30]:
        print("  !", p)
    if len(stats["problems"]) > 30:
        print(f"  ... and {len(stats['problems']) - 30} more")
    wrote = not stats["problems"] or args.force
    print(("written" if wrote else "NOT written (csv and tsv disagree; see make_patch.py)")
          + ": " + ", ".join(f"{k}={v}" for k, v in stats.items() if k != "problems"))
    return 0 if wrote else 1


if __name__ == "__main__":
    sys.exit(main())
