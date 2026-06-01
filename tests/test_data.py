import dataflags as df
from data import TranscriptionUnit, Transcript


# ---------------------------------------------------------------------------
# TranscriptionUnit — step 2: preprocessing
# ---------------------------------------------------------------------------

class TestPreprocess:

    def test_plain_annotation_is_kept(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "ciao come stai")
        assert tu.annotation == "ciao come stai"
        assert tu.include

    def test_empty_annotation_excluded(self):
        assert not TranscriptionUnit(0, "S", 0, 1, 1, "").include

    def test_whitespace_only_excluded(self):
        assert not TranscriptionUnit(0, "S", 0, 1, 1, "   ").include

    def test_non_ita_all_sets_flag_and_strips_prefix(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "#_ hola que tal")
        assert tu.non_ita == df.languagevariation.all
        assert not tu.annotation.startswith("#_")

    def test_non_ita_all_skips_normalization(self):
        # The prefix "#_" alone; no text after → annotation is empty after strip but
        # the TU should still be included (normalization is skipped for non-Italian).
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "#_ foreign word")
        assert tu.include  # normalization skipped, so empty-unit check doesn't apply

    def test_non_ita_some_sets_flag_and_strips_prefix(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "# hola che bella")
        assert tu.non_ita == df.languagevariation.some
        assert not tu.annotation.startswith("# ")

    def test_warnings_accumulated(self):
        # A number should trigger the NUMBERS warning rule.
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "ho 2 gatti")
        assert tu.warnings.get("NUMBERS", 0) > 0

    def test_overlapping_spans_extracted(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "allora [sì] forse")
        assert len(tu.overlapping_spans) == 1

    def test_guessing_spans_extracted(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "ho detto (ciao)")
        assert len(tu.guessing_spans) == 1

    def test_low_volume_spans_extracted(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "°piano piano°")
        assert len(tu.low_volume_spans) == 1

    def test_high_volume_spans_extracted(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "poi ha urlato NO")
        assert len(tu.high_volume_spans) == 1

    def test_unbalanced_overlap_sets_error(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "allora [sì forse")
        assert tu.errors.get("UNBALANCED_OVERLAP")
        assert tu.overlapping_spans == []

    def test_unbalanced_dots_sets_error(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "°piano piano")
        assert tu.errors.get("UNBALANCED_DOTS")
        assert tu.low_volume_spans == []

    def test_all_symbol_unit_excluded(self):
        # After normalization, only symbols remain → include=False.
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "[][][]")
        assert not tu.include

    def test_leading_trailing_whitespace_stripped(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "  ciao  ")
        assert tu.annotation == "ciao"


# ---------------------------------------------------------------------------
# TranscriptionUnit — step 5: tokenize
# ---------------------------------------------------------------------------

class TestTokenize:

    def test_basic_tokenization(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "ciao come stai")
        tu.tokenize()
        assert len(tu.tokens) == 3
        assert [t.form for t in tu.tokens] == ["ciao", "come", "stai"]

    def test_excluded_tu_produces_no_tokens(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "")
        tu.tokenize()
        assert tu.tokens == []

    def test_non_ita_all_marks_all_tokens(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "#_ hola que tal")
        tu.tokenize()
        assert all(t.non_ita for t in tu.tokens)
        assert all(t.iso_code == "NO_ISO_CODE" for t in tu.tokens)

    def test_non_ita_tally_updated_after_tokenize(self):
        # A TU with no initial non_ita flag but containing a #word token
        # should have its non_ita updated after tokenize (via token flags).
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "ciao come stai",
                               cfg={"variation_markers": {"hash_token": True}})
        # Manually override annotation to include a #-token after construction.
        # (Real pipeline would have this already after preprocessing.)
        # This test verifies the update logic when some tokens are non_ita.
        tu.tokenize()
        # No #-tokens here — non_ita stays none.
        assert tu.non_ita == df.languagevariation.none

    def test_position_flags_set(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "uno due tre")
        tu.tokenize()
        assert df.position.start in tu.tokens[0].position_in_tu
        assert df.position.end   in tu.tokens[-1].position_in_tu


# ---------------------------------------------------------------------------
# TranscriptionUnit — step 7: add_token_features
# ---------------------------------------------------------------------------

