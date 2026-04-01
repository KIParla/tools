#!/usr/bin/env python3
"""
Convert ELAN (.eaf) annotation files to CSV.

Each annotation tier becomes a speaker and each annotation becomes one row,
with columns:

  tu_id     sequential integer (0-based), assigned after sorting by start time
  speaker   ELAN tier ID
  start     annotation start time in seconds (3 decimal places)
  end       annotation end time in seconds (3 decimal places)
  duration  annotation duration in seconds (3 decimal places)
  text      annotation text (id:N prefix stripped if present)

Usage:
  python eaf2csv.py --input-files FILE [FILE ...]  -o OUTPUT_DIR
  python eaf2csv.py --input-dir DIR               -o OUTPUT_DIR
  python eaf2csv.py --input-dir DIR --annotations-dir ANNOT_DIR -o OUTPUT_DIR

Annotations files (--annotations-dir):
  For each input FILE.eaf the script looks for FILE.yml in the annotations
  directory. The YAML may contain an 'ignore' key whose value is a list of
  strings, each string being a space-separated pair of original annotation IDs
  that should not be treated as overlapping during later processing. The IDs
  are remapped to the new sequential tu_id values and the YAML is written back.
"""

import argparse
import csv
import pathlib
import re
import sys

import yaml
from speach import elan


FIELDNAMES = ["tu_id", "speaker", "start", "end", "duration", "text"]


def convert(input_path, output_path, annotations=None, sep="\t"):
    """
    Convert a single EAF file to CSV.

    :param input_path:   path to the .eaf file
    :param output_path:  path to the output .csv file (will be overwritten)
    :param annotations:  dict loaded from the accompanying .yml file, or {}
    :param sep:          column delimiter for the output CSV
    """
    if annotations is None:
        annotations = {}

    rows = []
    doc = elan.read_eaf(input_path)

    for tier in doc:
        for anno in tier.annotations:
            start = f"{anno.from_ts.sec:.3f}" if anno.from_ts is not None else ""
            end   = f"{anno.to_ts.sec:.3f}"   if anno.to_ts   is not None else ""
            dur   = f"{anno.duration:.3f}"     if anno.duration is not None else ""

            # Annotations written by the pipeline may carry an 'id:N ' prefix
            # (e.g. "id:15 ciao ciao"). Extract it so downstream tools can
            # correlate rows with their original EAF annotation IDs.
            parts = re.split(r"^(id:)([0-9]+) ", anno.value.strip())
            text = parts[-1]
            orig_id = parts[2] if len(parts) > 1 else None

            rows.append({
                "speaker":  tier.ID,
                "start":    start,
                "end":      end,
                "duration": dur,
                "text":     text,
                "_orig_id": orig_id,
            })

    rows.sort(key=lambda r: float(r["start"]) if r["start"] else 0.0)

    # Build a mapping from original annotation ID → new sequential tu_id,
    # used to remap 'ignore' pairs in the annotations YAML.
    id_remap = {}

    with open(output_path, "w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=FIELDNAMES, delimiter=sep,
                                extrasaction="ignore")
        writer.writeheader()
        for tu_id, row in enumerate(rows):
            row["tu_id"] = tu_id
            if row["_orig_id"] is not None:
                id_remap[row["_orig_id"]] = tu_id
            writer.writerow(row)

    # Remap 'ignore' pairs so they refer to the new tu_id values
    if "ignore" in annotations:
        for pos, pair_str in enumerate(annotations["ignore"]):
            new_ids = []
            for orig in pair_str.split():
                new_ids.append(str(id_remap.get(orig, orig)))
            annotations["ignore"][pos] = " ".join(new_ids)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="output/",
        type=pathlib.Path,
        help="Directory where output CSV files are written (default: output/).",
    )
    parser.add_argument(
        "--annotations-dir",
        type=pathlib.Path,
        metavar="DIR",
        help=(
            "Directory containing per-file YAML annotation files. "
            "For each input FILE.eaf the script loads FILE.yml if present, "
            "remaps any 'ignore' IDs to the new tu_id numbering, "
            "and writes the updated YAML back."
        ),
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input-files",
        nargs="+",
        type=pathlib.Path,
        metavar="FILE",
        help="One or more .eaf files to convert.",
    )
    group.add_argument(
        "--input-dir",
        type=pathlib.Path,
        metavar="DIR",
        help="Directory of .eaf files; all will be converted.",
    )

    args = parser.parse_args()

    if args.input_dir:
        input_files = sorted(args.input_dir.glob("*.eaf"))
        if not input_files:
            print(f"error: no .eaf files found in {args.input_dir}", file=sys.stderr)
            sys.exit(1)
    else:
        input_files = [pathlib.Path(f) for f in args.input_files]

    args.output_dir.mkdir(parents=True, exist_ok=True)

    for input_path in input_files:
        print(f"  {input_path.name} ...", file=sys.stderr)
        output_path = args.output_dir / f"{input_path.stem}.csv"

        annotations = {}
        annot_path = None
        if args.annotations_dir:
            annot_path = args.annotations_dir / f"{input_path.stem}.yml"
            if annot_path.is_file():
                with open(annot_path, encoding="utf-8") as f:
                    annotations = yaml.safe_load(f) or {}

        convert(input_path, output_path, annotations)

        if annot_path is not None and annotations:
            with open(annot_path, "w", encoding="utf-8") as f:
                yaml.dump(annotations, f, indent=2)

    print("done.", file=sys.stderr)


if __name__ == "__main__":
    main()
