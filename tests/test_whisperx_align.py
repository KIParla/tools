import csv
import json

from conftest import load_tool_module

wx = load_tool_module("whisperx_align")

HEADER = ["token_id", "speaker", "tu_id", "id", "span", "form", "type", "align"]
ALIGN = {0: "Begin=1.5", 1: "End=2.5"}  # first / last token of the unit


def _row(i, form, type_="linguistic"):
    return dict(zip(HEADER, [f"0-{i}", "S1", "7", str(i), form, form, type_, ALIGN.get(i, "_")]))


def _write_tsv(tmp_path, forms):
    p = tmp_path / "X1.vert.tsv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for i, form in enumerate(forms):
            kind = "shortpause" if form == "(.)" else "linguistic"
            w.writerow(_row(i, form, kind))
    return str(p)


def _write_json(tmp_path, *items, plain=False):
    p = tmp_path / "wx.json"
    if plain:
        segs = [{"id": 0, "start": 0.0, "end": 4.0, "text": " ".join(items)}]
    else:
        words = [dict(word=w, start=s, end=e, score=0.9) for w, s, e in items]
        segs = [{"start": 0, "end": 9, "text": "", "words": words}]
    p.write_text(json.dumps({"segments": segs}))
    return str(p)


def _run(tmp_path, forms, *items, **kw):
    out = tmp_path / "out.tsv"
    wx.main([_write_json(tmp_path, *items, **kw), _write_tsv(tmp_path, forms), "-o", str(out)])
    return list(csv.DictReader(open(out), delimiter="\t"))


def test_normalize():
    assert wx.normalize("Più,") == "piu"
    assert wx.normalize("l~") == "l"
    assert wx.normalize("(.)") == ""


def test_elision_split_shares_time_by_length():
    pieces = wx._split_word("l'ho?", 1.0, 2.0, 0.8, None, "0")
    assert [p.norm for p in pieces] == ["l", "ho"]
    assert pieces[0].start == 1.0 and pieces[1].end == 2.0
    assert abs(pieces[0].end - pieces[1].start) < 1e-9 and pieces[0].end < 1.5


def test_untimed_words_are_interpolated():
    words = [wx.WhisperWord("a", "a", "0", 0.0, 1.0, 0.9),
             wx.WhisperWord("2024", "2024", "0", None, None, None),
             wx.WhisperWord("b", "b", "0", 3.0, 4.0, 0.9)]
    wx._interpolate_untimed(words)
    assert (words[1].start, words[1].end) == (1.0, 3.0)


def test_timestamps_and_alignment_labels(tmp_path):
    rows = _run(tmp_path, ["l'", "ho", "visto", "mh", "casa"],
                ("L'ho", 0.0, 0.6), ("visto,", 1.0, 1.5), ("bella", 2.0, 2.5), ("casa.", 3.0, 3.5))
    by = {r["token_id"]: r for r in rows}
    assert list(rows[0]) == wx.OUTPUT_COLUMNS         # only the requested columns
    assert by["0-0"]["alignment"] == by["0-1"]["alignment"] == "match"
    assert by["0-0"]["whisper_begin"] == "0.000" and by["0-1"]["whisper_end"] == "0.600"
    assert (by["0-0"]["speaker"], by["0-0"]["form"], by["0-0"]["tu_id"]) == ("S1", "l'", "7")
    assert by["0-0"]["whisper_segment"] == "0"
    assert (by["0-0"]["whisper_text"], by["0-1"]["whisper_text"]) == ("l", "ho")
    assert by["0-3"]["whisper_text"] == "bella"      # mismatch: you can see what was heard
    assert by["0-3"]["form"] == "mh"
    assert (by["0-0"]["tsv_begin"], by["0-0"]["tsv_end"]) == ("1.5", "_")
    assert (by["0-1"]["tsv_begin"], by["0-1"]["tsv_end"]) == ("_", "2.5")
    assert by["0-3"]["alignment"] == "mismatch"      # mh <-> bella, paired positionally
    assert by["0-4"]["whisper_begin"] == "3.000"


