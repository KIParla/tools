import csv
import unicodedata

import pytest

import dataflags as df
from data import TranscriptionUnit
from textutil import nfc

DECOMPOSED = "t\u0323-t\u0323alyan h\u0323abib"   # t/h + COMBINING DOT BELOW
COMPOSED = unicodedata.normalize("NFC", DECOMPOSED)


def test_fixture_is_really_decomposed():
    assert DECOMPOSED != COMPOSED


def test_nfc_composes_and_passes_non_str_through():
    assert nfc(DECOMPOSED) == COMPOSED
    assert nfc(None) is None


def test_transcription_unit_normalizes_annotation_to_nfc():
    tu = TranscriptionUnit(0, "S", 0, 1, 1, "#_ " + DECOMPOSED)
    assert unicodedata.is_normalized("NFC", tu.annotation)
    assert tu.annotation == COMPOSED


def test_decomposed_letters_tokenize_as_words_not_warnings():
    tu = TranscriptionUnit(0, "S", 0, 1, 1, "#_ " + DECOMPOSED)
    tu.tokenize()
    assert [t.form for t in tu.tokens] == COMPOSED.split()
    assert all(t.token_type == df.tokentype.linguistic for t in tu.tokens)


def test_eaf2csv_output_is_nfc_utf8_lf_no_bom(tmp_path):
    from pympi import Elan as EL
    from serialize import eaf2csv

    doc = EL.Eaf(author="test")
    doc.add_tier("PSB001")
    doc.add_annotation("PSB001", 0, 1000, "#_ " + DECOMPOSED)
    eaf = tmp_path / "X.eaf"
    doc.to_file(str(eaf))

    out = tmp_path / "X.csv"
    eaf2csv(eaf, out, {})
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert unicodedata.is_normalized("NFC", text)
    assert COMPOSED in text


def test_vert_and_translations_writers_use_lf(tmp_path):
    import serialize
    from data import Transcript

    tu = TranscriptionUnit(0, "S", 0.0, 1.0, 0, "ciao come stai")
    tu.tokenize()
    tr = Transcript("X")
    tr.add(tu)
    vert = tmp_path / "X.vert.tsv"
    serialize.conversation_to_conll(tr, vert)
    assert vert.read_bytes().endswith(b"\n")
    assert b"\r" not in vert.read_bytes()

    trans = tmp_path / "X.translations.tsv"
    serialize.write_translations(
        [{"tu_id": 1, "speaker": "S_trad", "start": "0", "end": "1",
          "parent_tu_id": 0, "text": "ciao"}], trans)
    assert b"\r" not in trans.read_bytes()
