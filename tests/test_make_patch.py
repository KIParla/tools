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
    # pause row: CSV's legacy `{P}` is canonicalized to `(.)`, never written
    # back as-is (see normalize_legacy_pause_nvb) — span already matched the
    # (trimmed) TSV value so only `form` actually changes, from the TSV's
    # own [PAUSE] placeholder to the canonical `(.)`.
    assert "{P}" not in patch
    assert "-3-0\tSPK2\t3\t(.)  \t[PAUSE]" in patch  # old row, trailing spaces from source
    assert "+3-0\tSPK2\t3\t(.)\t(.)" in patch


def test_change_summary_in_recap(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, CSV_ROWS)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")
    assert "| modifiche | 3 |" in recap
    assert "| eliminazioni | 1 |" in recap
    assert "| aggiunte | 0 |" in recap
    assert "| token_id | id | tipo | livello | span | form | altro |" in recap
    assert "| `1-0` | _ | [MOD] modifica | [OK] gestito automaticamente" in recap
    assert "`sostiuire` → `sostituire`" in recap


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

    # The successful transfer is a plain "✅ gestito automaticamente" with no
    # separate "✅ End=... → token_id" note — that's redundant with 2-0's own
    # align: `_` → `End=3.5` diff.
    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")
    assert "| `2-1` | _ | [DEL] eliminazione | [OK] gestito automaticamente" in recap
    assert "End=3.5` → `2-0`" not in recap


# ---------------------------------------------------------------------------
# Positional feature transfer on merge (prolongations/pace/guesses/overlaps)
# ---------------------------------------------------------------------------

def test_pace_transferred_and_shifted_on_merge(tmp_path):
    """Real case (KIP BOA1001 tu_id 393): "elle" + "due" merge into
    "elledue". The dropped token's pace span (over its own form "due") must
    be transferred onto the kept token, shifted by len("elle")=4 so it still
    points at the right characters in the merged form "elledue".
    """
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\telle\telle\tlinguistic\t_\t_\t_\t_\t_\t_",
        "0-1\tSPK1\t0\tdue\tdue\tlinguistic\t_\t_\t_\tSlow=0-3(0)\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,elledue,elledue,elledue,NOUN,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "+0-0\tSPK1\t0\telledue\telledue\tlinguistic\t_\t_\t_\tSlow=4-7(0)\t_\t_" in patch

    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")
    # Both rows report as fully automatic; the transfer itself is visible from
    # the field diffs (kept row's own pace: `_` → `Slow=4-7(0)`), so no
    # separate "✅ pace=... → token_id (shifted)" note is expected anymore.
    assert "| `0-0` | _ | [MOD] modifica | [OK] gestito automaticamente" in recap
    assert "pace: `_` → `Slow=4-7(0)`" in recap
    assert "| `0-1` | _ | [DEL] eliminazione | [OK] gestito automaticamente" in recap
    assert "shifted" not in recap
    assert "⚠️ pace" not in recap


def test_contiguous_pace_spans_collapse_into_one(tmp_path):
    """If the kept token already carries a pace span that ends exactly
    where the (shifted) dropped span begins, and both share the same
    Fast=/Slow= prefix and group id, they collapse into one continuous
    span (Slow=0-4(0) + Slow=4-7(0) → Slow=0-7(0)) rather than being kept
    as two adjacent comma-separated entries.
    """
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\telle\telle\tlinguistic\t_\t_\t_\tSlow=0-4(0)\t_\t_",
        "0-1\tSPK1\t0\tdue\tdue\tlinguistic\t_\t_\t_\tSlow=0-3(0)\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,elledue,elledue,elledue,NOUN,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "+0-0\tSPK1\t0\telledue\telledue\tlinguistic\t_\t_\t_\tSlow=0-7(0)\t_\t_" in patch
    assert "Slow=0-4(0),Slow=4-7(0)" not in patch


