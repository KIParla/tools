#!/usr/bin/env python3
"""
generate_validation_report.py — Build the two validation pages.

Combines two data sources, across all auto-discovered KIParla modules:

  1. Metadata consistency (check_participants.check_module): participant/
     transcript mismatches, unregistered speakers, conversations<->participants
     back-reference gaps.
  2. Pipeline warnings/errors (module/tmp/process/json/summary.json, produced
     by `cli.py process`): per-conversation counts from serialize.build_json.

Writes AsciiDoc pages (published as part of the shared KIParla docs-site) into
docs/modules/ROOT/pages/:

  - validation-<module>.adoc, one per module, with two sections:
      Errors:   an interactive, filterable table of only real ERRORS,
                metadata-consistency gaps, and missing transcripts -- the
                actionable "go fix this" list, with GitHub links (dev branch)
                to jump straight to each file.
      Warnings: a diary of every WARNING the pipeline auto-fixed per
                conversation. Informational, not actionable.
  - validation.adoc: index of the module pages, with per-module counts.

Usage:
    python generate_validation_report.py
    python generate_validation_report.py --modules /path/to/KIP ...
    python generate_validation_report.py --output-dir custom/pages

Note: pipeline warnings/errors are only available for a module if it has been
run through `cli.py process` (module/tmp/process/json/summary.json present).
Modules without that file are still checked for metadata consistency, with a
note that pipeline data is unavailable.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from check_participants import check_module, discover_modules


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
    """Return (rows, has_pipeline_data) for one module.

    Each row separates two things that need different treatment downstream:
      - `errors` / `metadata_issues` / `missing_transcript`: real problems
        that need a human fix -- the actionable set (validation-errors.adoc).
      - `warnings`: the *full* WARNINGS dict (every key, not just a curated
        subset) -- the pipeline's auto-fix diary, informational only
        (validation-log.adoc).
    """
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
        error_details = entry.get("ERROR_DETAILS", []) if entry else []
        error_tokens = entry.get("ERROR_TOKENS", []) if entry else []
        tokens_err = sum(s.get("tokens-err", 0) for s in entry["speakers"].values()) if entry else 0
        missing_transcript = meta_issues == ["no transcript found"]

        rows.append({
            "code": code,
            "missing_transcript": missing_transcript,
            "metadata_issues": [] if missing_transcript else meta_issues,
            "errors": errors,
            "error_details": error_details,
            "error_tokens": error_tokens,
            "warnings": warnings,
            "tokens_err": tokens_err,
            "audio": (entry or {}).get("AUDIO_CHECK"),
            "has_pipeline_data": entry is not None,
        })

    def _rank(r):
        if r["missing_transcript"]:
            return 0
        if r["errors"] or r["metadata_issues"] or r["error_tokens"]:
            return 1
        if r["warnings"]:
            return 2
        if not r["has_pipeline_data"]:
            return 3
        return 4

    rows.sort(key=lambda r: (_rank(r), r["code"]))
    return rows, has_pipeline_data


def _fmt_counts(d: dict) -> str:
    if not d:
        return "_"
    return ", ".join(f"{k}:{v}" for k, v in sorted(d.items()))


# ---------------------------------------------------------------------------
# Pages
#
#   validation.adoc              index: one row per module, linking to its page
#   validation-<module>.adoc     one page per module: the actionable errors
#                                (interactive table) + the auto-fix warnings
#                                diary (informational)
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def slug(module_name: str) -> str:
    return module_name.lower().replace(" ", "-")


def page_name(module_name: str) -> str:
    return f"validation-{slug(module_name)}.adoc"


def _hms(seconds) -> str:
    s = int(round(seconds or 0))
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _is_actionable(r: dict) -> bool:
    return bool(r["missing_transcript"] or r["errors"] or r["metadata_issues"]
                or r["error_tokens"])


def _gh_urls(module_name: str, code: str) -> tuple[str, str]:
    base = f"https://github.com/KIParla/{module_name}/blob/dev"
    return f"{base}/tsv/{code}.vert.tsv", f"{base}/eaf/{code}.eaf"


def _error_records(module_name: str, rows: list[dict]) -> list[dict]:
    """One record per *occurrence*, not per conversation: each real error
    carries the actual TU text it fired on (tu_id, speaker, text), so the
    offending span is visible right in the page instead of just a rule-name
    count."""
    records = []
    for r in rows:
        if not (r["missing_transcript"] or r["errors"] or r["metadata_issues"]):
            continue
        tsv_url, eaf_url = _gh_urls(module_name, r["code"])

        if r["missing_transcript"]:
            records.append({
                "module": module_name, "code": r["code"], "kind": "no-transcript",
                "rule": None, "tuId": None, "speaker": None, "text": None,
                "detail": "no transcript found for this conversation",
                "tsvUrl": None, "eafUrl": None,
            })
            continue

        for issue in r["metadata_issues"]:
            records.append({
                "module": module_name, "code": r["code"], "kind": "metadata",
                "rule": None, "tuId": None, "speaker": None, "text": None,
                "detail": issue,
                "tsvUrl": tsv_url, "eafUrl": eaf_url,
            })

        for occ in r["error_details"]:
            records.append({
                "module": module_name, "code": r["code"], "kind": "error",
                "rule": occ["rule"], "tuId": occ["tu_id"], "speaker": occ["speaker"],
                "text": occ["text"], "span": None, "detail": None,
                "tsvUrl": tsv_url, "eafUrl": eaf_url,
            })

    # Tokens the tokenizer could not parse (listed separately because they
    # are not rule violations on the TU text).
    for r in rows:
        if r["missing_transcript"]:
            continue
        tsv_url, eaf_url = _gh_urls(module_name, r["code"])
        for occ in r["error_tokens"]:
            records.append({
                "module": module_name, "code": r["code"], "kind": "error",
                "rule": "TOKEN_TYPE_ERROR", "tuId": occ["tu_id"], "speaker": occ["speaker"],
                "text": occ["context"], "span": occ["span"], "detail": None,
                "tsvUrl": tsv_url, "eafUrl": eaf_url,
            })
    return records


def render_module_page(name: str, rows: list[dict], has_pipeline: bool) -> str:
    """Build validation-<module>.adoc."""
    records = _error_records(name, rows)
    n_conv = len(rows)
    n_err_conv = sum(1 for r in rows if _is_actionable(r))
    n_warn_conv = sum(1 for r in rows if r["warnings"] or r["tokens_err"])

    lines = [f"= Validation: {name}", ""]
    lines.append(f"Generated {_now()} by `tools/generate_validation_report.py`. "
                 f"Regenerate after reprocessing {name}, or after `sync.py --from-eaf`, "
                 "to refresh this page. All modules: xref:validation.adoc[Validation].")
    lines.append("")
    if not has_pipeline:
        lines.append("CAUTION: no `tmp/process/json/summary.json` found for this module — "
                     "pipeline warnings and errors are unavailable; only metadata "
                     "consistency was checked.")
        lines.append("")
    lines.append(f"{n_conv} conversations: {n_err_conv} with errors or metadata gaps, "
                 f"{n_warn_conv} with auto-fixed warnings.")
    lines.append("")

    lines.append("== Errors")
    lines.append("")
    lines.append("Only real problems: malformed enriched notation the pipeline couldn't "
                 "auto-fix, a word the tokenizer couldn't parse at all (`TOKEN_TYPE_ERROR`), "
                 "a missing source recording, or a metadata cross-reference gap. One row per "
                 "*occurrence* (not per file) — each shows the actual transcription-unit text "
                 "it fired on, with the offending marker or word highlighted, so you can see "
                 "exactly what to fix without opening the file first.")
    lines.append("")
    if records:
        data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
        lines.append("++++")
        lines.append(_ERRORS_PAGE_TEMPLATE.replace("__DATA__", data_json))
        lines.append("++++")
    else:
        lines.append("No errors.")
    lines.append("")

    audio_rows = [r for r in rows
                  if r.get("audio") and r["audio"].get("status") in ("overrun", "underrun")]
    checked = sum(1 for r in rows if r.get("audio") and r["audio"].get("status") != "unavailable")
    lines.append("== Audio length")
    lines.append("")
    lines.append("The last annotation of each transcript is compared with the length of its "
                 "recording (`audio_check.py`): `overrun` means it ends after the audio, "
                 "`underrun` that it ends well before the end of the audio (incomplete "
                 "transcription or a long silent tail). Both are warnings. The length is "
                 "measured on the audio file when it was available (`audio`), otherwise "
                 "taken from `duration` in `conversations.tsv` (`metadata`).")
    lines.append("")
    if not checked:
        lines.append("Not checked: no audio length available (reprocess the module, "
                     "optionally with `--audio-dir`).")
    elif not audio_rows:
        lines.append(f"All {checked} checked conversations are compatible with their audio length.")
    else:
        lines.append('[cols="1,1,1,1,1,1"]')
        lines.append("|===")
        lines.append("|Code |Status |Audio |Last annotation |Difference (audio − annotation) |Length from")
        for r in audio_rows:
            a = r["audio"]
            lines.append(f"|{r['code']} |{a['status']} |{_hms(a['audio_seconds'])} "
                         f"|{_hms(a['last_annotation_seconds'])} "
                         f"|{a['difference_seconds']:+.0f} s |{a['source']}")
        lines.append("|===")
    lines.append("")

    lines.append("== Warnings")
    lines.append("")
    lines.append("Every warning the pipeline auto-fixed while processing each conversation "
                 "(accent corrections, number-to-words, boundary nudges on short overlaps, "
                 "and so on) -- already resolved, not action items.")
    lines.append("")
    lines.append('[cols="1,4,1"]')
    lines.append("|===")
    lines.append("|Code |Warnings |Error tokens")
    for r in rows:
        if not (r["warnings"] or r["tokens_err"]):
            continue
        lines.append(
            f"|{r['code']} |{_fmt_counts(r['warnings'])} "
            f"|{r['tokens_err'] if r['tokens_err'] else '_'}"
        )
    lines.append("|===")
    lines.append("")
    return "\n".join(lines)


def _summary(name: str, rows: list[dict], has_pipeline: bool) -> dict:
    return {
        "name": name, "conversations": len(rows), "has_pipeline": has_pipeline,
        "with_errors": sum(1 for r in rows if _is_actionable(r)),
        "with_warnings": sum(1 for r in rows if r["warnings"] or r["tokens_err"]),
    }


def render_index(summaries: list[dict]) -> str:
    lines = ["= Validation", ""]
    lines.append(f"Generated {_now()} by `tools/generate_validation_report.py`. "
                 "One page per module, combining `check_participants.py`'s metadata-consistency "
                 "results with the pipeline's per-conversation warnings and errors "
                 "(`tmp/process/json/summary.json`). Each module page has the actionable errors "
                 "(an interactive, filterable table) and the diary of auto-fixed warnings.")
    lines.append("")
    lines.append('[cols="2,1,1,1"]')
    lines.append("|===")
    lines.append("|Module |Conversations |With errors / gaps |With warnings")
    for m in sorted(summaries, key=lambda m: m["name"].lower()):
        note = "" if m["has_pipeline"] else " (no pipeline data)"
        lines.append(f"|xref:{page_name(m['name'])}[{m['name']}]{note} "
                     f"|{m['conversations']} |{m['with_errors']} |{m['with_warnings']}")
    lines.append("|===")
    lines.append("")
    return "\n".join(lines)


_INDEX_ROW_RE = re.compile(
    r"^\|xref:validation-[^\[]+\.adoc\[(?P<name>[^\]]+)\](?P<note> \(no pipeline data\))? "
    r"\|(?P<n>\d+) \|(?P<err>\d+) \|(?P<warn>\d+)$", re.M)


def _existing_summaries(output_dir: Path, exclude: set[str]) -> list[dict]:
    """Index rows already on disk for modules that are not being regenerated now,
    so regenerating one module doesn't drop the others from the index."""
    index = output_dir / "validation.adoc"
    if not index.is_file():
        return []
    out = []
    for m in _INDEX_ROW_RE.finditer(index.read_text(encoding="utf-8")):
        if m["name"] in exclude or not (output_dir / page_name(m["name"])).is_file():
            continue
        out.append({"name": m["name"], "conversations": int(m["n"]), "with_errors": int(m["err"]),
                    "with_warnings": int(m["warn"]), "has_pipeline": m["note"] is None})
    return out


