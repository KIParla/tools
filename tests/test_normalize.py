from conftest import load_tool_module

normalize = load_tool_module("normalize")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _warning_rule(name, fn=None, enabled=True):
    """Build a ValidationRule with a warning-signature function."""
    if fn is None:
        fn = lambda s: (0, s)
    return normalize.ValidationRule(name=name, function=fn, enabled_by_default=enabled)


def _error_rule(name, fn=None, enabled=True):
    """Build a ValidationRule with an error-signature function."""
    if fn is None:
        fn = lambda s: True   # valid by default
    return normalize.ValidationRule(name=name, function=fn, enabled_by_default=enabled)


class _with_rules:
    """Context manager that temporarily appends rules to a registry list."""
    def __init__(self, registry, *rules):
        self.registry = registry
        self.rules = list(rules)

    def __enter__(self):
        self.registry.extend(self.rules)
        return self

    def __exit__(self, *_):
        for rule in self.rules:
            self.registry.remove(rule)


# ---------------------------------------------------------------------------
# Empty pipeline
# ---------------------------------------------------------------------------

def test_empty_pipeline_returns_annotation_unchanged():
    annotation = "ciao come stai?"
    result, warnings, errors = normalize.validate_and_normalize(annotation)
    assert result == annotation
    assert warnings == {}
    assert errors == {}


def test_empty_pipeline_with_explicit_config():
    annotation = "qualcosa"
    result, warnings, errors = normalize.validate_and_normalize(annotation, config={"NONEXISTENT": False})
    assert result == annotation
    assert warnings == {}
    assert errors == {}


def test_none_config_is_equivalent_to_empty_dict():
    annotation = "test"
    result_none, w1, e1 = normalize.validate_and_normalize(annotation, config=None)
    result_empty, w2, e2 = normalize.validate_and_normalize(annotation, config={})
    assert result_none == result_empty
    assert w1 == w2
    assert e1 == e2


# ---------------------------------------------------------------------------
# Warning rules
# ---------------------------------------------------------------------------

def test_warning_rule_modifies_annotation():
    rule = _warning_rule("EXCLAIM", fn=lambda s: (1, s + "!"))
    with _with_rules(normalize.WARNING_RULES, rule):
        result, warnings, _ = normalize.validate_and_normalize("ciao")
    assert result == "ciao!"
    assert warnings == {"EXCLAIM": 1}


def test_warning_rule_not_in_warnings_when_zero_substitutions():
    rule = _warning_rule("NOOP", fn=lambda s: (0, s))
    with _with_rules(normalize.WARNING_RULES, rule):
        _, warnings, _ = normalize.validate_and_normalize("ciao")
    assert "NOOP" not in warnings


def test_warning_rule_disabled_via_config():
    rule = _warning_rule("EXCLAIM", fn=lambda s: (1, s + "!"), enabled=True)
    with _with_rules(normalize.WARNING_RULES, rule):
        result, warnings, _ = normalize.validate_and_normalize("ciao", config={"EXCLAIM": False})
    assert result == "ciao"
    assert "EXCLAIM" not in warnings


def test_warning_rule_enabled_via_config_overrides_default_off():
    rule = _warning_rule("EXCLAIM", fn=lambda s: (1, s + "!"), enabled=False)
    with _with_rules(normalize.WARNING_RULES, rule):
        result, warnings, _ = normalize.validate_and_normalize("ciao", config={"EXCLAIM": True})
    assert result == "ciao!"
    assert warnings == {"EXCLAIM": 1}


def test_warning_counts_accumulate_for_same_name():
    """Two rules sharing a name should sum their substitution counts."""
    rule1 = _warning_rule("SHARED", fn=lambda s: (2, s))
    rule2 = _warning_rule("SHARED", fn=lambda s: (3, s))
    with _with_rules(normalize.WARNING_RULES, rule1, rule2):
        _, warnings, _ = normalize.validate_and_normalize("x")
    assert warnings["SHARED"] == 5


def test_warning_rules_applied_in_registry_order():
    log = []
    rule1 = _warning_rule("FIRST",  fn=lambda s: (log.append(1) or (0, s)))
    rule2 = _warning_rule("SECOND", fn=lambda s: (log.append(2) or (0, s)))
    with _with_rules(normalize.WARNING_RULES, rule1, rule2):
        normalize.validate_and_normalize("x")
    assert log == [1, 2]


