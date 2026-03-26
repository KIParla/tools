#!/usr/bin/env python3
"""
Merge participant and conversation metadata from KIParla module repos
into the KIParla-collection metadata files.

Usage:
    python merge_metadata.py \
        --modules /path/to/KIP /path/to/KIPasti /path/to/ParlaBO /path/to/ParlaTO \
        --output-dir /path/to/KIParla-collection/metadata

Each module directory must contain a metadata/ subdirectory with
participants.tsv and conversations.tsv.

Column normalisation applied before merging:
  - participants: KIP uses 'school-region' → renamed to 'birth-region'
  - Missing target columns are filled with an empty string.
  - Extra columns not in the target set are dropped.
  - Duplicate codes (same speaker/conversation across modules) are deduplicated,
    keeping the first occurrence.
"""

import argparse
import os
import sys
import pandas as pd


# Columns kept in the merged output
PARTICIPANTS_COLS = [
    "code",
    "occupation",
    "gender",
    "conversations",
    "birth-region",
    "age-range",
    "study-level",
]

CONVERSATIONS_COLS = [
    "code",
    "type",
    "duration",
    "participants-number",
    "participants",
    "participants-relationship",
    "moderator",
    "topic",
    "year",
    "collection-point",
]

# Per-module column renames applied before selecting target columns.
# Keys are module directory basenames (case-insensitive).
COLUMN_RENAMES = {
    "kip": {
        "school-region": "birth-region",
    },
}


def load_tsv(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        print(f"  [skip] {path} not found", file=sys.stderr)
        return pd.DataFrame()
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)


def normalise(df: pd.DataFrame, module_name: str, target_cols: list[str]) -> pd.DataFrame:
    # Apply module-specific renames
    renames = COLUMN_RENAMES.get(module_name.lower(), {})
    df = df.rename(columns=renames)

    # Add missing target columns as empty strings
    for col in target_cols:
        if col not in df.columns:
            df[col] = ""

    return df[target_cols]


def merge(module_dirs: list[str], filename: str, target_cols: list[str]) -> pd.DataFrame:
    frames = []
    for module_dir in module_dirs:
        module_name = os.path.basename(module_dir.rstrip("/"))
        path = os.path.join(module_dir, "metadata", filename)
        df = load_tsv(path)
        if df.empty:
            continue
        df = normalise(df, module_name, target_cols)
        frames.append(df)
        print(f"  loaded {len(df)} rows from {module_name}/{filename}")

    if not frames:
        print(f"No data found for {filename}", file=sys.stderr)
        sys.exit(1)

    merged = pd.concat(frames, ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates(subset=["code"], keep="first")
    after = len(merged)
    if before != after:
        print(f"  deduplicated {before - after} rows in {filename}")
    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules", nargs="+", required=True,
                    help="Paths to module root directories (e.g. /tmp/KIP /tmp/KIPasti ...)")
    ap.add_argument("--output-dir", required=True,
                    help="Directory where merged participants.tsv and conversations.tsv are written")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    for filename, target_cols in [
        ("participants.tsv", PARTICIPANTS_COLS),
        ("conversations.tsv", CONVERSATIONS_COLS),
    ]:
        print(f"\nMerging {filename}...")
        merged = merge(args.modules, filename, target_cols)
        out_path = os.path.join(args.output_dir, filename)
        merged.to_csv(out_path, sep="\t", index=False)
        print(f"  wrote {len(merged)} rows → {out_path}")


if __name__ == "__main__":
    main()