_ERRORS_PAGE_TEMPLATE = """
<div class="vr">
<style>
.vr { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
.vr-controls { display: flex; flex-wrap: wrap; gap: 1.5rem; margin-bottom: 1rem; padding: 0.75rem 1rem;
  border: 1px solid #d7dad4; border-radius: 6px; background: #f7f7f5; }
.vr-group { display: flex; flex-direction: column; gap: 0.25rem; }
.vr-group-label { font-size: 0.7rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: #666; margin-bottom: 0.15rem; }
.vr-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; max-width: 34rem; }
.vr-chip { font-size: 0.78rem; padding: 2px 8px; border-radius: 999px; border: 1px solid #c3c7be;
  background: #fff; cursor: pointer; user-select: none; color: #333; }
.vr-chip.active { background: #33477a; border-color: #33477a; color: #fff; }
.vr-search { padding: 4px 8px; border: 1px solid #c3c7be; border-radius: 4px; font-size: 0.85rem; min-width: 12rem; }
.vr-count { font-size: 0.85rem; color: #666; margin: 0.5rem 0; }
.vr-table-wrap { overflow-x: auto; border: 1px solid #d7dad4; border-radius: 6px; }
table.vr-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
table.vr-table th { text-align: left; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
  color: #666; padding: 8px 10px; border-bottom: 1px solid #c3c7be; white-space: nowrap; background: #f7f7f5; }
table.vr-table td { padding: 6px 10px; border-bottom: 1px solid #e5e5e0; vertical-align: top; }
table.vr-table tr:hover td { background: #f0f2fa; }
.vr-code { font-family: "SF Mono", Consolas, monospace; font-weight: 600; white-space: nowrap; }
.vr-kind { display: inline-block; font-size: 0.72rem; font-weight: 600; padding: 1px 7px; border-radius: 999px; }
.vr-kind-error { background: #f6e6e4; color: #a5342f; }
.vr-kind-metadata { background: #f6ecd7; color: #8a641a; }
.vr-kind-no-transcript { background: #ece9f4; color: #4a3f8a; }
.vr-rule { font-family: "SF Mono", Consolas, monospace; font-size: 0.72rem; color: #555; }
.vr-text { font-family: "SF Mono", Consolas, monospace; font-size: 0.82rem; white-space: pre-wrap;
  word-break: break-word; }
.vr-text mark { background: #ffe27a; color: #1a1a1a; border-radius: 2px; padding: 0 1px; font-weight: 700; }
.vr-tuinfo { font-size: 0.72rem; color: #888; margin-top: 2px; }
.vr-links a { display: block; font-size: 0.8rem; white-space: nowrap; }
.vr-empty { padding: 1.5rem; text-align: center; color: #888; }
</style>

<div class="vr-controls">
  <div class="vr-group">
    <span class="vr-group-label">Module</span>
    <div class="vr-chips" id="vr-modules"></div>
  </div>
  <div class="vr-group">
    <span class="vr-group-label">Issue type</span>
    <div class="vr-chips" id="vr-kinds"></div>
  </div>
  <div class="vr-group">
    <span class="vr-group-label">Rule</span>
    <div class="vr-chips" id="vr-rules"></div>
  </div>
  <div class="vr-group">
    <span class="vr-group-label">Code contains</span>
    <input class="vr-search" id="vr-search" type="text" placeholder="e.g. SBCA">
  </div>
</div>

<p class="vr-count" id="vr-count"></p>
<div class="vr-table-wrap">
  <table class="vr-table">
    <thead>
      <tr><th>Module</th><th>Code</th><th>Type</th><th>What's wrong</th><th>Jump to file</th></tr>
    </thead>
    <tbody id="vr-body"></tbody>
  </table>
</div>

<script>
(function () {
  var DATA = __DATA__;

  // Which enriched marker to highlight for each error rule, so the
  // imbalance is visible at a glance in the raw TU text.
  var RULE_CHARS = {
    "UNBALANCED_DOTS": "\\u00b0",
    "UNBALANCED_PACE": "<>",
    "UNBALANCED_GUESS": "()",
    "UNBALANCED_OVERLAP": "[]",
    "MISMATCHING_OVERLAPS": "[]",
    "OVERLAPS:MISSING_ANNOTATION": "[]",
    "OVERLAPS:MISSING_TIME": "[]"
  };

  var activeModules = new Set();
  var activeKinds = new Set();
  var activeRules = new Set();

  var modules = Array.from(new Set(DATA.map(function (r) { return r.module; }))).sort();
  var kinds = Array.from(new Set(DATA.map(function (r) { return r.kind; }))).sort();
  var rules = Array.from(new Set(DATA.filter(function (r) { return r.rule; })
    .map(function (r) { return r.rule; }))).sort();

  function chip(label, active, onClick) {
    var el = document.createElement("span");
    el.className = "vr-chip" + (active ? " active" : "");
    el.textContent = label;
    el.addEventListener("click", onClick);
    return el;
  }

  function renderChipGroup(containerId, values, activeSet) {
    var el = document.getElementById(containerId);
    el.innerHTML = "";
    values.forEach(function (v) {
      el.appendChild(chip(v, activeSet.has(v), function () {
        activeSet.has(v) ? activeSet.delete(v) : activeSet.add(v);
        renderChips();
        renderTable();
      }));
    });
  }

  function renderChips() {
    renderChipGroup("vr-modules", modules, activeModules);
    renderChipGroup("vr-kinds", kinds, activeKinds);
    renderChipGroup("vr-rules", rules, activeRules);
  }

  function matches(r) {
    if (activeModules.size && !activeModules.has(r.module)) return false;
    if (activeKinds.size && !activeKinds.has(r.kind)) return false;
    if (activeRules.size && !activeRules.has(r.rule)) return false;
    var q = document.getElementById("vr-search").value.trim().toLowerCase();
    if (q && r.code.toLowerCase().indexOf(q) === -1) return false;
    return true;
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : s;
    return d.innerHTML;
  }

  function highlightChars(text, chars) {
    var escaped = esc(text);
    if (!chars) return escaped;
    var set = chars.split("");
    return escaped.split("").map(function (ch) {
      return set.indexOf(ch) !== -1 ? "<mark>" + ch + "</mark>" : ch;
    }).join("");
  }

  // TOKEN_TYPE_ERROR: highlight the specific malformed word (r.span) inside
  // its surrounding TU text (r.text), rather than a character set.
  function highlightSpan(text, span) {
    if (!span) return esc(text);
    var idx = text.indexOf(span);
    if (idx === -1) return esc(text);
    return esc(text.slice(0, idx)) + "<mark>" + esc(span) + "</mark>" + esc(text.slice(idx + span.length));
  }

  function kindLabel(kind) {
    return { "error": "Error", "metadata": "Metadata", "no-transcript": "No transcript" }[kind] || kind;
  }

  function whatCell(r) {
    if (r.kind === "no-transcript") return "no transcript found for this conversation";
    if (r.kind === "metadata") return esc(r.detail);
    var highlighted = r.span ? highlightSpan(r.text, r.span) : highlightChars(r.text, RULE_CHARS[r.rule]);
    var html = '<div class="vr-rule">' + esc(r.rule) + '</div>' +
      '<div class="vr-text">' + highlighted + '</div>';
    if (r.tuId != null) {
      html += '<div class="vr-tuinfo">tu_id ' + esc(r.tuId) + (r.speaker ? " · " + esc(r.speaker) : "") + '</div>';
    }
    return html;
  }

  var RENDER_CAP = 500;

  function renderTable() {
    var matched = DATA.filter(matches);
    var rows = matched.slice(0, RENDER_CAP);
    var countText = matched.length + " of " + DATA.length + " issue(s) match";
    if (matched.length > RENDER_CAP) {
      countText += " — showing the first " + RENDER_CAP + "; narrow the filters to see the rest";
    }
    document.getElementById("vr-count").textContent = countText;
    var body = document.getElementById("vr-body");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="vr-empty">No issues match these filters.</td></tr>';
      return;
    }
    body.innerHTML = rows.map(function (r) {
      var links = r.tsvUrl ?
        '<a href="' + r.tsvUrl + '" target="_blank" rel="noopener">vert.tsv</a>' +
        '<a href="' + r.eafUrl + '" target="_blank" rel="noopener">eaf</a>' : "";
      return "<tr>" +
        "<td>" + esc(r.module) + "</td>" +
        '<td class="vr-code">' + esc(r.code) + "</td>" +
        '<td><span class="vr-kind vr-kind-' + r.kind + '">' + kindLabel(r.kind) + "</span></td>" +
        "<td>" + whatCell(r) + "</td>" +
        '<td class="vr-links">' + links + "</td>" +
        "</tr>";
    }).join("");
  }

  document.getElementById("vr-search").addEventListener("input", renderTable);
  renderChips();
  renderTable();
})();
</script>
</div>
"""


