#!/usr/bin/env python3
"""
summarize.py — per-conversation summaries and a module-level "snapshot" of a
KIParla module, computed from its tsv/*.vert.tsv files.

Writes, in the summaries repository (KIParla-summaries), one folder per module:

    <root>/<Module>/<code>.json    one conversation: metadata, token counts by
                                   type, time and overlap statistics,
                                   per-speaker figures, and (when present) the
                                   pipeline's own report (warnings, errors,
                                   audio check)
    <root>/<Module>/snapshot.json  the whole module: totals, a breakdown by
                                   conversation type, and flat tables
                                   (conversations, speakers, participants)
                                   meant to be loaded straight into pandas

Everything is derived from the vert.tsv (the canonical output), so the
summaries can be regenerated at any time without reprocessing the EAF files.
`sync.py` and `cli.py process` refresh them automatically.

The summaries live in their own repository, not in the modules. It is a separate
step, run by hand after `sync.py` / `cli.py process`; each summary records the
SHA-256 of its vert.tsv, so `--check` can tell when the committed summaries are
out of date.

Usage:
    python summarize.py --output-dir KIParla-summaries MODULE_DIR [MODULE_DIR ...] [--code CODE ...]
    python summarize.py --output-dir KIParla-summaries MODULE_DIR [MODULE_DIR ...] --check

Definitions
-----------
tokens                 every row of the vert.tsv (any `type`).
linguistic tokens      rows whose `type` is `linguistic` (words); the other
                       types are shortpause, nonverbalbehavior, unknown,
                       anonymized, error, warning.
unit                   one transcription unit (`tu_id`). Its interval is the
                       `Begin=` of its first token to the `End=` of its last.
speech seconds         time during which the speaker has at least one unit
                       active (union of their own units, so overlapping units
                       of the same speaker are not counted twice).
overlap seconds        time during which at least two different speakers are
                       active. Computed from unit intervals, independently of
                       how the transcribers marked overlaps.
annotated overlap      tokens carrying an `overlaps` annotation: the span a
                       transcriber bracketed with [ ... ]. `unmatched` spans
                       are annotated overlaps for which the pipeline found no
                       matching time overlap, written `(?)` in the vert.tsv.
in_participants_metadata
                       False for tiers that are not listed in participants.tsv
                       (unidentified speakers written `???`, `environment`);
                       filter on it for per-person figures.
turn                   a maximal run of consecutive units (in time order) by
                       the same speaker.
rates                  tokens (or linguistic tokens) per second of the
                       speaker's own speech seconds, also given per minute.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import statistics
from pathlib import Path

from audio_check import parse_duration
from textutil import ENCODING_READ, ENCODING_WRITE
from variety import (
    feat_value, iter_vert_rows, row_contains_variation, row_language,
    row_nonce, row_variety,
)

SCHEMA_VERSION = 1
SNAPSHOT_NAME = "snapshot.json"

_EVENT_ID = re.compile(r"\((\d+)\)")
_UNMATCHED = re.compile(r"\(\?\)")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _round(x, nd=3):
    return None if x is None else round(x, nd)


def _ratio(num, den, nd=4):
    return round(num / den, nd) if den else None


def _per_minute(per_second):
    return None if per_second is None else round(per_second * 60, 2)


def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _read_tsv(path: Path) -> list[dict]:
    import csv
    if not path.is_file():
        return []
    with path.open(encoding=ENCODING_READ, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module_name(module_dir: Path) -> str:
    return Path(module_dir).resolve().name


def summaries_dir(module_dir: Path, out_root: Path) -> Path:
    """Where a module's summaries go: <out_root>/<Module>/."""
    return Path(out_root) / module_name(module_dir)


def module_version(module_dir: Path) -> str | None:
    cff = Path(module_dir) / "CITATION.cff"
    if cff.is_file():
        for line in cff.read_text(encoding=ENCODING_READ).splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    return None


