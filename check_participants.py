#!/usr/bin/env python3
"""
check_participants.py — Cross-check conversation participants against transcripts.

For every conversation listed in a module's metadata/conversations.tsv, compares
the `participants` column (semicolon-separated codes) against the actual set of
`speaker` values found in that conversation's tsv/<code>.vert.tsv. Also checks
that every speaker found in a transcript has a corresponding row in
metadata/participants.tsv.

Usage:
    python check_participants.py --modules /path/to/KIP /path/to/ParlaBO ...
    python check_participants.py                     # auto-discover modules

Auto-discovery (no --modules given): scans the parent of this script's
directory for subdirectories containing both metadata/conversations.tsv and a
tsv/ folder with at least one *.vert.tsv file, skipping known aggregate/
non-source directories (see SKIP_DIRS).

Checks performed per conversation:
  1. participants-not-in-transcript — code listed in `participants` but no
     matching speaker ever appears in the vert.tsv
  2. speakers-not-in-metadata       — speaker appears in the vert.tsv but is
     not listed in `participants`
  3. missing-transcript             — conversation has no tsv/<code>.vert.tsv
  4. unregistered-speaker           — a transcript speaker has no row in
     metadata/participants.tsv

Checks performed between conversations.tsv `participants` and
participants.tsv `conversations` (both directions):
  5. conversation-not-backreferenced — conversations.tsv lists a participant
     for a conversation, but that participant's `conversations` field in
     participants.tsv doesn't list the conversation back (or the participant
     has no row at all)
  6. participant-not-forwardreferenced — participants.tsv lists a conversation
     for a participant, but that conversation's `participants` field in
     conversations.tsv doesn't list the participant back (or the conversation
     has no row at all)

Exit code: 0 if no issues found, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    "KIParla-collection", "KIParla-module", "KIParla-artifacts",
    "KIParla-Forest", "KIParla-NoSketch-Data",
}

# Speaker tiers that are known annotation conventions rather than real
# participants: unidentified-speaker placeholders ("?", "??", "???", ...)
# and annotator meta-tiers (comments, background sounds). These are excluded
# from the "speaks in transcript but not listed as participant" check so
# genuine mismatches aren't drowned out.
NON_PARTICIPANT_TIERS = {"commenti", "suoni", "sottofondo", "gruppo", "video", "trad_video"}


def is_placeholder_speaker(speaker: str) -> bool:
    if re.fullmatch(r"\?+", speaker):
        return True
    return speaker.lower() in NON_PARTICIPANT_TIERS


def discover_modules(root: Path) -> list[Path]:
    modules = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS or d.name.startswith("."):
            continue
        conv_path = d / "metadata" / "conversations.tsv"
        tsv_dir = d / "tsv"
        if conv_path.is_file() and tsv_dir.is_dir() and any(tsv_dir.glob("*.vert.tsv")):
            modules.append(d)
    return modules


def load_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        reader.fieldnames = [(fn or "").strip() for fn in reader.fieldnames]
        return list(reader)


def speakers_in_vert(path: Path) -> set[str]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # Some legacy vert.tsv files pad header names with spaces
        # (e.g. " speaker" instead of "speaker") — normalize before lookup.
        reader.fieldnames = [(fn or "").strip() for fn in reader.fieldnames]
        return {row["speaker"].strip() for row in reader if row.get("speaker", "").strip()}


def split_codes(value: str) -> set[str]:
    if not value or value.strip() in ("_", ""):
        return set()
    return {v.strip() for v in value.split(";") if v.strip()}


def check_backreferences(conversations: list[dict], participants_rows: list[dict]) -> dict:
    """Cross-check conversations.tsv `participants` against participants.tsv
    `conversations`, in both directions."""
    conv_participants = {
        (row.get("code") or "").strip(): split_codes(row.get("participants", ""))
        for row in conversations if (row.get("code") or "").strip()
    }
    part_conversations = {
        (row.get("code") or "").strip(): split_codes(row.get("conversations", ""))
        for row in participants_rows if (row.get("code") or "").strip()
    }

    conversation_not_backreferenced = []  # (conv_code, participant_code)
    for conv_code, participants in conv_participants.items():
        for p in participants:
            if conv_code not in part_conversations.get(p, set()):
                conversation_not_backreferenced.append((conv_code, p))

    participant_not_forwardreferenced = []  # (participant_code, conv_code)
    for p_code, convs in part_conversations.items():
        for c in convs:
            if p_code not in conv_participants.get(c, set()):
                participant_not_forwardreferenced.append((p_code, c))

    return {
        "conversation_not_backreferenced": conversation_not_backreferenced,
        "participant_not_forwardreferenced": participant_not_forwardreferenced,
    }


def check_module(module_dir: Path) -> dict:
    """Run all checks for one module. Returns a result dict."""
    conversations = load_tsv(module_dir / "metadata" / "conversations.tsv")
    participants_path = module_dir / "metadata" / "participants.tsv"
    participants_rows = load_tsv(participants_path) if participants_path.is_file() else []
    registered_participants = {
        row["code"].strip() for row in participants_rows if row.get("code", "").strip()
    }
    backrefs = check_backreferences(conversations, participants_rows) \
        if participants_path.is_file() else {
            "conversation_not_backreferenced": [], "participant_not_forwardreferenced": []
        }

    tsv_dir = module_dir / "tsv"
    results = []
    all_transcript_speakers: set[str] = set()

    for row in conversations:
        code = (row.get("code") or "").strip()
        if not code:
            continue
        meta_participants = split_codes(row.get("participants", ""))

        vert_path = tsv_dir / f"{code}.vert.tsv"
        if not vert_path.is_file():
            results.append({
                "code": code,
                "missing_transcript": True,
                "participants_not_in_transcript": sorted(meta_participants),
                "speakers_not_in_metadata": [],
            })
            continue

        transcript_speakers = speakers_in_vert(vert_path)
        all_transcript_speakers |= transcript_speakers

        real_speakers = {s for s in transcript_speakers if not is_placeholder_speaker(s)}
        missing_in_transcript = meta_participants - transcript_speakers
        missing_in_metadata = real_speakers - meta_participants

        if missing_in_transcript or missing_in_metadata:
            results.append({
                "code": code,
                "missing_transcript": False,
                "participants_not_in_transcript": sorted(missing_in_transcript),
                "speakers_not_in_metadata": sorted(missing_in_metadata),
            })

    real_transcript_speakers = {s for s in all_transcript_speakers if not is_placeholder_speaker(s)}
    unregistered_speakers = sorted(real_transcript_speakers - registered_participants) \
        if participants_path.is_file() else []

    return {
        "module": module_dir.name,
        "n_conversations": len(conversations),
        "conversation_issues": results,
        "unregistered_speakers": unregistered_speakers,
        "participants_tsv_missing": not participants_path.is_file(),
        "conversation_not_backreferenced": backrefs["conversation_not_backreferenced"],
        "participant_not_forwardreferenced": backrefs["participant_not_forwardreferenced"],
    }


def add_unknown_participant_column(module_dir: Path) -> int:
    """Add/refresh an `unknown-participant` column in metadata/conversations.tsv:
    "yes" if the conversation's transcript contains any placeholder speaker
    tier (??, ???, ...), "no" otherwise. Returns the number of rows written."""
    conv_path = module_dir / "metadata" / "conversations.tsv"
    tsv_dir = module_dir / "tsv"

    with conv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = [(fn or "").strip() for fn in reader.fieldnames]
        rows = list(reader)

    if "unknown-participant" not in fieldnames:
        fieldnames = fieldnames + ["unknown-participant"]

    for row in rows:
        code = (row.get("code") or "").strip()
        vert_path = tsv_dir / f"{code}.vert.tsv"
        if code and vert_path.is_file():
            speakers = speakers_in_vert(vert_path)
            has_unknown = any(re.fullmatch(r"\?+", s) for s in speakers)
            row["unknown-participant"] = "yes" if has_unknown else "no"
        else:
            row["unknown-participant"] = "_"

    with conv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", restval="_",
                                 lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def report(result: dict) -> int:
    """Print a human-readable report for one module. Returns issue count."""
    print(f"\n=== {result['module']} ({result['n_conversations']} conversations) ===")

    n_issues = 0

    if result["participants_tsv_missing"]:
        print("  WARN  metadata/participants.tsv not found — skipping unregistered-speaker check")

    has_backref_issues = (
        result["conversation_not_backreferenced"] or result["participant_not_forwardreferenced"]
    )
    if not result["conversation_issues"] and not result["unregistered_speakers"] and not has_backref_issues:
        print("  OK  all conversations' participants match their transcripts and metadata")
        return 0

    for issue in result["conversation_issues"]:
        code = issue["code"]
        if issue["missing_transcript"]:
            print(f"  FAIL  {code}: no transcript found (tsv/{code}.vert.tsv missing)")
            n_issues += 1
            continue
        if issue["participants_not_in_transcript"]:
            print(f"  WARN  {code}: listed as participant but never speaks: "
                  f"{', '.join(issue['participants_not_in_transcript'])}")
            n_issues += 1
        if issue["speakers_not_in_metadata"]:
            print(f"  WARN  {code}: speaks in transcript but not listed as participant: "
                  f"{', '.join(issue['speakers_not_in_metadata'])}")
            n_issues += 1

    if result["unregistered_speakers"]:
        print(f"  WARN  speaker code(s) with no row in participants.tsv: "
              f"{', '.join(result['unregistered_speakers'])}")
        n_issues += 1

    for conv_code, p_code in result["conversation_not_backreferenced"]:
        print(f"  WARN  {conv_code}: participant {p_code} is not back-referenced "
              f"in participants.tsv's `conversations` field (missing row or missing {conv_code})")
        n_issues += 1

    for p_code, conv_code in result["participant_not_forwardreferenced"]:
        print(f"  WARN  participant {p_code}: conversation {conv_code} (from participants.tsv "
              f"`conversations`) does not list {p_code} back in its `participants` field "
              f"(missing row or missing {p_code})")
        n_issues += 1

    return n_issues


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules", nargs="+", type=Path,
                     help="Paths to module root directories. Default: auto-discover "
                          "siblings of this script's parent directory.")
    ap.add_argument("--add-unknown-participant-column", action="store_true",
                     help="Add/refresh an `unknown-participant` column in each module's "
                          "metadata/conversations.tsv, listing placeholder speaker tiers "
                          "(??, ???, ...) found per conversation. Writes files in place.")
    args = ap.parse_args()

    if args.modules:
        modules = args.modules
    else:
        modules = discover_modules(Path(__file__).resolve().parent.parent)

    if not modules:
        print("No modules found (looked for metadata/conversations.tsv + tsv/*.vert.tsv).",
              file=sys.stderr)
        sys.exit(1)

    if args.add_unknown_participant_column:
        for module_dir in modules:
            n = add_unknown_participant_column(module_dir)
            print(f"{module_dir.name}: wrote unknown-participant column for {n} conversations")
        sys.exit(0)

    total_issues = 0
    for module_dir in modules:
        result = check_module(module_dir)
        total_issues += report(result)

    print(f"\n{len(modules)} module(s) checked, {total_issues} issue(s) found.")
    sys.exit(1 if total_issues else 0)


if __name__ == "__main__":
    main()
