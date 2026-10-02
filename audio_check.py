#!/usr/bin/env python3
"""
audio_check.py — Is the last annotation of a transcript compatible with the
length of its recording?

A transcript whose last annotation ends *after* the audio, or *long before* it,
usually points at a problem: a wrong or swapped recording, a truncated or
unfinished transcription, a time-shifted tier, or a wrong `duration` in
conversations.tsv.

Audio length comes from, in order:
  1. the audio file itself (``<audio_dir>/<code>.<mp3|wav|flac|m4a|ogg|aac>``,
     measured with ffprobe), when an audio directory is given;
  2. otherwise the ``duration`` column of ``metadata/conversations.tsv``.
The result records which one was used (``source``).

Two rules, both reported as *warnings* (they never block the pipeline):

  AUDIO_OVERRUN    last annotation ends more than ``max_overrun`` seconds after
                   the audio ends (it cannot be right)
  AUDIO_UNDERRUN   last annotation ends more than ``max_underrun`` seconds
                   before the audio ends (incomplete transcription, or a long
                   silent tail)

Metadata durations are often rounded to the minute, so when the source is the
metadata ``max_overrun`` is widened by ``metadata_slack`` seconds.

Configuration (``audio_check`` in configs/defaults.yml, overridable per module):
``enabled``, ``max_overrun``, ``max_underrun``, ``metadata_slack``.

Used by:
  * sync.py --from-eaf, cli.py process     annotate_summary() on each transcript
  * standalone, over whole modules:
        python audio_check.py <module_dir> [<module_dir> ...] [--audio-dir DIR] [--update-summary]
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
from pathlib import Path

import config as config_mod
from textutil import ENCODING_READ

AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac")

DEFAULTS = {
    "enabled": True,
    "max_overrun": 1.0,       # seconds the last annotation may end after the audio
    "max_underrun": 120.0,    # seconds it may end before the audio
    "metadata_slack": 60.0,   # extra overrun allowed when the length comes from metadata
}

_END_RE = re.compile(r"\bEnd=([0-9]+(?:\.[0-9]+)?)")


# ---------------------------------------------------------------------------
# Audio length
# ---------------------------------------------------------------------------

def parse_duration(value: str | None) -> float | None:
    """``hh:mm:ss`` / ``mm:ss`` / seconds -> seconds; None when not parseable."""
    value = (value or "").strip()
    if not value or value == "_":
        return None
    try:
        seconds = 0.0
        for part in value.split(":"):
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        return None


def find_audio(audio_dir: Path | None, code: str) -> Path | None:
    if not audio_dir:
        return None
    for ext in AUDIO_EXTENSIONS:
        candidate = Path(audio_dir) / f"{code}{ext}"
        if candidate.is_file():
            return candidate
    return None


def probe_duration(path: Path) -> float | None:
    """Duration in seconds of an audio file via ffprobe, or None when ffprobe is
    missing or fails."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def metadata_duration(module_dir: Path, code: str) -> float | None:
    path = Path(module_dir) / "metadata" / "conversations.tsv"
    if not path.is_file():
        return None
    with path.open(encoding=ENCODING_READ, newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if (row.get("code") or "").strip() == code:
                return parse_duration(row.get("duration"))
    return None


def audio_length(code: str, module_dir: Path, audio_dir: Path | None
                 ) -> tuple[float | None, str | None]:
    """(seconds, source) with source in {"audio", "metadata", None}."""
    audio = find_audio(audio_dir, code)
    if audio is not None:
        seconds = probe_duration(audio)
        if seconds is not None:
            return seconds, "audio"
    seconds = metadata_duration(module_dir, code)
    if seconds is not None:
        return seconds, "metadata"
    return None, None


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def settings(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    merged.update((cfg or {}).get("audio_check") or {})
    return merged


def evaluate(last_end: float | None, audio_seconds: float | None, source: str | None,
             cfg: dict | None = None) -> dict:
    """Compare the end of the last annotation with the audio length.

    Returns {"status": ok|overrun|underrun|unavailable, "source", "audio_seconds",
    "last_annotation_seconds", "difference_seconds"} where the difference is
    audio minus last annotation (negative = annotations run past the audio).
    """
    s = settings(cfg)
    if last_end is None or audio_seconds is None:
        return {"status": "unavailable", "source": source, "audio_seconds": audio_seconds,
                "last_annotation_seconds": last_end, "difference_seconds": None}
    diff = audio_seconds - last_end
    overrun_limit = s["max_overrun"] + (s["metadata_slack"] if source == "metadata" else 0.0)
    if -diff > overrun_limit:
        status = "overrun"
    elif diff > s["max_underrun"]:
        status = "underrun"
    else:
        status = "ok"
    return {"status": status, "source": source,
            "audio_seconds": round(audio_seconds, 3),
            "last_annotation_seconds": round(last_end, 3),
            "difference_seconds": round(diff, 3)}


_RULE = {"overrun": "AUDIO_OVERRUN", "underrun": "AUDIO_UNDERRUN"}


def last_annotation_end(transcript) -> float | None:
    ends = [tu.end for tu in transcript.transcription_units
            if tu.include and tu.end is not None]
    return max(ends) if ends else None


def annotate_summary(summary: dict, transcript, module_dir: Path,
                     audio_dir: Path | None = None, cfg: dict | None = None) -> dict:
    """Run the check for one processed transcript and record it in its summary:
    ``summary["AUDIO_CHECK"]`` always, and a count under
    ``summary["WARNINGS"]["AUDIO_OVERRUN" | "AUDIO_UNDERRUN"]`` when it fires."""
    if not settings(cfg)["enabled"]:
        return summary
    code = summary.get("transcript")
    seconds, source = audio_length(code, Path(module_dir), audio_dir)
    result = evaluate(last_annotation_end(transcript), seconds, source, cfg)
    summary["AUDIO_CHECK"] = result
    rule = _RULE.get(result["status"])
    if rule:
        summary.setdefault("WARNINGS", {})[rule] = 1
    return summary


# ---------------------------------------------------------------------------
# Whole-module run (from the vert.tsv files)
# ---------------------------------------------------------------------------

def last_end_from_vert(path: Path) -> float | None:
    last = None
    with Path(path).open(encoding=ENCODING_READ, newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            m = _END_RE.search(row.get("align") or "")
            if m:
                value = float(m.group(1))
                last = value if last is None else max(last, value)
    return last


def check_module(module_dir: Path, audio_dir: Path | None = None,
                 cfg: dict | None = None) -> list[dict]:
    """Check every tsv/<code>.vert.tsv of a module. One result per conversation,
    each with a ``code``."""
    module_dir = Path(module_dir)
    results = []
    for vert in sorted((module_dir / "tsv").glob("*.vert.tsv")):
        code = vert.name[: -len(".vert.tsv")]
        seconds, source = audio_length(code, module_dir, audio_dir)
        result = evaluate(last_end_from_vert(vert), seconds, source, cfg)
        result["code"] = code
        results.append(result)
    return results


def update_summary(module_dir: Path, results: list[dict]) -> int:
    """Write the results into <module>/tmp/process/json/summary.json (the file
    the validation page is built from), so a module processed before this check
    existed can be brought up to date without reprocessing. Returns the number
    of conversations updated."""
    import json
    path = Path(module_dir) / "tmp" / "process" / "json" / "summary.json"
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    by_code = {r["code"]: r for r in results}
    n = 0
    for entry in data:
        result = by_code.get(entry.get("transcript"))
        if result is None:
            continue
        check = {k: v for k, v in result.items() if k != "code"}
        entry["AUDIO_CHECK"] = check
        warnings = entry.setdefault("WARNINGS", {})
        for rule in _RULE.values():
            warnings.pop(rule, None)
        if check["status"] in _RULE:
            warnings[_RULE[check["status"]]] = 1
        n += 1
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return n


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(round(seconds))
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def module_config(module_dir: Path) -> dict:
    """Config for a module dir (Stra-ParlaBO -> StraParlaBO), defaults if none."""
    name = Path(module_dir).resolve().name.replace("-", "")
    try:
        return config_mod.load_config(name)
    except FileNotFoundError:
        return config_mod.load_config(None)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="+", type=Path, help="Module root directories.")
    ap.add_argument("--audio-dir", type=Path,
                    help="Directory with <code>.mp3/.wav/... Default: use metadata `duration`.")
    ap.add_argument("--all", action="store_true", help="Also list conversations that are fine.")
    ap.add_argument("--update-summary", action="store_true",
                    help="Also record the results in <module>/tmp/process/json/summary.json, "
                         "so the validation page shows them without reprocessing.")
    args = ap.parse_args()

    problems = 0
    for module_dir in args.modules:
        cfg = module_config(module_dir)
        results = check_module(module_dir, args.audio_dir, cfg)
        counts = {k: sum(1 for r in results if r["status"] == k)
                  for k in ("ok", "overrun", "underrun", "unavailable")}
        sources = {r["source"] for r in results if r["source"]}
        print(f"== {module_dir.name}: {len(results)} conversations "
              f"(length from: {', '.join(sorted(sources)) or 'nothing available'}) — "
              + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
        for r in results:
            if r["status"] == "ok" and not args.all:
                continue
            print(f"  {r['code']:10} {r['status']:11} audio {_fmt(r['audio_seconds'])} "
                  f"({r['source'] or '-'})  last annotation {_fmt(r['last_annotation_seconds'])}  "
                  f"diff {r['difference_seconds'] if r['difference_seconds'] is not None else '-'}s")
        problems += counts["overrun"] + counts["underrun"]
        if args.update_summary:
            print(f"  summary.json updated for {update_summary(module_dir, results)} conversations")
    # Warnings only: never fail the shell.
    print(f"{problems} conversation(s) to look at")


if __name__ == "__main__":
    main()