# ---------------------------------------------------------------------------
# Time statistics (sweep line over unit intervals)
# ---------------------------------------------------------------------------

def time_statistics(units: list[dict]) -> dict:
    """Speech / overlap seconds from unit intervals.

    *units*: dicts with ``key``, ``speaker``, ``begin``, ``end`` (floats).
    Returns {"speech", "overlap", "speaker_speech", "speaker_overlap",
    "overlapped_units"}; touching intervals (end == begin) do not overlap.
    """
    events = []
    for u in units:
        events.append((u["begin"], 1, u["key"], u["speaker"]))
        events.append((u["end"], 0, u["key"], u["speaker"]))   # ends sort first
    events.sort(key=lambda e: (e[0], e[1]))

    counts: dict[str, int] = collections.defaultdict(int)
    active_units: dict[str, str] = {}
    speech = overlap = 0.0
    speaker_speech: dict[str, float] = collections.defaultdict(float)
    speaker_overlap: dict[str, float] = collections.defaultdict(float)
    overlapped: set[str] = set()
    prev = None

    for t, is_start, key, speaker in events:
        if prev is not None and t > prev:
            dt = t - prev
            active = [s for s, c in counts.items() if c > 0]
            if active:
                speech += dt
                for s in active:
                    speaker_speech[s] += dt
            if len(active) >= 2:
                overlap += dt
                for s in active:
                    speaker_overlap[s] += dt
        prev = t
        if is_start:
            others = [k for k, s in active_units.items() if s != speaker]
            if others:
                overlapped.add(key)
                overlapped.update(others)
            active_units[key] = speaker
            counts[speaker] += 1
        else:
            active_units.pop(key, None)
            counts[speaker] -= 1

    return {
        "speech": speech, "overlap": overlap,
        "speaker_speech": dict(speaker_speech),
        "speaker_overlap": dict(speaker_overlap),
        "overlapped_units": overlapped,
    }


# ---------------------------------------------------------------------------
# One conversation
# ---------------------------------------------------------------------------

def _new_speaker() -> dict:
    return {
        "units": 0, "tokens": 0, "by_type": collections.Counter(),
        "annotated_overlap_tokens": 0, "annotated_overlap_linguistic": 0,
        "variety": collections.Counter(), "languages": collections.Counter(),
        "nonce": 0, "unit_seconds": 0.0, "units_with_variation": 0,
        "turns": 0, "turn_tokens": 0,
    }


def read_conversation(vert_path: Path) -> dict:
    """Single pass over a vert.tsv, collecting everything the summary needs."""
    units: list[dict] = []
    cur = None
    event_ids: set[str] = set()
    unmatched = 0
    lemma = upos = 0
    with Path(vert_path).open(encoding=ENCODING_READ, newline="") as f:
        for row in iter_vert_rows(f, source=str(vert_path)):
            key = row.get("tu_id")
            if cur is None or key != cur["key"]:
                cur = {"key": key, "speaker": row.get("speaker"), "begin": None,
                       "end": None, "tokens": []}
                units.append(cur)
            begin = _float(feat_value(row.get("align"), "Begin"))
            end = _float(feat_value(row.get("align"), "End"))
            if begin is not None and cur["begin"] is None:
                cur["begin"] = begin
            if end is not None:
                cur["end"] = end
            ov = row.get("overlaps") or "_"
            if ov != "_":
                event_ids.update(_EVENT_ID.findall(ov))
                unmatched += len(_UNMATCHED.findall(ov))
            if (row.get("lemma") or "_") != "_":
                lemma += 1
            if (row.get("upos") or "_") != "_":
                upos += 1
            cur["tokens"].append({
                "type": row.get("type") or "_",
                "overlap": ov != "_",
                "variety": row_variety(row),
                "language": row_language(row),
                "nonce": row_nonce(row),
                "unit_variation": row_contains_variation(row),
            })
    return {"units": units, "event_ids": event_ids, "unmatched": unmatched,
            "lemma": lemma, "upos": upos}