def test_non_contiguous_pace_spans_stay_separate(tmp_path):
    """Same shape, but the kept token's existing span doesn't end where the
    shifted dropped span begins — must NOT be merged into one range."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\telle\telle\tlinguistic\t_\t_\t_\tSlow=0-2(0)\t_\t_",
        "0-1\tSPK1\t0\tdue\tdue\tlinguistic\t_\t_\t_\tSlow=0-3(0)\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,elledue,elledue,elledue,NOUN,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "+0-0\tSPK1\t0\telledue\telledue\tlinguistic\t_\t_\t_\tSlow=0-2(0),Slow=4-7(0)\t_\t_" in patch


def test_positional_feature_not_transferred_when_not_a_clean_merge(tmp_path):
    """If the kept token's new form doesn't end with the dropped token's old
    form, this isn't a simple append-merge — don't guess an offset, just
    warn as before."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\telle\telle\tlinguistic\t_\t_\t_\t_\t_\t_",
        "0-1\tSPK1\t0\tdue\tdue\tlinguistic\t_\t_\t_\tSlow=0-3(0)\t_\t_",
    ]
    csv_rows = [
        # completely unrelated correction — doesn't even end with "due"
        "0-0,SPK1,0,0,1,gattone,gattone,gattone,NOUN,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")
    assert "⚠️ pace=`Slow=0-3(0)`" in recap


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
        "3-0\tSPK2\t3\t(.)\t(.)\tshortpause\t_\t_\t_\t_\t_\t_",
    ]
    identical_csv_rows = [
        "0-0,SPK1,0,0,1,°ciao.°,ciao,ciao,INTJ,Intonation=Falling",
        "1-0,SPK1,1,1,1,sostiuire,sostiuire,sostiuire,VERB,_",
        "2-0,SPK2,2,2,1,elle,elle,_,_,_",
        "2-1,SPK2,2,2,2,due.,due,_,_,Intonation=Falling",
        "3-0,SPK2,3,3,1,(.),[PAUSE],_,_,_",  # CSV still uses the CoNLL-U placeholder; must mirror the already-canonical TSV span, not diff against it
    ]
    write_crlf(tsv_path, TSV_HEADER, clean_tsv_rows)
    write_csv(csv_path, CSV_HEADER, identical_csv_rows)

    result = make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    assert result is False
    assert not patch_path.exists()


# ---------------------------------------------------------------------------
# Legacy {P}/{tag} notation vs. literal (.)/((tag)) — see normalize.py's
# decision to preserve literal Jefferson notation (not rewrite it to
# {P}/{...}). make_patch must canonicalize, never write {P}/{tag} back out.
# ---------------------------------------------------------------------------

def test_normalize_legacy_pause():
    assert make_patch.normalize_legacy_pause_nvb("{P}") == "(.)"


def test_normalize_legacy_nvb_single_word():
    assert make_patch.normalize_legacy_pause_nvb("{ride}") == "((ride))"


def test_normalize_legacy_nvb_multi_word():
    assert make_patch.normalize_legacy_pause_nvb("{suona_il_pianoforte}") == "((suona il pianoforte))"


def test_normalize_leaves_literal_notation_untouched():
    assert make_patch.normalize_legacy_pause_nvb("(.)") == "(.)"
    assert make_patch.normalize_legacy_pause_nvb("((ride))") == "((ride))"


def test_normalize_leaves_conllu_placeholders_untouched():
    # [PAUSE]/[NVB] are the lemmatization project's own CoNLL-U form
    # convention, distinct from the {P}/{tag} legacy notation.
    assert make_patch.normalize_legacy_pause_nvb("[PAUSE]") == "[PAUSE]"
    assert make_patch.normalize_legacy_pause_nvb("[NVB]") == "[NVB]"


def test_normalize_leaves_ordinary_words_untouched():
    assert make_patch.normalize_legacy_pause_nvb("ciao") == "ciao"


