#!/usr/bin/env python3
"""
generate_validation_report.py — Build a per-conversation validation recap page.

Combines two data sources, across all auto-discovered KIParla modules:

  1. Metadata consistency (check_participants.check_module): participant/
     transcript mismatches, unregistered speakers, conversations<->participants
     back-reference gaps.
  2. Pipeline warnings/errors (module/tmp/process/json/summary.json, produced
     by `cli.py process`): per-conversation counts from serialize.build_json.

Writes an AsciiDoc page to tools/docs/modules/ROOT/pages/validation-report.adoc
(published as part of the shared KIParla docs-site).

Usage:
    python generate_validation_report.py
    python generate_validation_report.py --modules /path/to/KIP ...
    python generate_validation_report.py -o custom/output.adoc

Note: pipeline warnings/errors are only available for a module if it has been
run through `cli.py process` (module/tmp/process/json/summary.json present).
Modules without that file are still checked for metadata consistency, with a
note that pipeline data is unavailable.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from check_participants import check_module, discover_modules

# Warnings that reflect a real structural issue worth a human look, as
# opposed to routine normalization counts (ACCENTS, NUMBERS, MULTIPLE_SPACES,
# ...) which are auto-fixed and not actionable.
STRUCTURAL_WARNINGS = {"MOVED_BOUNDARIES", "SWITCHES", "MISMATCHING_OVERLAPS", "EMPTY_SPANS"}


def load_summary(module_dir: Path) -> dict[str, dict] | None:
    summary_path = module_dir / "tmp" / "process" / "json" / "summary.json"
    if not summary_path.is_file():
        return None
    with summary_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {entry["transcript"]: entry for entry in data}


def metadata_issues_for(code: str, check_result: dict) -> list[str]:
    issues = []
    for issue in check_result["conversation_issues"]:
        if issue["code"] != code:
            continue
        if issue["missing_transcript"]:
            issues.append("no transcript found")
            continue
        if issue["participants_not_in_transcript"]:
            issues.append("listed but never speaks: " + ", ".join(issue["participants_not_in_transcript"]))
        if issue["speakers_not_in_metadata"]:
            issues.append("speaks but not listed: " + ", ".join(issue["speakers_not_in_metadata"]))
    for conv_code, p_code in check_result["conversation_not_backreferenced"]:
        if conv_code == code:
            issues.append(f"{p_code} not back-referenced in participants.tsv")
    for p_code, conv_code in check_result["participant_not_forwardreferenced"]:
        if conv_code == code:
            issues.append(f"{p_code}'s reference to this conversation is one-sided")
    return issues


def build_rows(module_dir: Path) -> tuple[list[dict], bool]:
    """Return (rows, has_pipeline_data) for one module."""
    check_result = check_module(module_dir)
    summary = load_summary(module_dir)
    has_pipeline_data = summary is not None

    codes = sorted({row["code"] for row in check_result["conversation_issues"]}
                   | (set(summary.keys()) if summary else set()))
    # Also include every conversation that has neither metadata nor pipeline
    # issues, so the report reflects the full corpus, not just problem files.
    conv_path = module_dir / "metadata" / "conversations.tsv"
    if conv_path.is_file():
        import csv
        with conv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            all_codes = {(row.get("code") or "").strip() for row in reader}
            all_codes.discard("")
        codes = sorted(all_codes)

    rows = []
    for code in codes:
        meta_issues = metadata_issues_for(code, check_result)
        entry = summary.get(code) if summary else None

        errors = entry["ERRORS"] if entry else {}
        warnings = entry["WARNINGS"] if entry else {}
        structural = {k: v for k, v in warnings.items() if k in STRUCTURAL_WARNINGS}
        tokens_err = sum(s.get("tokens-err", 0) for s in entry["speakers"].values()) if entry else 0

        if meta_issues == ["no transcript found"]:
            status = "no-transcript"
        elif errors or meta_issues:
            status = "error"
        elif structural or tokens_err:
            status = "warn"
        elif entry is None:
            status = "no-pipeline-data"
        else:
            status = "ok"

        rows.append({
            "code": code,
            "status": status,
            "metadata_issues": meta_issues,
            "errors": errors,
            "structural_warnings": structural,
            "tokens_err": tokens_err,
        })

    _status_order = {"error": 0, "warn": 1, "no-transcript": 2, "no-pipeline-data": 3, "ok": 4}
    rows.sort(key=lambda r: (_status_order[r["status"]], r["code"]))
    return rows, has_pipeline_data


def _fmt_counts(d: dict) -> str:
    if not d:
        return "_"
    return ", ".join(f"{k}:{v}" for k, v in sorted(d.items()))


def _status_label(status: str) -> str:
    return {
        "error": "*ERROR*",
        "warn": "WARN",
        "no-transcript": "_no transcript_",
        "no-pipeline-data": "_no pipeline data_",
        "ok": "OK",
    }[status]


def render_adoc(module_reports: list[tuple[str, list[dict], bool]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append("= Validation report")
    lines.append("")
    lines.append(f"Generated {now} by `tools/generate_validation_report.py`. "
                  "Regenerate after reprocessing a module to refresh this page.")
    lines.append("")
    lines.append("Combines metadata consistency checks (`check_participants.py`) and "
                  "pipeline warnings/errors (`cli.py process` summaries). Rows are sorted "
                  "so files needing attention come first: *ERROR* (transcription or metadata "
                  "problems needing a fix) > WARN (structural warnings worth a look) > "
                  "no transcript / no pipeline data > OK.")
    lines.append("")

    # Overview table
    lines.append("== Overview")
    lines.append("")
    lines.append('[cols="1,1,1,1,1,1"]')
    lines.append("|===")
    lines.append("|Module |Conversations |Error |Warn |No transcript |OK")
    for name, rows, _has_pipeline in module_reports:
        counts = {"error": 0, "warn": 0, "no-transcript": 0, "no-pipeline-data": 0, "ok": 0}
        for r in rows:
            counts[r["status"]] += 1
        ok_total = counts["ok"] + counts["no-pipeline-data"]
        lines.append(f"|{name} |{len(rows)} |{counts['error']} |{counts['warn']} "
                      f"|{counts['no-transcript']} |{ok_total}")
    lines.append("|===")
    lines.append("")

    for name, rows, has_pipeline in module_reports:
        anchor = name.lower().replace(" ", "-")
        lines.append(f"[[{anchor}]]")
        lines.append(f"== {name}")
        lines.append("")
        if not has_pipeline:
            lines.append("CAUTION: no `tmp/process/json/summary.json` found for this module — "
                          "pipeline warnings/errors are unavailable; only metadata consistency "
                          "is shown below.")
            lines.append("")
        lines.append('[cols="1,1,3,2,2,1"]')
        lines.append("|===")
        lines.append("|Code |Status |Metadata issues |Errors |Structural warnings |Error tokens")
        for r in rows:
            meta = "<br>".join(r["metadata_issues"]) if r["metadata_issues"] else "_"
            lines.append(
                f"|{r['code']} |{_status_label(r['status'])} |{meta} "
                f"|{_fmt_counts(r['errors'])} |{_fmt_counts(r['structural_warnings'])} "
                f"|{r['tokens_err'] if r['tokens_err'] else '_'}"
            )
        lines.append("|===")
        lines.append("")

    return "\n".join(lines)


DEFAULT_OUTPUT = Path(__file__).parent / "docs/modules/ROOT/pages/validation-report.adoc"


def generate_report(modules: list[Path], output: Path = DEFAULT_OUTPUT, verbose: bool = True) -> Path:
    """Build the validation report for *modules* and write it to *output*.

    Shared by ``main()`` (CLI) and ``sync.py`` (called after a single-file
    sync, to keep the report current without a full module reprocess).
    """
    module_reports = []
    for module_dir in modules:
        rows, has_pipeline = build_rows(module_dir)
        module_reports.append((module_dir.name, rows, has_pipeline))
        if verbose:
            n_needs_review = sum(1 for r in rows if r["status"] in ("error", "warn"))
            print(f"{module_dir.name}: {len(rows)} conversations, {n_needs_review} need review")

    adoc = render_adoc(module_reports)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(adoc, encoding="utf-8")
    if verbose:
        print(f"wrote {output}")
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules", nargs="+", type=Path,
                     help="Paths to module root directories. Default: auto-discover.")
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                     help="Output .adoc path.")
    args = ap.parse_args()

    modules = args.modules or discover_modules(Path(__file__).resolve().parent.parent)
    if not modules:
        raise SystemExit("No modules found.")

    generate_report(modules, args.output)


if __name__ == "__main__":
    main()
