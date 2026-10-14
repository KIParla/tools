import json

import reconcile

FIELDS = ["token_id", "speaker", "tu_id", "id", "span", "form", "lemma", "upos", "xpos",
          "feats", "deprel", "type", "meta_label", "code_variation", "jefferson_feats",
          "align", "prolongations", "pace", "guesses", "overlaps"]


def tok(tu, idx, span, speaker="A", begin=None, end=None, **over):
    row = dict.fromkeys(FIELDS, "_")
    row.update(token_id=f"{tu}-{idx}", speaker=speaker, tu_id=str(tu), id=str(idx),
               span=span, form=span.lower().strip(), type="linguistic")
    align = []
    if begin is not None:
        align.append(f"Begin={begin}")
    if end is not None:
        align.append(f"End={end}")
    if align:
        row["align"] = "|".join(align)
    row.update(over)
    return row


def unit(tu, spans, speaker="A", start=0.0, **over):
    rows = [tok(tu, i, s, speaker) for i, s in enumerate(spans)]
    rows[0]["align"] = f"Begin={start}"
    rows[-1]["align"] = (rows[-1]["align"] + "|" if rows[-1]["align"] != "_" else "") + f"End={start + 1}"
    for r in rows:
        r.update(over)
    return rows


def ids(rows):
    return [(r["token_id"], r["tu_id"], r["id"]) for r in rows]


def test_unchanged_eaf_keeps_every_row():
    old = unit(10, ["ciao", "mondo"], start=1.0) + unit(20, ["sì"], "B", start=3.0)
    new = unit(1, ["ciao", "mondo"], start=1.0) + unit(2, ["sì"], "B", start=3.0)
    rows, mapping, stats = reconcile.reconcile_rows(old, new)
    assert rows == old
    assert mapping == {1: 10, 2: 20}
    assert stats["tokens_kept"] == 3


def test_annotations_only_in_the_tsv_survive():
    old = unit(10, ["ciao", "mondo"], start=1.0)
    old[0].update(lemma="ciao", upos="INTJ", code_variation="Language=eng")
    new = unit(1, ["ciao", "mondo"], start=1.0)
    rows, _, _ = reconcile.reconcile_rows(old, new)
    assert rows[0]["lemma"] == "ciao" and rows[0]["code_variation"] == "Language=eng"


def test_ids_stay_when_a_unit_is_inserted_or_reordered():
    old = unit(10, ["uno"], "A", start=1.0) + unit(20, ["due"], "B", start=1.0)
    # the eaf lists the two simultaneous units in the opposite order, plus a new one
    new = (unit(1, ["due"], "B", start=1.0) + unit(2, ["uno"], "A", start=1.0)
           + unit(3, ["tre"], "A", start=5.0))
    rows, mapping, stats = reconcile.reconcile_rows(old, new)
    assert mapping == {1: 20, 2: 10, 3: 21}
    # a new unit goes after the unit that precedes it in the eaf
    assert [r["token_id"] for r in rows] == ["10-0", "21-0", "20-0"]
    assert stats["tus_added"] == 1


def test_edited_token_keeps_its_id_and_added_token_gets_a_fresh_one():
    old = unit(10, ["ciao", "mondo"], start=1.0)
    old[1].update(lemma="mondo")
    new = unit(1, ["ciao", "monda", "bello"], start=1.0)
    rows, _, stats = reconcile.reconcile_rows(old, new)
    assert [r["token_id"] for r in rows] == ["10-0", "10-1", "10-2"]
    assert [r["id"] for r in rows] == ["0", "1", "2"]
    assert rows[1]["span"] == "monda" and rows[1]["lemma"] == "_"  # form changed
    assert stats["tokens_changed"] == 1 and stats["tokens_added"] == 1


def test_same_form_keeps_the_lemma_and_language_when_only_the_span_changes():
    old = unit(10, ["ciao"], start=1.0)
    old[0].update(lemma="ciao", code_variation="ContainsVariation=Yes|Language=eng")
    new = unit(1, ["ciao"], start=1.0)
    new[0].update(span="ciao:", form="ciao", code_variation="ContainsVariation=Yes")
    rows, _, _ = reconcile.reconcile_rows(old, new)
    assert rows[0]["span"] == "ciao:" and rows[0]["lemma"] == "ciao"
    assert rows[0]["code_variation"] == "ContainsVariation=Yes|Language=eng"


def test_removed_token_disappears_but_token_without_form_stays():
    old = unit(10, ["a", "b", "c"], start=1.0)
    old[1].update(form="_", span="b")
    new = unit(1, ["a", "c"], start=1.0)
    rows, _, _ = reconcile.reconcile_rows(old, new)
    assert [r["token_id"] for r in rows] == ["10-0", "10-1", "10-2"]
    assert rows[1]["form"] == "_"


def test_rounded_align_is_not_a_change():
    old = unit(10, ["a"], start=1.0)
    old[0]["align"] = "Begin=1.619|End=2.3735"
    new = unit(1, ["a"], start=1.0)
    new[0]["align"] = "Begin=1.619|End=2.373"
    rows, _, _ = reconcile.reconcile_rows(old, new)
    assert rows[0]["align"] == "Begin=1.619|End=2.3735"


def test_new_unit_ids_never_reuse_old_ones():
    old = unit(10, ["a"], start=1.0) + unit(30, ["b"], "B", start=2.0)
    new = unit(1, ["a"], start=1.0) + unit(2, ["z"], "A", start=9.0)  # b removed, z added
    rows, mapping, stats = reconcile.reconcile_rows(old, new)
    assert mapping == {1: 10, 2: 31}
    assert stats["tus_removed"] == 1