def summarize_conversation(vert_path: Path, *, code: str | None = None,
                           module: str | None = None, metadata: dict | None = None,
                           participants: dict | None = None,
                           pipeline: dict | None = None) -> dict:
    """The summary dict of one conversation (see the module docstring)."""
    data = read_conversation(vert_path)
    units = data["units"]
    code = code or Path(vert_path).name.removesuffix(".vert.tsv")
    metadata = metadata or {}
    participants = participants or {}

    speakers: dict[str, dict] = {}
    all_types: collections.Counter = collections.Counter()
    timed, untimed = [], 0
    previous_speaker = None
    overlap_linguistic = overlap_tokens = 0

    for u in units:
        s = speakers.setdefault(u["speaker"], _new_speaker())
        s["units"] += 1
        n = len(u["tokens"])
        s["tokens"] += n
        if u["speaker"] != previous_speaker:
            s["turns"] += 1
        previous_speaker = u["speaker"]
        s["turn_tokens"] += n
        if any(t["unit_variation"] for t in u["tokens"]):
            s["units_with_variation"] += 1
        for t in u["tokens"]:
            s["by_type"][t["type"]] += 1
            all_types[t["type"]] += 1
            if t["overlap"]:
                s["annotated_overlap_tokens"] += 1
                overlap_tokens += 1
                if t["type"] == "linguistic":
                    s["annotated_overlap_linguistic"] += 1
                    overlap_linguistic += 1
            if t["variety"]:
                s["variety"][t["variety"]] += 1
            if t["language"]:
                s["languages"][t["language"]] += 1
            if t["nonce"]:
                s["nonce"] += 1
        if u["begin"] is not None and u["end"] is not None and u["end"] >= u["begin"]:
            s["unit_seconds"] += u["end"] - u["begin"]
            timed.append(u)
        else:
            untimed += 1

    ts = time_statistics(timed)
    first_begin = min((u["begin"] for u in timed), default=None)
    last_end = max((u["end"] for u in timed), default=None)
    span = (last_end - first_begin) if timed else None
    total_speaker_time = sum(ts["speaker_speech"].values())
    n_overlapped_units = len(ts["overlapped_units"])

    ling_total = all_types.get("linguistic", 0)
    tokens_total = sum(all_types.values())

    speaker_out = {}
    for spk in sorted(speakers):
        s = speakers[spk]
        speech = ts["speaker_speech"].get(spk, 0.0)
        ovl_sec = ts["speaker_overlap"].get(spk, 0.0)
        ling = s["by_type"].get("linguistic", 0)
        tps = (s["tokens"] / speech) if speech else None
        lps = (ling / speech) if speech else None
        speaker_out[spk] = {
            "in_participants_metadata": spk in participants,
            "participant": participants.get(spk, {}),
            "units": s["units"],
            "turns": s["turns"],
            "tokens": s["tokens"],
            "tokens_by_type": dict(sorted(s["by_type"].items())),
            "linguistic_tokens": ling,
            "share_of_linguistic_tokens": _ratio(ling, ling_total),
            "speech_seconds": _round(speech),
            "unit_seconds": _round(s["unit_seconds"]),
            "share_of_speech_time": _ratio(speech, total_speaker_time),
            "tokens_per_second": _round(tps, 4),
            "linguistic_tokens_per_second": _round(lps, 4),
            "linguistic_tokens_per_minute": _per_minute(lps),
            "mean_tokens_per_unit": _ratio(s["tokens"], s["units"], 2),
            "mean_tokens_per_turn": _ratio(s["turn_tokens"], s["turns"], 2),
            "overlap_seconds": _round(ovl_sec),
            "share_of_own_speech_overlapped": _ratio(ovl_sec, speech),
            "annotated_overlap_tokens": s["annotated_overlap_tokens"],
            "annotated_overlap_linguistic_tokens": s["annotated_overlap_linguistic"],
            "share_of_linguistic_tokens_in_annotated_overlap":
                _ratio(s["annotated_overlap_linguistic"], ling),
            "variation": {
                "units_with_variation": s["units_with_variation"],
                "tokens_by_code": dict(sorted(s["variety"].items())),
                "tokens_by_language": dict(sorted(s["languages"].items())),
                "nonce_tokens": s["nonce"],
            },
        }

    variety_total: collections.Counter = collections.Counter()
    language_total: collections.Counter = collections.Counter()
    for s in speakers.values():
        variety_total.update(s["variety"])
        language_total.update(s["languages"])

    out = {
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "module": module,
        "source": {"vert_sha256": file_sha256(vert_path)},
        "metadata": metadata,
        "time": {
            "first_begin": _round(first_begin),
            "last_end": _round(last_end),
            "span_seconds": _round(span),
            "metadata_duration_seconds": parse_duration(metadata.get("duration")),
            "speech_seconds": _round(ts["speech"]),
            "silence_seconds": _round(span - ts["speech"]) if span is not None else None,
            "share_silence_of_span": _ratio(span - ts["speech"], span) if span else None,
            "overlap_seconds": _round(ts["overlap"]),
            "share_overlap_of_speech": _ratio(ts["overlap"], ts["speech"]),
            "share_overlap_of_span": _ratio(ts["overlap"], span) if span else None,
        },
        "units": {
            "count": len(units),
            "without_valid_time": untimed,
            "turns": sum(s["turns"] for s in speakers.values()),
            "mean_tokens_per_unit": _ratio(tokens_total, len(units), 2),
            "mean_unit_seconds": _ratio(sum(s["unit_seconds"] for s in speakers.values()),
                                        len(timed), 3),
            "overlapped_in_time": n_overlapped_units,
            "share_overlapped_in_time": _ratio(n_overlapped_units, len(timed)),
        },
        "tokens": {
            "total": tokens_total,
            "by_type": dict(sorted(all_types.items())),
            "linguistic": ling_total,
            "share_linguistic": _ratio(ling_total, tokens_total),
            "with_lemma": data["lemma"],
            "with_upos": data["upos"],
        },
        "rates": {
            "tokens_per_second": _round(tokens_total / ts["speech"], 4) if ts["speech"] else None,
            "linguistic_tokens_per_second":
                _round(ling_total / ts["speech"], 4) if ts["speech"] else None,
            "tokens_per_span_second": _round(tokens_total / span, 4) if span else None,
        },
        "annotated_overlaps": {
            "events": len(data["event_ids"]),
            "unmatched_spans": data["unmatched"],
            "tokens": overlap_tokens,
            "linguistic_tokens": overlap_linguistic,
            "share_of_linguistic_tokens": _ratio(overlap_linguistic, ling_total),
            "share_of_tokens": _ratio(overlap_tokens, tokens_total),
        },
        "variation": {
            "units_with_variation": sum(s["units_with_variation"] for s in speakers.values()),
            "tokens_by_code": dict(sorted(variety_total.items())),
            "tokens_by_language": dict(sorted(language_total.items())),
            "nonce_tokens": sum(s["nonce"] for s in speakers.values()),
        },
        "speakers": speaker_out,
    }
    if pipeline is not None:
        out["pipeline"] = pipeline
    return out


