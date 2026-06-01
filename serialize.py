"""
serialize.py — Pipeline step 8: read CSV input and write output files.

Public API:
    read_csv(path, cfg)             → (Transcript, translations)
    conversation_to_conll(t, path)  → writes <name>.vert.tsv
    build_json(t)                   → dict summary
    write_json(t, path)             → writes <name>.json
    write_translations(rows, path)  → writes <name>.translations.tsv
    process(input_path, output_dir, cfg, annotations)  → runs full pipeline
"""

from __future__ import annotations

import csv
import json
import logging
import re
import yaml
from ast import literal_eval
from pathlib import Path
from typing import Optional

import dataflags as df
from data import Transcript, TranscriptionUnit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

VERT_FIELDNAMES = [
    "token_id", "speaker", "tu_id", "unit", "id", "span",
    "form", "lemma", "upos", "xpos", "feats", "deprel",
    "type", "meta_label", "variation", "jefferson_feats",
    "align", "prolongations", "pace", "guesses", "overlaps",
]

TRANSLATIONS_FIELDNAMES = [
    "tu_id", "speaker", "start", "end", "parent_tu_id", "text",
]


# ---------------------------------------------------------------------------
# Step 1 — Read CSV
# ---------------------------------------------------------------------------

def read_csv(
    path: Path,
    cfg: dict | None = None,
) -> tuple[Transcript, list[dict]]:
    """Read the eaf2csv output and build a Transcript.

    TUs from tiers in ``cfg["tiers_to_ignore"]`` are skipped entirely.
    TUs from tiers in ``cfg["tiers_to_extract"]`` bypass the pipeline and
    are collected as translation rows (returned separately).

    Args:
        path:   path to the tab-separated input CSV.
        cfg:    full pipeline config dict.

    Returns:
        transcript:   Transcript with all included TUs preprocessed.
        translations: list of raw row dicts for extracted tiers.
    """
    if cfg is None:
        cfg = {}

    ignore  = set(cfg.get("tiers_to_ignore", []))
    extract = set(cfg.get("tiers_to_extract", []))

    transcript   = Transcript(path.stem)
    translations: list[dict] = []

    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            speaker = row.get("speaker", "")

            if speaker in ignore:
                continue

            if speaker in extract:
                translations.append({
                    "tu_id":        row.get("tu_id", ""),
                    "speaker":      speaker,
                    "start":        row.get("start", ""),
                    "end":          row.get("end", ""),
                    "parent_tu_id": row.get("parent_tu_id", "_"),
                    "text":         row.get("text", ""),
                })
                continue

            parent_tu_id = row.get("parent_tu_id", None)
            if parent_tu_id == "_" or parent_tu_id == "":
                parent_tu_id = None
            elif parent_tu_id is not None:
                try:
                    parent_tu_id = int(parent_tu_id)
                except (ValueError, TypeError):
                    parent_tu_id = None

            tu = TranscriptionUnit(
                tu_id        = int(row["tu_id"]),
                speaker      = speaker,
                start        = float(row["start"]),
                end          = float(row["end"]),
                duration     = float(row["duration"]),
                annotation   = row["text"],
                parent_tu_id = parent_tu_id,
                cfg          = cfg,
            )
            transcript.add(tu)

    return transcript, translations


# ---------------------------------------------------------------------------
# Step 8a — Write vert.tsv
# ---------------------------------------------------------------------------

def _jefferson_feats(tok) -> str:
    """Render the pipe-separated jefferson_feats string for one token."""
    parts = []

    if tok.intonation != df.intonation.plain:
        parts.append(f"Intonation={tok.intonation.name}")

    if tok.interruption:
        parts.append("Interrupted=Yes")

    if tok.truncation:
        parts.append("Truncated=Yes")

    if tok.prosodiclink:
        parts.append("ProsodicLink=Yes")

    if not tok.spaceafter:
        parts.append("SpaceAfter=No")

    if tok.pauseafter:
        parts.append("PauseAfter=Yes")

    if tok.non_ita:
        parts.append(f"Language={tok.iso_code}")

    if tok.non_ortho:
        parts.append("Orthography=Yes")

    if tok.volume is not None:
        parts.append(f"Volume={tok.volume.name}")

    if tok.variation != df.tokenvariation.none:
        parts.append(f"Variation={tok.variation.name.capitalize()}")

    if tok.syllables is not None:
        parts.append(f"Syllables={tok.syllables}")

    return "|".join(parts) if parts else "_"


