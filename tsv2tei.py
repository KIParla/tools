"""
tsv2tei.py — Convert pipeline vert.tsv to TEI P5 XML (SpeechTEI / ISO 24624).

Public API:
    vert2tei(input_path, output_path, media_file=None)  → writes .xml file
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
import xml.etree.ElementTree as ET

TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
T = f"{{{TEI_NS}}}"
X = f"{{{XML_NS}}}"

ET.register_namespace("", TEI_NS)
ET.register_namespace("xml", XML_NS)


# ---------------------------------------------------------------------------
# Field parsers (shared with tsv2chat)
# ---------------------------------------------------------------------------

def _parse_kv(field: str) -> dict[str, str]:
    if not field or field == "_":
        return {}
    result = {}
    for part in field.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v
    return result


def _pace_flags(pace_field: str) -> tuple[bool, bool]:
    if not pace_field or pace_field == "_":
        return False, False
    parts = pace_field.split("|")
    return (
        any(p.startswith("Slow=") for p in parts),
        any(p.startswith("Fast=") for p in parts),
    )


def _clique_ids(overlaps_field: str) -> list[str]:
    if not overlaps_field or overlaps_field == "_":
        return []
    return re.findall(r"\((\d+)\)", overlaps_field)


def _with_prolongations(form: str, prolongations_field: str) -> str:
    if not prolongations_field or prolongations_field == "_":
        return form
    insertions: dict[int, str] = {}
    for part in prolongations_field.split(","):
        if "x" in part:
            pos_str, count_str = part.split("x", 1)
            try:
                insertions[int(pos_str)] = ":" * int(count_str)
            except ValueError:
                pass
    if not insertions:
        return form
    result = []
    for i, ch in enumerate(form):
        result.append(ch)
        if i in insertions:
            result.append(insertions[i])
    return "".join(result)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _units_from_vert(fobj) -> list[tuple[str, list[dict]]]:
    units: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in csv.DictReader(fobj, delimiter="\t"):
        uid = row["tu_id"]
        if uid not in units:
            units[uid] = []
            order.append(uid)
        units[uid].append(row)
    return [(uid, units[uid]) for uid in order]


def _collect_timestamps(units: list[tuple[str, list[dict]]]) -> dict[float, str]:
    """Return a sorted map of unique timestamps → When xml:id."""
    ts_set: set[float] = set()
    for _, tokens in units:
        for tok in tokens:
            align = _parse_kv(tok.get("align", "_"))
            if "Begin" in align:
                ts_set.add(float(align["Begin"]))
            if "End" in align:
                ts_set.add(float(align["End"]))
    return {ts: f"T{i}" for i, ts in enumerate(sorted(ts_set))}


def _collect_overlap_cliques(units: list[tuple[str, list[dict]]]) -> dict[str, list[str]]:
    """Return {clique_id: [unit_id, ...]} in encounter order across the file."""
    cliques: dict[str, list[str]] = {}
    for unit_id, tokens in units:
        seen: set[str] = set()
        for tok in tokens:
            for cid in _clique_ids(tok.get("overlaps", "_")):
                if cid not in cliques:
                    cliques[cid] = []
                if cid not in seen:
                    cliques[cid].append(unit_id)
                    seen.add(cid)
    return cliques


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _sub(parent: ET.Element, tag: str, **attrib) -> ET.Element:
    return ET.SubElement(parent, f"{T}{tag}", **attrib)


def _elem(tag: str, **attrib) -> ET.Element:
    return ET.Element(f"{T}{tag}", **attrib)


def _xid(val: str) -> str:
    """Attribute key for xml:id."""
    return f"{X}id"


# ---------------------------------------------------------------------------
# Token rendering helpers
# ---------------------------------------------------------------------------

# jefferson_feats keys encoded via dedicated attributes or structural elements
_HANDLED_JF_KEYS = frozenset({
    "Truncated", "Volume", "Language",
    "Intonation", "SpaceAfter", "ProsodicLink",
})

_INTONATION_MAP: dict[str, str] = {
    "WeaklyRising":  "weakly_asc",
    "Rising":        "asc",
    "Falling":       "desc",
    "WeaklyFalling": "weakly_desc",
    "Level":         "normal",
}

_VOLUME_SHIFT: dict[str, str] = {"low": "p", "high": "f"}


def _apply_prolongations(w: ET.Element, form: str, prolongations_field: str) -> None:
    """
    Set w's text content with inline <c type="prolongation" n="N"/> milestones.
    Position of each <c> within the text encodes where the prolongation occurs;
    @n encodes the number of colons (duration).
    """
    insertions: dict[int, int] = {}
    for part in prolongations_field.split(","):
        if "x" in part:
            try:
                pos_str, count_str = part.split("x", 1)
                insertions[int(pos_str)] = int(count_str)
            except ValueError:
                pass
    if not insertions:
        w.text = form
        return
    last: ET.Element | None = None
    buf: list[str] = []
    for i, ch in enumerate(form):
        buf.append(ch)
        if i in insertions:
            text = "".join(buf)
            buf = []
            if last is None:
                w.text = text
            else:
                last.tail = text
            last = ET.SubElement(w, f"{T}c", type="prolongation", n=str(insertions[i]))
    remainder = "".join(buf)
    if last is None:
        w.text = remainder
    else:
        last.tail = remainder if remainder else None


def _add_feature_struct(w: ET.Element, tok: dict) -> None:
    """
    Append a <fs> child to w with all TSV features not already encoded as
    attributes or structural elements (lemma, upos, xpos, feats, deprel,
    variation, meta_label, pace, and unhandled jefferson_feats keys).
    """
    features: list[tuple[str, str]] = []

    for col in ("upos", "xpos", "feats", "deprel"):
        val = tok.get(col, "_")
        if val and val not in ("_", ""):
            features.append((col, val))

    for col in ("variation", "meta_label"):
        val = tok.get(col, "_")
        if val and val not in ("_", "", "none"):
            features.append((col, val))

    for k, v in _parse_kv(tok.get("jefferson_feats", "_")).items():
        if k not in _HANDLED_JF_KEYS:
            features.append((f"jf.{k}", v))

    if not features:
        return

    fs = _sub(w, "fs")
    for name, val in features:
        f_el = _sub(fs, "f", name=name)
        _sub(f_el, "string").text = val


# ---------------------------------------------------------------------------
# Token rendering
# ---------------------------------------------------------------------------

def _add_token(parent: ET.Element, tok: dict, unit_id: str, idx: int) -> ET.Element | None:
    """
    Append one token element to parent. Returns the element added, or None.
    Does NOT handle PauseAfter — caller must insert <pause> after if needed.
    """
    tok_type = tok.get("type", "linguistic")
    jf = _parse_kv(tok.get("jefferson_feats", "_"))

    if tok_type == "shortpause":
        return _sub(parent, "pause", dur="short")

    if tok_type == "nonverbalbehavior":
        nvb = tok.get("form", tok.get("span", "nvb")).strip("()")
        vocal = _sub(parent, "vocal")
        desc = _sub(vocal, "desc")
        desc.text = nvb
        return vocal

    if tok_type == "anonymized":
        tok_id = tok.get("token_id", f"{unit_id}_{idx}")
        w = _sub(parent, "w", **{f"{X}id": f"w{tok_id}", "type": "anonymized"})
        w.text = tok.get("form", "_")
        return w

    form = tok.get("form", "_")
    tok_id = tok.get("token_id", f"{unit_id}_{idx}")
    attribs: dict[str, str] = {f"{X}id": f"w{tok_id}"}

    lemma = tok.get("lemma", "_")
    if lemma and lemma not in ("_", ""):
        attribs["lemma"] = lemma

    if jf.get("Truncated") == "Yes":
        attribs["type"] = "interrupted"
    elif tok_type == "error":
        attribs["type"] = "error"

    lang = jf.get("Language")
    if lang and lang != "ita":
        attribs[f"{X}lang"] = lang

    if tok.get("guesses", "_") != "_":
        attribs["cert"] = "low"

    if jf.get("SpaceAfter") == "No":
        attribs["join"] = "right"

    rend = []
    if jf.get("Reduced") == "Yes":
        rend.append("reduced")
    if jf.get("ProsodicLink") == "Yes":
        rend.append("prosodicLink")
    if rend:
        attribs["rend"] = " ".join(rend)

    w = _sub(parent, "w", **attribs)

    prol = tok.get("prolongations", "_")
    if prol and prol not in ("_", ""):
        _apply_prolongations(w, form, prol)
    else:
        w.text = form

    intonation = jf.get("Intonation")
    if intonation:
        _sub(w, "shift", feature="pitch", new=_INTONATION_MAP.get(intonation, intonation))

    _add_feature_struct(w, tok)
    return w


# ---------------------------------------------------------------------------
# Utterance block builder
# ---------------------------------------------------------------------------

def _build_annotation_block(
    unit_id: str,
    tokens: list[dict],
    ts_map: dict[float, str],
    ab_idx: int,
) -> ET.Element | None:
    if not tokens:
        return None

    speaker = tokens[0].get("speaker", "UNK")
    begin_ts = end_ts = None

    for tok in tokens:
        align = _parse_kv(tok.get("align", "_"))
        if "Begin" in align:
            begin_ts = float(align["Begin"])
        if "End" in align:
            end_ts = float(align["End"])

    ab_attribs: dict[str, str] = {
        "who": f"#{speaker}",
        f"{X}id": f"ab{ab_idx}",
    }
    if begin_ts is not None and begin_ts in ts_map:
        ab_attribs["start"] = f"#{ts_map[begin_ts]}"
    if end_ts is not None and end_ts in ts_map:
        ab_attribs["end"] = f"#{ts_map[end_ts]}"

    ab = _elem("annotationBlock", **ab_attribs)
    u = _sub(ab, "u", **{f"{X}id": f"u{ab_idx}"})

    # Build per-token overlap clique map
    tok_clique: dict[int, str] = {}
    for i, tok in enumerate(tokens):
        cids = _clique_ids(tok.get("overlaps", "_"))
        if cids:
            tok_clique[i] = cids[0]

    word_counter = 0
    i = 0
    current_loud: str | None = None
    current_tempo: str | None = None

    while i < len(tokens):
        tok = tokens[i]
        tok_type = tok.get("type", "linguistic")
        jf = _parse_kv(tok.get("jefferson_feats", "_"))

        # Shortpause / NVB / anonymized — no shift tracking needed
        if tok_type in ("shortpause", "nonverbalbehavior", "anonymized"):
            _add_token(u, tok, unit_id, word_counter)
            word_counter += 1
            i += 1
            continue

        # MWT: surface-form token followed by syntactic component tokens.
        # Collect all consecutive syntactic children and nest them inside the mwt <w>.
        if tok_type == "mwt":
            tok_id = tok.get("token_id", f"{unit_id}_{word_counter}")
            mwt_w = _sub(u, "w", **{f"{X}id": f"w{tok_id}", "type": "mwt"})
            mwt_w.text = tok.get("form", "_")
            # TODO: add prolongation, intonation, SpaceAfter, ProsodicLink to mwt_w
            word_counter += 1
            i += 1
            while i < len(tokens) and tokens[i].get("type") == "syntactic":
                child = tokens[i]
                child_id = child.get("token_id", f"{unit_id}_{word_counter}")
                child_w = _sub(mwt_w, "w", **{f"{X}id": f"w{child_id}", "type": "syntactic"})
                child_w.text = child.get("form", "_")
                # TODO: add lemma, upos, xpos, feats, deprel to child_w via <fs>
                word_counter += 1
                i += 1
            continue

        # syntactic tokens are consumed above; a stray one here is a data anomaly
        if tok_type == "syntactic":
            i += 1
            continue

        # Overlap: place milestone anchors; shifts not tracked inside overlap spans
        cid = tok_clique.get(i)
        if cid is not None:
            j = i + 1
            while j < len(tokens) and tok_clique.get(j) == cid:
                j += 1
            _sub(u, "anchor", **{f"{X}id": f"OVS_{cid}_{unit_id}"})
            for k in range(i, j):
                t2 = tokens[k]
                _add_token(u, t2, unit_id, word_counter)
                word_counter += 1
            _sub(u, "anchor", **{f"{X}id": f"OVE_{cid}_{unit_id}"})
            i = j
            continue

        # Emit <shift> for loud and tempo when state changes
        new_loud = _VOLUME_SHIFT.get(jf.get("Volume", ""))
        if new_loud != current_loud:
            _sub(u, "shift", feature="loud", new=new_loud if new_loud else "normal")
            current_loud = new_loud

        is_slow, is_fast = _pace_flags(tok.get("pace", "_"))
        new_tempo = "l" if is_slow else ("aa" if is_fast else None)
        if new_tempo != current_tempo:
            _sub(u, "shift", feature="tempo", new=new_tempo if new_tempo else "normal")
            current_tempo = new_tempo

        _add_token(u, tok, unit_id, word_counter)
        word_counter += 1
        i += 1

    # Close any open prosodic shifts at utterance boundary
    if current_loud is not None:
        _sub(u, "shift", feature="loud", new="normal")
    if current_tempo is not None:
        _sub(u, "shift", feature="tempo", new="normal")

    if len(u) == 0:
        return None

    return ab


# ---------------------------------------------------------------------------
# Metadata loading
# ---------------------------------------------------------------------------

_LANG_ISO = {
    "italian": "ita",
    "dialect": "it-x-dialect",
    "english": "eng",
    "french": "fra",
    "german": "deu",
    "spanish": "spa",
}

_SEX_MAP = {"M": "2", "F": "1"}  # TEI @value convention


def _hms_to_iso(hms: str) -> str:
    """Convert HH:MM:SS to ISO 8601 duration PT#H#M#S."""
    parts = hms.strip().split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if h:
            return f"PT{h}H{m}M{s}S"
        return f"PT{m}M{s}S"
    return hms


