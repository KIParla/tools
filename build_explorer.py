#!/usr/bin/env python3
"""
build_explorer.py — build the KIParla corpus explorer, a small static site
(index.html, explorer.css, core.js, app.js, data.json), from the modules'
summaries/snapshot.json (see summarize.py).

The page lets you filter the corpus by conversation metadata, speaker
attributes and measured features (overlap shares, token/time rates, ...), see
basic figures about the resulting sub-corpus, and export it (list of codes,
CSV, a script that copies the files).

Usage:
    python build_explorer.py --modules KIP KIPasti ParlaBO ParlaTO ParlaBZ \\
        Stra-ParlaBO Stra-ParlaTO --output-dir KIParla-artifacts/explorer \\
        [--artifacts-url URL] [--offline] [--zip kiparla-explorer.zip]

Run summarize.py on the modules first. The explorer reads only the snapshots,
so it can be rebuilt without the vert.tsv files. A conversation that appears in
more than one module (KIP and ParlaTO share 16) is listed once, under the first
module given, and records the others in `modules`.

In production the page fetches data.json at load time, so the data can be cached
and updated without touching the code, and is usable directly for analysis. A page
opened from disk cannot fetch, so --offline (and --zip, which always does this)
embeds the data as a classic script, data.js, instead. Links to the transcription pages use
--artifacts-url (default: the published KIParla-artifacts site) so they work
wherever the folder is; pass ../ to link to a sibling checkout instead.

Source: explorer/template.html (markup), explorer/explorer.css (styles),
explorer/app.js (interface) and explorer/core.js (filtering and statistics,
tested under Node).
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from textutil import ENCODING_READ, ENCODING_WRITE

SCHEMA_VERSION = 1
EXPLORER_DIR = Path(__file__).resolve().parent / "explorer"
UNKNOWN = "unknown"
DEFAULT_ARTIFACTS_URL = "https://kiparla.github.io/KIParla-artifacts/"
SEARCH_URL = "https://search.corpuskiparla.it/"
SITE_FILES = ("index.html", "explorer.css", "core.js", "app.js")   # copied as they are
DATA_SCRIPT_MARKER = "<!--__DATA_SCRIPT__-->"

# Conversation-level measured features. `get` builds the value from a snapshot
# row; `unit` and `decimals` drive how the page formats it.
# id, label, unit, decimals, description
METRICS = [
    ("hours", "Duration", "h", 2, "Time from the first to the last annotated unit"),
    ("speech_hours", "Speech time", "h", 2, "Time with at least one speaker talking"),
    ("silence_pct", "Silence", "%", 1, "Share of the duration with nobody talking"),
    ("overlap_pct", "Overlap (time)", "%", 1,
     "Share of speech time with two or more speakers talking at once"),
    ("units_overlap_pct", "Units overlapped", "%", 1,
     "Share of transcription units that overlap in time with another speaker's unit"),
    ("ann_overlap_pct", "Overlap (tokens)", "%", 1,
     "Share of linguistic tokens inside overlaps marked by the transcribers"),
    ("tokens", "Tokens", "", 0, "All tokens (words, pauses, non-verbal behaviour, ...)"),
    ("ling", "Linguistic tokens", "", 0, "Word tokens"),
    ("units", "Units", "", 0, "Transcription units"),
    ("turns", "Turns", "", 0, "Runs of consecutive units by the same speaker"),
    ("tokens_per_unit", "Tokens per unit", "", 1, "Mean length of a transcription unit"),
    ("rate", "Speech rate", "tok/s", 2,
     "Linguistic tokens per second of speech time"),
    ("variation_pct", "Units with variation", "%", 1,
     "Share of units containing another variety or language"),
    ("speakers_n", "Speakers", "", 0, "Identified speakers in the conversation"),
]

# Conversation facets (categorical). id, label, description
CONVERSATION_FACETS = [
    ("module", "Module", "Module(s) that contain the conversation"),
    ("type", "Type", "Kind of interaction"),
    ("subtype", "Subtype", "Refinement of the type, where recorded"),
    ("relationship", "Relationship", "Symmetric or asymmetric roles of the speakers"),
    ("moderator", "Moderator", "Whether an interviewer or moderator is present"),
    ("topic", "Topic", "Fixed or free topic"),
    ("year", "Year", "Year of the recording"),
    ("point", "Collection point", "Where the conversation was collected"),
    ("languages", "Languages", "Languages in the conversation, where recorded"),
]

# Speaker facets: id, label, participants.tsv column(s), in order of preference
SPEAKER_FACETS = [
    ("gender", "Gender", ("gender",)),
    ("age", "Age range", ("age-range",)),
    ("occupation", "Occupation", ("occupation",)),
    ("study", "Study level", ("study-level",)),
    ("region", "Region (birth or school)", ("birth-region", "school-region")),
    ("mothertongue", "Mother tongue", ("mothertongue",)),
]


# Facets whose cell holds several values separated by ";" (split into options).
MULTI_VALUED = {"languages", "mothertongue"}


def _missing(value) -> bool:
    return value is None or str(value).strip() in ("", "_", "N/A")


def _clean(value):
    return None if _missing(value) else str(value).strip()


def _top(value):
    """'free-conversation:meal' -> ('free-conversation', 'meal')."""
    value = _clean(value)
    if value is None:
        return None, None
    head, _, tail = value.partition(":")
    return head, (tail or None)


def _round(x, nd):
    return None if x is None else round(x, nd)


def _pct(x):
    return None if x is None else round(100 * x, 2)


def conversation_record(row: dict, module: str) -> dict:
    """One conversation of the explorer dataset from a snapshot's flat row."""
    meta = row.get("metadata") or {}
    ctype, subtype = _top(meta.get("type"))
    span = row.get("span_seconds")
    speech = row.get("speech_seconds")
    units = row.get("units") or 0
    tokens = row.get("tokens") or 0
    year = _clean(meta.get("year"))
    return {
        "code": row["code"],
        "modules": [module],
        "type": ctype,
        "subtype": subtype,
        "relationship": _clean(meta.get("participants-relationship")),
        "moderator": _clean(meta.get("moderator")),
        "topic": _clean(meta.get("topic")),
        "year": year,
        "point": _clean(meta.get("collection-point")),
        "languages": _clean(meta.get("languages")),
        "hours": _round(span / 3600, 4) if span is not None else None,
        "speech_hours": _round(speech / 3600, 4) if speech is not None else None,
        "silence_pct": (_pct(row["silence_seconds"] / span)
                        if span and row.get("silence_seconds") is not None else None),
        "overlap_pct": _pct(row.get("share_overlap_of_speech")),
        "units_overlap_pct": _pct(row.get("share_units_overlapped_in_time")),
        "ann_overlap_pct": _pct(row.get("share_linguistic_tokens_in_annotated_overlap")),
        "tokens": tokens,
        "ling": row.get("linguistic_tokens"),
        "units": units,
        "turns": row.get("turns"),
        "tokens_per_unit": _round(tokens / units, 2) if units else None,
        "rate": _round(row.get("linguistic_tokens_per_second"), 3),
        "variation_pct": _pct(row["units_with_variation"] / units) if units else None,
        "speakers_n": None,   # filled from the speaker rows
    }