def test_each_warning_rule_receives_output_of_previous():
    """Rules form a chain: each sees the result of the one before it."""
    rule1 = _warning_rule("STEP1", fn=lambda s: (1, s + "A"))
    rule2 = _warning_rule("STEP2", fn=lambda s: (1, s + "B"))
    with _with_rules(normalize.WARNING_RULES, rule1, rule2):
        result, _, _ = normalize.validate_and_normalize("X")
    assert result == "XAB"


# ---------------------------------------------------------------------------
# Error rules
# ---------------------------------------------------------------------------

def test_error_rule_records_error_when_invalid():
    rule = _error_rule("ALWAYS_FAILS", fn=lambda s: False)
    with _with_rules(normalize.ERROR_RULES, rule):
        _, _, errors = normalize.validate_and_normalize("anything")
    assert errors.get("ALWAYS_FAILS") is True


def test_error_rule_absent_from_errors_when_valid():
    rule = _error_rule("ALWAYS_PASSES", fn=lambda s: True)
    with _with_rules(normalize.ERROR_RULES, rule):
        _, _, errors = normalize.validate_and_normalize("anything")
    assert "ALWAYS_PASSES" not in errors


def test_error_rule_disabled_via_config():
    rule = _error_rule("ALWAYS_FAILS", fn=lambda s: False, enabled=True)
    with _with_rules(normalize.ERROR_RULES, rule):
        _, _, errors = normalize.validate_and_normalize("anything", config={"ALWAYS_FAILS": False})
    assert "ALWAYS_FAILS" not in errors


def test_error_rule_sees_normalized_annotation():
    """Error rules run after all warning rules, on the final normalized text."""
    seen = []
    warning_rule = _warning_rule("TRANSFORM", fn=lambda s: (1, "CLEAN"))
    error_rule   = _error_rule("INSPECT",    fn=lambda s: seen.append(s) or True)
    with _with_rules(normalize.WARNING_RULES, warning_rule):
        with _with_rules(normalize.ERROR_RULES, error_rule):
            normalize.validate_and_normalize("RAW")
    assert seen == ["CLEAN"]


# ---------------------------------------------------------------------------
# Return value structure
# ---------------------------------------------------------------------------

def test_return_type_is_three_tuple():
    result = normalize.validate_and_normalize("test")
    assert len(result) == 3
    text, warnings, errors = result
    assert isinstance(text, str)
    assert isinstance(warnings, dict)
    assert isinstance(errors, dict)


# ===========================================================================
# Text normalization functions
# ===========================================================================

# ---------------------------------------------------------------------------
# remove_spaces
# ---------------------------------------------------------------------------

def test_remove_spaces_double_space():
    assert normalize.remove_spaces("ci  si  ") == (2, "ci si")

def test_remove_spaces_leading_trailing():
    assert normalize.remove_spaces("  ciao") == (1, "ciao")
    assert normalize.remove_spaces("ciao  ") == (1, "ciao")

def test_remove_spaces_no_change():
    assert normalize.remove_spaces("ciao come stai") == (0, "ciao come stai")

def test_remove_spaces_tabs_and_newlines():
    n, result = normalize.remove_spaces("ciao\tcosa\nfai")
    assert n == 2
    assert result == "ciao cosa fai"


# ---------------------------------------------------------------------------
# meta_tag
# ---------------------------------------------------------------------------

def test_meta_tag_shortpause_unchanged():
    assert normalize.meta_tag("(.) ciao") == (0, "(.) ciao")

def test_meta_tag_comment():
    assert normalize.meta_tag("ciao ((bla bla)) ciao") == (1, "ciao ((bla_bla)) ciao")

def test_meta_tag_no_change():
    n, result = normalize.meta_tag("ciao come stai")
    assert n == 0
    assert result == "ciao come stai"

def test_meta_tag_spaces_become_underscores():
    _, result = normalize.meta_tag("((parla veloce))")
    assert result == "((parla_veloce))"


# ---------------------------------------------------------------------------
# check_spaces
# ---------------------------------------------------------------------------

def test_check_spaces_after_open_bracket():
    assert normalize.check_spaces("[ ciao]") == (1, "[ciao]")
    assert normalize.check_spaces("bla[ ciao]") == (1, "bla [ciao]")