def test_tsv_only_whisper_only_and_wer(tmp_path, capsys):
    rows = _run(tmp_path, ["ciao", "mh", "tutti", "bene"],
                ("ciao", 0, 1), ("tutti", 2, 3), ("quindi", 3, 4), ("bene", 4, 5))
    kinds = [r["alignment"] for r in rows]
    assert kinds.count("tsv_only") == 1 and kinds.count("whisper_only") == 1
    assert "WER (linguistic tokens only, 4 reference tokens): 50.00%" in capsys.readouterr().err


def test_non_linguistic_and_empty_forms_are_skipped_in_tsv_order(tmp_path, capsys):
    rows = _run(tmp_path, ["ciao", "(.)", "bene"], ("ciao", 0, 1), ("bene", 1, 2))
    assert [(r["token_id"], r["alignment"]) for r in rows] == [
        ("0-0", "match"), ("0-1", "skipped"), ("0-2", "match")]
    assert rows[1]["whisper_begin"] == "_" and rows[1]["form"] == "(.)"
    assert "WER (linguistic tokens only, 2 reference tokens): 0.00%" in capsys.readouterr().err

    p = tmp_path / "Y.vert.tsv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerow(_row(0, "ciao"))
        w.writerow(_row(1, ","))          # typed linguistic but no letters
        w.writerow(_row(2, "nvb", "nonverbalbehavior"))
    out = tmp_path / "y.tsv"
    wx.main([_write_json(tmp_path, ("ciao", 0, 1)), str(p), "-o", str(out)])
    got = list(csv.DictReader(open(out), delimiter="\t"))
    assert [r["alignment"] for r in got] == ["match", "skipped", "skipped"]


def test_plain_whisper_json_falls_back_to_interpolation(tmp_path):
    rows = _run(tmp_path, ["a", "b"], "a", "b", plain=True)
    assert [(r["whisper_begin"], r["whisper_end"]) for r in rows] == [("0.000", "2.000"), ("2.000", "4.000")]


def test_tsv_times_formats():
    assert wx.tsv_times("_") == ("_", "_")
    assert wx.tsv_times("Begin=1.28") == ("1.28", "_")
    assert wx.tsv_times("End=2.0") == ("_", "2.0")
    assert wx.tsv_times("Begin=1.28|End=2.0") == ("1.28", "2.0")
    assert wx.tsv_times(None) == ("_", "_")


def test_whisper_only_row_has_segment_and_times_only(tmp_path):
    rows = _run(tmp_path, ["ciao"], ("ciao", 0, 1), ("extra", 1, 2))
    extra = [r for r in rows if r["alignment"] == "whisper_only"][0]
    assert extra["whisper_segment"] == "0" and extra["whisper_begin"] == "1.000"
    assert extra["whisper_text"] == "extra"
    assert extra["token_id"] == extra["tu_id"] == extra["tsv_begin"] == "_"


def test_whisper_speaker_from_word_then_segment(tmp_path):
    p = tmp_path / "wx.json"
    p.write_text(json.dumps({"segments": [{"start": 0, "end": 9, "speaker": "SPEAKER_01", "words": [
        {"word": "ciao", "start": 0, "end": 1, "score": .9, "speaker": "SPEAKER_00"},
        {"word": "bene", "start": 1, "end": 2, "score": .9},          # falls back to the segment's
    ]}]}))
    out = tmp_path / "o.tsv"
    wx.main([str(p), _write_tsv(tmp_path, ["ciao", "bene", "ok"]), "-o", str(out)])
    got = list(csv.DictReader(open(out), delimiter="\t"))
    assert [r["whisper_speaker"] for r in got] == ["SPEAKER_00", "SPEAKER_01", "_"]