def speaker_record(row: dict, module: str) -> dict:
    p = row.get("participant") or {}
    out = {
        "conv": row["conversation"],
        "spk": row["speaker"],
        "known": bool(row.get("in_participants_metadata")),
        "tokens": row.get("tokens"),
        "ling": row.get("linguistic_tokens"),
        "speech_s": row.get("speech_seconds"),
        "rate": _round(row.get("linguistic_tokens_per_second"), 3),
        "overlap_pct": _pct(row.get("share_of_own_speech_overlapped")),
        "share_pct": _pct(row.get("share_of_linguistic_tokens")),
    }
    for fid, _label, columns in SPEAKER_FACETS:
        value = None
        for column in columns:
            value = _clean(p.get(column))
            if value is not None:
                break
        if fid == "region" and value is not None:
            value = value.split(":")[0]
        out[fid] = value
    return out


def _with_slash(url: str) -> str:
    return url if not url or url.endswith("/") else url + "/"


def build_dataset(module_dirs: list[Path], artifacts_url: str = DEFAULT_ARTIFACTS_URL) -> dict:
    conversations: dict[str, dict] = {}
    speakers: list[dict] = []
    sources: dict[str, str | None] = {}
    seen_speaker_rows: set[tuple[str, str]] = set()

    for module_dir in module_dirs:
        module_dir = Path(module_dir)
        snap_path = module_dir / "summaries" / "snapshot.json"
        if not snap_path.is_file():
            raise SystemExit(f"{module_dir}: no summaries/snapshot.json (run summarize.py first)")
        with snap_path.open(encoding=ENCODING_READ) as f:
            snap = json.load(f)
        module = snap["module"]
        sources[module] = snap.get("module_version")
        for row in snap["conversations"]:
            code = row["code"]
            if code in conversations:
                if module not in conversations[code]["modules"]:
                    conversations[code]["modules"].append(module)
                continue
            conversations[code] = conversation_record(row, module)
        for row in snap["speakers_in_conversations"]:
            key = (row["conversation"], row["speaker"])
            if key in seen_speaker_rows or conversations[row["conversation"]]["modules"][0] != module:
                continue
            seen_speaker_rows.add(key)
            speakers.append(speaker_record(row, module))

    by_conv: dict[str, int] = {}
    for s in speakers:
        if s["known"]:
            by_conv[s["conv"]] = by_conv.get(s["conv"], 0) + 1
    for code, c in conversations.items():
        c["speakers_n"] = by_conv.get(code, 0)

    ordered = [conversations[c] for c in sorted(conversations)]
    return {
        "schema": SCHEMA_VERSION,
        "sources": sources,
        "links": {"artifacts": _with_slash(artifacts_url), "search": SEARCH_URL},
        "metrics": [{"id": i, "label": l, "unit": u, "decimals": d, "description": t}
                    for i, l, u, d, t in METRICS],
        "conversation_facets": [{"id": i, "label": l, "description": d, "multi": i in MULTI_VALUED}
                                for i, l, d in CONVERSATION_FACETS],
        "speaker_facets": [{"id": i, "label": l, "multi": i in MULTI_VALUED}
                           for i, l, _c in SPEAKER_FACETS],
        "conversations": ordered,
        "speakers": sorted(speakers, key=lambda s: (s["conv"], s["spk"])),
    }


