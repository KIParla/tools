#!/usr/bin/env python3
"""
sync.py — One-shot, one-directional sync between a single .eaf and its .vert.tsv.

Usage:
    python sync.py --from-eaf <path/to/X.eaf> [--module NAME]
    python sync.py --from-vert <path/to/X.vert.tsv> [--module NAME]

Run manually after an editing session (opening/saving in ELAN, or
hand-editing a vert.tsv) to propagate the change and refresh derived
artifacts. Each direction is one-shot and one-directional: regenerating
TO a format never re-triggers regeneration OF that format, so there's no
ping-pong between the two commands.

  --from-eaf <X.eaf>:
    eaf2csv -> process (writes tsv/X.vert.tsv, translations/X.translations.*,
    tmp/process/{csv,json}/X.* and updates tmp/process/json/summary.json)
    -> tsv2formats (linear-jefferson/orthographic) -> check_participants
    (this module) -> generate_validation_report (all modules).

  --from-vert <X.vert.tsv>:
    vert2eaf (overwrites eaf/X.eaf in place, reattaching
    translations/X.translations.json if present) -> tsv2formats
    -> check_participants -> generate_validation_report.

    NOTE: pipeline warnings/errors in the validation report reflect the
    last full `process` run for this file, not this hand-edit -- refreshing
    them would require reprocessing the freshly-written eaf, which is
    exactly the reverse-direction auto-trigger this script avoids.

Module resolution: inferred from the path (a file at <module>/eaf/X.eaf or
<module>/tsv/X.vert.tsv resolves module dir to <module>), with the config
name derived by stripping "-" (Stra-ParlaBO -> StraParlaBO). Override with
--module if a module's directory name doesn't map that way.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import config as config_mod
import serialize
import tsv2formats
from check_participants import check_module, add_unknown_participant_column, report as report_module
from generate_validation_report import discover_modules, generate_report


def _resolve_module(file_path: Path, module_override: str | None) -> tuple[Path, str]:
    """Infer (module_dir, module_config_name) from a file under <module>/eaf/
    or <module>/tsv/."""
    module_dir = file_path.resolve().parent.parent
    if not (module_dir / "metadata" / "conversations.tsv").is_file():
        raise SystemExit(
            f"Could not resolve a module directory from {file_path} "
            f"(expected {module_dir} to contain metadata/conversations.tsv). "
            f"Pass --module explicitly if the layout differs."
        )
    module_name = module_override or module_dir.name.replace("-", "")
    return module_dir, module_name


def _update_summary_json(reports_dir: Path, entry: dict) -> None:
    """Insert/replace *entry* (a build_json() dict) in reports_dir/summary.json."""
    summary_path = reports_dir / "summary.json"
    data = []
    if summary_path.is_file():
        with summary_path.open(encoding="utf-8") as f:
            data = json.load(f)
    data = [d for d in data if d.get("transcript") != entry["transcript"]]
    data.append(entry)
    data.sort(key=lambda d: d["transcript"])
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _refresh_module_checks(module_dir: Path) -> None:
    """Refresh metadata-consistency report + unknown-participant column for one module."""
    result = check_module(module_dir)
    report_module(result)
    add_unknown_participant_column(module_dir)


def _refresh_validation_report(tools_dir: Path) -> None:
    modules = discover_modules(tools_dir.parent)
    if modules:
        generate_report(modules, verbose=False)
        print("refreshed docs/modules/ROOT/pages/validation-log.adoc and validation-errors.adoc")


def sync_from_eaf(eaf_path: Path, module_override: str | None) -> None:
    module_dir, module_name = _resolve_module(eaf_path, module_override)
    cfg = config_mod.load_config(module_name)
    cfg.setdefault("overlaps", {}).setdefault("duration_threshold", 0.1)

    translations_dir = module_dir / "translations"
    reports_dir = module_dir / "tmp" / "process" / "json"
    csv_dir = module_dir / "tmp" / "process" / "csv"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        raw_csv = Path(tmp) / f"{eaf_path.stem}.csv"
        serialize.eaf2csv(eaf_path, raw_csv, {})

        summary, transcript = serialize.process(
            raw_csv, module_dir / "tsv", cfg=cfg, return_transcript=True,
            translations_dir=translations_dir, reports_dir=reports_dir,
        )
        serialize.conversation_to_linear(transcript, csv_dir / f"{eaf_path.stem}.csv")

    _update_summary_json(reports_dir, summary)
    print(f"eaf -> tsv/{eaf_path.stem}.vert.tsv")

    vert_path = module_dir / "tsv" / f"{eaf_path.stem}.vert.tsv"
    tsv2formats.tsv2linear([vert_path], module_dir / "linear-jefferson", module_dir / "linear-orthographic")
    print(f"tsv -> linear-jefferson/, linear-orthographic/ ({eaf_path.stem})")

    _refresh_module_checks(module_dir)
    _refresh_validation_report(Path(__file__).resolve().parent)


def sync_from_vert(vert_path: Path, module_override: str | None, audio_dir: Path | None) -> None:
    module_dir, _module_name = _resolve_module(vert_path, module_override)

    stem = vert_path.name
    if stem.endswith(".vert.tsv"):
        stem = stem[: -len(".vert.tsv")]

    translations_path = module_dir / "translations" / f"{stem}.translations.json"
    if not translations_path.is_file():
        translations_path = None

    linked_file = f"{stem}.wav"
    if audio_dir:
        linked_file = str(audio_dir / f"{stem}.wav")

    eaf_out = module_dir / "eaf" / f"{stem}.eaf"
    serialize.vert2eaf(vert_path, linked_file, eaf_out, translations_path=translations_path)
    print(f"vert.tsv -> eaf/{stem}.eaf")

    tsv2formats.tsv2linear([vert_path], module_dir / "linear-jefferson", module_dir / "linear-orthographic")
    print(f"tsv -> linear-jefferson/, linear-orthographic/ ({stem})")

    _refresh_module_checks(module_dir)
    _refresh_validation_report(Path(__file__).resolve().parent)
    print("NOTE: pipeline warnings/errors in the validation report were not "
          "refreshed for this file (that requires reprocessing the eaf, the "
          "reverse direction this command doesn't auto-trigger).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-eaf", type=Path, help="Path to a single .eaf file.")
    group.add_argument("--from-vert", type=Path, help="Path to a single .vert.tsv file.")
    ap.add_argument("--module", help="Override module config name (default: inferred from path).")
    ap.add_argument("--audio-dir", type=Path, help="--from-vert only: directory for the linked .wav file.")
    args = ap.parse_args()

    if args.from_eaf:
        if not args.from_eaf.is_file():
            raise SystemExit(f"Not a file: {args.from_eaf}")
        sync_from_eaf(args.from_eaf, args.module)
    else:
        if not args.from_vert.is_file():
            raise SystemExit(f"Not a file: {args.from_vert}")
        sync_from_vert(args.from_vert, args.module, args.audio_dir)


if __name__ == "__main__":
    main()
