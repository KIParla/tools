"""
Full pipeline demo using the first 5 minutes of BOA1001.

Steps demonstrated:
  1. eaf2csv   — convert demo/BOA1001.eaf  →  demo/BOA1001.csv
  2. process   — run the 8-stage pipeline  →  demo/BOA1001.vert.tsv
                                              demo/BOA1001.csv (TU summary)
                                              demo/BOA1001.json
  3. csv2eaf   — round-trip back           →  demo/BOA1001.roundtrip.eaf

Run from the tools/ directory:
    python3.9 demo/demo.py
"""
from __future__ import annotations

import json
import pathlib
import sys

# Allow running from any location as long as tools/ is on the path
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import serialize

DEMO_DIR  = pathlib.Path(__file__).parent
EAF_IN    = DEMO_DIR / "BOA1001.eaf"
CSV_OUT   = DEMO_DIR / "BOA1001.csv"
VERT_OUT  = DEMO_DIR / "BOA1001.vert.tsv"
JSON_OUT  = DEMO_DIR / "BOA1001.json"
EAF_OUT   = DEMO_DIR / "BOA1001.roundtrip.eaf"


def step1_eaf2csv():
    print("── Step 1: EAF → CSV ─────────────────────────────────")
    serialize.eaf2csv(EAF_IN, CSV_OUT, annotations={})
    rows = list(open(CSV_OUT, encoding="utf-8"))
    print(f"   {len(rows) - 1} transcription units written to {CSV_OUT.name}")


def step2_process():
    print("── Step 2: Process pipeline (steps 1–8) ─────────────")
    summary = serialize.process(
        input_path=CSV_OUT,
        output_dir=DEMO_DIR,
        cfg={},
        annotations={},
    )
    speaker_ids = list(summary.get("speakers", {}).keys())
    total_tokens = sum(v.get("tokens", 0) for v in summary.get("speakers", {}).values())
    print(f"   Speakers : {speaker_ids}")
    print(f"   TUs      : {summary.get('TUs', '?')}")
    print(f"   Tokens   : {total_tokens}")
    print(f"   Warnings : {summary.get('WARNINGS', '?')}")
    print(f"   Errors   : {summary.get('ERRORS', '?')}")
    print(f"   Written  : {VERT_OUT.name}, {JSON_OUT.name}")


def step3_csv2eaf():
    print("── Step 3: CSV → EAF round-trip ──────────────────────")
    # Use the TU-summary CSV produced by process()
    tus_csv = DEMO_DIR / "BOA1001.csv"
    serialize.csv2eaf(
        input_filename=tus_csv,
        linked_file="BOA1001.wav",
        output_filename=EAF_OUT,
        sep="\t",
        multiplier=1000,
        include_ids=True,
    )
    print(f"   Written  : {EAF_OUT.name}")


def show_sample_vert():
    print("── Sample output (first 10 token rows) ───────────────")
    with open(VERT_OUT, encoding="utf-8") as f:
        reader = __import__("csv").DictReader(f, delimiter="\t")
        for i, row in enumerate(reader):
            if i >= 10:
                break
            print(f"   {row['token_id']:<14} {row['speaker']:<8} {row['span']:<20} {row['type']}")


if __name__ == "__main__":
    print(f"\nKIParla pipeline demo — {EAF_IN.name} (first 5 min)\n")
    step1_eaf2csv()
    step2_process()
    step3_csv2eaf()
    show_sample_vert()
    print("\nDone. Output files are in demo/")