def test_untouched_legacy_token_not_reverted(tmp_path):
    """Real-world regression: a module's source .eaf was hotfixed from
    legacy {P}/{tag} notation to literal (.)/((tag)) and reprocessed, but
    a wip/*.csv pulled before that hotfix still has the stale {tag} value
    for a token the lemmatizer never actually edited. Re-running make_patch
    against the now-corrected TSV must NOT flag this as a change (and must
    never write {tag} back into the corpus) — the token is untouched.
    """
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    # TSV already reprocessed from the hotfixed .eaf: literal notation.
    tsv_rows = [
        "0-0\tSPK1\t0\t((ride))\t((ride))\tnonverbalbehavior\t_\t_\t_\t_\t_\t_",
        "1-0\tSPK1\t1\t(.)\t(.)\tshortpause\t_\t_\t_\t_\t_\t_",
    ]
    # CSV pulled before the hotfix: still carries the legacy form verbatim,
    # since the lemmatizer never touched these two tokens.
    csv_rows = [
        "0-0,SPK1,0,0,1,{ride},{ride},[LAUGH],X,_",
        "1-0,SPK1,1,1,1,{P},{P},_,_,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    result = make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    assert result is False
    assert not patch_path.exists()


def test_conllu_placeholder_form_mirrors_span_not_written_literally(tmp_path):
    """CSV `form=[PAUSE]`/`[NVB]` is the lemmatization project's own CoNLL-U
    convention (see conllu2wip.py) — it must never be written into the
    corpus `form` column as-is. Per the normalize.py decision (pause/NVB
    tokens keep form==span in literal notation), the patched form should
    mirror the token's own span instead.
    """
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\t((ride))\t((ride))\tnonverbalbehavior\t_\t_\t_\t_\t_\t_",
        "1-0\tSPK1\t1\t(.)\t(.)\tshortpause\t_\t_\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,((ride)),[NVB],[LAUGH],X,_",
        "1-0,SPK1,1,1,1,(.),[PAUSE],_,_,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    result = make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    # span already matched, form already matched span too (both `[NVB]` and
    # `[PAUSE]` resolve to the existing literal form) → no genuine change.
    assert result is False
    assert not patch_path.exists()


def test_conllu_placeholder_form_applied_when_span_changes(tmp_path):
    """Same CoNLL-U placeholder, but this time the span itself is a genuine
    edit (legacy {P} not yet reprocessed) — the patched form should mirror
    the *new* canonical span, not the raw [PAUSE] placeholder text.
    """
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\t{P}\t{P}\tshortpause\t_\t_\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,(.),[PAUSE],_,_,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "[PAUSE]" not in patch
    assert "+0-0\tSPK1\t0\t(.)\t(.)" in patch


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
    # The applied span should be stripped ('{P}' not '{P} ') and then
    # canonicalized to literal notation ('(.)', not '{P}').
    assert "+3-0\tSPK2\t3\t(.)\t(.)" in patch


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
    # span changed from "due." to "due": Intonation=Falling is recomputed from new span → removed
    assert "+2-1\tSPK2\t2\tdue\tdue\tlinguistic\t_\t_\t_\t_\t_\t_" in patch
    assert "+2-2\tSPK2\t2\tehm\tehm\tlinguistic\t_\tEnd=3.5\t_\t_\t_\t_" in patch
    assert "| `2-2` | _ | [ADD] aggiunta | [OK] gestito automaticamente" in recap
    assert "End=3.5" in recap


def test_added_bis_token_id_is_not_filtered_as_subtoken(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "1-0\tSPK1\t1\tciao.\tciao\tlinguistic\tIntonation=Falling\tEnd=1.5\t_\t_\t_\t_",
        "2-0\tSPK1\t2\tdopo\tdopo\tlinguistic\t_\tBegin=1.6\t_\t_\t_\t_",
    ]
    csv_rows = [
        "1-0,SPK1,1,1,1,ciao.,ciao,ciao,INTJ,Intonation=Falling",
        "1bis,SPK1,1,1,2,ehm,ehm,ehm,INTJ,_",
        "2-0,SPK1,2,2,1,dopo,dopo,dopo,ADV,_",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")

    assert "+1bis\tSPK1\t1\tehm\tehm\tlinguistic\t_\tEnd=1.5\t_\t_\t_\t_" in patch
    assert "Begin=1.6" in patch
    assert "| `1bis` | _ | [ADD] aggiunta | [OK] gestito automaticamente" in recap


def test_added_token_infers_missing_speaker_and_tu_id(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "1-0\tSPK1\t1\tciao.\tciao\tlinguistic\tIntonation=Falling\tEnd=1.5\t_\t_\t_\t_",
        "2-0\tSPK1\t1\tdopo\tdopo\tlinguistic\t_\tBegin=1.6\t_\t_\t_\t_",
    ]
    csv_rows = [
        "1-0,SPK1,1,1,1,ciao.,ciao,ciao,INTJ,Intonation=Falling",
        "1bis,_,_,1,2,ehm,ehm,ehm,INTJ,_",
        "2-0,SPK1,1,2,1,dopo,dopo,dopo,ADV,_",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "+1bis\tSPK1\t1\tehm\tehm\tlinguistic\t_\tBegin=1.6|End=1.5\t_\t_\t_\t_" in patch


def test_kept_token_infers_missing_speaker_and_tu_id(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t1\tciao.\tciao\tlinguistic\tIntonation=Falling\t_\t_\t_\t_\t_",
        "1-0\t_\t_\te::hm::e\tehme\tlinguistic\t_\t_\t_\t_\t_\t_",
        "2-0\tSPK1\t1\tdopo\tdopo\tlinguistic\t_\t_\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,1,0,1,ciao.,ciao,ciao,INTJ,Intonation=Falling",
        "1-0,_,_,1,1,e::hm::e,ehm,ehm,INTJ,_",
        "2-0,SPK1,1,2,1,dopo,dopo,dopo,ADV,_",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "-1-0\t_\t_\te::hm::e\tehme\tlinguistic\t_\t_\t_\t_\t_\t_" in patch
    assert "+1-0\tSPK1\t1\te::hm::e\tehm\tlinguistic\t_\t_\t_\t_\t_\t_" in patch


def test_recap_is_in_row_order_with_change_drop_and_add(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    csv_rows = [
        "0-0,SPK1,0,0,1,°ciao.°,ciao,ciao,INTJ,Intonation=Falling",
        "1-0,SPK1,1,1,1,sostituire,sostituire,sostituire,VERB,_",
        "2-0,SPK2,2,2,1,elledue.,elledue,L2,NOUN,Intonation=Falling",
        "2bis,SPK2,2,2,2,ehm,ehm,ehm,INTJ,_",
        "3-0,SPK2,3,3,1,{P},{P},_,_,_",
    ]

    write_crlf(tsv_path, TSV_HEADER, TSV_ROWS)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")

    pos_change = recap.index("| `1-0` | _ | [MOD] modifica |")
    pos_drop = recap.index("| `2-1` | _ | [DEL] eliminazione |")
    pos_add = recap.index("| `2bis` | _ | [ADD] aggiunta |")
    assert pos_change < pos_drop < pos_add


def test_non_lang_csv_feats_are_ignored(tmp_path):
    """jefferson_feats from CSV other than Lang=* must not overwrite TSV values."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\tciao.\tciao\tlinguistic\tIntonation=falling\tEnd=1.5\t_\t_\t_\t_",
    ]
    csv_rows = [
        # CSV has wrong Intonation — should be ignored; span unchanged
        "0-0,SPK1,0,0,1,ciao.,ciao,ciao,INTJ,Intonation=rising",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    result = make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    # No diff: TSV Intonation=falling must be preserved, CSV Intonation=rising ignored
    assert result is False


def test_span_change_recomputes_intonation(tmp_path):
    """If span changes, span-derived features are recomputed; non-span features preserved."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        # ProsodicLink is non-span-derived → must survive the span change
        "0-0\tSPK1\t0\tciao.\tciao\tlinguistic\tIntonation=falling|ProsodicLink=Yes\tEnd=1.5\t_\t_\t_\t_",
    ]
    csv_rows = [
        # span changed: no intonation marker, Lang added
        "0-0,SPK1,0,0,1,ciao,ciao,ciao,INTJ,Lang=ita",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    # Intonation=falling removed (span no longer ends with "."), ProsodicLink preserved, Lang added
    assert "ProsodicLink=Yes|Lang=ita" in patch
    assert "Intonation" not in patch.split("jefferson_feats")[1].split("\n")[0]


def test_volume_not_derivable_from_own_span_is_preserved_on_edit(tmp_path):
    """Real case (KIP BOA1002 tu_id 66): a token can carry Volume=low while
    its own span has no literal `°` — it's inside a multi-word °...° span
    where only the boundary tokens carry the ° (see feats_from_span). An
    unrelated accent fix to this token's span ("vabbé" → "vabbè") shouldn't
    wipe Volume just because it isn't derivable from this token's span text
    alone — that was already true before the edit too.
    """
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\tvabbé\tvabbé\tlinguistic\tVolume=low\t_\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,vabbè,vabbè,vabbè,INTJ,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "+0-0\tSPK1\t0\tvabbè\tvabbè\tlinguistic\tVolume=low\t_\t_\t_\t_\t_" in patch


def test_volume_actually_removed_when_own_marker_is_removed(tmp_path):
    """Contrast case: the old span itself carried the literal `°`, so
    Volume=low *was* locally derivable — if the edit removes it, that's a
    genuine change and Volume should be dropped, same as before."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        "0-0\tSPK1\t0\t°vabbé°\tvabbé\tlinguistic\tVolume=low\t_\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,vabbè,vabbè,vabbè,INTJ,_",
    ]
    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    assert "+0-0\tSPK1\t0\tvabbè\tvabbè\tlinguistic\t_\t_\t_\t_\t_\t_" in patch


def test_lang_merged_when_span_unchanged(tmp_path):
    """When span is unchanged, non-span feats from TSV are preserved and Lang=* is added."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        # span ends with "," → Intonation=weakly_rising; ProsodicLink is non-span-derived
        "0-0\tSPK1\t0\tlatin,\tlatin\tlinguistic\tIntonation=weakly_rising|ProsodicLink=Yes\tEnd=1.5\t_\t_\t_\t_",
    ]
    csv_rows = [
        # span identical to TSV → no span change; only Lang should be added
        '0-0,SPK1,0,0,1,"latin,",latin,latin,NOUN,Lang=lat',
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    patch = patch_path.read_text(encoding="utf-8")
    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")

    # Both TSV feats preserved, Lang added
    assert "Intonation=weakly_rising|ProsodicLink=Yes|Lang=lat" in patch
    # recap table cells escape `|` (it's the Markdown column separator)
    assert r"Intonation=weakly_rising\|ProsodicLink=Yes\|Lang=lat" in recap


def test_form_not_derivable_from_span_flagged_in_recap(tmp_path):
    """When form changes but span is unchanged, flag if form is not derivable from span."""
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    tsv_rows = [
        # form "ehme" is derivable from span "e::hm::e" (remove elongations)
        "0-0\tSPK1\t0\te::hm::e\tehme\tlinguistic\t_\t_\t_\t_\t_\t_",
        # form "ciao" is derivable from span "ciao." (remove intonation marker)
        "1-0\tSPK1\t1\tciao.\tciao\tlinguistic\tIntonation=Falling\t_\t_\t_\t_\t_",
    ]
    csv_rows = [
        # form corrected to "ehm" — NOT derivable from span "e::hm::e" → should flag ⚠️
        "0-0,SPK1,0,0,1,e::hm::e,ehm,ehm,INTJ,_",
        # form unchanged
        "1-0,SPK1,1,1,1,ciao.,ciao,ciao,INTJ,Intonation=Falling",
    ]

    write_crlf(tsv_path, TSV_HEADER, tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))

    recap = (tmp_path / "CONV001.vert.tsv.recap.md").read_text(encoding="utf-8")
    # Token 0-0: form change not derivable → flagged
    assert "⚠️ forma non derivabile dallo span" in recap
    assert "| `0-0` | _ | [MOD] modifica | [WARN] verifica manuale" in recap
    # Token 1-0: no change → not in recap
    assert "`1-0`" not in recap


def test_empty_csv_span_raises(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    bad_csv_rows = [
        "0-0,SPK1,0,0,1,,ciao,ciao,INTJ,Intonation=Falling",
    ]

    write_crlf(tsv_path, TSV_HEADER, [TSV_ROWS[0]])
    write_csv(csv_path, CSV_HEADER, bad_csv_rows)

    try:
        make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))
    except ValueError as exc:
        assert "empty `span`" in str(exc)
    else:
        raise AssertionError("Expected ValueError for empty CSV span")


def test_empty_tsv_form_raises(tmp_path):
    tsv_path = tmp_path / "CONV001.vert.tsv"
    csv_path = tmp_path / "wip" / "KIP" / "CONV001.csv"
    csv_path.parent.mkdir(parents=True)
    patch_path = tmp_path / "CONV001.vert.tsv.patch"

    bad_tsv_rows = [
        "0-0\tSPK1\t0\tciao.\t\tlinguistic\tIntonation=Falling\tEnd=1.5\t_\t_\t_\t_",
    ]
    csv_rows = [
        "0-0,SPK1,0,0,1,ciao.,ciao,ciao,INTJ,Intonation=Falling",
    ]

    write_crlf(tsv_path, TSV_HEADER, bad_tsv_rows)
    write_csv(csv_path, CSV_HEADER, csv_rows)

    try:
        make_patch.make_patch(str(csv_path), str(tsv_path), str(patch_path))
    except ValueError as exc:
        assert "empty `form`" in str(exc)
    else:
        raise AssertionError("Expected ValueError for empty TSV form")


def test_infer_tsv_path_from_wip_csv():
    csv_path = "/tmp/lemmatization-project/wip/KIP/BOA1007.csv"
    inferred = make_patch.infer_tsv_path(make_patch.Path(csv_path))
    assert inferred == make_patch.Path("/tmp/KIP/tsv/BOA1007.vert.tsv")


def test_batch_mode_processes_all_csvs_in_wip_subdir(tmp_path):
    project_root = tmp_path / "lemmatization-project"
    source_root = tmp_path
    wip_dir = project_root / "wip" / "KIP"
    tsv_dir = source_root / "KIP" / "tsv"

    wip_dir.mkdir(parents=True)
    tsv_dir.mkdir(parents=True)

    csv_rows_one = [
        "0-0,SPK1,0,0,1,ciao.,ciao,ciao,INTJ,Intonation=Falling",
    ]
    csv_rows_two = [
        "0-0,SPK1,0,0,1,{P},{P},_,_,_",
    ]
    tsv_rows_one = [
        "0-0\tSPK1\t0\tciao.\tciaoo\tlinguistic\tIntonation=Falling\tEnd=1.5\t_\t_\t_\t_",
    ]
    tsv_rows_two = [
        "0-0\tSPK1\t0\t(.)\t[PAUSE]\tshortpause\t_\t_\t_\t_\t_\t_",
    ]

    write_csv(wip_dir / "ONE.csv", CSV_HEADER, csv_rows_one)
    write_csv(wip_dir / "TWO.csv", CSV_HEADER, csv_rows_two)
    write_crlf(tsv_dir / "ONE.vert.tsv", TSV_HEADER, tsv_rows_one)
    write_crlf(tsv_dir / "TWO.vert.tsv", TSV_HEADER, tsv_rows_two)

    failures = make_patch.make_patches_in_dir(str(wip_dir))

    assert failures == 0
    assert (project_root / "patches" / "KIP" / "ONE.vert.tsv.patch").exists()
    assert (project_root / "patches" / "KIP" / "TWO.vert.tsv.patch").exists()
