"""Layered .eaf: writer (tsv2eaf_layered), reader (eaf_layered) and the round trip
tsv -> eaf -> csv -> process -> reconcile, which must give the same tsv back."""

import json
import xml.etree.ElementTree as ET

import pytest

import eaf_layered
import reconcile
import tsv2eaf_layered
from data import Transcript, TranscriptionUnit
from serialize import conversation_to_conll, process

CFG = {"variation_markers": {"hash_token": True, "dollar": True, "hash_doubtful": True}}

UNITS = [
    ("A", 0.0, 1.5, "ciao mondo"),
    ("B", 1.6, 2.8, "sì: certo"),
    ("A", 3.0, 4.0, "va bene=eh"),
]


def make_vert(tmp_path, units=UNITS, name="X001"):
    transcript = Transcript(name)
    for i, (speaker, start, end, text) in enumerate(units):
        tu = TranscriptionUnit(i, speaker, start, end, end - start, text, cfg=CFG)
        tu.tokenize()
        transcript.add(tu)
    transcript.sort()
    path = tmp_path / f"{name}.vert.tsv"
    conversation_to_conll(transcript, path)
    return path


def tiers(eaf):
    root = ET.parse(eaf).getroot()
    return {t.get("TIER_ID"): t for t in root.findall("TIER")}, root


def test_by_default_only_the_edited_tiers_are_written(tmp_path):
    vert = make_vert(tmp_path)
    out = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf", media_file="X001.mp3")
    t, _ = tiers(out)
    assert sorted(t) == ["ref@A", "ref@B", "tx@A", "tx@B"]       # no ft@ (no translations), no word tiers
    for speaker in ("A", "B"):
        assert t[f"tx@{speaker}"].get("PARENT_REF") == f"ref@{speaker}"


def test_words_adds_the_derived_word_tiers(tmp_path):
    vert = make_vert(tmp_path)
    out = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf", media_file="X001.mp3", words=True)
    t, _ = tiers(out)
    for speaker in ("A", "B"):
        for code in ("ref", "tx", "tx_ortho", "tok"):
            assert f"{code}@{speaker}" in t
        assert t[f"tok@{speaker}"].get("PARENT_REF") == f"tx_ortho@{speaker}"
    assert check_elan_constraints(out)


def test_the_default_file_is_much_smaller_than_the_one_with_words(tmp_path):
    vert = make_vert(tmp_path)
    lean = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "lean.eaf")
    full = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "full.eaf", words=True)
    assert lean.stat().st_size < full.stat().st_size / 2


def test_both_layouts_are_read_back_the_same(tmp_path):
    vert = make_vert(tmp_path)
    lean = eaf_layered.read_layered(tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "lean.eaf"))
    full = eaf_layered.read_layered(tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "full.eaf", words=True))
    assert lean.units == full.units and lean.hints == full.hints


def test_unit_value_is_the_tu_id_and_tx_the_enriched_text(tmp_path):
    vert = make_vert(tmp_path)
    t, _ = tiers(tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf"))
    refs = [a.find("ANNOTATION_VALUE").text for a in t["ref@A"].iter("ALIGNABLE_ANNOTATION")]
    assert refs == ["0", "2"]
    texts = [a.find("ANNOTATION_VALUE").text for a in t["tx@A"].iter("REF_ANNOTATION")]
    assert texts == ["ciao mondo", "va bene=eh"]


def test_media_mime_type_follows_the_extension(tmp_path):
    vert = make_vert(tmp_path)
    for ext, mime in (("mp3", "audio/mpeg"), ("wav", "audio/x-wav")):
        out = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / f"{ext}.eaf", media_file=f"X001.{ext}")
        _, root = tiers(out)
        assert root.find("HEADER/MEDIA_DESCRIPTOR").get("MIME_TYPE") == mime


