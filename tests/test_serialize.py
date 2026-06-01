"""Tests for serialize.py (pipeline step 8)."""

import io
import csv
import json
import tempfile
from pathlib import Path

import dataflags as df
from data import TranscriptionUnit, Transcript
from serialize import (
    conversation_to_conll,
    build_json,
    write_json,
    write_translations,
    read_csv,
    process,
    VERT_FIELDNAMES,
    TRANSLATIONS_FIELDNAMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_simple_transcript(annotations=None):
    """Return a small Transcript, tokenized and ready to serialize."""
    if annotations is None:
        annotations = ["ciao come stai.", "bene grazie,"]
    t = Transcript("TEST")
    for i, ann in enumerate(annotations):
        tu = TranscriptionUnit(i, f"SPK{i}", i * 2.0, i * 2.0 + 1.5, 1.5, ann)
        tu.tokenize()
        t.add(tu)
    t.sort()
    return t


def _read_vert(path):
    """Read a vert.tsv into a list of dicts."""
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


# ---------------------------------------------------------------------------
# conversation_to_conll
# ---------------------------------------------------------------------------

class TestConversationToConll:

    def test_header_matches_fieldnames(self, tmp_path):
        t = _make_simple_transcript()
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert rows  # not empty
        # all expected columns present
        for col in VERT_FIELDNAMES:
            assert col in rows[0], f"Missing column: {col}"

    def test_one_row_per_token(self, tmp_path):
        t = _make_simple_transcript(["ciao come", "bene grazie"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        # "ciao come" = 2 tokens, "bene grazie" = 2 tokens
        assert len(rows) == 4

    def test_token_id_format(self, tmp_path):
        t = _make_simple_transcript(["uno due tre"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        token_ids = [r["token_id"] for r in rows]
        assert token_ids == ["0-0", "0-1", "0-2"]

    def test_id_is_index_within_tu(self, tmp_path):
        t = _make_simple_transcript(["uno due tre"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert [r["id"] for r in rows] == ["0", "1", "2"]

    def test_form_is_normalized(self, tmp_path):
        t = _make_simple_transcript(["ciao:"])  # prolongation stripped from form
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert rows[0]["form"] == "ciao"

    def test_intonation_in_jefferson_feats(self, tmp_path):
        t = _make_simple_transcript(["davvero?"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert "Intonation=rising" in rows[0]["jefferson_feats"]

    def test_falling_intonation_serialized(self, tmp_path):
        t = _make_simple_transcript(["capito."])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert "Intonation=falling" in rows[0]["jefferson_feats"]

    def test_no_intonation_is_underscore(self, tmp_path):
        t = _make_simple_transcript(["ciao"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert "Intonation" not in rows[0]["jefferson_feats"]

    def test_prolongation_field(self, tmp_path):
        t = _make_simple_transcript(["cia::o"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert rows[0]["prolongations"] != "_"
        assert "x2" in rows[0]["prolongations"]

    def test_align_begin_on_first_token(self, tmp_path):
        t = _make_simple_transcript(["uno due"])
        for tu in t.transcription_units:
            tu.add_token_features()
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert "Begin=" in rows[0]["align"]

    def test_align_end_on_last_token(self, tmp_path):
        t = _make_simple_transcript(["uno due"])
        for tu in t.transcription_units:
            tu.add_token_features()
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert "End=" in rows[-1]["align"]

    def test_excluded_tu_not_written(self, tmp_path):
        t = Transcript("T")
        tu_excl = TranscriptionUnit(0, "A", 0, 1, 1, "")   # empty → excluded
        tu_incl = TranscriptionUnit(1, "A", 1, 2, 1, "ciao")
        tu_incl.tokenize()
        t.add(tu_excl)
        t.add(tu_incl)
        t.sort()
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert all(r["tu_id"] == "1" for r in rows)

    def test_type_column_is_token_type_name(self, tmp_path):
        # A short pause embedded between words survives normalization.
        t = _make_simple_transcript(["ciao {P} bene"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        types = [r["type"] for r in rows]
        assert "shortpause" in types

    def test_variation_column_is_tu_non_ita_name(self, tmp_path):
        t = Transcript("T")
        tu = TranscriptionUnit(0, "A", 0, 1, 1, "# hola")
        tu.tokenize()
        t.add(tu)
        t.sort()
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        assert rows[0]["variation"] == "some"

    def test_lemma_upos_xpos_feats_deprel_are_underscore(self, tmp_path):
        t = _make_simple_transcript(["ciao"])
        out = tmp_path / "test.vert.tsv"
        conversation_to_conll(t, out)
        rows = _read_vert(out)
        for col in ["lemma", "upos", "xpos", "feats", "deprel"]:
            assert rows[0][col] == "_", f"{col} should be _"


# ---------------------------------------------------------------------------
# build_json / write_json
# ---------------------------------------------------------------------------

class TestBuildJson:

    def test_returns_dict(self):
        t = _make_simple_transcript()
        result = build_json(t)
        assert isinstance(result, dict)

    def test_transcript_id_present(self):
        t = _make_simple_transcript()
        result = build_json(t)
        assert result["transcript"] == "TEST"

    def test_tu_count(self):
        t = _make_simple_transcript(["ciao", "bene"])
        result = build_json(t)
        assert result["TUs"] == 2

    def test_removed_tu_count(self):
        t = Transcript("T")
        t.add(TranscriptionUnit(0, "A", 0, 1, 1, ""))    # excluded
        t.add(TranscriptionUnit(1, "A", 1, 2, 1, "ciao"))
        t.sort()
        t.transcription_units[1].tokenize()
        result = build_json(t)
        assert result["removed_TUs"] == 1

    def test_speakers_dict_present(self):
        t = _make_simple_transcript(["ciao", "bene"])
        result = build_json(t)
        assert "speakers" in result
        assert len(result["speakers"]) > 0

    def test_speaker_token_counts(self):
        t = Transcript("T")
        tu = TranscriptionUnit(0, "SPK", 0, 1, 1, "uno due tre")
        tu.tokenize()
        t.add(tu)
        t.sort()
        result = build_json(t)
        assert result["speakers"]["SPK"]["tokens"] == 3
        assert result["speakers"]["SPK"]["tokens-ling"] == 3

    def test_write_json_produces_valid_file(self, tmp_path):
        t = _make_simple_transcript()
        out = tmp_path / "test.json"
        write_json(t, out)
        with out.open(encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["transcript"] == "TEST"


# ---------------------------------------------------------------------------
# write_translations
# ---------------------------------------------------------------------------

class TestWriteTranslations:

    def test_writes_header_and_rows(self, tmp_path):
        rows = [
            {"tu_id": "0", "speaker": "Traduzione", "start": "0.0",
             "end": "1.0", "parent_tu_id": "1", "text": "hello"},
        ]
        out = tmp_path / "test.translations.tsv"
        write_translations(rows, out)
        with out.open() as f:
            reader = list(csv.DictReader(f, delimiter="\t"))
        assert len(reader) == 1
        assert reader[0]["text"] == "hello"
        assert reader[0]["parent_tu_id"] == "1"

    def test_column_order(self, tmp_path):
        rows = [{"tu_id": "0", "speaker": "T", "start": "0", "end": "1",
                 "parent_tu_id": "_", "text": "x"}]
        out = tmp_path / "test.translations.tsv"
        write_translations(rows, out)
        with out.open() as f:
            header = f.readline().strip().split("\t")
        assert header == TRANSLATIONS_FIELDNAMES


# ---------------------------------------------------------------------------
# read_csv
# ---------------------------------------------------------------------------

class TestReadCsv:

    def _write_csv(self, path, rows, extra_col=False):
        cols = ["tu_id", "speaker", "start", "end", "duration", "text"]
        if extra_col:
            cols.append("parent_tu_id")
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def test_builds_transcript(self, tmp_path):
        p = tmp_path / "conv.tsv"
        self._write_csv(p, [
            {"tu_id": 0, "speaker": "A", "start": 0, "end": 1, "duration": 1, "text": "ciao"},
            {"tu_id": 1, "speaker": "B", "start": 1, "end": 2, "duration": 1, "text": "bene"},
        ])
        t, trans = read_csv(p)
        assert len(t._tu_by_id) == 2
        assert trans == []

    def test_tiers_to_ignore_skipped(self, tmp_path):
        p = tmp_path / "conv.tsv"
        self._write_csv(p, [
            {"tu_id": 0, "speaker": "A",          "start": 0, "end": 1, "duration": 1, "text": "ciao"},
            {"tu_id": 1, "speaker": "Traduzione",  "start": 0, "end": 1, "duration": 1, "text": "hello"},
        ])
        cfg = {"tiers_to_ignore": ["Traduzione"]}
        t, trans = read_csv(p, cfg)
        assert len(t._tu_by_id) == 1
        assert 0 in t._tu_by_id

    def test_tiers_to_extract_collected(self, tmp_path):
        p = tmp_path / "conv.tsv"
        self._write_csv(p, [
            {"tu_id": 0, "speaker": "A",           "start": 0, "end": 1, "duration": 1, "text": "ciao"},
            {"tu_id": 1, "speaker": "Traduzione",   "start": 0, "end": 1, "duration": 1, "text": "hello"},
        ])
        cfg = {"tiers_to_extract": ["Traduzione"]}
        t, trans = read_csv(p, cfg)
        assert len(t._tu_by_id) == 1
        assert len(trans) == 1
        assert trans[0]["text"] == "hello"

    def test_stem_used_as_transcript_id(self, tmp_path):
        p = tmp_path / "myconv.tsv"
        self._write_csv(p, [
            {"tu_id": 0, "speaker": "A", "start": 0, "end": 1, "duration": 1, "text": "ciao"},
        ])
        t, _ = read_csv(p)
        assert t.tr_id == "myconv"


# ---------------------------------------------------------------------------
# process (end-to-end)
# ---------------------------------------------------------------------------

DEMO_CSV = Path("/Users/ludovica/Documents/KIParla/kiparla-tools/data/csv_demo/PBB004.csv")


class TestProcess:

    def test_produces_vert_and_json(self, tmp_path):
        if not DEMO_CSV.exists():
            import pytest; pytest.skip("demo CSV not available")
        result = process(DEMO_CSV, tmp_path)
        assert (tmp_path / "PBB004.vert.tsv").exists()
        assert (tmp_path / "PBB004.json").exists()

    def test_vert_has_correct_columns(self, tmp_path):
        if not DEMO_CSV.exists():
            import pytest; pytest.skip("demo CSV not available")
        process(DEMO_CSV, tmp_path)
        rows = _read_vert(tmp_path / "PBB004.vert.tsv")
        assert rows
        for col in VERT_FIELDNAMES:
            assert col in rows[0]

    def test_json_summary_structure(self, tmp_path):
        if not DEMO_CSV.exists():
            import pytest; pytest.skip("demo CSV not available")
        result = process(DEMO_CSV, tmp_path)
        assert result["transcript"] == "PBB004"
        assert result["TUs"] > 0
        assert "speakers" in result

    def test_token_ids_sequential_within_tu(self, tmp_path):
        if not DEMO_CSV.exists():
            import pytest; pytest.skip("demo CSV not available")
        process(DEMO_CSV, tmp_path)
        rows = _read_vert(tmp_path / "PBB004.vert.tsv")
        # For each tu, token ids should start at 0 and be sequential.
        from itertools import groupby
        for tu_id, group in groupby(rows, key=lambda r: r["tu_id"]):
            ids = [int(r["id"]) for r in group]
            assert ids == list(range(len(ids))), f"TU {tu_id}: non-sequential ids {ids}"
