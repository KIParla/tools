import pytest
from conftest import load_tool_module

# tokenize.py uses `import dataflags` (direct), so we must use the same
# module object here — not a separate load_tool_module copy.
import dataflags as df
from tokens import Token, tokenize_tu


# ===========================================================================
# Token classification
# ===========================================================================

class TestTokenClassification:

    def _tok(self, text, cfg=None):
        return Token(text, _cfg_variation=cfg or {})

    # --- anonymized ---
    def test_anonymized(self):
        t = self._tok("@nome")
        assert t.token_type == df.tokentype.anonymized
        assert t.form == "@nome"

    # --- short pause ---
    def test_shortpause(self):
        t = self._tok("{P}")
        assert t.token_type == df.tokentype.shortpause

    # --- non-verbal behavior ---
    def test_nvb(self):
        t = self._tok("{ride}")
        assert t.token_type == df.tokentype.nonverbalbehavior

    # --- unknown ---
    def test_unknown_single(self):
        t = self._tok("x")
        assert t.token_type == df.tokentype.unknown
        assert t.form == "x"
        assert t.syllables == 1

    def test_unknown_multiple_keeps_form(self):
        t = self._tok("xxx")
        assert t.token_type == df.tokentype.unknown
        assert t.form == "xxx"
        assert t.syllables == 3

    # --- linguistic ---
    def test_simple_word(self):
        t = self._tok("ciao")
        assert t.token_type == df.tokentype.linguistic
        assert t.form == "ciao"

    def test_word_with_intonation_falling(self):
        t = self._tok("ciao.")
        assert t.token_type == df.tokentype.linguistic
        assert t.intonation == df.intonation.falling
        assert "." not in t.form

    def test_word_with_intonation_rising(self):
        t = self._tok("ciao?")
        assert t.intonation == df.intonation.rising

    def test_word_with_intonation_weakly_rising(self):
        t = self._tok("ciao,")
        assert t.intonation == df.intonation.weakly_rising

    def test_word_lowercased(self):
        t = self._tok("CIAO")
        assert t.form == "ciao"
        assert t.volume == df.volume.high

    def test_prolongation_stripped_from_form(self):
        t = self._tok("ca::sa")
        assert t.form == "casa"
        assert t.prolongations == {1: 2}

    def test_prolongation_at_end(self):
        t = self._tok("ciao:")
        assert t.form == "ciao"
        assert t.prolongations == {3: 1}

    def test_multiple_prolongations(self):
        t = self._tok("c:a:sa")
        assert t.form == "casa"
        assert 0 in t.prolongations  # after 'c'
        assert 1 in t.prolongations  # after 'a'

    def test_interruption_trailing_dash(self):
        t = self._tok("par-")
        assert t.interruption is True

    def test_interruption_leading_dash(self):
        t = self._tok("-lando")
        assert t.interruption is True

    def test_truncation_trailing_apostrophe(self):
        t = self._tok("parol'")
        assert t.truncation is True

    def test_po_variant_not_truncation(self):
        t = self._tok("po'")
        assert t.token_type == df.tokentype.linguistic
        assert t.truncation is False

    def test_internal_dash_not_interruption(self):
        t = self._tok("anti-sociale")
        assert t.interruption is False
        assert t.token_type == df.tokentype.linguistic

    # --- error / warning ---
    def test_unrecognized_token_is_error(self):
        t = self._tok("123abc")
        assert t.token_type == df.tokentype.error

    def test_error_downgraded_to_warning_in_variation_context(self):
        cfg = {"hash_token": True}
        t = Token("#123abc", _cfg_variation=cfg)
        assert t.token_type == df.tokentype.warning

    # --- variation markers ---
    def test_hash_token_variation_when_enabled(self):
        cfg = {"hash_token": True}
        t = Token("#ciao", _cfg_variation=cfg)
        assert t.variation == df.tokenvariation.token
        assert t.non_ita is True
        assert t.iso_code == "NO_ISO_CODE"
        assert "ciao" in t.form

    def test_hash_token_not_stripped_when_disabled(self):
        t = Token("#ciao", _cfg_variation={})
        # # is stripped by classification (not a letter), so the token won't be linguistic
        assert t.token_type in (df.tokentype.error, df.tokentype.warning)

    def test_dollar_variation_when_enabled(self):
        cfg = {"dollar": True}
        t = Token("$ciao", _cfg_variation=cfg)
        assert t.variation == df.tokenvariation.emerging
        assert t.non_ortho is True
        assert "ciao" in t.form

    def test_hash_doubtful_variation_when_enabled(self):
        cfg = {"hash_doubtful": True}
        t = Token("#*ciao", _cfg_variation=cfg)
        assert t.variation == df.tokenvariation.doubtful
        assert t.non_ita is True

    def test_hash_doubtful_takes_priority_over_hash_token(self):
        cfg = {"hash_token": True, "hash_doubtful": True}
        t = Token("#*ciao", _cfg_variation=cfg)
        assert t.variation == df.tokenvariation.doubtful


