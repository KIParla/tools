#!/usr/bin/env python3
"""
eaf_layered.py - Read a layered ELAN .eaf (see tsv2eaf_layered.py) back into the
linear CSV that `serialize.process` consumes.

Per speaker the .eaf has

    ref@<speaker>   time-aligned unit, value = the `tu_id` of the vert.tsv
      ├─ tx@<speaker>   enriched transcription of the unit        <- the only text read
      ├─ ft@<speaker>   free translation (optional)               <- read
      └─ tx_ortho@<speaker> -> tok@<speaker> -> nvb@, lemma@, ... <- derived, never read

`tx@` is the single source of the text: the orthographic and token tiers are rebuilt from
it, so editing them in ELAN has no effect (and cannot make the file inconsistent).

The unit id written in `ref@` is returned as a hint, so that `reconcile.py` can pair every
unit with its vert.tsv rows exactly instead of guessing from text and times. A value that
is missing, not an integer or used by two units (an annotation split or copied in ELAN) gives
no hint for those units: they are paired by the usual heuristics.

Usage:
    python eaf_layered.py <file.eaf> <out.csv>
"""

import csv
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from textutil import ENCODING_WRITE, nfc  # noqa: E402

CSV_FIELDS = ["tu_id", "speaker", "start", "end", "duration", "text", "parent_tu_id"]


@dataclass
class Layered:
    units: list[dict] = field(default_factory=list)          # sorted by start time
    translations: list[dict] = field(default_factory=list)   # rows for reconcile_translations
    hints: dict[int, int] = field(default_factory=dict)      # tu_id in the csv -> tu_id in the tsv
    warnings: list[str] = field(default_factory=list)


def is_layered(path: Path) -> bool:
    """True if the .eaf has `ref@<speaker>` tiers."""
    for _, element in ET.iterparse(path, events=("start",)):
        if element.tag == "TIER" and element.get("TIER_ID", "").startswith("ref@"):
            return True
    return False


def _text_by_parent(tier: ET.Element | None) -> dict[str, str]:
    """Value of each REF_ANNOTATION of a tier, by the annotation it refers to."""
    values: dict[str, str] = {}
    if tier is None:
        return values
    for ref in tier.iter("REF_ANNOTATION"):
        value = ref.find("ANNOTATION_VALUE")
        values[ref.get("ANNOTATION_REF")] = nfc((value.text or "") if value is not None else "").strip()
    return values


def read_layered(path: Path) -> Layered:
    root = ET.parse(path).getroot()
    slots = {s.get("TIME_SLOT_ID"): s.get("TIME_VALUE") for s in root.iter("TIME_SLOT")}
    tiers = {t.get("TIER_ID"): t for t in root.findall("TIER")}
    result = Layered()

    units = []  # (start, order, speaker, source_id or None, text, ft, annotation id)
    order = 0
    for tier_id, tier in tiers.items():
        if not tier_id.startswith("ref@"):
            continue
        speaker = nfc(tier_id[len("ref@"):])
        text = _text_by_parent(tiers.get(f"tx@{speaker}"))
        translation = _text_by_parent(tiers.get(f"ft@{speaker}"))
        for annotation in tier.iter("ALIGNABLE_ANNOTATION"):
            aid = annotation.get("ANNOTATION_ID")
            start, end = slots.get(annotation.get("TIME_SLOT_REF1")), slots.get(annotation.get("TIME_SLOT_REF2"))
            value = annotation.find("ANNOTATION_VALUE")
            label = ((value.text or "") if value is not None else "").strip()
            if start is None or end is None:
                result.warnings.append(f"{tier_id}: unit {label or aid} has no time, skipped")
                continue
            if not text.get(aid):
                result.warnings.append(f"{tier_id}: unit {label or aid} has no transcription in tx@{speaker}, skipped")
                continue
            start_s, end_s = int(start) / 1000, int(end) / 1000
            if end_s <= start_s:
                result.warnings.append(f"{tier_id}: unit {label or aid} has a non-positive duration, skipped")
                continue
            source_id = int(label) if label.isdigit() else None
            units.append((start_s, order, speaker, source_id, text[aid], translation.get(aid), end_s))
            order += 1

    units.sort(key=lambda u: (u[0], u[1]))  # by time, ties in file order (as eaf2csv)

    counts: dict[int, int] = {}
    for _, _, _, source_id, *_ in units:
        if source_id is not None:
            counts[source_id] = counts.get(source_id, 0) + 1
    for source_id, n in counts.items():
        if n > 1:
            result.warnings.append(f"ref value {source_id} is used by {n} units: paired by text and time")

    for tu_id, (start, _, speaker, source_id, text, ft, end) in enumerate(units):
        result.units.append({"tu_id": tu_id, "speaker": speaker, "start": f"{start:.3f}",
                             "end": f"{end:.3f}", "duration": f"{end - start:.3f}",
                             "text": text, "parent_tu_id": "_"})
        if source_id is not None and counts[source_id] == 1:
            result.hints[tu_id] = source_id
        if ft:
            result.translations.append({"tu_id": "_", "speaker": f"{speaker}_trad",
                                        "start": f"{start:.3f}", "end": f"{end:.3f}",
                                        "parent_tu_id": str(tu_id), "text": ft})
    return result


def layered2csv(path: Path, output_csv: Path) -> Layered:
    """Write the CSV `serialize.process` reads; return units, translations and hints."""
    layered = read_layered(path)
    with open(output_csv, "w", encoding=ENCODING_WRITE, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(layered.units)
    return layered


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    layered = layered2csv(Path(sys.argv[1]), Path(sys.argv[2]))
    for warning in layered.warnings:
        print("WARNING", warning, file=sys.stderr)
    print(f"{len(layered.units)} units, {len(layered.translations)} translations")


if __name__ == "__main__":
    main()
