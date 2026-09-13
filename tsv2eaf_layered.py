"""
tsv2eaf_layered.py — convert a ``.vert.tsv`` into a layered ELAN ``.eaf``.

Unlike ``serialize.vert2eaf`` (which rebuilds a single transcription tier per
speaker), this builds a tier hierarchy per speaker, with tier names following
the Toolbox/ELAN interlinear ``<code>@<speaker>`` convention:

    ref@<speaker>              top-level, time-alignable   value = unit id
      ├─ ft@<speaker>          Symbolic_Association        free translation of the unit
      ├─ tx@<speaker>          Symbolic_Association        Jefferson text of the unit
      └─ tx_ortho@<speaker>    Symbolic_Association        orthographic text of the unit
           └─ tok@<speaker>    Symbolic_Subdivision       one annotation per token, value = form
                ├─ lemma@<speaker>  Symbolic_Association   lemma        (only with --linguistic)
                ├─ pos@<speaker>    Symbolic_Association   UD POS       (only with --linguistic)
                ├─ xpos@<speaker>   Symbolic_Association   language POS (only with --linguistic)
                ├─ feats@<speaker> Symbolic_Association    morph. feats (only with --linguistic)
                ├─ dep@<speaker>    Symbolic_Association   dependency relation (only with --linguistic)
                ├─ sent_id@<speaker> Symbolic_Association  syntactic-sentence id(s) of the token
                └─ nvb@<speaker>    Symbolic_Association   non-verbal / pause token type (+ meta_label)

``sent_id@`` carries the many-to-many syntax<->TU relation: a syntactic sentence
that spans several TUs is the same id on tokens under different ``ref@``
parents; a TU holding several sentences has tokens with different ids.

The unit tier would be a ``Time_Subdivision`` if it had a parent TU tier; as
the top tier it is a plain time-alignable tier. Equivalent today, since every
TU holds exactly one unit.

The EAF XML is written directly (pympi cannot express this hierarchy cleanly).
"""

from __future__ import annotations

import csv
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BEGIN_RE = re.compile(r"\bBegin\s*=\s*([0-9]+(?:\.[0-9]+)?)")
_END_RE = re.compile(r"\bEnd\s*=\s*([0-9]+(?:\.[0-9]+)?)")

_STEREOTYPE_DESC = {
    "Time_Subdivision": "Time subdivision of parent annotation's time interval, no time gaps allowed within this interval",
    "Symbolic_Subdivision": "Symbolic subdivision of a parent annotation. Annotations refering to the same parent are ordered",
    "Symbolic_Association": "1-1 association with a parent annotation",
    "Included_In": "Time alignable annotations within the parent annotation's time interval, gaps are allowed",
}

# Tier-name codes (Toolbox/ELAN interlinear convention): tier id is
# ``<code>@<speaker>``. Edit here to rename tiers.
_TIER_CODES = {
    "units": "ref",
    "translation": "ft",
    "transcription": "tx",
    "orthographic": "tx_ortho",
    "tokens": "tok",
    "nvb": "nvb",
    "sentence": "sent_id",
}

# vert.tsv column holding the syntactic-sentence id(s) of each token. Syntactic
# sentences relate many-to-many to transcription units (a sentence may span
# several TUs; a TU may contain several sentences), which an EAF tier hierarchy
# — a strict tree — cannot express structurally. Instead each token carries its
# sentence id(s) (space-separated) on a ``sent_id@<speaker>`` association tier
# under ``tok@``; the many-to-many is recovered from shared ids.
_SENT_COL = "sent_id"

# token-level linguistic tiers gated behind --linguistic: (key, vert column, code)
_LINGUISTIC_TIERS = [
    ("lemma", "lemma", "lemma"),
    ("upos", "upos", "pos"),
    ("xpos", "xpos", "xpos"),
    ("feats", "feats", "feats"),
    ("deprel", "deprel", "dep"),
]

_NVB_TYPES = {"nonverbalbehavior", "shortpause", "unknown"}


def _tid(key_or_code, speaker):
    """Tier id ``<code>@<speaker>`` for a hierarchy key (or a raw code)."""
    return f"{_TIER_CODES.get(key_or_code, key_or_code)}@{speaker}"


def _units(fobj):
    """Yield (speaker, tu_id, unit_id, token_rows) for each unit in the file.

    Groups on the ``unit`` column when present, otherwise falls back to
    ``tu_id`` (older schema without intonation-unit segmentation).
    """
    reader = csv.DictReader(fobj, delimiter="\t")
    has_unit = reader.fieldnames is not None and "unit" in reader.fieldnames
    curr_key = None
    curr_rows = []
    for row in reader:
        unit_id = row["unit"] if has_unit else row["tu_id"]
        key = (row["speaker"], row["tu_id"], unit_id)
        if key != curr_key:
            if curr_rows:
                yield (*curr_key, curr_rows)
            curr_key = key
            curr_rows = [row]
        else:
            curr_rows.append(row)
    if curr_rows:
        yield (*curr_key, curr_rows)