def test_check_spaces_before_close_bracket():
    assert normalize.check_spaces("[ciao ]") == (1, "[ciao]")
    assert normalize.check_spaces("[ciao ]bla") == (1, "[ciao] bla")

def test_check_spaces_before_punctuation():
    assert normalize.check_spaces("ciao ,bla") == (1, "ciao, bla")
    assert normalize.check_spaces("ciao ,") == (1, "ciao,")

def test_check_spaces_no_change():
    assert normalize.check_spaces("[ciao] come stai.") == (0, "[ciao] come stai.")


# ---------------------------------------------------------------------------
# remove_pauses
# ---------------------------------------------------------------------------

def test_remove_pauses_leading():
    assert normalize.remove_pauses("(.) ciao") == (1, "ciao")

def test_remove_pauses_trailing():
    assert normalize.remove_pauses("ciao (.)") == (1, "ciao")

def test_remove_pauses_both():
    assert normalize.remove_pauses("(.) ciao (.)") == (2, "ciao")

def test_remove_pauses_internal_not_removed():
    assert normalize.remove_pauses("ciao (.) ciao") == (0, "ciao (.) ciao")

def test_remove_pauses_with_bracket():
    assert normalize.remove_pauses("[(.) casa") == (1, "[casa")
    assert normalize.remove_pauses("casa (.) >") == (1, "casa>")

def test_remove_pauses_no_pause():
    assert normalize.remove_pauses("(a) casa") == (0, "(a) casa")


# ---------------------------------------------------------------------------
# remove_prosodiclinks / space_prosodiclink
# ---------------------------------------------------------------------------

def test_remove_prosodiclinks_leading():
    n, result = normalize.remove_prosodiclinks("= ciao")
    assert n == 1
    assert result == "ciao"

def test_remove_prosodiclinks_trailing():
    n, result = normalize.remove_prosodiclinks("ciao =")
    assert n == 1
    assert result == "ciao"

def test_space_prosodiclink_space_before():
    # ' =' is consumed; trailing space stays until next call
    assert normalize.space_prosodiclink("ciao =cosa") == (1, "ciao=cosa")

def test_space_prosodiclink_space_after():
    assert normalize.space_prosodiclink("ciao= cosa") == (1, "ciao=cosa")

def test_space_prosodiclink_no_change():
    assert normalize.space_prosodiclink("ciao=cosa") == (0, "ciao=cosa")


# ---------------------------------------------------------------------------
# overlap_prolongations
# ---------------------------------------------------------------------------

def test_overlap_prolongations_fixes():
    assert normalize.overlap_prolongations("questo:[::") == (1, "quest[o:::")

def test_overlap_prolongations_no_change():
    assert normalize.overlap_prolongations("quest[o::") == (0, "quest[o::")


# ---------------------------------------------------------------------------
# clean_non_jefferson_symbols
# ---------------------------------------------------------------------------

def test_clean_non_jefferson_removes_unknown():
    n, result = normalize.clean_non_jefferson_symbols("ciao! come stai%")
    assert n == 2
    assert result == "ciao come stai"

def test_clean_non_jefferson_keeps_allowed():
    n, result = normalize.clean_non_jefferson_symbols("ciao [come] {P} stai?")
    assert n == 0
    assert result == "ciao [come] {P} stai?"

def test_clean_non_jefferson_keeps_doubtful_marker():
    # '*' is half of the '#*word' doubtful-variation marker (tokens.py's
    # hash_doubtful) and must survive normalization like '$'/'#' already do.
    n, result = normalize.clean_non_jefferson_symbols("ciao #*word dopo")
    assert n == 0
    assert result == "ciao #*word dopo"


# ---------------------------------------------------------------------------
# replace_che / replace_po / replace_pero
# ---------------------------------------------------------------------------

def test_replace_che_perche():
    assert normalize.replace_che("perchè") == (1, "perché")

def test_replace_che_finche():
    assert normalize.replace_che("finchè") == (1, "finché")

def test_replace_che_ne():
    assert normalize.replace_che("nè") == (1, "né")

def test_replace_che_no_change():
    assert normalize.replace_che("ciao") == (0, "ciao")

def test_replace_po_basic():
    assert normalize.replace_po("pò") == (1, "po'")

def test_replace_po_with_prolongation():
    assert normalize.replace_po("p:ò") == (1, "p:o'")

