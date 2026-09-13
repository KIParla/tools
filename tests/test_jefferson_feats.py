from conftest import load_tool_module

jefferson_feats = load_tool_module("jefferson_feats")
feats_from_span = jefferson_feats.feats_from_span


class TestIntonation:

    def test_falling(self):
        assert feats_from_span("ciao.")["Intonation"] == "Falling"

    def test_weakly_rising(self):
        assert feats_from_span("ciao,")["Intonation"] == "WeaklyRising"

    def test_rising(self):
        assert feats_from_span("ciao?")["Intonation"] == "Rising"

    def test_hidden_behind_guess_paren(self):
        """"inserito.)" is "inserito." inside a guess span — the trailing
        ")" must not hide the "." from the intonation check."""
        assert feats_from_span("inserito.)")["Intonation"] == "Falling"

    def test_hidden_behind_overlap_and_volume(self):
        assert feats_from_span("lei?)°]") == {"Volume": "Low", "Intonation": "Rising"}

    def test_hidden_behind_guess_paren_weakly_rising(self):
        assert feats_from_span("(no,)")["Intonation"] == "WeaklyRising"


class TestInterrupted:

    def test_trailing_tilde(self):
        assert feats_from_span("u~")["Interrupted"] == "Yes"

    def test_leading_hyphen(self):
        assert feats_from_span("-lando")["Interrupted"] == "Yes"

    def test_hidden_behind_pace_marker(self):
        """"u~<" is an interrupted "u~" inside a (closing) pace span."""
        assert feats_from_span("u~<")["Interrupted"] == "Yes"

    def test_hidden_behind_overlap_and_pace(self):
        assert feats_from_span("[secon~<]")["Interrupted"] == "Yes"


class TestTruncated:

    def test_trailing_apostrophe(self):
        assert feats_from_span("bo'")["Truncated"] == "Yes"

    def test_leading_apostrophe(self):
        assert feats_from_span("'ndran")["Truncated"] == "Yes"

    def test_po_is_not_truncated(self):
        """"po'" is a real word (più/po'), not a truncation marker."""
        assert "Truncated" not in feats_from_span("po'")

    def test_hidden_behind_pace_and_guess(self):
        """"vede'<)" is a truncated "vede'" inside a pace span and a guess span."""
        assert feats_from_span("vede'<)")["Truncated"] == "Yes"

    def test_hidden_behind_guess_paren(self):
        assert feats_from_span("('damo)")["Truncated"] == "Yes"

    def test_hidden_behind_variation_marker(self):
        """"#'mbusse" is a truncated "'mbusse" with a #variation prefix."""
        assert feats_from_span("#'mbusse")["Truncated"] == "Yes"


class TestInterruptedAndTruncatedTogether:

    def test_truncated_start_interrupted_end(self):
        """A token can be truncated at one end and interrupted at the
        other — e.g. "'sti~": starts with "'" (Truncated), ends with "~"
        (Interrupted). Both flags must be set, not just one."""
        feats = feats_from_span("'sti~")
        assert feats["Truncated"] == "Yes"
        assert feats["Interrupted"] == "Yes"

    def test_hidden_behind_variation_marker_and_guess(self):
        feats = feats_from_span("('spe(r)~)")
        assert feats.get("Interrupted") == "Yes"


class TestVolume:

    def test_low_from_degree_marker(self):
        assert feats_from_span("°piano°")["Volume"] == "Low"

    def test_high_from_uppercase(self):
        assert feats_from_span("NO")["Volume"] == "High"

    def test_no_volume_by_default(self):
        assert "Volume" not in feats_from_span("ciao")

    def test_not_derivable_for_mid_span_token(self):
        """A token in the middle of a multi-word °...° span carries no
        literal ° in its own span — Volume can't be derived from this
        token alone (see make_patch.py's update_jefferson_feats, which
        accounts for this by not overwriting an inherited Volume)."""
        assert "Volume" not in feats_from_span("cambiato")


class TestEmptyAndNone:

    def test_empty_string(self):
        assert feats_from_span("") == {}

    def test_underscore_placeholder(self):
        assert feats_from_span("_") == {}
