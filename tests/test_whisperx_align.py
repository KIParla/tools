import csv
import json

from conftest import load_tool_module

wx = load_tool_module("whisperx_align")

HEADER = ["token_id", "speaker", "tu_id", "id", "span", "form", "type", "align"]


def _row(i, form, type_="linguistic"):
    return dict(zip(HEADER, [f"0-{i}", "S1", "0", str(i), form, form, type_, "Begin=1"]))


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
    assert (by["0-0"]["speaker"], by["0-0"]["form"]) == ("S1", "l'")
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