# ---------------------------------------------------------------------------
# Module level
# ---------------------------------------------------------------------------

def _pipeline_report(module_dir: Path, code: str) -> dict | None:
    """The pipeline's own per-conversation report (warnings, errors, audio
    check), when `tmp/process/json/<code>.json` exists. The per-speaker counts
    it holds are superseded by this summary, so they are left out."""
    path = module_dir / "tmp" / "process" / "json" / f"{code}.json"
    if not path.is_file():
        return None
    with path.open(encoding=ENCODING_READ) as f:
        report = json.load(f)
    for key in ("transcript", "speakers"):
        report.pop(key, None)
    return report


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=ENCODING_WRITE, newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_inputs(module_dir: Path):
    conversations = {r["code"]: r for r in _read_tsv(module_dir / "metadata" / "conversations.tsv")}
    participants = {r["code"]: {k: v for k, v in r.items() if k != "code"}
                    for r in _read_tsv(module_dir / "metadata" / "participants.tsv")}
    return conversations, participants


def write_conversation(module_dir: Path, code: str, out_root: Path,
                       conversations=None, participants=None) -> Path:
    module_dir = Path(module_dir)
    if conversations is None or participants is None:
        conversations, participants = _load_inputs(module_dir)
    vert = module_dir / "tsv" / f"{code}.vert.tsv"
    summary = summarize_conversation(
        vert, code=code, module=module_name(module_dir),
        metadata=conversations.get(code, {}), participants=participants,
        pipeline=_pipeline_report(module_dir, code))
    out = summaries_dir(module_dir, out_root) / f"{code}.json"
    _write_json(out, summary)
    return out