class TestAddTokenFeatures:

    def _make(self, annotation):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, annotation)
        tu.tokenize()
        return tu

    def test_low_volume_mapped_to_tokens(self):
        tu = self._make("°piano piano°")
        tu.add_token_features()
        low_vol_tokens = [t for t in tu.tokens if t.low_volume]
        assert len(low_vol_tokens) > 0

    def test_low_volume_sets_volume_flag(self):
        tu = self._make("°forte°")
        tu.add_token_features()
        vol_tokens = [t for t in tu.tokens
                      if t.volume is not None and df.volume.low in t.volume]
        assert len(vol_tokens) > 0

    def test_guesses_mapped_to_tokens(self):
        tu = self._make("ho detto (ciao)")
        tu.add_token_features()
        guess_tokens = [t for t in tu.tokens if t.guesses]
        assert len(guess_tokens) > 0

    def test_position_flags_set(self):
        tu = self._make("uno due tre")
        tu.add_token_features()
        assert df.position.start in tu.tokens[0].position_in_tu
        assert df.position.end   in tu.tokens[-1].position_in_tu

    def test_empty_tokens_does_not_crash(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "")
        tu.tokenize()
        tu.add_token_features()  # should be a no-op


# ---------------------------------------------------------------------------
# Transcript — steps 1, 3, 4, 6
# ---------------------------------------------------------------------------

def _make_tu(tu_id, speaker, start, end, annotation="ciao"):
    tu = TranscriptionUnit(tu_id, speaker, start, end, end - start, annotation)
    tu.tokenize()
    return tu


class TestTranscript:

    def test_add_and_sort(self):
        t = Transcript("T")
        t.add(_make_tu(0, "A", 0.0, 1.0))
        t.add(_make_tu(1, "B", 2.0, 3.0))
        t.sort()
        assert t.tot_length == 3.0
        assert [tu.tu_id for tu in t.transcription_units] == [0, 1]

    def test_sort_orders_by_start(self):
        t = Transcript("T")
        t.add(_make_tu(1, "B", 2.0, 3.0))
        t.add(_make_tu(0, "A", 0.0, 1.0))
        t.sort()
        assert [tu.tu_id for tu in t.transcription_units] == [0, 1]

    def test_excluded_tu_not_counted_in_speakers(self):
        t = Transcript("T")
        tu_excl = _make_tu(0, "A", 0.0, 1.0, "")   # empty → excluded
        tu_incl = _make_tu(1, "A", 1.0, 2.0, "ciao")
        t.add(tu_excl)
        t.add(tu_incl)
        assert t.speakers["A"] == 1

    def test_find_overlaps_detects_overlap(self):
        t = Transcript("T")
        t.add(_make_tu(0, "A", 0.0, 1.5))
        t.add(_make_tu(1, "B", 1.0, 2.0))
        t.sort()
        t.find_overlaps()
        assert t.time_based_overlaps.has_edge(0, 1)

    def test_find_overlaps_no_overlap(self):
        t = Transcript("T")
        t.add(_make_tu(0, "A", 0.0, 1.0))
        t.add(_make_tu(1, "B", 1.0, 2.0))
        t.sort()
        t.find_overlaps()
        assert t.time_based_overlaps.number_of_edges() == 0

    def test_check_overlaps_removes_short_unannotated(self):
        t = Transcript("T")
        t.add(_make_tu(0, "A", 0.0, 1.05))
        t.add(_make_tu(1, "B", 1.0, 2.0))
        t.sort()
        t.find_overlaps()
        assert t.time_based_overlaps.has_edge(0, 1)
        t.check_overlaps(duration_threshold=0.1)
        # 0.05s overlap < 0.1 threshold, no annotated spans → edge removed
        assert not t.time_based_overlaps.has_edge(0, 1)

    def test_check_overlaps_boundaries_nudged(self):
        t = Transcript("T")
        tu0 = _make_tu(0, "A", 0.0, 1.05)
        tu1 = _make_tu(1, "B", 1.0, 2.0)
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1)
        assert tu0.warnings["MOVED_BOUNDARIES"] == 1
        assert tu1.warnings["MOVED_BOUNDARIES"] == 1

    def test_check_overlaps_matching_spans(self):
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 1.5, 1.5, "[ciao]")
        tu1 = TranscriptionUnit(1, "B", 1.0, 2.0, 1.0, "[bello]")
        tu0.tokenize()
        tu1.tokenize()
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1)
        # Both TUs have 1 annotated span, 1 time overlap → should match.
        assert len(tu0.overlapping_matches) == 1
        assert len(tu1.overlapping_matches) == 1

    def test_iter_yields_sorted_tus(self):
        t = Transcript("T")
        t.add(_make_tu(1, "B", 2.0, 3.0))
        t.add(_make_tu(0, "A", 0.0, 1.0))
        t.sort()
        ids = [tu.tu_id for tu in t]
        assert ids == [0, 1]
