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

def test_meta_tag_shortpause():
    assert normalize.meta_tag("(.) ciao") == (1, "{P} ciao")

def test_meta_tag_comment():
    assert normalize.meta_tag("ciao ((bla bla)) ciao") == (2, "ciao {bla_bla} ciao")

def test_meta_tag_no_change():
    n, result = normalize.meta_tag("ciao come stai")
    assert n == 0
    assert result == "ciao come stai"

def test_meta_tag_spaces_become_underscores():
    _, result = normalize.meta_tag("((parla veloce))")
    assert result == "{parla_veloce}"


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
    assert normalize.remove_pauses("{P} ciao") == (1, "ciao")

def test_remove_pauses_trailing():
    assert normalize.remove_pauses("ciao {P}") == (1, "ciao")

def test_remove_pauses_both():
    assert normalize.remove_pauses("{P} ciao {P}") == (2, "ciao")

def test_remove_pauses_internal_not_removed():
    assert normalize.remove_pauses("ciao {P} ciao") == (0, "ciao {P} ciao")

def test_remove_pauses_with_bracket():
    assert normalize.remove_pauses("[{P} casa") == (1, "[casa")
    assert normalize.remove_pauses("casa {P} >") == (1, "casa>")

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
    assert normalize.switch_NVB("[{ride}") == (1, "{ride} [")

def test_switch_NVB_overlap_pause_exempt_opening():
    # {P} immediately after [ stays (pause inside overlap is valid)
    assert normalize.switch_NVB("[{P}") == (0, "[{P}")

def test_switch_NVB_overlap_pause_exempt_closing():
    # {P} immediately before ] stays
    assert normalize.switch_NVB("{P}]") == (0, "{P}]")

def test_switch_NVB_overlap_pause_exempt_full_span():
    # [{P}] must not change at all
    assert normalize.switch_NVB("[{P}]") == (0, "[{P}]")

def test_switch_NVB_overlap_non_pause_closing():
    assert normalize.switch_NVB("{ride}]") == (1, "] {ride}")

def test_switch_NVB_guess_span():
    # ( ) : all NVBs move out, including {P}
    # Opening rule fires first: ({P} → {P} (, then {P} is no longer adjacent to )
    assert normalize.switch_NVB("({P})") == (1, "{P} ()")
    assert normalize.switch_NVB("({ride})") == (1, "{ride} ()")

def test_switch_NVB_pace_span():
    # < > : all NVBs move out
    n, result = normalize.switch_NVB("<{laugh}>")
    assert n == 1
    assert result == "{laugh} <>"

def test_switch_NVB_volume_span():
    # ° ° : all NVBs move out
    n, result = normalize.switch_NVB("°{laugh}°")
    assert n == 1
    assert result == "{laugh} °°"

def test_switch_NVB_no_change():
    assert normalize.switch_NVB("{P} ciao") == (0, "{P} ciao")


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
