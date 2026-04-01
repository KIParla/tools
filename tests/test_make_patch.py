from conftest import load_tool_module


make_patch = load_tool_module("make_patch")


# Minimal CRLF TSV (matches real corpus files)
TSV_HEADER = "token_id\tspeaker\ttu_id\tspan\tform\ttype\tjefferson_feats\talign\tprolongations\tpace\tguesses\toverlaps"
TSV_ROWS = [
    "0-0\tSPK1\t0\t°ciao.°\tciao\tlinguistic\tIntonation=Falling\tBegin=1.0|End=1.5\t_\t_\t_\t_",
    "1-0\tSPK1\t1\tsostiuire\tsostiuire\tlinguistic\t_\t_\t_\t_\t_\t_",
    "2-0\tSPK2\t2\telle\telle\tlinguistic\t_\t_\t_\t_\t_\t_",
    "2-1\tSPK2\t2\tdue.\tdue\tlinguistic\tIntonation=Falling\tEnd=3.5\t_\t_\t_\t_",
    "3-0\tSPK2\t3\t(.)  \t[PAUSE]\tshortpause\t_\t_\t_\t_\t_\t_",
]

# CSV with corrections (BOM-less for simplicity; read_csv uses utf-8-sig)
CSV_HEADER = "token_id,speaker,tu_id,unit,id,span,form,lemma,upos,jefferson_feats"
CSV_ROWS = [
    "0-0,SPK1,0,0,1,°ciao.°,ciao,ciao,INTJ,Intonation=Falling",    # no change
    "1-0,SPK1,1,1,1,sostituire,sostituire,sostituire,VERB,_",       # typo fix
    "2-0,SPK2,2,2,1,elledue.,elledue,L2,NOUN,Intonation=Falling",  # merge: 2-1 removed
    # 2-1 absent → will be dropped; End=3.5 should transfer to 2-0
    "3-0,SPK2,3,3,1,{P},{P},_,_,_",                                 # pause fix + strip test
]


def write_crlf(path, header, rows):
    path.write_bytes(("\r\n".join([header] + rows) + "\r\n").encode("utf-8"))


def write_csv(path, header, rows):
    path.write_text("\n".join([header] + rows) + "\n", encoding="utf-8")


def test_patchable_columns_updated(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, CSV_ROWS)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    # typo fix
    assert "-1-0\tSPK1\t1\tsostiuire\tsostiuire" in patch
    assert "+1-0\tSPK1\t1\tsostituire\tsostituire" in patch
    # pause notation fix
    assert "-(.)  " not in patch          # strip removed trailing spaces
    assert "+3-0" in patch
    assert "{P}" in patch


def test_change_summary_in_recap(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, CSV_ROWS)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")
    assert "**CHANGE** `1-0`" in recap
    assert "`form`: 'sostiuire' → 'sostituire'" in recap


def test_token_drop_transfers_end_timestamp(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, CSV_ROWS)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    # 2-0 should now carry End=3.5 (transferred from dropped 2-1)
    assert "End=3.5" in patch
    # 2-1 should be deleted
    assert "-2-1\t" in patch
    assert "+2-1\t" not in patch


def test_no_diff_produces_no_patch(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    # TSV with no trailing spaces, so a CSV that exactly matches produces no diff
    clean_tsv_rows = [
        "0-0\tSPK1\t0\t°ciao.°\tciao\tlinguistic\tIntonation=Falling\tBegin=1.0|End=1.5\t_\t_\t_\t_",
        "1-0\tSPK1\t1\tsostiuire\tsostiuire\tlinguistic\t_\t_\t_\t_\t_\t_",
        "2-0\tSPK2\t2\telle\telle\tlinguistic\t_\t_\t_\t_\t_\t_",
        "2-1\tSPK2\t2\tdue.\tdue\tlinguistic\tIntonation=Falling\tEnd=3.5\t_\t_\t_\t_",
        "3-0\tSPK2\t3\t(.)\t[PAUSE]\tshortpause\t_\t_\t_\t_\t_\t_",
    ]
    identical_csv_rows = [
        "0-0,SPK1,0,0,1,°ciao.°,ciao,ciao,INTJ,Intonation=Falling",
        "1-0,SPK1,1,1,1,sostiuire,sostiuire,sostiuire,VERB,_",
        "2-0,SPK2,2,2,1,elle,elle,_,_,_",
        "2-1,SPK2,2,2,2,due.,due,_,_,Intonation=Falling",
        "3-0,SPK2,3,3,1,(.),[PAUSE],_,_,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, clean_tsv_rows)
    write_csv(csv_path, CSV_HEADER, identical_csv_rows)

    result = make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    assert result is False
    assert not patch_path.exists()


def test_strip_whitespace_from_csv_values(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    # CSV has a trailing space in span of 3-0
    spacey_csv_rows = [
        "0-0,SPK1,0,0,1,°ciao.°,ciao,ciao,INTJ,Intonation=Falling",
        "1-0,SPK1,1,1,1,sostiuire,sostiuire,sostiuire,VERB,_",
        "2-0,SPK2,2,2,1,elle,elle,_,_,_",
        "2-1,SPK2,2,2,2,due.,due,_,_,Intonation=Falling",
        "3-0,SPK2,3,3,1,{P} ,{P},_,_,_",   # trailing space in span
    ]
    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, spacey_csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    # The applied span should be stripped: '{P}' not '{P} '
    assert "+3-0\tSPK2\t3\t{P}\t{P}" in patch


def test_added_token_is_inserted_and_receives_end(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    added_csv_rows = [
        "0-0,SPK1,0,0,1,°ciao.°,ciao,ciao,INTJ,Intonation=Falling",
        "1-0,SPK1,1,1,1,sostiuire,sostiuire,sostiuire,VERB,_",
        "2-0,SPK2,2,2,1,elle,elle,_,_,_",
        "2-1,SPK2,2,2,2,due,due,_,_,_",
        "2-2,SPK2,2,2,3,ehm,ehm,ehm,INTJ,_",
        "3-0,SPK2,3,3,1,{P},{P},_,_,_",
    ]

    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, added_csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")

    assert "-2-1\tSPK2\t2\tdue.\tdue\tlinguistic\tIntonation=Falling\tEnd=3.5\t_\t_\t_\t_" in patch
    assert "+2-1\tSPK2\t2\tdue\tdue\tlinguistic\tIntonation=Falling\t_\t_\t_\t_\t_" in patch
    assert "+2-2\tSPK2\t2\tehm\tehm\tlinguistic\t_\tEnd=3.5\t_\t_\t_\t_" in patch
    assert "**ADD** `2-2`" in recap
    assert "End=3.5" in recap


def test_jefferson_feats_are_merged_not_replaced(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\tlatin,\tlatin\tlinguistic\tIntonation=WeaklyRising\tEnd=1.5\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,latin,latin,latin,NOUN,Lang=lat",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")

    assert "Intonation=WeaklyRising|Lang=lat" in patch
    assert "`jefferson_feats`: 'Intonation=WeaklyRising' → 'Intonation=WeaklyRising|Lang=lat'" in recap
