#!/usr/bin/env python3
"""
Translate KIParla collection metadata from English to Italian.

Reads participants.tsv and conversations.tsv from the merged collection,
applies value-level translations defined in a translations TSV, and writes
Italian-labelled copies suitable for NoSketchEngine corpus configuration.

Usage:
    python translate_metadata.py \
        --input-dir /path/to/KIParla-collection/metadata \
        --output-dir /path/to/NoSketchEngine/metadata \
        --translations /path/to/NoSketchEngine/translations.tsv

The translations file is a TSV with columns: table, column, en, it
  table   – 'participants' or 'conversations'
  column  – column name the mapping applies to
  en      – English source value
  it      – Italian target value

Semicolon-separated values (e.g. the 'languages' column) are split and
each token translated individually before rejoining.

Note: type subtypes (e.g. 'free-conversation:meal') are split on ':' and
each part translated individually, so both the top-level type and any
subtype need entries in the translations file.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

# Columns whose values are semicolon-separated tokens, each translated individually
MULTIVALUED: set[str] = {"languages"}

# Columns whose values are colon-separated tokens (type subtypes)
COLON_SEPARATED: set[str] = {"type"}


def load_translations(path: str) -> dict[str, dict[str, dict[str, str]]]:
    """Load translations.tsv → {table: {column: {en: it}}}."""
    result: dict = defaultdict(lambda: defaultdict(dict))
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            result[row["table"]][row["column"]][row["en"]] = row["it"]
    return result


def translate_value(value: str, mapping: dict[str, str], col: str) -> str:
    if col in MULTIVALUED and ";" in value:
        return ";".join(mapping.get(token, token) for token in value.split(";"))
    if col in COLON_SEPARATED and ":" in value:
        return ":".join(mapping.get(token, token) for token in value.split(":"))
    return mapping.get(value, value)


def translate_file(
    input_path: str,
    output_path: str,
    table_name: str,
    translations: dict,
) -> int:
    table_trans = translations.get(table_name, {})

    with open(input_path, newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        print(f"  [warn] {input_path} is empty", file=sys.stderr)
        return 0

    untranslated: dict[str, set] = defaultdict(set)

    for row in rows:
        for col, mapping in table_trans.items():
            if col not in row:
                continue
            original = row[col]
            row[col] = translate_value(original, mapping, col)
            # Flag values that passed through unchanged and have no mapping entry
            if row[col] == original:
                tokens = (
                    original.split(";") if col in MULTIVALUED
                    else original.split(":") if col in COLON_SEPARATED
                    else [original]
                )
                for token in tokens:
                    if token and token not in mapping:
                        untranslated[col].add(token)

    for col, vals in sorted(untranslated.items()):
        for v in sorted(vals):
            print(f"  [warn] {table_name}.{col}: no translation for {v!r}", file=sys.stderr)

    with open(output_path, "w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing merged participants.tsv and conversations.tsv",
    )
    ap.add_argument(
        "--output-dir",
        required=True,
        help="Directory where Italian-translated TSV files are written",
    )
    ap.add_argument(
        "--translations",
        required=True,
        help="Path to translations.tsv",
    )
    args = ap.parse_args()

    if not os.path.isfile(args.translations):
        print(f"Translations file not found: {args.translations}", file=sys.stderr)
        sys.exit(1)

    translations = load_translations(args.translations)
    os.makedirs(args.output_dir, exist_ok=True)

    for filename, table_name in [
        ("participants.tsv", "participants"),
        ("conversations.tsv", "conversations"),
    ]:
        in_path = os.path.join(args.input_dir, filename)
        out_path = os.path.join(args.output_dir, filename)
        if not os.path.isfile(in_path):
            print(f"  [skip] {in_path} not found", file=sys.stderr)
            continue
        print(f"Translating {filename}...")
        n = translate_file(in_path, out_path, table_name, translations)
        print(f"  wrote {n} rows → {out_path}")


if __name__ == "__main__":
    main()