def test_replace_po_no_change():
    assert normalize.replace_po("ciao") == (0, "ciao")

def test_replace_pero_basic():
    assert normalize.replace_pero("pero'") == (1, "però")

def test_replace_pero_puo():
    assert normalize.replace_pero("puo'") == (1, "può")

def test_replace_pero_no_change():
    assert normalize.replace_pero("ciao") == (0, "ciao")


# ---------------------------------------------------------------------------
# check_numbers
# ---------------------------------------------------------------------------

def test_check_numbers_single():
    n, result = normalize.check_numbers("sono 2 gatti")
    assert n == 1
    assert result == "sono due gatti"

def test_check_numbers_multiple():
    n, result = normalize.check_numbers("1 e 2")
    assert n == 2
    assert "uno" in result
    assert "due" in result

def test_check_numbers_no_digits():
    assert normalize.check_numbers("nessun numero") == (0, "nessun numero")

def test_check_numbers_tre_gets_accent():
    _, result = normalize.check_numbers("23")
    assert result.endswith("é")  # ventitré


# ---------------------------------------------------------------------------
# switch_symbols / switch_NVB
# ---------------------------------------------------------------------------

def test_switch_symbols_intonation_before_colon():
    # [:-~] is the ASCII range 58-126; ':' is the lower bound
    assert normalize.switch_symbols("ciao.:") == (1, "ciao:.")

def test_switch_symbols_question_before_tilde():
    assert normalize.switch_symbols("ciao?~") == (1, "ciao~?")

def test_switch_symbols_no_change():
    assert normalize.switch_symbols("ciao.") == (0, "ciao.")

def test_switch_NVB_overlap_nvb_moves_out():
    # Non-pause NVB after [ moves out
    assert normalize.switch_NVB("[((ride))") == (1, "((ride)) [")

def test_switch_NVB_overlap_pause_exempt_opening():
    # (.) immediately after [ stays (pause inside overlap is valid)
    assert normalize.switch_NVB("[(.)") == (0, "[(.)")

def test_switch_NVB_overlap_pause_exempt_closing():
    # (.) immediately before ] stays
    assert normalize.switch_NVB("(.)]") == (0, "(.)]")

def test_switch_NVB_overlap_pause_exempt_full_span():
    # [(.)] must not change at all
    assert normalize.switch_NVB("[(.)]") == (0, "[(.)]")

def test_switch_NVB_overlap_non_pause_closing():
    assert normalize.switch_NVB("((ride))]") == (1, "] ((ride))")

def test_switch_NVB_guess_span():
    # ( ) : all NVBs move out, including (.)
    assert normalize.switch_NVB("((.))") == (1, "(.) ()")
    assert normalize.switch_NVB("(((ride)))") == (1, "((ride)) ()")

def test_switch_NVB_pace_span():
    # < > : all NVBs move out
    n, result = normalize.switch_NVB("<((laugh))>")
    assert n == 1
    assert result == "((laugh)) <>"

def test_switch_NVB_volume_span():
    # ° ° : all NVBs move out
    n, result = normalize.switch_NVB("°((laugh))°")
    assert n == 1
    assert result == "((laugh)) °°"

def test_switch_NVB_no_change():
    assert normalize.switch_NVB("(.) ciao") == (0, "(.) ciao")


# ---------------------------------------------------------------------------
# check_spaces_dots / check_spaces_angular
# ---------------------------------------------------------------------------

def test_check_spaces_dots_leading_space():
    assert normalize.check_spaces_dots("° ciao°") == (1, "°ciao°")

def test_check_spaces_dots_trailing_space():
    assert normalize.check_spaces_dots("°ciao °") == (1, "°ciao°")

def test_check_spaces_dots_multiple():
    assert normalize.check_spaces_dots("bla °bla bla ° bla ° bla bla°") == \
                                       (2, "bla °bla bla° bla °bla bla°")

def test_check_spaces_dots_doubled_marker_does_not_crash():
    # A doubled °° marker leaves an unpaired leading/trailing ° after
    # re.split — must not be mistaken for a real °...° span (regression
    # test for IndexError on the stray single-character segment).
    subs, result = normalize.check_spaces_dots("°°xx  xx°° ciao")
    assert result == "°°xx  xx°° ciao"

def test_check_spaces_angular_slow_leading():
    assert normalize.check_spaces_angular("< ciao>") == (1, "<ciao>")