def _align(tok, tu) -> str:
    """Render the align field (Begin/End timestamps) for one token."""
    parts = []
    if tok.position_in_tu & df.position.start:
        parts.append(f"Begin={tu.start}")
    if tok.position_in_tu & df.position.end:
        parts.append(f"End={tu.end}")
    return "|".join(parts) if parts else "_"


def _prolongations(tok) -> str:
    if not tok.prolongations:
        return "_"
    return ",".join(f"{pos}x{length}" for pos, length in tok.prolongations.items())


def _pace(tok) -> str:
    slow = [f"{cs}-{ce}({sid})" for sid, (cs, ce) in tok.slow_pace.items()]
    fast = [f"{cs}-{ce}({sid})" for sid, (cs, ce) in tok.fast_pace.items()]
    if not slow and not fast:
        return "_"
    parts = []
    if slow:
        parts.append("Slow=" + ",".join(slow))
    if fast:
        parts.append("Fast=" + ",".join(fast))
    return "|".join(parts)


def _span_field(spans: dict) -> str:
    if not spans:
        return "_"
    return ",".join(f"{cs}-{ce}({sid})" for sid, (cs, ce) in spans.items())


def conversation_to_conll(transcript: Transcript, output_path: Path, sep: str = "\t"):
    """Write the vert.tsv file for *transcript* (step 8a)."""
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=VERT_FIELDNAMES, delimiter=sep, restval="_"
        )
        writer.writeheader()

        for tu in transcript.transcription_units:
            if not tu.include:
                continue
            for tok_idx, tok in enumerate(tu.tokens):
                row: dict = {
                    "token_id":      f"{tu.tu_id}-{tok_idx}",
                    "speaker":       tu.speaker,
                    "tu_id":         tu.tu_id,
                    "unit":          tu.tu_id,
                    "id":            tok_idx,
                    "span":          tu.annotation[tok.span[0]:tok.span[1]],
                    "form":          tok.form,
                    "lemma":         "_",
                    "upos":          "_",
                    "xpos":          "_",
                    "feats":         "_",
                    "deprel":        "_",
                    "type":          tok.token_type.name,
                    "meta_label":    "_",
                    "variation":     tu.non_ita.name,
                    "jefferson_feats": _jefferson_feats(tok),
                    "align":         _align(tok, tu),
                    "prolongations": _prolongations(tok),
                    "pace":          _pace(tok),
                    "guesses":       _span_field(tok.guesses),
                    "overlaps":      _span_field(tok.overlaps),
                }
                writer.writerow(row)


# ---------------------------------------------------------------------------
# Step 8b — Build / write JSON summary
# ---------------------------------------------------------------------------

def build_json(transcript: Transcript) -> dict:
    """Build the JSON summary dict for *transcript*."""
    ret: dict = {
        "transcript": transcript.tr_id,
        "speakers": {s: {} for s in transcript.speakers},
        "TUs": 0,
        "removed_TUs": 0,
        "overlaps": 0,
        "WARNINGS": {},
        "ERRORS": {},
    }

    for tu in transcript.transcription_units:
        if not tu.include:
            ret["removed_TUs"] += 1
            continue

        ret["TUs"] += 1
        spk = ret["speakers"].setdefault(tu.speaker, {})

        spk["TUs"]             = spk.get("TUs", 0) + 1
        spk["time"]            = spk.get("time", 0.0) + tu.duration
        spk["tokens"]          = spk.get("tokens", 0) + len(tu.tokens)
        spk["tokens-ling"]     = spk.get("tokens-ling", 0)     + sum(1 for t in tu.tokens if df.tokentype.linguistic     in t.token_type)
        spk["tokens-unk"]      = spk.get("tokens-unk", 0)      + sum(1 for t in tu.tokens if df.tokentype.unknown        in t.token_type)
        spk["tokens-anonym"]   = spk.get("tokens-anonym", 0)   + sum(1 for t in tu.tokens if df.tokentype.anonymized     in t.token_type)
        spk["tokens-nvb"]      = spk.get("tokens-nvb", 0)      + sum(1 for t in tu.tokens if df.tokentype.nonverbalbehavior in t.token_type)
        spk["tokens-pause"]    = spk.get("tokens-pause", 0)    + sum(1 for t in tu.tokens if df.tokentype.shortpause     in t.token_type)
        spk["tokens-err"]      = spk.get("tokens-err", 0)      + sum(1 for t in tu.tokens if df.tokentype.error          in t.token_type)
        spk["code-switching"]  = spk.get("code-switching", 0)  + (1 if tu.non_ita != df.languagevariation.none else 0)

        for key, count in tu.warnings.items():
            ret["WARNINGS"][key] = ret["WARNINGS"].get(key, 0) + count
        for key, has_error in tu.errors.items():
            if has_error:
                ret["ERRORS"][key] = ret["ERRORS"].get(key, 0) + 1

    ret["overlaps"] = len(transcript.overlap_events)
    return ret