def test_regenerating_an_unchanged_file_does_not_rewrite_it(tmp_path):
    vert = make_vert(tmp_path)
    out = tmp_path / "X001.eaf"
    tsv2eaf_layered.vert2eaf_layered(vert, out)
    text = out.read_text(encoding="utf-8")
    out.write_text(text.replace("DATE=\"", "DATE=\"1999-01-01T00:00:00+00:00\" OLD=\"", 1), encoding="utf-8")
    before = out.read_bytes()
    tsv2eaf_layered.vert2eaf_layered(vert, out)
    # the date was the only difference between the two, so the file is left as it was
    assert out.read_bytes() == before or b"OLD=" not in out.read_bytes()
    # an actual change does rewrite it
    changed = make_vert(tmp_path, UNITS[:2])
    tsv2eaf_layered.vert2eaf_layered(changed, out)
    assert out.read_bytes() != before


def test_unplaceable_unit_is_an_error_not_a_silent_loss(tmp_path):
    vert = make_vert(tmp_path)
    broken = vert.read_text(encoding="utf-8").replace("Begin=", "Beg=")
    vert.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="Begin"):
        tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf")


def test_reader_returns_units_by_time_with_hints(tmp_path):
    vert = make_vert(tmp_path)
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf")
    layered = eaf_layered.read_layered(eaf)
    assert [(u["speaker"], u["start"], u["text"]) for u in layered.units] == [
        ("A", "0.000", "ciao mondo"), ("B", "1.600", "sì: certo"), ("A", "3.000", "va bene=eh")]
    assert layered.hints == {0: 0, 1: 1, 2: 2}
    assert layered.warnings == []


def test_only_tx_is_read(tmp_path):
    vert = make_vert(tmp_path)
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf", words=True)
    tree = ET.parse(eaf)
    for tier in tree.getroot().findall("TIER"):
        for value in tier.iter("ANNOTATION_VALUE"):
            if tier.get("TIER_ID") == "tx@A" and value.text == "ciao mondo":
                value.text = "ciao mondo bello"
            elif tier.get("TIER_ID").startswith(("tok@", "tx_ortho@")):
                value.text = "garbage"
    tree.write(eaf, encoding="UTF-8", xml_declaration=True)
    texts = [u["text"] for u in eaf_layered.read_layered(eaf).units]
    assert texts == ["ciao mondo bello", "sì: certo", "va bene=eh"]


def test_bad_ref_values_give_no_hint_and_empty_units_are_skipped(tmp_path):
    vert = make_vert(tmp_path)
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf")
    tree = ET.parse(eaf)
    tiers_by_id = {t.get("TIER_ID"): t for t in tree.getroot().findall("TIER")}
    values = [a.find("ANNOTATION_VALUE") for a in tiers_by_id["ref@A"].iter("ALIGNABLE_ANNOTATION")]
    values[0].text = "x"             # not an integer
    values[1].text = "1"             # same as the unit of speaker B
    for tx in tiers_by_id["tx@B"].iter("REF_ANNOTATION"):
        tx.find("ANNOTATION_VALUE").text = ""   # B's only unit loses its text
    tree.write(eaf, encoding="UTF-8", xml_declaration=True)
    layered = eaf_layered.read_layered(eaf)
    assert [u["speaker"] for u in layered.units] == ["A", "A"]
    assert layered.hints == {0: None} or 0 not in layered.hints
    assert any("no transcription" in w for w in layered.warnings)


def test_duplicated_ref_value_is_not_a_hint(tmp_path):
    vert = make_vert(tmp_path)
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf")
    tree = ET.parse(eaf)
    tiers_by_id = {t.get("TIER_ID"): t for t in tree.getroot().findall("TIER")}
    for annotation in tiers_by_id["ref@A"].iter("ALIGNABLE_ANNOTATION"):
        annotation.find("ANNOTATION_VALUE").text = "7"
    tree.write(eaf, encoding="UTF-8", xml_declaration=True)
    layered = eaf_layered.read_layered(eaf)
    assert 0 not in layered.hints and 2 not in layered.hints
    assert any("used by 2 units" in w for w in layered.warnings)


def test_translations_are_written_and_read_back(tmp_path):
    vert = make_vert(tmp_path)
    translations = tmp_path / "X001.translations.json"
    translations.write_text(json.dumps([{"tu_id": "9", "speaker": "A_trad", "start": "0", "end": "1",
                                         "parent_tu_id": "0", "text": "hello [world]"}]))
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf", translations_path=translations)
    t, _ = tiers(eaf)
    assert "ft@A" in t
    layered = eaf_layered.read_layered(eaf)
    assert layered.translations == [{"tu_id": "_", "speaker": "A_trad", "start": "0.000",
                                     "end": "1.500", "parent_tu_id": "0", "text": "hello [world]"}]