def load_metadata(metadata_dir: Path, corpus_id: str) -> tuple[dict, dict[str, dict]]:
    """
    Load conversation and participant metadata from a module metadata/ directory.

    Returns:
        conv:   row dict for corpus_id from conversations.tsv (empty dict if not found)
        parts:  {speaker_code: row_dict} from participants.tsv
    """
    conv: dict = {}
    conv_path = metadata_dir / "conversations.tsv"
    if conv_path.is_file():
        with conv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row.get("code", "") == corpus_id:
                    conv = dict(row)
                    break

    parts: dict[str, dict] = {}
    part_path = metadata_dir / "participants.tsv"
    if part_path.is_file():
        # Determine speaker codes from conversation if available
        speaker_set: set[str] = set()
        if conv.get("participants"):
            speaker_set = set(conv["participants"].split(";"))
        with part_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                code = row.get("code", "")
                if not speaker_set or code in speaker_set:
                    parts[code] = dict(row)

    return conv, parts


# ---------------------------------------------------------------------------
# TEI document builder
# ---------------------------------------------------------------------------

def _build_tei_header(
    corpus_id: str,
    speakers: list[str],
    conv: dict,
    parts: dict[str, dict],
    media_file: str,
) -> ET.Element:
    header = _elem("teiHeader")

    # fileDesc
    fileDesc = _sub(header, "fileDesc")
    titleStmt = _sub(fileDesc, "titleStmt")
    _sub(titleStmt, "title").text = corpus_id

    pubStmt = _sub(fileDesc, "publicationStmt")
    _sub(pubStmt, "p").text = "KIParla corpus"

    sourceDesc = _sub(fileDesc, "sourceDesc")
    recStmt = _sub(sourceDesc, "recordingStmt")
    rec_attribs: dict[str, str] = {"type": "audio"}
    if conv.get("duration"):
        rec_attribs["dur"] = _hms_to_iso(conv["duration"])
    recording = _sub(recStmt, "recording", **rec_attribs)
    _sub(recording, "media", mimeType="audio/wav", url=f"{media_file}.wav")

    if conv:
        bibl = _sub(sourceDesc, "bibl")
        _sub(bibl, "title").text = corpus_id
        if conv.get("year"):
            _sub(bibl, "date", when=conv["year"])
        for field in ("type", "collection-point", "topic", "participants-relationship", "moderator"):
            if conv.get(field):
                _sub(bibl, "note", type=field).text = conv[field]

    # profileDesc
    profileDesc = _sub(header, "profileDesc")

    # langUsage
    raw_langs = conv.get("languages", "")
    if raw_langs:
        langUsage = _sub(profileDesc, "langUsage")
        for lang in raw_langs.split(";"):
            lang = lang.strip()
            ident = _LANG_ISO.get(lang, lang)
            _sub(langUsage, "language", ident=ident).text = lang.capitalize()

    # particDesc
    particDesc = _sub(profileDesc, "particDesc")
    listPerson = _sub(particDesc, "listPerson")

    moderator_codes: set[str] = set()
    if conv.get("moderator", "").lower() == "yes" and conv.get("participants"):
        # Moderator codes have 'R' as their 3rd character (e.g. TOR001, BOR001)
        for code in conv["participants"].split(";"):
            if len(code) >= 3 and code[2] == "R":
                moderator_codes.add(code)

    for sp in speakers:
        p_data = parts.get(sp, {})
        role = "moderator" if sp in moderator_codes else "participant"
        person_attribs: dict[str, str] = {f"{X}id": sp, "role": role}
        person = _sub(listPerson, "person", **person_attribs)
        _sub(person, "persName").text = sp

        if p_data.get("gender"):
            _sub(person, "sex", value=_SEX_MAP.get(p_data["gender"], p_data["gender"]))
        if p_data.get("age-range"):
            _sub(person, "age").text = p_data["age-range"]
        if p_data.get("birth-region"):
            birth = _sub(person, "birth")
            _sub(birth, "region").text = p_data["birth-region"]
        if p_data.get("occupation"):
            _sub(person, "occupation").text = p_data["occupation"]
        if p_data.get("study-level"):
            _sub(person, "education").text = p_data["study-level"]

    # settingDesc
    if conv.get("collection-point") or conv.get("year"):
        settingDesc = _sub(profileDesc, "settingDesc")
        setting = _sub(settingDesc, "setting")
        if conv.get("collection-point"):
            _sub(setting, "name", type="collection-point").text = conv["collection-point"]
        if conv.get("year"):
            _sub(setting, "date", when=conv["year"])

    return header