def write_json(transcript: Transcript, output_path: Path):
    """Write the JSON summary file for *transcript* (step 8b)."""
    data = build_json(transcript)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Step 8c — Write translations TSV
# ---------------------------------------------------------------------------

def write_translations(rows: list[dict], output_path: Path, sep: str = "\t"):
    """Write the translations TSV for extracted tiers (step 8c)."""
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=TRANSLATIONS_FIELDNAMES, delimiter=sep, restval="_"
        )
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Full pipeline (steps 1–8)
# ---------------------------------------------------------------------------

def process(
    input_path: Path,
    output_dir: Path,
    cfg: dict | None = None,
    annotations: dict | None = None,
) -> dict:
    """Run the full processing pipeline on one conversation CSV.

    Args:
        input_path:  path to the input CSV (eaf2csv output).
        output_dir:  directory for output files.
        cfg:         pipeline config dict (from load_config).
        annotations: per-file annotation dict (e.g. ``{"ignore": [...]}``).

    Returns:
        The JSON summary dict.
    """
    if cfg is None:
        cfg = {}
    if annotations is None:
        annotations = {}

    overlap_cfg = cfg.get("overlaps", {})
    duration_threshold = overlap_cfg.get("duration_threshold", 0.1)
    nvb_participates   = overlap_cfg.get("nvb_participates_in_overlaps", False)

    relations_to_ignore: list[tuple] = []
    for pair in annotations.get("ignore", []):
        parts = str(pair).split()
        if len(parts) == 2:
            try:
                relations_to_ignore.append((int(parts[0]), int(parts[1])))
            except ValueError:
                logger.warning("Could not parse ignore pair: %s", pair)

    # Step 1 — Read CSV and preprocess (preprocessing runs in __post_init__).
    transcript, translations = read_csv(input_path, cfg)

    # Step 3 — Sort.
    transcript.sort()

    # Step 5 — Tokenize.
    for tu in transcript.transcription_units:
        tu.tokenize(cfg)

    # Step 4 — Find time-based overlaps.
    transcript.find_overlaps(duration_threshold)

    # Step 6 — Resolve overlaps.
    transcript.check_overlaps(
        duration_threshold,
        relations_to_ignore=relations_to_ignore,
        nvb_participates=nvb_participates,
    )

    # Step 7 — Map span features to tokens.
    for tu in transcript.transcription_units:
        if tu.include:
            tu.add_token_features()

    # Step 8 — Serialize.
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem

    conversation_to_conll(transcript, output_dir / f"{stem}.vert.tsv")
    write_json(transcript, output_dir / f"{stem}.json")

    if translations:
        write_translations(translations, output_dir / f"{stem}.translations.tsv")

    return build_json(transcript)


# ---------------------------------------------------------------------------
# EAF ↔ CSV conversion
# ---------------------------------------------------------------------------