def _flat_conversation(s: dict) -> dict:
    t, u, k, r, o, v = (s[x] for x in ("time", "units", "tokens", "rates",
                                       "annotated_overlaps", "variation"))
    return {
        "code": s["code"],
        "metadata": s["metadata"],
        "speakers": len(s["speakers"]),
        "span_seconds": t["span_seconds"],
        "speech_seconds": t["speech_seconds"],
        "silence_seconds": t["silence_seconds"],
        "overlap_seconds": t["overlap_seconds"],
        "share_overlap_of_speech": t["share_overlap_of_speech"],
        "units": u["count"],
        "turns": u["turns"],
        "tokens": k["total"],
        "linguistic_tokens": k["linguistic"],
        "tokens_by_type": k["by_type"],
        "tokens_per_second": r["tokens_per_second"],
        "linguistic_tokens_per_second": r["linguistic_tokens_per_second"],
        "annotated_overlap_events": o["events"],
        "share_linguistic_tokens_in_annotated_overlap": o["share_of_linguistic_tokens"],
        "share_units_overlapped_in_time": u["share_overlapped_in_time"],
        "units_with_variation": v["units_with_variation"],
        "tokens_by_code": v["tokens_by_code"],
    }


def build_snapshot(module_dir: Path, out_root: Path) -> dict:
    """Aggregate summaries/*.json (all conversations of the module) into the
    module snapshot."""
    module_dir = Path(module_dir)
    summaries = []
    for p in sorted(summaries_dir(module_dir, out_root).glob("*.json")):
        if p.name == SNAPSHOT_NAME:
            continue
        with p.open(encoding=ENCODING_READ) as f:
            summaries.append(json.load(f))

    conv_rows = [_flat_conversation(s) for s in summaries]
    speaker_rows = []
    people: dict[str, dict] = {}
    for s in summaries:
        for spk, d in s["speakers"].items():
            row = {"conversation": s["code"], "speaker": spk,
                   "conversation_type": s["metadata"].get("type"), **d}
            speaker_rows.append(row)
            p = people.setdefault(spk, {
                "speaker": spk, "in_participants_metadata": d["in_participants_metadata"],
                "participant": d["participant"], "conversations": [],
                "units": 0, "turns": 0, "tokens": 0, "linguistic_tokens": 0,
                "speech_seconds": 0.0, "overlap_seconds": 0.0})
            p["conversations"].append(s["code"])
            for key in ("units", "turns", "tokens", "linguistic_tokens"):
                p[key] += d[key]
            p["speech_seconds"] += d["speech_seconds"] or 0.0
            p["overlap_seconds"] += d["overlap_seconds"] or 0.0
    participants = []
    for spk in sorted(people):
        p = people[spk]
        sec = p["speech_seconds"]
        p["speech_seconds"] = _round(sec)
        p["overlap_seconds"] = _round(p["overlap_seconds"])
        p["tokens_per_second"] = _round(p["tokens"] / sec, 4) if sec else None
        p["linguistic_tokens_per_second"] = _round(p["linguistic_tokens"] / sec, 4) if sec else None
        p["linguistic_tokens_per_minute"] = _per_minute(p["linguistic_tokens_per_second"])
        participants.append(p)

    def total(path):
        out = 0
        for s in summaries:
            node = s
            for k in path:
                node = node[k]
            out += node or 0
        return out

    tokens, ling = total(("tokens", "total")), total(("tokens", "linguistic"))
    speech, overlap = total(("time", "speech_seconds")), total(("time", "overlap_seconds"))
    span = total(("time", "span_seconds"))
    by_type_tokens: collections.Counter = collections.Counter()
    for s in summaries:
        by_type_tokens.update(s["tokens"]["by_type"])
    ovl_ling = total(("annotated_overlaps", "linguistic_tokens"))

    by_conv_type: dict[str, list] = collections.defaultdict(list)
    for s in summaries:
        by_conv_type[(s["metadata"].get("type") or "_").split(":")[0]].append(s)
    type_rows = {}
    for ctype in sorted(by_conv_type):
        group = by_conv_type[ctype]
        g_speech = sum(x["time"]["speech_seconds"] or 0 for x in group)
        g_overlap = sum(x["time"]["overlap_seconds"] or 0 for x in group)
        g_ling = sum(x["tokens"]["linguistic"] for x in group)
        g_ovl_ling = sum(x["annotated_overlaps"]["linguistic_tokens"] for x in group)
        type_rows[ctype] = {
            "conversations": len(group),
            "span_hours": _round(sum(x["time"]["span_seconds"] or 0 for x in group) / 3600, 2),
            "tokens": sum(x["tokens"]["total"] for x in group),
            "linguistic_tokens": g_ling,
            "speech_seconds": _round(g_speech),
            "overlap_seconds": _round(g_overlap),
            "share_overlap_of_speech": _ratio(g_overlap, g_speech),
            "share_linguistic_tokens_in_annotated_overlap": _ratio(g_ovl_ling, g_ling),
            "linguistic_tokens_per_second": _round(g_ling / g_speech, 4) if g_speech else None,
        }

    spans = [s["time"]["span_seconds"] for s in summaries if s["time"]["span_seconds"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "module": module_name(module_dir),
        "module_version": module_version(module_dir),
        "totals": {
            "conversations": len(summaries),
            "participants": len(people),
            "speaker_conversation_pairs": len(speaker_rows),
            "span_seconds": _round(span),
            "span_hours": _round(span / 3600, 2),
            "speech_seconds": _round(speech),
            "overlap_seconds": _round(overlap),
            "share_overlap_of_speech": _ratio(overlap, speech),
            "units": total(("units", "count")),
            "turns": total(("units", "turns")),
            "tokens": tokens,
            "tokens_by_type": dict(sorted(by_type_tokens.items())),
            "linguistic_tokens": ling,
            "share_linguistic": _ratio(ling, tokens),
            "annotated_overlap_events": total(("annotated_overlaps", "events")),
            "share_linguistic_tokens_in_annotated_overlap": _ratio(ovl_ling, ling),
            "units_with_variation": total(("variation", "units_with_variation")),
            "tokens_per_second": _round(tokens / speech, 4) if speech else None,
            "linguistic_tokens_per_second": _round(ling / speech, 4) if speech else None,
            "conversation_span_seconds": {
                "min": _round(min(spans)) if spans else None,
                "median": _round(statistics.median(spans)) if spans else None,
                "max": _round(max(spans)) if spans else None,
            },
        },
        "by_conversation_type": type_rows,
        "conversations": conv_rows,
        "participants": participants,
        "speakers_in_conversations": speaker_rows,
    }


def update_module(module_dir: Path, out_root: Path, codes: list[str] | None = None) -> Path:
    """Regenerate the summaries of *codes* (default: every tsv/*.vert.tsv,
    removing summaries of conversations that no longer exist), then rebuild
    the module snapshot. Returns the snapshot path."""
    module_dir = Path(module_dir)
    conversations, participants = _load_inputs(module_dir)
    available = sorted(p.name.removesuffix(".vert.tsv")
                       for p in (module_dir / "tsv").glob("*.vert.tsv"))
    if codes is None:
        codes = available
        for p in summaries_dir(module_dir, out_root).glob("*.json"):
            if p.name != SNAPSHOT_NAME and p.stem not in available:
                p.unlink()
    for code in codes:
        write_conversation(module_dir, code, out_root, conversations, participants)
    snapshot = build_snapshot(module_dir, out_root)
    path = summaries_dir(module_dir, out_root) / SNAPSHOT_NAME
    _write_json(path, snapshot)
    return path


def check_module(module_dir: Path, out_root: Path) -> list[str]:
    """Problems that make the committed summaries disagree with tsv/ (empty
    when they are up to date): a missing or stale summary, a summary without
    a tsv, or a snapshot that does not list exactly the module's conversations."""
    module_dir = Path(module_dir)
    problems = []
    available = {p.name.removesuffix(".vert.tsv"): p
                 for p in (module_dir / "tsv").glob("*.vert.tsv")}
    sdir = summaries_dir(module_dir, out_root)
    present = {p.stem: p for p in sdir.glob("*.json") if p.name != SNAPSHOT_NAME}
    for code, vert in sorted(available.items()):
        if code not in present:
            problems.append(f"{code}: no summary")
            continue
        with present[code].open(encoding=ENCODING_READ) as f:
            recorded = json.load(f).get("source", {}).get("vert_sha256")
        if recorded != file_sha256(vert):
            problems.append(f"{code}: summary is older than tsv/{code}.vert.tsv")
    for code in sorted(set(present) - set(available)):
        problems.append(f"{code}: summary has no tsv/{code}.vert.tsv")
    snapshot = sdir / SNAPSHOT_NAME
    if not snapshot.is_file():
        problems.append(f"{SNAPSHOT_NAME}: missing")
    else:
        with snapshot.open(encoding=ENCODING_READ) as f:
            listed = {c["code"] for c in json.load(f).get("conversations", [])}
        if listed != set(available):
            problems.append(f"{SNAPSHOT_NAME}: lists {len(listed)} conversations, "
                            f"tsv/ has {len(available)}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output-dir", required=True, type=Path, metavar="DIR",
                    help="Root of the summaries repository; each module gets a folder <DIR>/<Module>/")
    ap.add_argument("module_dirs", nargs="+", type=Path, help="Module directories, e.g. KIP ParlaBO")
    ap.add_argument("--code", action="append",
                    help="Only regenerate this conversation (repeatable); the snapshot is rebuilt anyway.")
    ap.add_argument("--check", action="store_true",
                    help="Write nothing; list summaries that are missing or older than their "
                         "tsv/ file, and exit 1 if there are any.")
    args = ap.parse_args()
    if args.check:
        failed = False
        for module_dir in args.module_dirs:
            problems = check_module(module_dir, args.output_dir)
            failed |= bool(problems)
            print(f"{module_name(module_dir)}: " + ("up to date" if not problems else
                  f"{len(problems)} problem(s)"))
            for p in problems:
                print(f"  {p}")
        raise SystemExit(1 if failed else 0)
    for module_dir in args.module_dirs:
        if not (module_dir / "tsv").is_dir():
            raise SystemExit(f"{module_dir}: no tsv/ directory")
        path = update_module(module_dir, args.output_dir, args.code)
        snap = json.loads(path.read_text(encoding="utf-8"))
        print(f"{module_name(module_dir)}: {snap['totals']['conversations']} conversations, "
              f"{snap['totals']['tokens']} tokens -> {path}")


if __name__ == "__main__":
    main()
