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
    [--artifacts-module ARTIFACTS_MODULE] [--issues-base-url ISSUES_BASE_URL]
    [--translations-dir TRANSLATIONS_DIR]
    CONVERSATIONS_TSV PARTICIPANTS_TSV INPUT_TSV [INPUT_TSV ...]
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape


DEFAULT_BASE_URL = "https://search.corpuskiparla.it/corpus"
DEFAULT_ISSUES_BASE_URL = "https://github.com/KIParla"

# Extra positional (per-token) attributes emitted after `word` and `token_id`,
# in this order. Empty when the token is not annotated. `upos` also drives the
# conversation-level `linguistic_annotation` flag.
LINGUISTIC_ATTRS = ("lemma", "upos")


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


def _xml_attr(value):
    """Escape a value for use inside a double-quoted XML attribute."""
    return escape(str(value), {'"': "&quot;"})


def _pos_value(row, key):
    """Positional-attribute value from a TSV row: CoNLL-U '_' / missing -> ''."""
    value = (row.get(key) or "").strip()
    return "" if value == "_" else value


def is_shortpause(row):
    return row.get("type") == "shortpause" or (row.get("form") or "").strip() == "(.)"


def is_nvb(row):
    form = (row.get("form") or "").strip()
    return row.get("type") == "nonverbalbehavior" or (
        form.startswith("((") and form.endswith("))")
    )


def nvb_descr(form):
    """'((versa_il_tè))' -> 'versa il tè'; '((_ride))' -> 'ride'."""
    return form.strip().removeprefix("((").removesuffix("))").strip("_").replace("_", " ")


def load_translations(tsv_path, translations_dir):
    """Return {parent_tu_id: translation_text} for one conversation, or {}.

    Looks for ``<CODE>.translations.json`` in ``translations_dir`` if given,
    otherwise next to the input file at ``../translations/<CODE>.translations.json``.
    Rows come from ``serialize.write_translations_json`` and carry
    ``parent_tu_id`` (the id of the original transcription unit) and ``text``.
    """
    code = Path(tsv_path).stem.split(".")[0]
    if translations_dir:
        path = Path(translations_dir) / f"{code}.translations.json"
    else:
        path = Path(tsv_path).resolve().parent.parent / "translations" / f"{code}.translations.json"
    if not path.is_file():
        return {}

    out = {}
    for row in json.loads(path.read_text()):
        parent = row.get("parent_tu_id")
        text = (row.get("text") or "").strip()
        if not parent or not text:
            continue
        out[parent] = f"{out[parent]} {text}".strip() if parent in out else text
    return out


def build_report_url(issues_base_url, module, code, tu_id, speaker, begin_str, end_str, doc_url):
    """Prefilled "open a GitHub issue about this transcription unit" URL.

    Points at the issue tracker of the conversation's own module repository
    (mapped by code prefix for the aggregated KIParla corpus).
    """
    body = (
        f"Conversazione: {code}\n"
        f"Unità di trascrizione: {tu_id}\n"
        f"Parlante: {speaker}\n"
        f"Intervallo: {begin_str}–{end_str} ms\n"
        f"Trascrizione completa: {doc_url}\n\n"
        "<!-- Descrivi il problema (token, trascrizione, allineamento, metadati...). -->\n"
    )
    query = urlencode({
        "title": f"[{code}] problema di trascrizione — TU {tu_id}",
        "body": body,
    })
    return f"{issues_base_url.rstrip('/')}/{module}/issues/new?{query}"


def convert_file(
    tsv_path, conversations, participants, out, base_url, artifacts_base_url,
    artifacts_module, issues_base_url, translations_dir,
):
    code = Path(tsv_path).stem.split(".")[0]

    conv = conversations.get(code, {})
    doc_module = module_for_code(code, artifacts_module)
    doc_url = build_url(artifacts_base_url, f"{doc_module}/html/{code}.html")
    translations = load_translations(tsv_path, translations_dir)

    with open(tsv_path) as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    # A conversation is "annotated" when any of its tokens carries a UPOS tag.
    has_linguistic = any(_pos_value(row, "upos") for row in rows)

    conversation_attrs = "".join(
        f' {name}="{value}"'
        for name, value in [
            *iter_conversation_attrs(conv, code, doc_url),
            ("linguistic_annotation", "yes" if has_linguistic else "no"),
        ]
    )
    print(f"<conversation{conversation_attrs}>", file=out)

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
        if issues_base_url:
            transcription_unit_attrs.append((
                "report_url",
                build_report_url(
                    issues_base_url, doc_module, code, tu_id, speaker,
                    begin_str, end_str, doc_url,
                ),
            ))
        if tu_id in translations:
            transcription_unit_attrs.append(
                ("translation", _xml_attr(translations[tu_id]))
            )
        attrs_str = "".join(f' {name}="{value}"' for name, value in transcription_unit_attrs)

        # Build the unit body first. Short pauses and non-verbal behaviour become
        # zero-width structures rather than tokens, so they never break token
        # adjacency (`the (.) dog` / `the ((ride)) dog` still match `"the" "dog"`).
        body = []
        has_token = False
        for row in tu_rows:
            form = row["form"]
            if is_shortpause(row):
                body.append("<pause/>")
                continue
            if is_nvb(row):
                body.append(f'<nvb descr="{_xml_attr(nvb_descr(form))}"/>')
                continue
            token_id = row.get("token_id", "") or ""
            if form and form != "_":
                fields = [form, token_id, *(_pos_value(row, a) for a in LINGUISTIC_ATTRS)]
                body.append("\t".join(fields))
                has_token = True
                if has_space_after_no(row):
                    body.append("<g/>")

        # A transcription_unit structure must span at least one token. Units that
        # are pure pause / non-verbal behaviour keep their markers but drop the
        # (empty) wrapper.
        if has_token:
            print(f"<transcription_unit{attrs_str}>", file=out)
            print("\n".join(body), file=out)
            print("</transcription_unit>", file=out)
        elif body:
            print("\n".join(body), file=out)

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
    parser.add_argument(
        "--issues-base-url",
        default=DEFAULT_ISSUES_BASE_URL,
        help=(
            "GitHub org/user URL used to build the per-transcription-unit "
            "'report_url' attribute: <issues-base-url>/<MODULE>/issues/new?... "
            "Pass an empty string to omit the attribute."
        ),
    )
    parser.add_argument(
        "--translations-dir",
        default=None,
        help=(
            "Directory with <CODE>.translations.json files. A translated "
            "transcription unit gets a 'translation' structure attribute. "
            "Defaults to a 'translations/' directory next to each input file's "
            "'tsv/' directory."
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
            args.issues_base_url,
            args.translations_dir,
        )


if __name__ == "__main__":
    main()