def _join(tokens, field, prosodic_link="="):
    """Join a unit's tokens on ``field``, honouring SpaceAfter=No / ProsodicLink=Yes.

    ``prosodic_link`` is the separator inserted on ProsodicLink=Yes: ``"="`` for
    the Jefferson transcription, ``" "`` for the orthographic one.
    """
    parts = []
    for i, tok in enumerate(tokens):
        parts.append(tok.get(field, "") or "")
        if i == len(tokens) - 1:
            continue
        feats = tok.get("jefferson_feats", "") or ""
        if "SpaceAfter=No" in feats:
            continue
        if "ProsodicLink=Yes" in feats:
            parts.append(prosodic_link)
        else:
            parts.append(" ")
    return re.sub(r" +", " ", "".join(parts).strip())


def _load_translations(translations_path):
    """Return {parent_tu_id (int) -> translation text} from a translations.json."""
    out = {}
    with Path(translations_path).open(encoding="utf-8") as jf:
        for row in json.load(jf):
            parent = row.get("parent_tu_id")
            if parent in (None, "", "_"):
                continue
            try:
                out[int(parent)] = row.get("text", "") or ""
            except (TypeError, ValueError):
                continue
    return out


class _EafBuilder:
    def __init__(self, media_file=""):
        self._aid = 0
        self._tsid = 0
        self.timeslots = {}          # ms -> ts id
        self.ling_types = {}         # ling type id -> stereotype (or None)
        self.tiers = []              # (attrib dict, [<ANNOTATION> elements])

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.doc = ET.Element("ANNOTATION_DOCUMENT", {
            "AUTHOR": "kiparla-tools",
            "DATE": now,
            "FORMAT": "3.0",
            "VERSION": "3.0",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://www.mpi.nl/tools/elan/EAFv3.0.xsd",
        })
        header = ET.SubElement(self.doc, "HEADER", {
            "MEDIA_FILE": "", "TIME_UNITS": "milliseconds",
        })
        if media_file:
            ET.SubElement(header, "MEDIA_DESCRIPTOR", {
                "MEDIA_URL": media_file,
                "MIME_TYPE": "audio/x-wav",
                "RELATIVE_MEDIA_URL": media_file,
            })

    def _next_aid(self):
        self._aid += 1
        return f"a{self._aid}"

    def _ts(self, ms):
        if ms not in self.timeslots:
            self._tsid += 1
            self.timeslots[ms] = f"ts{self._tsid}"
        return self.timeslots[ms]

    def add_tier(self, tier_id, ling_ref, stereotype=None, parent=None):
        self.ling_types.setdefault(ling_ref, stereotype)
        attrib = {"LINGUISTIC_TYPE_REF": ling_ref, "TIER_ID": tier_id}
        if parent:
            attrib["PARENT_REF"] = parent
        entry = (attrib, [])
        self.tiers.append(entry)
        return entry

    def add_aligned(self, tier, start_ms, end_ms, value):
        aid = self._next_aid()
        ann = ET.Element("ANNOTATION")
        al = ET.SubElement(ann, "ALIGNABLE_ANNOTATION", {
            "ANNOTATION_ID": aid,
            "TIME_SLOT_REF1": self._ts(start_ms),
            "TIME_SLOT_REF2": self._ts(end_ms),
        })
        ET.SubElement(al, "ANNOTATION_VALUE").text = value
        tier[1].append(ann)
        return aid

    def add_ref(self, tier, ref_aid, value, prev_aid=None):
        aid = self._next_aid()
        ann = ET.Element("ANNOTATION")
        attrib = {"ANNOTATION_ID": aid, "ANNOTATION_REF": ref_aid}
        if prev_aid:
            attrib["PREVIOUS_ANNOTATION"] = prev_aid
        ref = ET.SubElement(ann, "REF_ANNOTATION", attrib)
        ET.SubElement(ref, "ANNOTATION_VALUE").text = value
        tier[1].append(ann)
        return aid

    def to_tree(self, prune=True):
        time_order = ET.SubElement(self.doc, "TIME_ORDER")
        for ms, tsid in sorted(self.timeslots.items(), key=lambda kv: (kv[0], int(kv[1][2:]))):
            ET.SubElement(time_order, "TIME_SLOT", {
                "TIME_SLOT_ID": tsid, "TIME_VALUE": str(ms),
            })
        # Drop empty leaf tiers (e.g. ft@ when the file has no translations,
        # nvb@ when there are no non-verbal tokens). Iterate so a tier only
        # survives if it has annotations or a surviving child. Disabled
        # (prune=False) for the .etf template, which lists the full tier set.
        kept = list(self.tiers)
        changed = prune
        while changed:
            changed = False
            parents = {a.get("PARENT_REF") for a, _ in kept}
            for entry in list(kept):
                attrib, annotations = entry
                if not annotations and attrib["TIER_ID"] not in parents:
                    kept.remove(entry)
                    changed = True

        for attrib, annotations in kept:
            tier_el = ET.SubElement(self.doc, "TIER", attrib)
            tier_el.extend(annotations)

        used = {attrib["LINGUISTIC_TYPE_REF"] for attrib, _ in kept}
        for lt, stereo in sorted(self.ling_types.items()):
            if lt not in used:
                continue
            a = {
                "GRAPHIC_REFERENCES": "false",
                "LINGUISTIC_TYPE_ID": lt,
                "TIME_ALIGNABLE": "true" if stereo is None else "false",
            }
            if stereo:
                a["CONSTRAINTS"] = stereo
            ET.SubElement(self.doc, "LINGUISTIC_TYPE", a)

        for stereo in sorted({s for lt, s in self.ling_types.items() if s and lt in used}):
            ET.SubElement(self.doc, "CONSTRAINT", {
                "DESCRIPTION": _STEREOTYPE_DESC[stereo], "STEREOTYPE": stereo,
            })

        tree = ET.ElementTree(self.doc)
        ET.indent(tree, space="    ")
        return tree