def test_check_spaces_angular_fast_trailing():
    assert normalize.check_spaces_angular(">ciao <") == (1, ">ciao<")

def test_check_spaces_angular_multiple():
    assert normalize.check_spaces_angular("bla >bla bla < bla < bla bla> bla") == \
                                          (2, "bla >bla bla< bla <bla bla> bla")


# ---------------------------------------------------------------------------
# check_even_dots
# ---------------------------------------------------------------------------

def test_check_even_dots_balanced():
    assert normalize.check_even_dots("°ciao°") is True

def test_check_even_dots_unbalanced():
    assert normalize.check_even_dots("°ciao") is False

def test_check_even_dots_empty():
    assert normalize.check_even_dots("ciao") is True


# ---------------------------------------------------------------------------
# check_normal_parentheses
# ---------------------------------------------------------------------------

def test_check_normal_parentheses_round_balanced():
    assert normalize.check_normal_parentheses("(ciao)", "(", ")") is True

def test_check_normal_parentheses_round_unclosed():
    assert normalize.check_normal_parentheses("(ciao", "(", ")") is False

def test_check_normal_parentheses_square_balanced():
    assert normalize.check_normal_parentheses("[ciao]", "[", "]") is True

def test_check_normal_parentheses_square_unopened():
    assert normalize.check_normal_parentheses("ciao]", "[", "]") is False

def test_check_normal_parentheses_nested_invalid():
    assert normalize.check_normal_parentheses("((ciao))", "(", ")") is False


# ---------------------------------------------------------------------------
# check_angular_parentheses
# ---------------------------------------------------------------------------

def test_check_angular_slow_balanced():
    assert normalize.check_angular_parentheses("<ciao>") is True

def test_check_angular_fast_balanced():
    assert normalize.check_angular_parentheses(">ciao<") is True

def test_check_angular_slow_unclosed():
    assert normalize.check_angular_parentheses("<ciao") is False

def test_check_angular_fast_unclosed():
    assert normalize.check_angular_parentheses("ciao>") is False

def test_check_angular_nested_invalid():
    assert normalize.check_angular_parentheses("<<ciao>>") is False

def test_check_angular_mixed_balanced():
    assert normalize.check_angular_parentheses("bla <slow> followed by >fast<") is True
    assert normalize.check_angular_parentheses("bla >fast< followed by <slow>") is True


# ---------------------------------------------------------------------------
# remove_empty_spans
# ---------------------------------------------------------------------------

def test_remove_empty_spans_overlap():
    assert normalize.remove_empty_spans("[]") == (1, "")

def test_remove_empty_spans_guess():
    assert normalize.remove_empty_spans("()") == (1, "")

def test_remove_empty_spans_slow_pace():
    assert normalize.remove_empty_spans("<>") == (1, "")

def test_remove_empty_spans_fast_pace():
    assert normalize.remove_empty_spans("><") == (1, "")

def test_remove_empty_spans_volume():
    assert normalize.remove_empty_spans("°°") == (1, "")

def test_remove_empty_spans_with_whitespace_inside():
    assert normalize.remove_empty_spans("[ ]") == (1, "")

def test_remove_empty_spans_non_empty_span_unchanged():
    assert normalize.remove_empty_spans("[ciao]") == (0, "[ciao]")

def test_remove_empty_spans_collapses_double_space():
    n, result = normalize.remove_empty_spans("ciao [] bla")
    assert n == 1
    assert result == "ciao bla"

def test_remove_empty_spans_multiple():
    n, result = normalize.remove_empty_spans("[] ()")
    assert n == 2
    assert result == ""


# ---------------------------------------------------------------------------
# apply_word_corrections
# ---------------------------------------------------------------------------

def test_word_corrections_single_word_variants():
    assert normalize.apply_word_corrections("vabbe dai") == (1, "vabbè dai")
    assert normalize.apply_word_corrections("cè bene") == (1, "c(io)è bene")
    assert normalize.apply_word_corrections("mha non so") == (1, "mah non so")
    assert normalize.apply_word_corrections("emh forse") == (1, "ehm forse")
    assert normalize.apply_word_corrections("hem forse") == (1, "ehm forse")
    assert normalize.apply_word_corrections("he sì") == (1, "eh sì")
    assert normalize.apply_word_corrections("ih che ridere") == (1, "hi che ridere")

