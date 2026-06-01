import pathlib
import pytest
import yaml
import config

CONFIGS_DIR = pathlib.Path(__file__).resolve().parents[1] / "configs"


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------

def test_deep_merge_scalar_override():
    result = config._deep_merge({"a": 1}, {"a": 2})
    assert result["a"] == 2


def test_deep_merge_missing_key_kept_from_base():
    result = config._deep_merge({"a": 1, "b": 2}, {"a": 9})
    assert result["b"] == 2


def test_deep_merge_new_key_added():
    result = config._deep_merge({"a": 1}, {"b": 2})
    assert result == {"a": 1, "b": 2}


def test_deep_merge_nested_dict_partial_override():
    base     = {"x": {"a": 1, "b": 2}}
    override = {"x": {"b": 99}}
    result   = config._deep_merge(base, override)
    assert result["x"]["a"] == 1    # kept
    assert result["x"]["b"] == 99   # overridden


def test_deep_merge_list_replaced_not_merged():
    base     = {"items": [1, 2, 3]}
    override = {"items": [4, 5]}
    result   = config._deep_merge(base, override)
    assert result["items"] == [4, 5]


def test_deep_merge_empty_list_replaces():
    base     = {"tiers_to_ignore": ["Traduzione"]}
    override = {"tiers_to_ignore": []}
    result   = config._deep_merge(base, override)
    assert result["tiers_to_ignore"] == []


def test_deep_merge_does_not_mutate_base():
    base     = {"x": {"a": 1}}
    override = {"x": {"a": 2}}
    config._deep_merge(base, override)
    assert base["x"]["a"] == 1


def test_deep_merge_does_not_mutate_override():
    base     = {"x": {"a": 1}}
    override = {"x": {"b": 2}}
    config._deep_merge(base, override)
    assert "a" not in override["x"]


# ---------------------------------------------------------------------------
# load_config — defaults only
# ---------------------------------------------------------------------------

def test_load_config_none_returns_defaults():
    cfg = config.load_config(None, CONFIGS_DIR)
    assert "tiers_to_ignore" in cfg
    assert "overlaps" in cfg
    assert "normalization" in cfg


def test_load_config_defaults_tiers_to_ignore():
    cfg = config.load_config(None, CONFIGS_DIR)
    assert "Traduzione" in cfg["tiers_to_ignore"]


def test_load_config_defaults_nvb_participates_false():
    cfg = config.load_config(None, CONFIGS_DIR)
    assert cfg["overlaps"]["nvb_participates_in_overlaps"] is False


def test_load_config_defaults_duration_threshold():
    cfg = config.load_config(None, CONFIGS_DIR)
    assert cfg["overlaps"]["duration_threshold"] == pytest.approx(0.1)


def test_load_config_defaults_normalization_keys():
    cfg = config.load_config(None, CONFIGS_DIR)
    norm = cfg["normalization"]
    for key in ["SYMBOL_NOT_ALLOWED", "META_TAGS", "ACCENTS", "NUMBERS"]:
        assert norm[key] is True, f"{key} should be True by default"
    for key in ["OVERLAP_PROLONGATION", "HASH_UNIT_SPACE", "HASH_PREFIX_SPACE"]:
        assert norm[key] is False, f"{key} should be False by default"


def test_load_config_defaults_variation_markers_all_false():
    cfg = config.load_config(None, CONFIGS_DIR)
    vm = cfg["variation_markers"]
    assert vm["hash_token"] is False
    assert vm["dollar"] is False
    assert vm["hash_doubtful"] is False


# ---------------------------------------------------------------------------
# load_config — module overrides
# ---------------------------------------------------------------------------

def test_load_config_kipasti_enables_hash_prefix_space():
    cfg = config.load_config("KIPasti", CONFIGS_DIR)
    assert cfg["normalization"]["HASH_PREFIX_SPACE"] is True


def test_load_config_kipasti_keeps_other_normalization_defaults():
    cfg = config.load_config("KIPasti", CONFIGS_DIR)
    assert cfg["normalization"]["SYMBOL_NOT_ALLOWED"] is True
    assert cfg["normalization"]["HASH_UNIT_SPACE"] is False


def test_load_config_straparlabo_enables_variation_markers():
    cfg = config.load_config("StraParlaBO", CONFIGS_DIR)
    vm = cfg["variation_markers"]
    assert vm["hash_token"] is True
    assert vm["dollar"] is True
    assert vm["hash_doubtful"] is True


def test_load_config_straparlabo_enables_hash_unit_space():
    cfg = config.load_config("StraParlaBO", CONFIGS_DIR)
    assert cfg["normalization"]["HASH_UNIT_SPACE"] is True


def test_load_config_straparlabo_clears_tiers_to_ignore():
    cfg = config.load_config("StraParlaBO", CONFIGS_DIR)
    assert cfg["tiers_to_ignore"] == []


def test_load_config_straparlabo_extracts_traduzione():
    cfg = config.load_config("StraParlaBO", CONFIGS_DIR)
    assert "Traduzione" in cfg["tiers_to_extract"]


def test_load_config_straparlato_matches_straparlabo():
    bo = config.load_config("StraParlaBO", CONFIGS_DIR)
    to = config.load_config("StraParlaTO", CONFIGS_DIR)
    assert bo["variation_markers"] == to["variation_markers"]
    assert bo["tiers_to_ignore"] == to["tiers_to_ignore"]
    assert bo["tiers_to_extract"] == to["tiers_to_extract"]


def test_load_config_parlabz_nvb_participates_true():
    cfg = config.load_config("ParlaBZ", CONFIGS_DIR)
    assert cfg["overlaps"]["nvb_participates_in_overlaps"] is True


def test_load_config_parlabz_keeps_duration_threshold():
    cfg = config.load_config("ParlaBZ", CONFIGS_DIR)
    assert cfg["overlaps"]["duration_threshold"] == pytest.approx(0.1)


def test_load_config_kip_is_all_defaults():
    defaults = config.load_config(None, CONFIGS_DIR)
    kip      = config.load_config("KIP", CONFIGS_DIR)
    assert kip == defaults


def test_load_config_parla_modules_are_all_defaults():
    defaults = config.load_config(None, CONFIGS_DIR)
    for module in ("ParlaBO", "ParlaTO"):
        assert config.load_config(module, CONFIGS_DIR) == defaults


# ---------------------------------------------------------------------------
# load_config — error handling
# ---------------------------------------------------------------------------

def test_load_config_missing_defaults_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="defaults.yml"):
        config.load_config(None, tmp_path)


def test_load_config_unknown_module_raises():
    with pytest.raises(FileNotFoundError, match="NonExistent"):
        config.load_config("NonExistent", CONFIGS_DIR)