_SA = "Symbolic_Association"
_SSUB = "Symbolic_Subdivision"


def _add_speaker_tiers(builder, speaker, active_ling, has_sent):
    """Create one speaker's tier hierarchy on ``builder``; return the entry dict.

    ``active_ling`` is the subset of ``_LINGUISTIC_TIERS`` to include and
    ``has_sent`` whether to add the ``sent_id@`` tier. The ``.etf`` template
    passes the full set for both.
    """
    ref, tok = _tid("units", speaker), _tid("tokens", speaker)
    entry = {
        "units": builder.add_tier(ref, "ref-lt"),
        "translation": builder.add_tier(_tid("translation", speaker), "ft-sa", _SA, ref),
        "transcription": builder.add_tier(_tid("transcription", speaker), "tx-sa", _SA, ref),
        "orthographic": builder.add_tier(_tid("orthographic", speaker), "tx_ortho-sa", _SA, ref),
        "tokens": builder.add_tier(tok, "tok-ssub", _SSUB, _tid("orthographic", speaker)),
        "nvb": builder.add_tier(_tid("nvb", speaker), "nvb-sa", _SA, tok),
    }
    if has_sent:
        entry["sentence"] = builder.add_tier(_tid("sentence", speaker), "sent_id-sa", _SA, tok)
    for key, _col, code in active_ling:
        entry[key] = builder.add_tier(_tid(code, speaker), f"{code}-sa", _SA, tok)
    return entry


def write_template(vert_paths, output_path):
    """Write an ELAN ``.etf`` template with the full tier set for every speaker
    seen across ``vert_paths``.

    Per-file EAFs keep only the tiers they have data for (auto-prune); opening
    the folder against this template — ELAN *Edit ▸ Add tiers from template* —
    brings every file to the same complete structure. The template also serves
    as the schema reference (all linguistic types + constraints).
    """
    speakers = {}
    for vp in vert_paths:
        with Path(vp).open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                speakers.setdefault(row["speaker"], None)

    builder = _EafBuilder()
    for speaker in speakers:
        _add_speaker_tiers(builder, speaker, _LINGUISTIC_TIERS, has_sent=True)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    builder.to_tree(prune=False).write(output_path, encoding="UTF-8", xml_declaration=True)
    return output_path