def test_translations_follow_their_parent(tmp_path):
    old_json = tmp_path / "X.translations.json"
    old_json.write_text(json.dumps([{"tu_id": "11", "speaker": "A_trad", "start": "1", "end": "2",
                                     "parent_tu_id": "10", "text": "hello"}]))
    new_rows = [{"tu_id": "2", "speaker": "A_trad", "start": "1", "end": "2",
                 "parent_tu_id": "1", "text": "hello"},
                {"tu_id": "9", "speaker": "A_trad", "start": "5", "end": "6",
                 "parent_tu_id": "3", "text": "new"}]
    out = reconcile.reconcile_translations(old_json, new_rows, {1: 10, 3: 40}, {10, 40})
    assert [(r["tu_id"], r["parent_tu_id"]) for r in out] == [("11", "10"), ("41", "40")]


def test_without_old_file_the_new_one_is_copied(tmp_path):
    new = tmp_path / "new.vert.tsv"
    reconcile.write_vert(new, FIELDS, unit(1, ["ciao"]))
    out = tmp_path / "out.vert.tsv"
    mapping, _ = reconcile.reconcile_vert(None, new, out)
    assert mapping == {1: 1} and out.read_text() == new.read_text()


def test_overlap_group_numbers_follow_the_old_file():
    old = unit(10, ["a"], start=1.0) + unit(20, ["b"], "B", start=1.0)
    for r in old:
        r["overlaps"] = "0-1(53)"
    new = unit(1, ["a"], start=1.0) + unit(2, ["b"], "B", start=1.0) + unit(3, ["c"], start=8.0)
    for r in new[:2]:
        r["overlaps"] = "0-1(7)"
    new[2]["overlaps"] = "0-1(8)"
    rows, _, _ = reconcile.reconcile_rows(old, new)
    assert [r["overlaps"] for r in rows] == ["0-1(53)", "0-1(53)", "0-1(54)"]


def test_hints_pair_units_exactly_even_when_text_and_times_changed():
    old = unit(10, ["uno", "due"], start=1.0) + unit(20, ["tre"], "B", start=5.0)
    new = unit(1, ["quattro"], "A", start=40.0) + unit(2, ["tre"], "B", start=5.0)  # unit 10 rewritten
    rows, mapping, _ = reconcile.reconcile_rows(old, new, hints={1: 10})
    assert mapping == {1: 10, 2: 20}
    assert {r["token_id"] for r in rows if r["tu_id"] == "10"} == {"10-0"}


def test_wrong_hints_are_ignored():
    old = unit(10, ["uno"], start=1.0)
    new = unit(1, ["uno"], start=1.0) + unit(2, ["due"], start=9.0)
    _, mapping, _ = reconcile.reconcile_rows(old, new, hints={2: 99, 1: 10})
    assert mapping == {1: 10, 2: 11}


def test_translations_without_a_unit_are_kept(tmp_path):
    old_json = tmp_path / "X.translations.json"
    old_json.write_text(json.dumps([
        {"tu_id": "11", "speaker": "A_trad", "start": "1", "end": "2", "parent_tu_id": "10", "text": "hello"},
        {"tu_id": "50", "speaker": "A_trad", "start": "9", "end": "9.5", "parent_tu_id": "_", "text": "floating"},
        {"tu_id": "51", "speaker": "A_trad", "start": "9", "end": "9.5", "parent_tu_id": "99", "text": "gone"},
        {"tu_id": "52", "speaker": "A_trad", "start": "9", "end": "9.5", "parent_tu_id": "_", "text": ""},
    ]))
    new_rows = [{"tu_id": "_", "speaker": "A_trad", "start": "1", "end": "2",
                 "parent_tu_id": "1", "text": "hello!"}]
    out = reconcile.reconcile_translations(old_json, new_rows, {1: 10}, {10}, old_unit_ids={10})
    assert [(r["parent_tu_id"], r["text"]) for r in out] == [("10", "hello!"), ("_", "floating"), ("99", "gone")]


def test_a_unit_whose_time_was_not_edited_keeps_its_times():
    old = unit(10, ["a", "b"], start=1.0)
    old[0]["align"], old[1]["align"] = "Begin=1.619", "End=2.778"
    new = unit(1, ["a", "b"], start=1.0)
    new[0]["align"], new[1]["align"] = "Begin=1.7305", "End=2.9"   # pipeline moved both edges
    # the .eaf still says 1.619-2.778: nobody touched the unit
    rows, _, _ = reconcile.reconcile_rows(old, new, raw_times={1: (1.619, 2.778)})
    assert (rows[0]["align"], rows[1]["align"]) == ("Begin=1.619", "End=2.778")
    # begin edited in ELAN, end untouched
    rows, _, _ = reconcile.reconcile_rows(old, new, raw_times={1: (1.7305, 2.778)})
    assert (rows[0]["align"], rows[1]["align"]) == ("Begin=1.7305", "End=2.778")
    # without raw times the new values win
    rows, _, _ = reconcile.reconcile_rows(old, new)
    assert (rows[0]["align"], rows[1]["align"]) == ("Begin=1.7305", "End=2.9")


def test_unchanged_translation_is_kept_exactly(tmp_path):
    old_json = tmp_path / "X.translations.json"
    old_json.write_text(json.dumps([{"tu_id": "11", "speaker": "A_trad", "start": "1.000",
                                     "end": "2.203", "parent_tu_id": "10", "text": "hello"}]))
    new_rows = [{"tu_id": "_", "speaker": "A_trad", "start": "1.000", "end": "2.200",
                 "parent_tu_id": "1", "text": "hello"}]
    out = reconcile.reconcile_translations(old_json, new_rows, {1: 10}, {10}, old_unit_ids={10})
    assert out[0]["end"] == "2.203"
