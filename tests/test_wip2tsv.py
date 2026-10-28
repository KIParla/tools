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


def test_pause_is_not_annotated_and_other_variety_keeps_no_lemma(tmp_path):
    stats, rows = run(tmp_path)
    by_id = {r["token_id"]: r for r in rows}
    assert by_id["0-2"]["lemma"] == by_id["0-2"]["upos"] == "_"
    assert by_id["0-3"]["lemma"] == "_" and by_id["0-3"]["upos"] == "NOUN"
    assert stats["dialect_lemma_skipped"] == 1


def test_conllu_dialect_and_lang_und_skip_the_lemma(tmp_path):
    conllu = tmp_path / "a.conllu"
    conllu.write_text("1\teh\teh\tINTJ\t_\t_\t0\troot\t_\tKID=0-0|Lang=und\n\n")
    stats, rows = run(tmp_path, conllu=conllu)
    assert rows[0]["lemma"] == "_" and rows[0]["upos"] == "INTJ"


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