# ===========================================================================
# tokenize_tu — splitting
# ===========================================================================

class TestTokenizeTU:

    def test_single_word(self):
        tokens = tokenize_tu("ciao", tu_id=0)
        assert len(tokens) == 1
        assert tokens[0].form == "ciao"

    def test_two_words(self):
        tokens = tokenize_tu("ciao mondo", tu_id=0)
        assert len(tokens) == 2

    def test_prosodic_link_sets_feature(self):
        tokens = tokenize_tu("ciao=mondo", tu_id=0)
        assert len(tokens) == 2
        assert tokens[0].prosodiclink is True
        assert tokens[1].prosodiclink is False

    def test_prosodic_link_between_spaces(self):
        tokens = tokenize_tu("fine= inizio", tu_id=0)
        assert tokens[0].prosodiclink is True

    def test_apostrophe_elision_splits(self):
        tokens = tokenize_tu("l'albero", tu_id=0)
        assert len(tokens) == 2
        assert tokens[0].spaceafter is False
        assert tokens[0].form == "l'"  # apostrophe kept as part of elided article
        assert tokens[1].form == "albero"

    def test_apostrophe_truncation_not_split(self):
        # "parol'" — apostrophe at end, no letter after → single token
        tokens = tokenize_tu("parol'", tu_id=0)
        assert len(tokens) == 1

    def test_position_flags(self):
        tokens = tokenize_tu("uno due tre", tu_id=0)
        assert df.position.start in tokens[0].position_in_tu
        assert df.position.end   in tokens[-1].position_in_tu
        assert df.position.start not in tokens[1].position_in_tu

    def test_pause_after_set_on_preceding_token(self):
        tokens = tokenize_tu("ciao {P} cosa", tu_id=0)
        # "ciao" precedes {P}
        ciao = tokens[0]
        assert ciao.pauseafter is True

    def test_pause_after_not_set_on_others(self):
        tokens = tokenize_tu("ciao {P} cosa", tu_id=0)
        cosa = tokens[2]
        assert cosa.pauseafter is False

    def test_span_positions(self):
        tokens = tokenize_tu("ciao mondo", tu_id=0)
        assert tokens[0].span == (0, 4)
        assert tokens[1].span == (5, 10)

    def test_apostrophe_split_span(self):
        tokens = tokenize_tu("l'albero", tu_id=0)
        assert tokens[0].span == (0, 2)   # "l'"
        assert tokens[1].span == (2, 8)   # "albero"

    # --- variation_context: all ---
    def test_all_variation_sets_language_on_tokens(self):
        tokens = tokenize_tu(
            "scherzando davvero", tu_id=0,
            variation_context=df.languagevariation.all,
        )
        for tok in tokens:
            assert tok.iso_code == "NO_ISO_CODE"
            assert tok.non_ita is True

    # --- config variation markers ---
    def test_variation_marker_in_token(self):
        cfg = {"hash_token": True}
        tokens = tokenize_tu("#ciao mondo", tu_id=0, cfg_variation=cfg)
        assert tokens[0].variation == df.tokenvariation.token