def test_word_corrections_mh_family_by_length():
    assert normalize.apply_word_corrections("m sì") == (1, "mh sì")
    assert normalize.apply_word_corrections("hm sì") == (1, "mh sì")
    assert normalize.apply_word_corrections("mhm sì") == (1, "mhmh sì")
    assert normalize.apply_word_corrections("hmhm sì") == (1, "mhmh sì")

def test_word_corrections_va_phrase_variants():
    assert normalize.apply_word_corrections("va beh dai") == (1, "vabbè dai")
    assert normalize.apply_word_corrections("va be dai") == (1, "vabbè dai")
    assert normalize.apply_word_corrections("va be' dai") == (1, "vabbè dai")
    assert normalize.apply_word_corrections("ma vah dai") == (1, "ma va dai")
    assert normalize.apply_word_corrections("ma và dai") == (1, "ma va dai")
    assert normalize.apply_word_corrections("ma va' dai") == (1, "ma va dai")

def test_word_corrections_va_apostrophe_not_touched_outside_ma_va():
    """va' (truncated "vai") has an unrelated meaning outside "ma va'" and
    must not be flattened to "va" in that context."""
    assert normalize.apply_word_corrections("va' a casa") == (0, "va' a casa")

def test_word_corrections_no_false_positive_inside_reduction_span():
    """The single-letter "m"->"mh" entry must not match inside a
    Jefferson-bracket-continued token like m(e l)o (a reduction span,
    not the standalone word "m")."""
    assert normalize.apply_word_corrections("m(e l)o segno") == (0, "m(e l)o segno")
    assert normalize.apply_word_corrections("co(m)e stai") == (0, "co(m)e stai")

def test_word_corrections_no_change():
    assert normalize.apply_word_corrections("ciao come stai") == (0, "ciao come stai")


# ---------------------------------------------------------------------------
# flag_empty_unit
# ---------------------------------------------------------------------------

def test_flag_empty_unit_empty_string():
    assert normalize.flag_empty_unit("") == (1, "")

def test_flag_empty_unit_only_brackets():
    assert normalize.flag_empty_unit("[]") == (1, "")

def test_flag_empty_unit_only_structural():
    assert normalize.flag_empty_unit("[ ] ( ) - #") == (1, "")

def test_flag_empty_unit_with_content():
    assert normalize.flag_empty_unit("ciao") == (0, "ciao")

def test_flag_empty_unit_nvb_counts_as_content():
    # {ride} contains word chars after stripping structural chars
    assert normalize.flag_empty_unit("{ride}") == (0, "{ride}")

def test_flag_empty_unit_pause_alone_is_content():
    # {P} contains "P" — a word char — so it counts as content
    assert normalize.flag_empty_unit("{P}") == (0, "{P}")


# ===========================================================================
# Integration tests — full populated pipeline
# ===========================================================================
# These tests exercise validate_and_normalize with the real registries.
# They verify multi-rule sequencing and realistic transcription inputs.
# ===========================================================================

def test_integration_clean_annotation_unchanged():
    """A well-formed annotation produces no warnings and no errors."""
    text = "ciao come stai"
    normalized, warnings, errors = normalize.validate_and_normalize(text)
    assert normalized == text
    assert warnings == {}
    assert errors == {}


def test_integration_meta_tag_conversion():
    """(.) and ((ride)) stay literal; the leading (.) is stripped by TRIM_PAUSES."""
    normalized, warnings, errors = normalize.validate_and_normalize("(.) ciao ((ride))")
    assert normalized == "ciao ((ride))"
    assert "TRIM_PAUSES" in warnings
    assert errors == {}


def test_integration_leading_pause_stripped():
    """(.) at the start is removed by TRIM_PAUSES."""
    normalized, warnings, _ = normalize.validate_and_normalize("(.) ciao")
    assert normalized == "ciao"
    assert "TRIM_PAUSES" in warnings


def test_integration_accent_normalization():
    normalized, warnings, errors = normalize.validate_and_normalize("perchè nè pò")
    assert normalized == "perché né po'"
    assert warnings.get("ACCENTS", 0) >= 2
    assert errors == {}


