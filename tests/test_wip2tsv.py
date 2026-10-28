import csv
import io

import make_patch
import reconcile
import wip2tsv
from variety import iter_vert_rows

FIELDS = ["token_id", "speaker", "tu_id", "id", "span", "form", "lemma", "upos", "xpos",
          "feats", "deprel", "type", "meta_label", "code_variation", "jefferson_feats",
          "align", "prolongations", "pace", "guesses", "overlaps"]
WIP = ["token_id", "speaker", "tu_id", "unit", "id", "span", "form", "lemma", "upos",
       "jefferson_feats", "space_after"]


def write_tsv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t", restval="_", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({**dict.fromkeys(FIELDS, "_"), **r})


def write_wip(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=WIP, restval="_", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def tsv_rows():
    return [
        dict(token_id="0-0", speaker="A", tu_id="0", id="0", span="eh", form="eh", type="linguistic"),
        dict(token_id="0-1", speaker="A", tu_id="0", id="1", span="della", form="della", type="linguistic"),
        dict(token_id="0-2", speaker="A", tu_id="0", id="2", span="(.)", form="[PAUSE]", type="shortpause"),
        dict(token_id="0-3", speaker="A", tu_id="0", id="3", span="mbare", form="mbare", type="linguistic",
             code_variation="Code=Other"),
    ]


def wip_rows():
    return [
        dict(token_id="0-0", form="eh", lemma="eh", upos="INTJ"),
        dict(token_id="0-1", form="della", lemma="_", upos="_"),
        dict(token_id="0-1a", form="di", lemma="di", upos="ADP"),
        dict(token_id="0-1b", form="la", lemma="il", upos="DET"),
        dict(token_id="0-2", form="[PAUSE]", lemma="_", upos="_"),
        dict(token_id="0-3", form="mbare", lemma="mbare", upos="NOUN"),
    ]


def run(tmp_path, **kw):
    write_tsv(tmp_path / "a.vert.tsv", tsv_rows())
    write_wip(tmp_path / "a.csv", wip_rows())
    stats = wip2tsv.annotate(tmp_path / "a.csv", tmp_path / "a.vert.tsv", tmp_path / "out.tsv", **kw)
    with open(tmp_path / "out.tsv", newline="") as f:
        return stats, list(iter_vert_rows(f, syntactic=True))


def test_multiword_rebuilt_from_its_syntactic_rows(tmp_path):
    stats, rows = run(tmp_path)
    by_id = {r["token_id"]: r for r in rows}
    assert by_id["0-1"]["lemma"] == "di+il" and by_id["0-1"]["upos"] == "ADP+DET"
    assert [r["token_id"] for r in rows] == ["0-0", "0-1", "0-1a", "0-1b", "0-2", "0-3"]
    child = by_id["0-1a"]
    assert (child["type"], child["speaker"], child["tu_id"], child["form"]) == ("syntactic", "A", "0", "di")
    assert child["id"] == child["span"] == child["align"] == "_"
    assert stats["multiword"] == 1 and stats["syntactic_rows"] == 2


def test_syntactic_rows_are_invisible_by_default(tmp_path):
    run(tmp_path)
    with open(tmp_path / "out.tsv", newline="") as f:
        assert [r["token_id"] for r in iter_vert_rows(f)] == ["0-0", "0-1", "0-2", "0-3"]


def test_pause_is_not_annotated_and_code_variation_is_not_used(tmp_path):
    stats, rows = run(tmp_path)
    by_id = {r["token_id"]: r for r in rows}
    assert by_id["0-2"]["lemma"] == by_id["0-2"]["upos"] == "_"
    # 0-3 is Code=Other in the tsv, but only Lang=und/Dialect=Yes in the csv withholds a lemma
    assert by_id["0-3"]["lemma"] == "mbare" and by_id["0-3"]["upos"] == "NOUN"
    assert stats["dialect_lemma_skipped"] == 0


def test_lang_und_and_dialect_yes_in_the_csv_withhold_the_lemma_but_not_the_upos(tmp_path):
    write_tsv(tmp_path / "a.vert.tsv", tsv_rows())
    wip = wip_rows()
    wip[0]["jefferson_feats"] = "Lang=und|Dialect=Yes"      # eh
    wip[3]["jefferson_feats"] = "Lang=en"                    # mbare: another language, lemma kept
    write_wip(tmp_path / "a.csv", wip)
    stats = wip2tsv.annotate(tmp_path / "a.csv", tmp_path / "a.vert.tsv", tmp_path / "out.tsv")
    with open(tmp_path / "out.tsv", newline="") as f:
        by_id = {r["token_id"]: r for r in iter_vert_rows(f, syntactic=True)}
    assert by_id["0-0"]["lemma"] == "_" and by_id["0-0"]["upos"] == "INTJ"
    assert by_id["0-3"]["lemma"] == "mbare"
    assert stats["dialect_lemma_skipped"] == 1


def test_dialect_on_a_syntactic_word_withholds_the_lemma_of_the_multiword(tmp_path):
    write_tsv(tmp_path / "a.vert.tsv", tsv_rows())
    wip = wip_rows()
    wip[2]["jefferson_feats"] = "Dialect=Yes"                # della = di + la, "di" is dialect
    write_wip(tmp_path / "a.csv", wip)
    wip2tsv.annotate(tmp_path / "a.csv", tmp_path / "a.vert.tsv", tmp_path / "out.tsv")
    with open(tmp_path / "out.tsv", newline="") as f:
        by_id = {r["token_id"]: r for r in iter_vert_rows(f, syntactic=True)}
    assert by_id["0-1"]["lemma"] == by_id["0-1a"]["lemma"] == "_"
    assert by_id["0-1"]["upos"] == "ADP+DET"


def test_idempotent(tmp_path):
    run(tmp_path)
    first = (tmp_path / "out.tsv").read_text()
    wip2tsv.annotate(tmp_path / "a.csv", tmp_path / "out.tsv", tmp_path / "again.tsv")
    assert (tmp_path / "again.tsv").read_text() == first


def test_disagreement_writes_nothing(tmp_path):
    write_tsv(tmp_path / "a.vert.tsv", tsv_rows())
    write_wip(tmp_path / "a.csv", wip_rows()[:-1])
    stats = wip2tsv.annotate(tmp_path / "a.csv", tmp_path / "a.vert.tsv", tmp_path / "out.tsv")
    assert stats["problems"] and not (tmp_path / "out.tsv").exists()


def test_make_patch_keeps_the_syntactic_rows_and_does_not_count_them_in_id(tmp_path):
    run(tmp_path)
    header, rows, _, eol = make_patch.read_tsv(tmp_path / "out.tsv")
    csv_rows = make_patch.read_csv(tmp_path / "a.csv")
    final, changes, drops, adds = make_patch.build_new_rows(csv_rows, header, rows, eol)
    assert [r["token_id"] for r in final] == [r["token_id"] for r in rows]
    assert not drops and not adds
    assert [r["id"] for r in final if r["type"] != "syntactic"] == ["0", "1", "2", "3"]


def test_make_patch_reports_the_syntactic_rows_of_a_dropped_token(tmp_path):
    run(tmp_path)
    header, rows, _, eol = make_patch.read_tsv(tmp_path / "out.tsv")
    csv_rows = make_patch.read_csv(tmp_path / "a.csv")
    del csv_rows["0-1"]
    final, _, drops, _ = make_patch.build_new_rows(csv_rows, header, rows, eol)
    assert not any(r["token_id"] in ("0-1a", "0-1b") for r in final)
    assert {d["token_id"] for d in drops} >= {"0-1", "0-1a", "0-1b"}


def test_reconcile_carries_the_syntactic_rows_to_the_surviving_token(tmp_path):
    run(tmp_path)
    old = tmp_path / "out.tsv"
    new = tmp_path / "new.tsv"
    write_tsv(new, tsv_rows())
    reconcile.reconcile_vert(old, new, tmp_path / "rec.tsv")
    with open(tmp_path / "rec.tsv", newline="") as f:
        rows = list(iter_vert_rows(f, syntactic=True))
    assert [r["token_id"] for r in rows] == ["0-0", "0-1", "0-1a", "0-1b", "0-2", "0-3"]


def test_linear_files_are_the_same_with_and_without_syntactic_rows(tmp_path):
    import tsv2formats
    write_tsv(tmp_path / "plain.vert.tsv", tsv_rows())
    run(tmp_path)
    (tmp_path / "out.tsv").rename(tmp_path / "annotated.vert.tsv")
    for name in ("plain", "annotated"):
        (tmp_path / f"{name}-enr").mkdir()
        (tmp_path / f"{name}-ort").mkdir()
        tsv2formats.tsv2linear([str(tmp_path / f"{name}.vert.tsv")],
                               tmp_path / f"{name}-enr", tmp_path / f"{name}-ort")
    for kind in ("enr", "ort"):
        plain = (tmp_path / f"plain-{kind}" / "plain.txt").read_text()
        assert plain and plain == (tmp_path / f"annotated-{kind}" / "annotated.txt").read_text()
