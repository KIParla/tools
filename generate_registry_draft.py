#!/usr/bin/env python3
"""
generate_registry_draft.py - Generate a draft NoSketch Engine registry from
KIParla metadata headers.

This script is intentionally conservative: it emits a usable draft aligned with
the attribute names produced by tsv2vert_v2.py, but it does not try to guess
all UI choices perfectly.
"""

import argparse
import csv
from pathlib import Path


MAXLISTSIZE_ZERO = {
    "full_conversation",
    "duration",
    "begin",
    "end",
    "audio_file",
}

CUSTOM_LABELS = {
    "code": "Codice conversazione",
    "full_conversation": "Trascrizione completa",
    "duration": "Durata",
    "topic": "Argomento",
    "participant_code": "Codice partecipante",
    "begin": "Inizio",
    "end": "Fine",
    "audio_file": "Audio",
}

MULTIVALUE_ATTRS = {
    "languages",
}

EXCLUDED_SUBCORPATTRS = {
    "code",
    "full_conversation",
    "duration",
    "begin",
    "end",
    "audio_file",
}


def read_headers(path):
    with open(path) as f:
        return next(csv.reader(f, delimiter="\t"))


def normalize_attr_name(name):
    return name.replace("-", "_")


def label_for(attr_name):
    return CUSTOM_LABELS.get(attr_name, attr_name.replace("_", " ").strip().title())


def attribute_block(name, indent="    "):
    lines = [f"{indent}ATTRIBUTE {name}"]
    body = []

    body.append(f'{indent}    LABEL "{label_for(name)}"')

    if name in MAXLISTSIZE_ZERO:
        body.append(f'{indent}    MAXLISTSIZE "0"')

    if name in MULTIVALUE_ATTRS:
        body.append(f'{indent}    MULTIVALUE "1"')
        body.append(f'{indent}    MULTISEP   ","')

    if not body:
        return lines[0]

    return "\n".join([f"{lines[0]} {{", *body, f"{indent}}}"])


def main():
    parser = argparse.ArgumentParser(description="Generate a draft registry file.")
    parser.add_argument("corpus_name", help="Corpus name, e.g. KIP")
    parser.add_argument("conversations", help="Path to conversations.tsv")
    parser.add_argument("participants", help="Path to participants.tsv")
    parser.add_argument("--maintainer", default="caterina.mauri@unibo.it")
    args = parser.parse_args()

    conv_headers = read_headers(args.conversations)
    part_headers = read_headers(args.participants)

    conversation_attrs = ["code", "full_conversation"]
    conversation_attrs.extend(
        normalize_attr_name(h) for h in conv_headers if h not in {"code", "participants"}
    )

    tu_attrs = ["participant_code", "begin", "end", "audio_file"]
    tu_attrs.extend(
        f'participant_{normalize_attr_name(h)}'
        for h in part_headers
        if h not in {"code", "conversations"}
    )

    subcorpattrs = [
        f"conversation.{attr}"
        for attr in conversation_attrs
        if attr not in EXCLUDED_SUBCORPATTRS
    ]
    subcorpattrs.extend(
        f"transcription_unit.{attr}"
        for attr in tu_attrs
        if attr not in EXCLUDED_SUBCORPATTRS
    )

    print(f'MAINTAINER "{args.maintainer}"')
    print(f'INFO       "{args.corpus_name}"')
    print(f'NAME       "{args.corpus_name}"')
    print(f"PATH       '/corpora/{args.corpus_name}/indexed/'")
    print('ENCODING   "UTF-8"')
    print('LANGUAGE   "Italian"')
    print(f"VERTICAL   '/corpora/{args.corpus_name}/vertical/source'")
    print('INFOHREF   "https://www.kiparla.it/"')
    print("DOCSTRUCTURE conversation")
    print()
    print("ATTRIBUTE word")
    print(attribute_block("token_id", indent=""))
    print('ATTRIBUTE lc {')
    print('    LABEL      "word (lowercase)"')
    print('    DYNAMIC    utf8lowercase')
    print('    DYNLIB     internal')
    print('    ARG1       "C"')
    print('    FUNTYPE    s')
    print('    FROMATTR   word')
    print('    TYPE       index')
    print('    TRANSQUERY yes')
    print("}")
    print()
    print("STRUCTURE conversation {")
    for attr in conversation_attrs:
        print(attribute_block(attr))
    print("}")
    print()
    print("STRUCTURE transcription_unit {")
    for attr in tu_attrs:
        print(attribute_block(attr))
    print('    DISPLAYTAG 0')
    print('    DISPLAYBEGIN "·[%(participant_code)]·"')
    print('    DISPLAYEND   ""')
    print('    NESTED 1')
    print("}")
    print()
    print(f'SUBCORPATTRS "{",".join(subcorpattrs)}"')
    print('SHORTREF     "=conversation.code"')


if __name__ == "__main__":
    main()
