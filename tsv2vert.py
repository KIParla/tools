#!/usr/bin/env python3
"""
tsv2vert.py - Convert KIParla vert.tsv files to NoSketch Engine vertical format.

Metadata (conversations.tsv, participants.tsv) must already be translated into
Italian via translate_metadata.py before being passed to this script.

Pass module-specific metadata to build a per-module corpus, or the merged
KIParla-collection metadata to build the full KIParla corpus.

Usage:
    python tools/tsv2vert.py [--base-url BASE_URL] [--artifacts-base-url ARTIFACTS_BASE_URL]
    [--artifacts-module ARTIFACTS_MODULE] CONVERSATIONS_TSV PARTICIPANTS_TSV INPUT_TSV [INPUT_TSV ...]

Output goes to stdout; redirect to a file or pipe to concatenate multiple conversations.

Example (single module):
    python tools/tsv2vert.py KIP/metadata/conversations-ita.tsv KIP/metadata/participants-ita.tsv \\
        KIP/tsv/TOA1005.vert.tsv > NoSketchEngine/KIP/vertical/TOA1005.vert

Example (whole module):
    python tools/tsv2vert.py KIP/metadata/conversations-ita.tsv KIP/metadata/participants-ita.tsv \\
        KIP/tsv/*.vert.tsv > NoSketchEngine/KIP/vertical/source

Example (KIParla collection):
    python tools/tsv2vert.py KIParla-collection/metadata/conversations.tsv \\
        KIParla-collection/metadata/participants.tsv \\
        KIP/tsv/*.vert.tsv KIPasti/tsv/*.vert.tsv ... > NoSketchEngine/KIParla/vertical/source
"""

import argparse
import csv
import sys
from pathlib import Path


DEFAULT_BASE_URL = "https://search.corpuskiparla.it/corpus"


def build_url(base_url, path):
    base = base_url.rstrip("/")
    suffix = path.lstrip("/")
    if not base:
        return f"/{suffix}"
    return f"{base}/{suffix}"


def infer_artifacts_module(conversations_path):
    return Path(conversations_path).resolve().parent.name


def module_for_code(code, default_module):
    if default_module != "KIParla":
        return default_module
    if code.startswith(("BOA", "BOC", "BOD", "TOA", "TOC", "TOD")):
        return "KIP"
    if code.startswith(("KPC", "KPN", "KPS")):
        return "KIPasti"
    if code.startswith(("PBA", "PBB", "PBC")):
        return "ParlaBO"
    if code.startswith(("PTA", "PTB", "PTD")):
        return "ParlaTO"
    return default_module


def load_conversations(path):
    with open(path) as f:
        return {row["code"]: row for row in csv.DictReader(f, delimiter="\t")}


def load_participants(path):
    with open(path) as f:
        return {row["code"]: row for row in csv.DictReader(f, delimiter="\t")}


def parse_begin_end(align):
    """Extract begin and end times (in ms) from an align column value like 'Begin=0.41|End=4.437'."""
    begin = end = None
    for part in align.split("|"):
        if part.startswith("Begin="):
            begin = round(float(part[6:]) * 1000)
        elif part.startswith("End="):
            end = round(float(part[4:]) * 1000)
    return begin, end


def convert_file(
    tsv_path, conversations, participants, out, base_url, artifacts_base_url, artifacts_module
):
    code = Path(tsv_path).stem.split(".")[0]  # e.g. TOA1005 from TOA1005.vert.tsv

    conv = conversations.get(code, {})
    doc_module = module_for_code(code, artifacts_module)
    doc_url = build_url(artifacts_base_url, f"{doc_module}/html/{code}.html")

    print(
        f'<doc'
        f' doc_number="{code}"'
        f' full_conversation="{doc_url}"'
        f">",
        file=out,
    )
    print(
        f'<conversation'
        f' code="{code}"'
        f' type="{conv.get("type", "")}"'
        f' duration="{conv.get("duration", "")}"'
        f' participants_number="{conv.get("participants-number", "")}"'
        f' participants_relationship="{conv.get("participants-relationship", "")}"'
        f' moderator="{conv.get("moderator", "")}"'
        f' topic="{conv.get("topic", "")}"'
        f' year="{conv.get("year", "")}"'
        f' point="{conv.get("collection-point", "")}"'
        f">",
        file=out,
    )

    with open(tsv_path) as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    # Group rows by tu_id while preserving order
    tus: dict[str, list] = {}
    tu_order: list[str] = []
    for row in rows:
        tu_id = row["tu_id"]
        if tu_id not in tus:
            tus[tu_id] = []
            tu_order.append(tu_id)
        tus[tu_id].append(row)

    for tu_id in tu_order:
        tu_rows = tus[tu_id]
        speaker = tu_rows[0]["speaker"]
        part = participants.get(speaker, {})

        occupation = part.get("occupation", "")
        sex = part.get("gender", "")
        school_region = part.get("birth-region", "") or part.get("school-region", "")
        age_range = part.get("age-range", "")

        # Scan the TU for the first Begin= and last End= timestamp
        begin_ms: int | None = None
        end_ms: int | None = None
        for row in tu_rows:
            b, e = parse_begin_end(row["align"])
            if b is not None and begin_ms is None:
                begin_ms = b
            if e is not None:
                end_ms = e

        begin_str = str(begin_ms) if begin_ms is not None else ""
        end_str = str(end_ms) if end_ms is not None else ""
        audio_url = build_url(
            base_url, f"player/player.cgi?code={code}&begin={begin_str}&end={end_str}"
        )

        print(
            f'<annotation'
            f' begin="{begin_str}"'
            f' end="{end_str}"'
            f' audio_file="{audio_url}"'
            f' participant_code="{speaker}"'
            f' participant_occupation="{occupation}"'
            f' participant_sex="{sex}"'
            f' participant_school_region="{school_region}"'
            f' participant_age_range="{age_range}"'
            f">",
            file=out,
        )

        for row in tu_rows:
            form = row["form"]
            if form and form != "_":
                print(form, file=out)

        print("//", file=out)
        print("</annotation>", file=out)

    print("</conversation>", file=out)
    print("</doc>", file=out)


def main():
    parser = argparse.ArgumentParser(
        description="Convert KIParla vert.tsv files to NoSketch Engine vertical format."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "Base URL for search/player links, e.g. "
            "https://search.corpuskiparla.it/corpus or http://localhost:10070/corpus. "
            "Use /corpus for relative links."
        ),
    )
    parser.add_argument(
        "--artifacts-base-url",
        default=None,
        help=(
            "Base URL for published HTML artifacts, e.g. "
            "https://<org>.github.io/KIParla-artifacts. Defaults to --base-url."
        ),
    )
    parser.add_argument(
        "--artifacts-module",
        default=None,
        help=(
            "Module segment for artifacts URLs, e.g. KIP or KIParla. "
            "Defaults to the parent directory name of conversations.tsv."
        ),
    )
    parser.add_argument("conversations", help="Path to conversations.tsv metadata file")
    parser.add_argument("participants", help="Path to participants.tsv metadata file")
    parser.add_argument("input", nargs="+", help="Input vert.tsv file(s)")
    args = parser.parse_args()

    conversations = load_conversations(args.conversations)
    participants_data = load_participants(args.participants)
    artifacts_base_url = args.artifacts_base_url or args.base_url
    artifacts_module = args.artifacts_module or infer_artifacts_module(args.conversations)

    for tsv_path in args.input:
        convert_file(
            tsv_path,
            conversations,
            participants_data,
            sys.stdout,
            args.base_url,
            artifacts_base_url,
            artifacts_module,
        )


if __name__ == "__main__":
    main()