def data_json(dataset: dict) -> str:
    return json.dumps(dataset, ensure_ascii=False, separators=(",", ":")) + "\n"


def data_script(dataset: dict) -> str:
    """data.js: the dataset as a classic script, for the offline bundle."""
    return ("/* KIParla corpus explorer: data, built by build_explorer.py */\n"
            "window.KIPARLA_DATA = " + data_json(dataset).rstrip("\n") + ";\n")


DATA_SCRIPT_TAG = '<script src="data.js"></script>'


def site_files(dataset: dict, offline: bool = False) -> dict[str, str]:
    """File name -> text of the explorer. Production loads data.json at run
    time; the offline bundle embeds the data as data.js (a page opened from
    disk cannot fetch)."""
    files = {}
    for name in SITE_FILES:
        src = EXPLORER_DIR / ("template.html" if name == "index.html" else name)
        if not src.is_file():
            raise SystemExit(f"{src} is missing")
        files[name] = src.read_text(encoding=ENCODING_READ)
    if DATA_SCRIPT_MARKER not in files["index.html"]:
        raise SystemExit(f"template.html has no {DATA_SCRIPT_MARKER} marker")
    files["index.html"] = files["index.html"].replace(
        DATA_SCRIPT_MARKER, DATA_SCRIPT_TAG if offline else "")
    if offline:
        files["data.js"] = data_script(dataset)
    else:
        files["data.json"] = data_json(dataset)
    return files


def write_files(files: dict[str, str], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in files.items():
        dest = out_dir / name
        with dest.open("w", encoding=ENCODING_WRITE, newline="\n") as f:
            f.write(text)
        written.append(dest)
    return written


def write_site(dataset: dict, out_dir: Path, offline: bool = False) -> list[Path]:
    """Write the explorer into *out_dir*; return the files written."""
    return write_files(site_files(dataset, offline), out_dir)


def write_zip(dataset: dict, zip_path: Path, folder: str = "kiparla-explorer") -> list[str]:
    """The offline bundle (data built in, opens from disk) as a zip."""
    files = site_files(dataset, offline=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(f"{folder}/{name}", text.encode(ENCODING_WRITE))
    return list(files)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules", nargs="+", required=True, type=Path,
                    help="Module directories, in priority order for shared conversations")
    ap.add_argument("--output-dir", required=True, type=Path,
                    help="Folder to write the explorer into, e.g. KIParla-artifacts/explorer")
    ap.add_argument("--artifacts-url", default=DEFAULT_ARTIFACTS_URL,
                    help="Where the HTML transcription pages are (default: the published "
                         "KIParla-artifacts site). Use ../ to link to a sibling checkout.")
    ap.add_argument("--offline", action="store_true",
                    help="Embed the data as data.js instead of data.json, so index.html opens "
                         "from disk (production should load data.json)")
    ap.add_argument("--zip", type=Path,
                    help="Also write the offline bundle as a zip, ready to send")
    args = ap.parse_args()

    dataset = build_dataset(args.modules, args.artifacts_url)
    files = write_site(dataset, args.output_dir, args.offline)
    if args.zip:
        write_zip(dataset, args.zip)
    size = sum(f.stat().st_size for f in files) / 1024
    print(f"{args.output_dir}: {len(dataset['conversations'])} conversations, "
          f"{len(dataset['speakers'])} speaker rows, {len(files)} files, {size:.0f} KiB"
          + (f"; zip: {args.zip}" if args.zip else ""))


if __name__ == "__main__":
    main()
