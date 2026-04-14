#!/usr/bin/env python3
"""
tsv2vert_v2.py - Convert KIParla vert.tsv files to NoSketch Engine vertical format
using only the <conversation> structure level.

This variant removes the outer <doc> wrapper used by tsv2vert.py, stores
document-level links directly on the <conversation> element, and renames
<annotation> to <transcription_unit>.

Metadata (conversations.tsv, participants.tsv) must already be translated into
Italian via translate_metadata.py before being passed to this script.

Usage:
    python tools/tsv2vert_v2.py [--base-url BASE_URL] [--artifacts-base-url ARTIFACTS_BASE_URL]
    [--artifacts-module ARTIFACTS_MODULE] CONVERSATIONS_TSV PARTICIPANTS_TSV INPUT_TSV [INPUT_TSV ...]
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


def normalize_attr_name(name):
    """Convert metadata column names to vertical-safe attribute names."""
    return name.replace("-", "_")


def parse_begin_end(align):
    """Extract begin and end times (in ms) from values like 'Begin=0.41|End=4.437'."""
    begin = end = None
    for part in align.split("|"):
        if part.startswith("Begin="):
            begin = round(float(part[6:]) * 1000)
        elif part.startswith("End="):
            end = round(float(part[4:]) * 1000)
    return begin, end


def format_files_field(conversations_str):
    """'TOD2015;TOA1004;TOD2015' -> 'TOA1004, TOD2015'."""
    parts = sorted(set(p.strip() for p in conversations_str.split(";") if p.strip()))
    return ", ".join(parts)


def normalize_multivalue_value(value):
    """Normalize semicolon-separated metadata values to comma-separated lists."""
    if value is None:
        return ""
    if ";" not in value:
        return value

    parts = []
    seen = set()
    for part in value.split(";"):
        cleaned = part.strip()
        if cleaned and cleaned not in seen:
            parts.append(cleaned)
            seen.add(cleaned)
    return ",".join(parts)


def has_space_after_no(row):
    """Return True when a TSV row marks the token as glued to the next one."""
    for value in row.values():
        if value and "SpaceAfter=No" in value:
            return True
    return False


def iter_conversation_attrs(conv, code, doc_url):
    yield ("code", code)
    yield ("full_conversation", doc_url)

    for key, value in conv.items():
        if key in {"code", "participants"}:
            continue

        attr_name = normalize_attr_name(key)
        attr_value = value

        attr_value = normalize_multivalue_value(value)

        yield (attr_name, attr_value)


def iter_participant_attrs(part):
    for key, value in part.items():
        if key in {"code", "conversations"}:
            continue

        attr_name = f'participant_{normalize_attr_name(key)}'
        attr_value = normalize_multivalue_value(value)

        yield (attr_name, attr_value)


def convert_file(
    tsv_path, conversations, participants, out, base_url, artifacts_base_url, artifacts_module
):
    code = Path(tsv_path).stem.split(".")[0]

    conv = conversations.get(code, {})
    doc_module = module_for_code(code, artifacts_module)
    doc_url = build_url(artifacts_base_url, f"{doc_module}/html/{code}.html")

    conversation_attrs = "".join(
        f' {name}="{value}"'
        for name, value in iter_conversation_attrs(conv, code, doc_url)
    )
    print(f"<conversation{conversation_attrs}>", file=out)

    with open(tsv_path) as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    tus = {}
    tu_order = []
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

        begin_ms = None
        end_ms = None
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

        transcription_unit_attrs = [
            ("begin", begin_str),
            ("end", end_str),
            ("audio_file", audio_url),
            ("participant_code", speaker),
            *iter_participant_attrs(part),
        ]
        attrs_str = "".join(f' {name}="{value}"' for name, value in transcription_unit_attrs)
        print(f"<transcription_unit{attrs_str}>", file=out)

        for row in tu_rows:
            form = row["form"]
            token_id = row.get("token_id", "") or ""
            if form and form != "_":
                print(f"{form}\t{token_id}", file=out)
                if has_space_after_no(row):
                    print("<g/>", file=out)

        print("</transcription_unit>", file=out)

    print("</conversation>", file=out)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert KIParla vert.tsv files to NoSketch Engine vertical format "
            "using only the conversation structure."
        )
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