_PAGES_DIR = Path(__file__).parent / "docs/modules/ROOT/pages"


def generate_report(
    modules: list[Path],
    output_dir: Path = _PAGES_DIR,
    verbose: bool = True,
) -> list[Path]:
    """Write one validation-<module>.adoc per module, plus the validation.adoc
    index, into *output_dir*. Returns the written paths.

    Shared by ``main()`` (CLI) and ``sync.py`` (called after a single-file
    sync, to keep the pages current without a full module reprocess). Each
    module page is built from that module's own data, so regenerating one
    module never touches another module's page; the index keeps the rows of
    modules that already have a page and aren't part of this run.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries, written = [], []
    for module_dir in modules:
        rows, has_pipeline = build_rows(module_dir)
        summaries.append(_summary(module_dir.name, rows, has_pipeline))
        path = output_dir / page_name(module_dir.name)
        path.write_text(render_module_page(module_dir.name, rows, has_pipeline), encoding="utf-8")
        written.append(path)
        if verbose:
            print(f"{module_dir.name}: {len(rows)} conversations, "
                  f"{summaries[-1]['with_errors']} need review -> {path.name}")

    summaries += _existing_summaries(output_dir, {m["name"] for m in summaries})
    index = output_dir / "validation.adoc"
    index.write_text(render_index(summaries), encoding="utf-8")
    written.append(index)
    if verbose:
        print(f"wrote {index}")
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules", nargs="+", type=Path,
                     help="Paths to module root directories. Default: auto-discover.")
    ap.add_argument("--output-dir", type=Path, default=_PAGES_DIR,
                     help="Directory for validation.adoc and validation-<module>.adoc.")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    # Modules live next to the tools checkout, or in a sibling "moduli/" folder.
    modules = args.modules or discover_modules(root) or (
        discover_modules(root / "moduli") if (root / "moduli").is_dir() else [])
    if not modules:
        raise SystemExit("No modules found.")

    generate_report(modules, args.output_dir)


if __name__ == "__main__":
    main()
