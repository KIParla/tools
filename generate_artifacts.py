#!/usr/bin/env python3
"""
generate_artifacts.py – Batch-run linear2html.py over every conversation in a module.

Loops the codes found in <module-root>/linear-orthographic (or
linear-jefferson, if orthographic is absent), and for each one invokes
linear2html.py with the matching linear-orthographic/linear-jefferson/tsv
files plus module metadata. If <module-root>/translations/<code>.translations.json
(or .tsv) exists, it is passed along too — translations are optional per
conversation, not required for the whole module.

Usage:
    python3 tools/generate_artifacts.py \\
        --module-root Stra-ParlaTO \\
        --artifacts-root KIParla-artifacts

    python3 tools/generate_artifacts.py \\
        --module-root Stra-ParlaBO \\
        --artifacts-root KIParla-artifacts \\
        --module Stra-ParlaBO
"""

import argparse
import subprocess
import sys
from pathlib import Path

import tqdm

import args_check as ac


def _codes(module_root: Path) -> list[str]:
    orth_dir = module_root / "linear-orthographic"
    jeff_dir = module_root / "linear-jefferson"
    src_dir = orth_dir if orth_dir.is_dir() else jeff_dir
    return sorted(p.stem for p in src_dir.glob("*.txt"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module-root", required=True, type=ac.valid_dirpath,
                     help="Module source root, e.g. Stra-ParlaTO")
    ap.add_argument("--artifacts-root", required=True,
                     help="Root of KIParla-artifacts")
    ap.add_argument("--module",
                     help="Module name for artifacts layout. Defaults to --module-root's directory name.")
    args = ap.parse_args()

    module_root = args.module_root
    module_name = args.module or module_root.name

    orth_dir = module_root / "linear-orthographic"
    jeff_dir = module_root / "linear-jefferson"
    tsv_dir = module_root / "tsv"
    translations_dir = module_root / "translations"
    conversations = module_root / "metadata" / "conversations.tsv"
    participants = module_root / "metadata" / "participants.tsv"

    codes = _codes(module_root)
    if not codes:
        print(f"[error] no conversations found under {orth_dir} or {jeff_dir}", file=sys.stderr)
        sys.exit(1)

    failures = []
    for code in tqdm.tqdm(codes, desc=f"linear2html [{module_name}]"):
        cmd = [
            sys.executable, str(Path(__file__).parent / "linear2html.py"),
            "--conversations", str(conversations),
            "--participants", str(participants),
            "--artifacts-root", args.artifacts_root,
            "--module", module_name,
        ]

        orth_file = orth_dir / f"{code}.txt"
        jeff_file = jeff_dir / f"{code}.txt"
        if orth_file.is_file():
            cmd += ["--orthographic", str(orth_file)]
        if jeff_file.is_file():
            cmd += ["--jefferson", str(jeff_file)]

        tsv_file = tsv_dir / f"{code}.vert.tsv"
        if tsv_file.is_file():
            cmd += ["--tsv", str(tsv_file)]

        translations_json = translations_dir / f"{code}.translations.json"
        translations_tsv = translations_dir / f"{code}.translations.tsv"
        if translations_json.is_file():
            cmd += ["--translations", str(translations_json)]
        elif translations_tsv.is_file():
            cmd += ["--translations", str(translations_tsv)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(code)
            print(f"[error] {code} failed:\n{result.stderr}", file=sys.stderr)

    print(f"Done: {len(codes) - len(failures)}/{len(codes)} conversations written to "
          f"{args.artifacts_root}/{module_name}/")
    if failures:
        print(f"[error] {len(failures)} failures: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
