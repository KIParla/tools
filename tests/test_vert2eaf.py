"""Tests for vert.tsv -> eaf reconstruction (serialize.vert_to_linear_rows / vert2eaf).

TU-level "# " (variation=unspecified) and "#_" (variation=all) markers both
round-trip losslessly — see the docstring of vert_to_linear_rows. This relies
on `unspecified` (explicit TU-level marker) and `yes` (derived bottom-up from
individually-marked tokens) being tracked as distinct languagevariation
values; collapsing them back into one would reintroduce the ambiguity.
"""

import json

from data import TranscriptionUnit, Transcript
from serialize import (
    conversation_to_conll,
    eaf2csv,
    read_csv,
    vert_to_linear_rows,
    vert2eaf,
)

VARIATION_CFG = {
    "variation_markers": {"hash_token": True, "dollar": True, "hash_doubtful": True},
}


def _make_transcript(annotations, cfg=None, speaker="SPK1"):
    """Build a small Transcript from raw Jefferson annotations, tokenized."""
    if cfg is None:
        cfg = VARIATION_CFG
    t = Transcript("TEST")
    for i, ann in enumerate(annotations):
        tu = TranscriptionUnit(i, speaker, i * 2.0, i * 2.0 + 1.5, 1.5, ann, cfg=cfg)
        tu.tokenize()
        t.add(tu)
    t.sort()
    return t


def _write_vert(transcript, tmp_path, name="test"):
    out = tmp_path / f"{name}.vert.tsv"
    conversation_to_conll(transcript, out)
    return out


# ---------------------------------------------------------------------------
# vert_to_linear_rows
# ---------------------------------------------------------------------------