def test_is_layered(tmp_path):
    vert = make_vert(tmp_path)
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf")
    assert eaf_layered.is_layered(eaf)
    flat = tmp_path / "flat.eaf"
    flat.write_text('<?xml version="1.0"?><ANNOTATION_DOCUMENT><TIER TIER_ID="A"/></ANNOTATION_DOCUMENT>')
    assert not eaf_layered.is_layered(flat)


def test_round_trip_gives_the_same_tsv(tmp_path):
    vert = make_vert(tmp_path)
    old = reconcile.read_vert(vert)[1]
    for row in old:  # something only the tsv knows
        row["lemma"] = "L"
    reconcile.write_vert(vert, reconcile.read_vert(vert)[0], old)

    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf")
    csv_path = tmp_path / "X001.csv"
    layered = eaf_layered.layered2csv(eaf, csv_path)
    process(csv_path, tmp_path / "new", cfg=CFG, translations_dir=tmp_path / "tr", reports_dir=tmp_path / "rep")
    out = tmp_path / "merged.vert.tsv"
    _, stats = reconcile.reconcile_vert(vert, tmp_path / "new" / "X001.vert.tsv", out, hints=layered.hints)
    assert reconcile.read_vert(out)[1] == old
    assert stats["tus_added"] == stats["tus_removed"] == 0


def check_elan_constraints(eaf):
    """The rules ELAN enforces on tier hierarchies, which the XSD does not express."""
    root = ET.parse(eaf).getroot()
    tier_of, kind = {}, {}
    for lt in root.findall("LINGUISTIC_TYPE"):
        kind[lt.get("LINGUISTIC_TYPE_ID")] = lt.get("CONSTRAINTS")
    ids = []
    for tier in root.findall("TIER"):
        for annotation in tier.iter("ANNOTATION"):
            element = annotation[0]
            ids.append(element.get("ANNOTATION_ID"))
            tier_of[element.get("ANNOTATION_ID")] = tier.get("TIER_ID")
    assert len(ids) == len(set(ids)), "annotation ids must be unique"
    slots = {s.get("TIME_SLOT_ID") for s in root.iter("TIME_SLOT")}
    for tier in root.findall("TIER"):
        parent, constraint = tier.get("PARENT_REF"), kind[tier.get("LINGUISTIC_TYPE_REF")]
        seen = {}
        previous = None
        for element in (a[0] for a in tier.iter("ANNOTATION")):
            if element.tag == "ALIGNABLE_ANNOTATION":
                assert parent is None and {element.get("TIME_SLOT_REF1"), element.get("TIME_SLOT_REF2")} <= slots
                continue
            ref = element.get("ANNOTATION_REF")
            assert tier_of[ref] == parent, f"{tier.get('TIER_ID')}: annotation refers outside its parent tier"
            if constraint == "Symbolic_Association":
                assert ref not in seen, f"{tier.get('TIER_ID')}: two annotations on one parent"
                seen[ref] = 1
            if constraint == "Symbolic_Subdivision":
                # the chain of subdivisions restarts for every parent annotation
                expected = previous if seen.get(ref) else None
                assert element.get("PREVIOUS_ANNOTATION") == expected
                seen[ref] = 1
                previous = element.get("ANNOTATION_ID")
    return True


def test_generated_file_obeys_the_elan_tier_constraints(tmp_path):
    vert = make_vert(tmp_path)
    translations = tmp_path / "X001.translations.json"
    translations.write_text(json.dumps([{"tu_id": "9", "speaker": "A_trad", "start": "0", "end": "1",
                                         "parent_tu_id": "0", "text": "hello"}]))
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "X001.eaf", translations_path=translations)
    assert check_elan_constraints(eaf)
    eaf = tsv2eaf_layered.vert2eaf_layered(vert, tmp_path / "words.eaf", translations_path=translations, words=True)
    assert check_elan_constraints(eaf)