def vert2eaf_layered(vert_path, output_filename, media_file="",
                     translations_path=None, linguistic=False):
    """Convert one ``.vert.tsv`` to a layered ELAN ``.eaf``.

    Args:
        translations_path: optional ``<name>.translations.json``; each row's
            ``parent_tu_id`` is matched to a unit's ``tu_id`` and attached to
            the ``ft@<speaker>`` tier.
        linguistic: also emit per-token ``lemma@`` / ``pos@`` / ``xpos@`` /
            ``feats@`` / ``dep@`` tiers. A tier whose column is ``_`` for
            every token in the file is skipped.
    """
    vert_path = Path(vert_path)
    translations = _load_translations(translations_path) if translations_path else {}

    # Pre-scan: which optional columns carry any real value?
    active_ling = set()
    has_sent = False
    scan_cols = ([t for t in _LINGUISTIC_TIERS] if linguistic else [])
    with vert_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            for key, col, _code in scan_cols:
                if (row.get(col, "_") or "_") != "_":
                    active_ling.add(key)
            if (row.get(_SENT_COL, "_") or "_") != "_":
                has_sent = True
    active_ling = [t for t in _LINGUISTIC_TIERS if t[0] in active_ling]

    builder = _EafBuilder(media_file=media_file)
    tiers = {}  # speaker -> dict of tier entries

    def speaker_tiers(speaker):
        if speaker not in tiers:
            tiers[speaker] = _add_speaker_tiers(builder, speaker, active_ling, has_sent)
        return tiers[speaker]

    with vert_path.open(encoding="utf-8", newline="") as f:
        for speaker, tu_id, unit_id, tok_rows in _units(f):
            m_start = _BEGIN_RE.search(tok_rows[0].get("align", "") or "")
            m_end = _END_RE.search(tok_rows[-1].get("align", "") or "")
            if m_start is None or m_end is None:
                logger.warning("%s: unit %s/%s (%s) missing Begin=/End=, skipping",
                               vert_path.stem, tu_id, unit_id, speaker)
                continue
            start_ms = round(float(m_start.group(1)) * 1000)
            end_ms = round(float(m_end.group(1)) * 1000)
            if end_ms <= start_ms:
                logger.warning("%s: unit %s/%s (%s) non-positive duration, skipping",
                               vert_path.stem, tu_id, unit_id, speaker)
                continue

            st = speaker_tiers(speaker)
            unit_aid = builder.add_aligned(st["units"], start_ms, end_ms, unit_id)

            translation = translations.get(_safe_int(tu_id))
            if translation:
                builder.add_ref(st["translation"], unit_aid, translation)

            builder.add_ref(st["transcription"], unit_aid, _join(tok_rows, "span"))
            ortho_aid = builder.add_ref(
                st["orthographic"], unit_aid, _join(tok_rows, "form", prosodic_link=" "))

            prev = None
            for tok in tok_rows:
                value = tok.get("form", "") or ""
                if value == "_":
                    value = tok.get("span", "") or ""
                tok_aid = builder.add_ref(st["tokens"], ortho_aid, value, prev_aid=prev)
                prev = tok_aid

                tok_type = tok.get("type", "") or ""
                if tok_type in _NVB_TYPES:
                    label = tok.get("meta_label", "_") or "_"
                    nvb_value = tok_type if label == "_" else f"{tok_type}: {label}"
                    builder.add_ref(st["nvb"], tok_aid, nvb_value)

                if has_sent:
                    sent_cell = tok.get(_SENT_COL, "_") or "_"
                    if sent_cell != "_":
                        builder.add_ref(st["sentence"], tok_aid, sent_cell)

                for key, col, _code in active_ling:
                    cell = tok.get(col, "_") or "_"
                    if cell != "_":
                        builder.add_ref(st[key], tok_aid, cell)

    output_filename = Path(output_filename)
    output_filename.parent.mkdir(parents=True, exist_ok=True)
    builder.to_tree().write(output_filename, encoding="UTF-8", xml_declaration=True)
    return output_filename


def _safe_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Convert .vert.tsv to a layered ELAN .eaf.")
    parser.add_argument("-i", "--input", required=True,
                        help="Input .vert.tsv file or a folder containing such files.")
    parser.add_argument("-o", "--output", default="eaf_layered",
                        help="Output folder for .eaf files (default: eaf_layered/).")
    parser.add_argument("--translations-dir",
                        help="folder with <name>.translations.json files to attach as __translation tiers.")
    parser.add_argument("--linguistic", action="store_true",
                        help="also emit per-token lemma/upos/xpos/feats/deprel tiers.")
    parser.add_argument("--template",
                        help="also write an ELAN .etf template at this path with the "
                             "full tier set for every speaker seen across the inputs.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    p = Path(args.input)
    if p.is_dir():
        files = sorted(p.glob("*.vert.tsv"))
    elif p.is_file():
        files = [p]
    else:
        raise SystemExit(f"Input path does not exist: {p}")
    if not files:
        raise SystemExit(f"No *.vert.tsv files found in {p}")

    out_dir = Path(args.output)
    tdir = Path(args.translations_dir) if args.translations_dir else None
    for f in files:
        stem = re.sub(r"\.vert$", "", f.stem)
        tpath = None
        if tdir is not None:
            cand = tdir / f"{stem}.translations.json"
            tpath = cand if cand.is_file() else None
        vert2eaf_layered(f, out_dir / f"{stem}.eaf", translations_path=tpath,
                         linguistic=args.linguistic)
        logger.info("wrote %s", out_dir / f"{stem}.eaf")

    if args.template:
        logger.info("wrote template %s", write_template(files, args.template))


if __name__ == "__main__":
    main()