def test_no_diarization_gives_empty_speaker(tmp_path):
    rows = _run(tmp_path, ["ciao"], ("ciao", 0, 1))
    assert rows[0]["whisper_speaker"] == "_"


def _speaker_case(tmp_path, tsv_speakers, wx_speakers, forms=None):
    """TSV tokens spoken by tsv_speakers, WhisperX words labelled wx_speakers."""
    forms = forms or [f"w{i}" for i in range(len(tsv_speakers))]
    p = tmp_path / "S.vert.tsv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for i, (form, spk) in enumerate(zip(forms, tsv_speakers)):
            row = _row(i, form)
            row["speaker"] = spk
            w.writerow(row)
    words = [{"word": form, "start": i, "end": i + 1, "score": .9, **({"speaker": s} if s else {})}
             for i, (form, s) in enumerate(zip(forms, wx_speakers))]
    j = tmp_path / "s.json"
    j.write_text(json.dumps({"segments": [{"start": 0, "end": 99, "words": words}]}))
    out = tmp_path / "s.tsv"
    stats = wx.align_files(str(j), str(p), str(out), verbose=False)
    return stats, list(csv.DictReader(open(out), delimiter="\t"))


def test_speakers_are_mapped_one_to_one(tmp_path):
    tsv = ["BO1"] * 5 + ["BO2"] * 4
    wxs = ["SPEAKER_01"] * 5 + ["SPEAKER_00"] * 3 + ["SPEAKER_01"]   # one wrong word
    stats, rows = _speaker_case(tmp_path, tsv, wxs)
    assert stats["speaker_map"] == "SPEAKER_00=BO2;SPEAKER_01=BO1"
    assert stats["speaker_tokens"] == 9
    assert abs(stats["speaker_agreement"] - 8 / 9) < 1e-9
    assert [r["whisper_speaker_mapped"] for r in rows][:5] == ["BO1"] * 5
    assert rows[5]["whisper_speaker"] == "SPEAKER_00" and rows[5]["whisper_speaker_mapped"] == "BO2"


def test_unknown_speaker_is_ignored_and_extra_labels_stay_unmapped(tmp_path):
    tsv = ["BO1"] * 4 + ["???"] * 3 + ["BO2"] * 4
    wxs = ["SPEAKER_00"] * 4 + ["SPEAKER_02"] * 3 + ["SPEAKER_01"] * 4
    stats, rows = _speaker_case(tmp_path, tsv, wxs)
    assert stats["speaker_map"] == "SPEAKER_00=BO1;SPEAKER_01=BO2"
    assert stats["speaker_agreement"] == 1.0
    unknown = [r for r in rows if r["speaker"] == "???"]
    assert all(r["whisper_speaker"] == "SPEAKER_02" and r["whisper_speaker_mapped"] == "_" for r in unknown)


def test_mapping_handles_more_labels_than_speakers_and_no_labels(tmp_path):
    stats, rows = _speaker_case(tmp_path, ["BO1"] * 6, ["SPEAKER_00"] * 4 + ["SPEAKER_01"] * 2)
    assert stats["speaker_map"] == "SPEAKER_00=BO1"          # the smaller label stays unmapped
    assert [r["whisper_speaker_mapped"] for r in rows] == ["BO1"] * 4 + ["_"] * 2

    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    stats, rows = _speaker_case(tmp2, ["BO1"] * 3, [None] * 3)   # no diarization
    assert stats["speaker_map"] == "" and stats["speaker_agreement"] is None
    assert {r["whisper_speaker_mapped"] for r in rows} == {"_"}


def test_greedy_fallback_matches_exhaustive_on_clear_case(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "_MAX_PERMUTATIONS", 0)
    stats, _ = _speaker_case(tmp_path, ["A"] * 4 + ["B"] * 3, ["S0"] * 4 + ["S1"] * 3)
    assert stats["speaker_map"] == "S0=A;S1=B"