class TestVertToLinearRows:

    def test_plain_italian_tu_round_trips(self, tmp_path):
        t = _make_transcript(["ciao come stai."])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert len(rows) == 1
        assert rows[0]["text"] == "ciao come stai."
        assert rows[0]["speaker"] == "SPK1"

    def test_elision_no_space(self, tmp_path):
        t = _make_transcript(["l'albero"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "l'albero"

    def test_prosodic_link_uses_equals(self, tmp_path):
        t = _make_transcript(["parola=dopo"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "parola=dopo"

    def test_token_level_hash_marker_preserved(self, tmp_path):
        t = _make_transcript(["ciao #word dopo"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "ciao #word dopo"

    def test_token_level_dollar_marker_preserved(self, tmp_path):
        t = _make_transcript(["ciao $word dopo"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "ciao $word dopo"

    def test_token_level_doubtful_marker_preserved(self, tmp_path):
        t = _make_transcript(["ciao #*word dopo"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "ciao #*word dopo"

    def test_tu_level_all_prefix_reconstructed(self, tmp_path):
        t = _make_transcript(["#_ ciao come stai"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"].startswith("#_ ")

    def test_tu_level_unspecified_prefix_reconstructed(self, tmp_path):
        """Explicit TU-level '# ' (undecidable attribution, no individually-
        marked tokens) round-trips: variation=unspecified tells vert2eaf to
        reconstruct the prefix."""
        t = _make_transcript(["# ciao come stai"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "# ciao come stai"

    def test_tu_level_yes_not_re_prefixed(self, tmp_path):
        """A TU with no explicit '# ' prefix, only an individually-marked
        token, must NOT get a synthesized '# ' prefix on reconstruction —
        the marker already round-trips via the token's own span."""
        t = _make_transcript(["ciao #word dopo"])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert rows[0]["text"] == "ciao #word dopo"
        assert not rows[0]["text"].startswith("# ")

    def test_begin_end_round_trip(self, tmp_path):
        t = _make_transcript(["ciao come stai."])
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert float(rows[0]["start"]) == 0.0
        assert float(rows[0]["end"]) == 1.5

    def test_multi_tu_preserves_order_and_speakers(self, tmp_path):
        t = Transcript("TEST")
        tu0 = TranscriptionUnit(0, "SPK1", 0.0, 1.0, 1.0, "ciao.", cfg=VARIATION_CFG)
        tu0.tokenize()
        tu1 = TranscriptionUnit(1, "SPK2", 1.0, 2.0, 1.0, "bene grazie,", cfg=VARIATION_CFG)
        tu1.tokenize()
        t.add(tu0)
        t.add(tu1)
        t.sort()
        vert = _write_vert(t, tmp_path)
        rows = vert_to_linear_rows(vert)
        assert [r["speaker"] for r in rows] == ["SPK1", "SPK2"]
        assert [r["text"] for r in rows] == ["ciao.", "bene grazie,"]


# ---------------------------------------------------------------------------
# vert2eaf — eaf structure
# ---------------------------------------------------------------------------

class TestVert2Eaf:

    def test_writes_tier_and_annotation(self, tmp_path):
        t = _make_transcript(["ciao come stai."])
        vert = _write_vert(t, tmp_path)
        out = tmp_path / "test.eaf"
        vert2eaf(vert, "audio.wav", out)
        assert out.is_file()

        from speach import elan
        doc = elan.read_eaf(out)
        tier_ids = {tier.ID for tier in doc}
        assert "SPK1" in tier_ids
        values = [anno.value for anno in doc["SPK1"].annotations]
        assert values == ["ciao come stai."]

    def test_translations_reattached(self, tmp_path):
        t = _make_transcript(["ciao come stai."])
        vert = _write_vert(t, tmp_path)

        translations = [{
            "tu_id": "10", "speaker": "SPK1_trad", "start": "0.0", "end": "1.5",
            "parent_tu_id": "0", "text": "hello how are you",
        }]
        trans_path = tmp_path / "test.translations.json"
        trans_path.write_text(json.dumps(translations), encoding="utf-8")

        out = tmp_path / "test.eaf"
        vert2eaf(vert, "audio.wav", out, translations_path=trans_path)

        from speach import elan
        doc = elan.read_eaf(out)
        tier_ids = {tier.ID for tier in doc}
        assert "SPK1_trad" in tier_ids
        trad_tier = doc["SPK1_trad"]
        assert trad_tier.parent.ID == "SPK1"
        values = [anno.value for anno in trad_tier.annotations]
        assert values == ["hello how are you"]


# ---------------------------------------------------------------------------
# Round-trip idempotency: eaf -> vert.tsv -> eaf -> vert.tsv
# ---------------------------------------------------------------------------

class TestRoundTripIdempotency:

    def test_second_pass_vert_matches_first(self, tmp_path):
        annotations = [
            "ciao come stai.",
            "l'albero",
            "parola=dopo",
            "ciao #word dopo",
            "ciao $word dopo",
            "#_ tutto questo e straniero",
        ]
        t = _make_transcript(annotations, speaker="SPK1")
        vert1 = _write_vert(t, tmp_path, name="pass1")

        eaf_path = tmp_path / "roundtrip.eaf"
        vert2eaf(vert1, "audio.wav", eaf_path)

        csv_path = tmp_path / "roundtrip.csv"
        eaf2csv(eaf_path, csv_path, {})

        transcript2, _translations = read_csv(csv_path, cfg=VARIATION_CFG)
        transcript2.sort()
        for tu in transcript2.transcription_units:
            tu.tokenize(VARIATION_CFG)

        vert2 = _write_vert(transcript2, tmp_path, name="pass2")

        import csv as csv_mod
        with vert1.open(encoding="utf-8") as f:
            rows1 = list(csv_mod.DictReader(f, delimiter="\t"))
        with vert2.open(encoding="utf-8") as f:
            rows2 = list(csv_mod.DictReader(f, delimiter="\t"))

        assert len(rows1) == len(rows2)
        for r1, r2 in zip(rows1, rows2):
            assert r1["form"] == r2["form"]
            assert r1["type"] == r2["type"]
            assert r1["jefferson_feats"] == r2["jefferson_feats"]
            assert r1["variation"] == r2["variation"]