def load_annotations(fname):
    """Load YAML annotation file."""
    with open(fname, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def eaf2csv(input_filename, output_filename, annotations, sep="\t"):
    """Read an ELAN .eaf file and write a CSV with tu_id/speaker/start/end/duration/text."""
    from speach import elan

    fieldnames = ["tu_id", "speaker", "start", "end", "duration", "text"]
    full_file = []

    eaf_doc = elan.read_eaf(input_filename)
    for tier in eaf_doc:
        for anno in tier.annotations:
            _from_ts = f"{anno.from_ts.sec:.3f}" if anno.from_ts is not None else ""
            _to_ts = f"{anno.to_ts.sec:.3f}" if anno.to_ts is not None else ""
            _duration = f"{anno.duration:.3f}" if anno.duration is not None else ""
            to_write = {
                "speaker": tier.ID,
                "start": _from_ts,
                "end": _to_ts,
                "duration": _duration,
                "id": None,
            }
            text_matches = re.split(r"^(id:)([0-9]+) ", anno.value.strip())
            to_write["text"] = text_matches[-1]
            if len(text_matches) > 1:
                to_write["id"] = text_matches[2]
            full_file.append(to_write)

    to_remap = {}
    full_file = sorted(full_file, key=lambda x: float(x["start"]))

    with open(output_filename, "w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=sep, extrasaction="ignore")
        writer.writeheader()
        for el_no, to_write in enumerate(full_file):
            to_write["tu_id"] = el_no
            to_remap[to_write["id"]] = el_no
            writer.writerow(to_write)

    if "ignore" in annotations:
        for pos, el_list in enumerate(annotations["ignore"]):
            el_list = el_list.split()
            new_list = []
            for x in el_list:
                x = int(x)
                new_list.append(str(to_remap[x]) if x in to_remap else str(x))
            annotations["ignore"][pos] = " ".join(new_list)


def csv2eaf(input_filename, linked_file, output_filename,
            sep="\t", multiplier=1000, include_ids=False):
    """Read a pipeline CSV and write an ELAN .eaf file."""
    from pympi import Elan as EL

    tus = []
    tiers = set()
    with open(input_filename, encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile, delimiter=sep)
        for row in reader:
            if "speaker" in row:
                tiers.add(row["speaker"])
                tus.append(row)

    doc = EL.Eaf(author="automatic_pipeline")
    doc.add_linked_file(linked_file, relpath=linked_file)
    for tier_id in tiers:
        doc.add_tier(tier_id=tier_id)

    for annotation in tus:
        if "include" not in annotation or literal_eval(annotation["include"]):
            value = annotation["text"]
            if include_ids:
                value = f"id:{annotation['tu_id']} {annotation['text']}"
            start = int(float(annotation["start"]) * multiplier)
            end = int(float(annotation["end"]) * multiplier)
            if end - start < 0:
                logger.error("Negative duration for %s %s %s %s",
                             annotation["speaker"], start, end, value)
            doc.add_annotation(
                id_tier=annotation["speaker"],
                start=start,
                end=end,
                value=value,
            )

    doc.to_file(output_filename)


# ---------------------------------------------------------------------------
# Transcript → linear CSV (TU-per-row summary)
# ---------------------------------------------------------------------------

def conversation_to_linear(transcript, output_filename, sep="\t"):
    """Write one row per TU with warnings, errors and token-type counts."""
    fieldnames = [
        "tu_id", "speaker", "start", "end", "duration", "include", "variation",
        "W:normalized_spaces", "W:numbers", "W:accents", "W:non_jefferson",
        "W:pauses_trim", "W:prosodic_trim", "W:moved_boundaries", "W:switches",
        "W:overlap_mismatch",
        "E:volume", "E:pace", "E:guess", "E:overlap", "E:overlap_mismatch",
        "E:overlap_annotation", "E:overlap_time", "E:overlap_duration",
        "T:shortpauses", "T:nonverbalbehavior", "T:errors", "T:linguistic",
        "original", "text", "orthographic",
    ]

    with open(output_filename, "w", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=sep, restval="_")
        writer.writeheader()

        for tu in transcript.transcription_units:
            if not tu.include:
                continue
            variation = "_"
            if df.languagevariation.some in tu.non_ita or df.languagevariation.all in tu.non_ita:
                variation = tu.non_ita.name

            text = tu.annotation.replace("{P}", "(.)").replace("{", "((").replace("}", "))")
            orthographic = " ".join(str(tok) for _, tok in tu.tokens.items())
            orthographic = orthographic.replace("{P}", "(.)").replace("{", "((").replace("}", "))")

            if df.languagevariation.some in tu.non_ita:
                text = "# " + text
            if df.languagevariation.all in tu.non_ita:
                text = "#_ " + text

            errors_str = " ".join(tok.text for _, tok in tu.tokens.items()
                                  if df.tokentype.error in tok.token_type)
            t_errors = str(sum(df.tokentype.error in tok.token_type for _, tok in tu.tokens.items()))
            if errors_str:
                t_errors += f", {errors_str}"

            overlap_duration_str = "_"
            if len(tu.overlap_duration) > 0:
                parts = [f"{uid}={dur:.3f}" for uid, dur in tu.overlap_duration.items()]
                overlap_duration_str = ",".join(parts)

            to_write = {
                "tu_id": tu.tu_id,
                "speaker": tu.speaker,
                "start": tu.start,
                "end": tu.end,
                "duration": tu.duration,
                "include": tu.include,
                "variation": variation,
                "original": tu.orig_annotation,
                "text": text,
                "orthographic": orthographic,
                "W:normalized_spaces": tu.warnings["UNEVEN_SPACES"],
                "W:numbers": tu.warnings["NUMBERS"],
                "W:accents": tu.warnings["ACCENTS"],
                "W:non_jefferson": tu.warnings["NON_JEFFERSON"],
                "W:pauses_trim": tu.warnings["TRIM_PAUSES"],
                "W:prosodic_trim": tu.warnings["TRIM_PROSODICLINKS"],
                "W:moved_boundaries": tu.warnings["MOVED_BOUNDARIES"],
                "W:switches": tu.warnings["SWITCHES"],
                "W:overlap_mismatch": tu.warnings["MISMATCHING_OVERLAPS"],
                "E:volume": tu.errors["UNBALANCED_DOTS"],
                "E:pace": tu.errors["UNBALANCED_PACE"],
                "E:guess": tu.errors["UNBALANCED_GUESS"],
                "E:overlap": tu.errors["UNBALANCED_OVERLAP"],
                "E:overlap_mismatch": tu.errors["MISMATCHING_OVERLAPS"],
                "E:overlap_annotation": tu.errors["OVERLAPS:MISSING_ANNOTATION"],
                "E:overlap_time": tu.errors["OVERLAPS:MISSING_TIME"],
                "E:overlap_duration": overlap_duration_str,
                "T:shortpauses": sum(df.tokentype.shortpause in tok.token_type
                                     for _, tok in tu.tokens.items()),
                "T:nonverbalbehavior": sum(df.tokentype.nonverbalbehavior in tok.token_type
                                           for _, tok in tu.tokens.items()),
                "T:errors": t_errors,
                "T:linguistic": sum(df.tokentype.linguistic in tok.token_type
                                    for _, tok in tu.tokens.items()),
            }
            writer.writerow(to_write)


# ---------------------------------------------------------------------------
# Transcript alignment output
# ---------------------------------------------------------------------------

def transcript_from_csv(input_filename, sep="\t"):
    """Build a Transcript from a pipeline CSV (does not run full pipeline)."""
    transcript = Transcript(Path(input_filename).stem)
    with open(input_filename, encoding="utf-8", newline="") as csvfile:
        reader = csv.DictReader(csvfile, delimiter=sep)
        for row in reader:
            new_tu = TranscriptionUnit(
                row["tu_id"],
                row["speaker"],
                float(row["start"]),
                float(row["end"]),
                float(row["duration"]),
                row["text"],
            )
            transcript.add(new_tu)

    transcript.sort()
    for tu in transcript:
        tu.tokenize()
    return transcript


def print_aligned(tokens_a, tokens_b, output_filename, sep="\t"):
    """Write token-pair alignment output (match/id_A/token_A/id_B/token_B)."""
    fieldnames = ["match", "id_A", "token_A", "id_B", "token_B"]
    with open(output_filename, "w", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=sep, restval="_")
        writer.writeheader()
        for toka, tokb in zip(tokens_a, tokens_b):
            row = {"match": 2, "id_A": "_", "token_A": "_", "id_B": "_", "token_B": "_"}
            if toka:
                row["token_A"] = toka.text
                row["id_A"] = toka.id
                row["match"] = 1
            if tokb:
                row["token_B"] = tokb.text
                row["id_B"] = tokb.id
                row["match"] = 2 if row["match"] == 1 else 1
            if row["token_A"] == row["token_B"]:
                row["match"] = 0
            writer.writerow(row)


def print_full_statistics(list_of_transcripts, output_filename):
    """Write per-transcript statistics to a tab-separated CSV."""
    import pandas as pd

    max_columns = 0
    full_statistics = []
    for _, transcript in list_of_transcripts.items():
        transcript.get_stats()
        stats_dict = transcript.statistics.set_index("Statistic")["Value"].to_dict()
        if len(stats_dict["num_tu"]) > max_columns:
            max_columns = len(stats_dict["num_tu"])
        full_statistics.append(stats_dict)

    for stats in full_statistics:
        for field in list(stats.keys()):
            if isinstance(stats[field], list):
                for el in range(max_columns):
                    stats[f"{field}::{el}"] = stats[field][el] if len(stats[field]) > el else 0
                del stats[field]

    pd.DataFrame(full_statistics).to_csv(output_filename, index=False, sep="\t")


# ---------------------------------------------------------------------------
# CoNLL → CoNLL-U conversion
# ---------------------------------------------------------------------------

def units_from_conll(fobj, source_col="tu_id"):
    """Yield (unit_id, rows) groups from a CoNLL TSV file object."""
    curr_sent = []
    curr_unit = "0"
    reader = csv.DictReader(fobj, delimiter="\t")
    for row in reader:
        unit = row[source_col]
        if unit == curr_unit or unit == "_":
            curr_sent.append(row)
        else:
            if curr_sent:
                yield curr_unit, curr_sent
            curr_unit = unit
            curr_sent = [row]
    if curr_sent:
        yield curr_unit, curr_sent


def conll2conllu(filename, output_filename):
    """Convert a pipeline CoNLL TSV to CoNLL-U format."""
    with open(filename, encoding="utf-8") as fin, \
         open(output_filename, "w", encoding="utf-8") as fout:
        for unit_id, unit in units_from_conll(fin):
            metadata = {"sent_id": unit_id, "text": "", "jefferson_text": "", "speaker": ""}
            token_added = False
            tokens = []

            for token in unit:
                conllu_tok = {
                    "ID": token["id"],
                    "FORM": token["form"],
                    "LEMMA": token["lemma"],
                    "UPOS": token["upos"],
                    "XPOS": token["xpos"],
                    "FEATS": token["feats"],
                    "HEAD": "_",
                    "DEPREL": "_",
                    "DEPS": "_",
                    "MISC": "_",
                }
                if token["type"] == "shortpause":
                    conllu_tok["MISC"] = f"Type={token['type']}"
                    metadata["jefferson_text"] += "(.) "
                    tokens.append(conllu_tok)
                    continue
                elif token["type"] == "nonverbalbehavior":
                    conllu_tok["MISC"] = f"Type={token['type']}"
                    metadata["jefferson_text"] += f"{token['span']} "
                    tokens.append(conllu_tok)
                    continue

                token_added = True
                if token["speaker"] != "_":
                    metadata["speaker"] = token["speaker"]

                if token["deprel"] != "_":
                    deprel, head = token["deprel"].rsplit(":", 1)
                    conllu_tok["HEAD"] = int(head)
                    conllu_tok["DEPREL"] = deprel

                if token["span"] != "_":
                    jf = token["jefferson_feats"]
                    if "ProsodicLink" in jf:
                        metadata["text"] = metadata["text"][:-1] + token["form"] + " "
                        metadata["jefferson_text"] = metadata["jefferson_text"][:-1] + "=" + token["span"] + " "
                    elif "SpaceAfter" in jf:
                        metadata["text"] += token["form"]
                        metadata["jefferson_text"] += token["span"]
                    else:
                        metadata["text"] += token["form"] + " "
                        metadata["jefferson_text"] += token["span"] + " "

                feats = {}
                if token["token_id"] != "_":
                    feats["KID"] = token["token_id"]
                for field in ["jefferson_feats", "meta_label", "align"]:
                    if token[field] != "_":
                        for element in token[field].split("|"):
                            element = element.strip()
                            if element:
                                k, v = element.split("=", 1)
                                feats[k] = v
                if token["type"] == "error":
                    feats["Type"] = token["type"]
                if token["prolongations"] not in ("_", ""):
                    feats["Prolonged"] = "Yes"
                if token["pace"] not in ("_", ""):
                    paces, _ = token["pace"].split("=")
                    feats[f"Pace{paces.capitalize()}"] = "Yes"
                if token["overlaps"] != "_":
                    feats["OverlappingGroup"] = ",".join(re.findall(r"\((\d+)\)", token["overlaps"]))

                conllu_tok["MISC"] = "|".join(f"{k}={v}" for k, v in sorted(feats.items())) or "_"
                tokens.append(conllu_tok)

            if token_added:
                print(f"# sent_id = {metadata['sent_id']}", file=fout)
                print(f"# text = {metadata['text'].strip()}", file=fout)
                print(f"# jefferson_text = {metadata['jefferson_text'].strip()}", file=fout)
                print(f"# speaker_id = {metadata['speaker']}", file=fout)
                for tok in tokens:
                    print(
                        f"{tok['ID']}\t{tok['FORM']}\t{tok['LEMMA']}\t{tok['UPOS']}\t"
                        f"{tok['XPOS']}\t{tok['FEATS']}\t{tok['HEAD']}\t{tok['DEPREL']}\t"
                        f"{tok['DEPS']}\t{tok['MISC']}",
                        file=fout,
                    )
                print("", file=fout)
