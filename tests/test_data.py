import pytest

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

    def test_non_ita_unspecified_sets_flag_and_strips_prefix(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "# hola che bella")
        assert tu.non_ita == df.languagevariation.unspecified
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

    def test_word_internal_span_classified_as_reduction(self):
        """c(io)è is phonetic reduction, not a genuine guess span."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "c(io)è ciao")
        assert len(tu.reduction_spans) == 1
        assert len(tu.guessing_spans) == 0

    def test_multiword_span_stays_a_guess(self):
        """A guess span with whitespace inside is never word-internal."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "(non lo so) ciao")
        assert len(tu.guessing_spans) == 1
        assert len(tu.reduction_spans) == 0

    def test_low_volume_spans_extracted(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "°piano piano°")
        assert len(tu.low_volume_spans) == 1

    def test_high_volume_spans_extracted(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "poi ha urlato NO")
        assert len(tu.high_volume_spans) == 1

    def test_overlapping_spans_bare_nvb_always_kept(self):
        """An overlap span that is *only* an NVB tag survives intact: NVB at
        the edge of `[...]` is always exempt from switch_NVB, unconditionally
        -- independent of nvb_participates_in_overlaps, same as shortpause.
        (That flag only governs whether an NVB-only TU *participates* in the
        overlap graph at the TU/clique level -- see check_overlaps tests.)"""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "[((ride))]")
        assert len(tu.overlapping_spans) == 1
        assert tu.annotation == "[((ride))]"

        tu2 = TranscriptionUnit(
            0, "S", 0, 1, 1, "[((ride))]",
            cfg={"overlaps": {"nvb_participates_in_overlaps": True}},
        )
        assert len(tu2.overlapping_spans) == 1
        assert tu2.annotation == "[((ride))]"

    def test_overlapping_spans_shortpause_at_edge_always_kept(self):
        """A shortpause at the edge of an overlap span (`[(.)`, `(.)]`) is
        never relocated out — this is unconditional, independent of
        nvb_participates_in_overlaps (see switch_NVB's docstring)."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "prima [(.)] dopo")
        assert len(tu.overlapping_spans) == 1
        assert tu.annotation == "prima [(.)] dopo"

        tu2 = TranscriptionUnit(
            0, "S", 0, 1, 1, "prima [(.)] dopo",
            cfg={"overlaps": {"nvb_participates_in_overlaps": True}},
        )
        assert len(tu2.overlapping_spans) == 1
        assert tu2.annotation == "prima [(.)] dopo"

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

    def test_non_ita_yes_derived_from_token_marker(self):
        """No TU-level '# ' prefix; a per-token #word marker alone derives
        `yes`, not `unspecified` — these must stay distinguishable so
        vert2eaf knows whether to reconstruct a '# ' prefix."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "ciao #come stai",
                               cfg={"variation_markers": {"hash_token": True}})
        tu.tokenize()
        assert tu.non_ita == df.languagevariation.yes

    def test_non_ita_unspecified_survives_tokenize(self):
        """An explicit TU-level '# ' prefix stays `unspecified` through
        tokenize(), even though no individual token ends up non_ita."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "# ciao come stai",
                               cfg={"variation_markers": {"hash_token": True}})
        tu.tokenize()
        assert tu.non_ita == df.languagevariation.unspecified
        assert not any(t.non_ita for t in tu.tokens)

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

    def test_whitelisted_reduction_sets_reduced_flag(self):
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "c(io)è ciao",
                                cfg={"reduction_words": ["cioè"]})
        tu.tokenize()
        tu.add_token_features()
        reduced_tokens = [t for t in tu.tokens if t.reduced]
        assert len(reduced_tokens) == 1
        assert reduced_tokens[0].form == "cioè"
        assert reduced_tokens[0].guesses == {}   # not double-counted as a guess

    def test_non_whitelisted_word_internal_span_falls_back_to_guess(self):
        """bu(o)no is word-internal but not on the (empty) whitelist here,
        so it must still surface as a guess rather than being dropped."""
        tu = self._make("bu(o)no ciao")
        tu.add_token_features()
        assert not any(t.reduced for t in tu.tokens)
        guess_tokens = [t for t in tu.tokens if t.guesses]
        assert len(guess_tokens) == 1
        assert guess_tokens[0].form == "buono"

    def test_whitelisted_multitoken_reduction_marks_both_tokens(self):
        """m(e l)o splits into two tokens (me, lo); if the reconstructed
        phrase "me lo" is whitelisted, both tokens get Reduced=Yes."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "m(e l)o segno",
                                cfg={"reduction_words": ["me lo"]})
        tu.tokenize()
        tu.add_token_features()
        assert [t.form for t in tu.tokens] == ["me", "lo", "segno"]
        assert tu.tokens[0].reduced and tu.tokens[1].reduced
        assert not tu.tokens[2].reduced
        assert tu.tokens[0].guesses == {} and tu.tokens[1].guesses == {}

    def test_non_whitelisted_multitoken_span_falls_back_to_linked_guesses(self):
        """Without 'me lo' on the whitelist, both touched tokens fall back
        to guesses sharing the same span id (they're one guess group)."""
        tu = TranscriptionUnit(0, "S", 0, 1, 1, "m(e l)o segno")
        tu.tokenize()
        tu.add_token_features()
        assert not tu.tokens[0].reduced and not tu.tokens[1].reduced
        assert set(tu.tokens[0].guesses) == set(tu.tokens[1].guesses)
        assert tu.tokens[0].guesses and tu.tokens[1].guesses

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

    def test_check_overlaps_nvb_only_tu_gets_matched_when_annotated(self):
        """An NVB-only TU with a genuinely annotated `[...]` overlap span,
        that really does time-overlap with another TU, gets a proper match
        (real clique id, no error/warning) -- regardless of
        nvb_participates_in_overlaps. That flag only affects *unannotated*
        NVB/shortpause-only overlap events (see the next two tests)."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 2.0, 2.0, "ciao [come stai]")
        tu1 = TranscriptionUnit(1, "B", 1.0, 1.5, 0.5, "[((ride))]")
        tu0.tokenize()
        tu1.tokenize()
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1, nvb_participates=False)

        assert tu0.errors == {}
        assert tu0.warnings == {}
        assert len(tu0.overlapping_matches) == 1
        assert tu1.errors == {}
        # META_TAGS fires unconditionally for any NVB tag encountered.
        assert dict(tu1.warnings) == {"META_TAGS": 1}
        assert len(tu1.overlapping_matches) == 1

        tu0.add_token_features()
        tu1.add_token_features()
        matched_id = next(iter(tu0.overlapping_matches.values()))
        assert matched_id != "?"
        assert tu0.tokens[-1].overlaps == {matched_id: (0, 4)}   # "stai" (ordinary token: letters-only range)
        # NVB is atomic -- always entirely in or out of an overlap, never
        # interrupted mid-marker -- so it gets the "X" sentinel (whole token)
        # instead of a char range.
        assert tu1.tokens[0].overlaps == {matched_id: "X"}    # "((ride))"

    def test_check_overlaps_shortpause_gets_whole_token_feature(self):
        """Same as the NVB case above, for a shortpause embedded in a TU
        with other content: it's atomic too, so it gets the "X" sentinel
        (whole token) rather than a char range (it has no letters at all, so
        a letters-only range would have been empty/invisible)."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 2.0, 2.0, "ciao [come stai]")
        tu1 = TranscriptionUnit(1, "B", 1.0, 1.5, 0.5, "boh [(.)] mah")
        tu0.tokenize()
        tu1.tokenize()
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1, nvb_participates=False)

        assert tu0.errors == {}
        assert tu1.errors == {}
        assert len(tu0.overlapping_matches) == 1
        assert len(tu1.overlapping_matches) == 1

        tu0.add_token_features()
        tu1.add_token_features()
        matched_id = next(iter(tu0.overlapping_matches.values()))
        pause_tok = next(t for t in tu1.tokens if t.form == "(.)")
        assert pause_tok.overlaps == {matched_id: "X"}

    def test_check_overlaps_unannotated_nvb_overlap_removable_by_default(self):
        """An NVB-only TU with *no* annotated overlap span, that happens to
        time-overlap with another TU that also didn't annotate it, is
        treated as noise by default (nvb_participates_in_overlaps=False):
        a MISMATCHING_OVERLAPS warning, no error, no feature assigned."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 2.0, 2.0, "ciao come stai")
        tu1 = TranscriptionUnit(1, "B", 1.0, 1.5, 0.5, "((ride))")
        tu0.tokenize()
        tu1.tokenize()
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1, nvb_participates=False)

        assert tu0.warnings["MISMATCHING_OVERLAPS"] is True
        assert tu0.errors == {}
        assert tu0.overlapping_matches == {}
        assert tu1.overlapping_matches == {}

    def test_check_overlaps_unannotated_nvb_overlap_errors_when_participates(self):
        """Same as above, but with nvb_participates_in_overlaps=True: the
        module now considers NVB overlaps real, so a missing annotation for
        a genuine time-based overlap is an error, not silently removable."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 2.0, 2.0, "ciao come stai")
        tu1 = TranscriptionUnit(1, "B", 1.0, 1.5, 0.5, "((ride))")
        tu0.tokenize()
        tu1.tokenize()
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1, nvb_participates=True)

        assert tu0.errors["OVERLAPS:MISSING_ANNOTATION"] is True
        assert "MISMATCHING_OVERLAPS" not in tu0.warnings

    def test_check_overlaps_drops_smallest_when_more_times_than_spans(self):
        """Real case (ParlaBZ BZA5003 tu_id 331): the middle TU has one
        annotated span but two incidental sub-threshold time overlaps (one
        with each neighbor) — e.g. boundary-rounding touches, not real
        overlaps. Both are individually "removable" (< duration_threshold),
        but since only one needs to go to reconcile 2 times vs 1 span, the
        smaller-duration one (the neighbor-B touch) should be dropped and
        the larger-duration one (the neighbor-A touch, matching the
        annotated span's position) kept — instead of erroring out just
        because more candidates were removable than needed.
        """
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "B", 0.0, 0.397, 0.397, "ah un esa[me],")
        tu1 = TranscriptionUnit(1, "A", 0.332, 3.442, 3.11, "[di] una cosa")
        tu2 = TranscriptionUnit(2, "B", 3.432, 3.812, 0.38, "ah,")
        for tu in (tu0, tu1, tu2):
            tu.tokenize()
            t.add(tu)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1)

        assert "MISMATCHING_OVERLAPS" not in tu1.errors
        assert tu1.warnings["MISMATCHING_OVERLAPS"] == 1
        assert len(tu1.overlapping_matches) == 1

        # The kept clique must be the one shared with tu0 (the larger,
        # 0.065s overlap), not tu2 (the smaller, 0.01s overlap).
        expected_clique_id = tu0.overlapping_times[(1,)][2]
        matched_clique_id = next(iter(tu1.overlapping_matches.values()))
        assert matched_clique_id == expected_clique_id

    def test_check_overlaps_still_errors_when_not_enough_removable(self):
        """If there aren't enough removable candidates to reconcile the
        mismatch (e.g. the extra overlap is long, non-nvb), it must still
        be reported as an error rather than guessed at."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "B", 0.0, 1.0, 1.0, "ah un esa[me],")
        tu1 = TranscriptionUnit(1, "A", 0.5, 3.5, 3.0, "[di] una cosa")
        tu2 = TranscriptionUnit(2, "B", 3.0, 4.0, 1.0, "ah,")
        for tu in (tu0, tu1, tu2):
            tu.tokenize()
            t.add(tu)
        t.sort()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1)

        assert tu1.errors.get("MISMATCHING_OVERLAPS") is True

    def test_stretch_disabled_by_default_leaves_gap_and_errors(self):
        """Real case (ParlaBZ BXA4001 tu_id 659): an annotated overlap span
        whose boundary exactly touches (0-gap) the next TU. With
        stretch_threshold=0 (default), nothing is changed and it's still
        an unresolvable OVERLAPS:MISSING_TIME."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 2.522, 2.522, "sì. con::, tipo un gancio in fer[ro].")
        tu1 = TranscriptionUnit(1, "A", 2.522, 3.862, 1.34, "[di] metallo,")
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.stretch_missing_overlaps(0.0)
        assert tu0.start == 0.0 and tu0.end == 2.522
        assert tu1.start == 2.522 and tu1.end == 3.862
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1)
        assert tu0.errors.get("OVERLAPS:MISSING_TIME") is True

    def test_stretch_creates_overlap_for_touching_boundary(self):
        """Same case, with stretch_threshold enabled: the exact-touch gap
        (0.0s) is within threshold and there's exactly one neighbor, so the
        boundary is nudged and the overlap resolves cleanly."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 2.522, 2.522, "sì. con::, tipo un gancio in fer[ro].")
        tu1 = TranscriptionUnit(1, "A", 2.522, 3.862, 1.34, "[di] metallo,")
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.stretch_missing_overlaps(0.01)

        assert tu0.end > 2.522
        assert tu1.start < 2.522
        assert tu0.end - tu1.start == pytest.approx(0.01)  # new overlap == stretch_threshold
        assert tu0.warnings["STRETCHED_BOUNDARIES"] == 1
        assert tu1.warnings["STRETCHED_BOUNDARIES"] == 1

        tu0.tokenize()
        tu1.tokenize()
        t.find_overlaps()
        t.check_overlaps(duration_threshold=0.1)
        assert "OVERLAPS:MISSING_TIME" not in tu0.errors

    def test_stretch_skips_gap_beyond_threshold(self):
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 1.0, 1.0, "fer[ro].")
        tu1 = TranscriptionUnit(1, "A", 1.5, 2.0, 0.5, "[di] metallo,")
        t.add(tu0)
        t.add(tu1)
        t.sort()
        t.stretch_missing_overlaps(0.1)  # gap is 0.5s, above threshold
        assert tu0.end == 1.0
        assert tu1.start == 1.5

    def test_stretch_skips_ambiguous_both_neighbors_in_range(self):
        """If both neighbors are close enough, don't guess which one the
        annotated span actually refers to."""
        t = Transcript("T")
        tu0 = TranscriptionUnit(0, "A", 0.0, 1.0, 1.0, "ciao")
        tu1 = TranscriptionUnit(1, "B", 1.02, 2.0, 0.98, "[di] una cosa")
        tu2 = TranscriptionUnit(2, "A", 2.03, 3.0, 0.97, "ok")
        for tu in (tu0, tu1, tu2):
            t.add(tu)
        t.sort()
        t.stretch_missing_overlaps(0.1)
        assert tu0.end == 1.0
        assert tu1.start == 1.02 and tu1.end == 2.0
        assert tu2.start == 2.03

    def test_iter_yields_sorted_tus(self):
        t = Transcript("T")
        t.add(_make_tu(1, "B", 2.0, 3.0))
        t.add(_make_tu(0, "A", 0.0, 1.0))
        t.sort()
        ids = [tu.tu_id for tu in t]
        assert ids == [0, 1]