def test_integration_number_conversion():
    normalized, warnings, _ = normalize.validate_and_normalize("ho 2 gatti e 3 cani")
    assert "due" in normalized
    assert "tre" in normalized   # 3 → "tre"; accent only added for e.g. "ventitré"
    assert warnings.get("NUMBERS", 0) == 2


def test_integration_multi_rule_sequence():
    """(.) + accent + number all fire in sequence."""
    normalized, warnings, errors = normalize.validate_and_normalize("(.) perchè 2")
    # trim_pauses strips the leading (.); replace_che fixes accent; check_numbers converts
    assert normalized == "perché due"
    assert "TRIM_PAUSES" in warnings
    assert "ACCENTS" in warnings
    assert "NUMBERS" in warnings
    assert errors == {}


def test_integration_overlap_pause_results_in_empty_unit():
    """(.) at span boundary is stripped by TRIM_PAUSES; the resulting []
    is removed by EMPTY_SPANS; flag_empty_unit then marks the TU for exclusion.
    """
    normalized, warnings, _ = normalize.validate_and_normalize("[(.)]")
    assert normalized == ""
    assert "SWITCHES" not in warnings   # switch_NVB correctly left (.) alone
    assert "TRIM_PAUSES" in warnings
    assert "EMPTY_SPANS" in warnings
    assert "EMPTY_UNIT" in warnings


def test_integration_nvb_moved_outside_overlap():
    """A non-pause NVB immediately after [ is relocated outside the span.
    The trailing space check_spaces pass (after switch_NVB) cleans the stray
    space that moving the tag leaves inside the bracket.
    """
    normalized, warnings, _ = normalize.validate_and_normalize("[((ride)) ciao]")
    assert normalized == "((ride)) [ciao]"
    assert "SWITCHES" in warnings


def test_integration_unbalanced_dots_error():
    """An odd number of ° markers is reported as UNBALANCED_DOTS."""
    _, _, errors = normalize.validate_and_normalize("°ciao")
    assert errors.get("UNBALANCED_DOTS") is True


def test_integration_unbalanced_overlap_error():
    _, _, errors = normalize.validate_and_normalize("[ciao")
    assert errors.get("UNBALANCED_OVERLAP") is True


def test_integration_unbalanced_guess_error():
    _, _, errors = normalize.validate_and_normalize("(ciao")
    assert errors.get("UNBALANCED_GUESS") is True


def test_integration_unbalanced_pace_error():
    _, _, errors = normalize.validate_and_normalize("<ciao")
    assert errors.get("UNBALANCED_PACE") is True


def test_integration_balanced_spans_no_errors():
    """All span types balanced → no errors."""
    _, _, errors = normalize.validate_and_normalize("[ciao] (forse) °piano° <lento>")
    assert errors == {}


def test_integration_config_disables_numbers():
    """Disabling NUMBERS leaves digits in the output."""
    normalized, warnings, _ = normalize.validate_and_normalize(
        "ho 2 gatti", config={"NUMBERS": False}
    )
    assert "2" in normalized
    assert "NUMBERS" not in warnings


def test_integration_config_disables_accents():
    normalized, warnings, _ = normalize.validate_and_normalize(
        "perchè", config={"ACCENTS": False}
    )
    assert normalized == "perchè"
    assert "ACCENTS" not in warnings


def test_integration_empty_span_from_nvb_removal():
    """[((ride))] → switch_NVB moves ((ride)) out → [] → removed by EMPTY_SPANS."""
    normalized, warnings, _ = normalize.validate_and_normalize("[((ride))]")
    assert normalized == "((ride))"
    assert "SWITCHES" in warnings
    assert "EMPTY_SPANS" in warnings
    assert "EMPTY_UNIT" not in warnings   # ((ride)) is content


def test_integration_empty_unit_excluded():
    """An annotation that reduces to nothing is flagged with EMPTY_UNIT."""
    normalized, warnings, _ = normalize.validate_and_normalize("[]")
    assert normalized == ""
    assert "EMPTY_SPANS" in warnings
    assert "EMPTY_UNIT" in warnings


def test_integration_warnings_accumulate_across_rules_with_same_label():
    """UNEVEN_SPACES is shared by multiple rules; counts must sum."""
    # "[ ciao ]" triggers check_spaces (opening bracket) and check_spaces (closing bracket)
    _, warnings, _ = normalize.validate_and_normalize("[ ciao ]")
    assert warnings.get("UNEVEN_SPACES", 0) >= 2