# ---------------------------------------------------------------------------
# Span layers (standoff annotation for orthogonal unit types)
# ---------------------------------------------------------------------------

# Maps TSV column name → TEI spanGrp @type value.
# Add entries here as new annotation layers are introduced in the pipeline.
SPAN_LAYERS: dict[str, str] = {
    # "sent_id": "sentences",
    # "iu_id":   "intonation-units",
}


def _build_span_groups(
    units: list[tuple[str, list[dict]]],
    tok_ids: dict[tuple[str, int], str],
) -> list[ET.Element]:
    """
    Build <spanGrp> elements for every SPAN_LAYERS column present in the data.

    tok_ids maps (unit_id, within-unit-index) → xml:id string of the <w> element.
    Returns one <spanGrp> per active layer (may be empty list).
    """
    # Detect which layers are actually present in this file
    active: dict[str, str] = {}
    for _, tokens in units:
        for tok in tokens:
            for col, span_type in SPAN_LAYERS.items():
                if tok.get(col, "_") not in ("_", "", None):
                    active[col] = span_type
        if len(active) == len(SPAN_LAYERS):
            break

    if not active:
        return []

    # Collect first/last token xml:id per span value, preserving order
    layer_spans: dict[str, dict[str, list[str]]] = {col: {} for col in active}
    for unit_id, tokens in units:
        for idx, tok in enumerate(tokens):
            wid = tok_ids.get((unit_id, idx))
            if wid is None:
                continue
            for col in active:
                val = tok.get(col, "_")
                if val in ("_", "", None):
                    continue
                if val not in layer_spans[col]:
                    layer_spans[col][val] = [wid, wid]
                else:
                    layer_spans[col][val][1] = wid  # keep updating last

    groups: list[ET.Element] = []
    for col, span_type in active.items():
        grp = _elem("spanGrp", type=span_type)
        for span_id, (first_wid, last_wid) in layer_spans[col].items():
            _sub(grp, "span", **{
                f"{X}id": f"{span_type[:2]}{span_id}",
                "from": f"#{first_wid}",
                "to": f"#{last_wid}",
            })
        groups.append(grp)

    return groups


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def vert2tei(
    input_path: Path,
    output_path: Path,
    media_file: str | None = None,
    metadata_dir: Path | None = None,
) -> None:
    """Write a TEI P5 XML file from a pipeline vert.tsv."""
    with input_path.open(encoding="utf-8") as fin:
        units = _units_from_vert(fin)

    speakers: list[str] = []
    for _, tokens in units:
        for tok in tokens:
            sp = tok.get("speaker", "")
            if sp and sp not in speakers:
                speakers.append(sp)

    ts_map = _collect_timestamps(units)
    overlap_cliques = _collect_overlap_cliques(units)
    corpus_id = re.sub(r"\.vert$", "", input_path.stem)
    media = media_file or corpus_id

    conv: dict = {}
    parts: dict[str, dict] = {}
    if metadata_dir is not None:
        conv, parts = load_metadata(Path(metadata_dir), corpus_id)

    tei = _elem("TEI")
    tei.append(_build_tei_header(corpus_id, speakers, conv, parts, media))

    text_el = _sub(tei, "text")
    body = _sub(text_el, "body")
    div = _sub(body, "div", type="interaction", **{f"{X}id": corpus_id})

    timeline = _sub(div, "timeline", unit="s", **{f"{X}id": "TL"})
    for ts, wid in sorted(ts_map.items(), key=lambda kv: kv[0]):
        _sub(timeline, "when", **{f"{X}id": wid, "absolute": f"PT{ts:.3f}S"})

    # tok_ids maps (unit_id, within-unit-index) → xml:id for standoff span layers
    tok_ids: dict[tuple[str, int], str] = {}
    for ab_idx, (unit_id, tokens) in enumerate(units):
        for idx, tok in enumerate(tokens):
            tok_type = tok.get("type", "linguistic")
            if tok_type not in ("shortpause", "nonverbalbehavior", "anonymized"):
                tok_id = tok.get("token_id", f"{unit_id}_{idx}")
                tok_ids[(unit_id, idx)] = f"w{tok_id}"
        ab = _build_annotation_block(unit_id, tokens, ts_map, ab_idx)
        if ab is not None:
            div.append(ab)

    for grp in _build_span_groups(units, tok_ids):
        div.append(grp)

    if overlap_cliques:
        link_grp = _sub(div, "linkGrp", type="overlaps")
        for cid, uids in overlap_cliques.items():
            _sub(link_grp, "link", targets=" ".join(f"#OVS_{cid}_{uid}" for uid in uids))
            _sub(link_grp, "link", targets=" ".join(f"#OVE_{cid}_{uid}" for uid in uids))

    ET.indent(tei, space="  ")
    tree = ET.ElementTree(tei)
    with output_path.open("wb") as fout:
        tree.write(fout, encoding="utf-8", xml_declaration=True)
