"""Tests for tsv2vert.py anonymized-token handling."""

import csv
from pathlib import Path

from serialize import VERT_FIELDNAMES
from tsv2vert import convert_file, is_anonymized


def _vert_row(**overrides):
    row = {col: "_" for col in VERT_FIELDNAMES}
    row.update(overrides)
    return row


class TestIsAnonymized:

    def test_true_for_anonymized_type(self):
        assert is_anonymized(_vert_row(type="anonymized"))

    def test_true_for_anonymized_feature_flag(self):
        assert is_anonymized(_vert_row(type="linguistic", jefferson_feats="Anonymized=Yes"))

    def test_false_otherwise(self):
        assert not is_anonymized(_vert_row(type="linguistic"))


class TestConvertFileStripsAtPrefix(object):

    def test_anonymized_word_loses_at_prefix_in_vertical(self, tmp_path):
        tsv_path = tmp_path / "TEST0001.vert.tsv"
        with tsv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=VERT_FIELDNAMES, delimiter="\t")
            writer.writeheader()
            writer.writerow(_vert_row(
                token_id="0-0", speaker="SPK0", tu_id="0", id="0",
                form="@nome", type="anonymized",
                jefferson_feats="Anonymized=Yes", align="Begin=0.0|End=1.0",
            ))

        conversations_path = tmp_path / "conversations.tsv"
        conversations_path.write_text("code\n")
        participants_path = tmp_path / "participants.tsv"
        participants_path.write_text("code\tconversations\n")

        import io
        out = io.StringIO()
        convert_file(
            str(tsv_path), {}, {}, [], out,
            base_url="/corpus", artifacts_base_url="/artifacts",
            artifacts_module="TEST", issues_base_url="", translations_dir=None,
        )
        vertical = out.getvalue()
        assert "\t@nome\t" not in vertical
        assert "nome\t0-0\t" in vertical


def _vertical(tmp_path, rows):
    """Run convert_file on vert rows (dicts of VERT_FIELDNAMES overrides)."""
    import io
    tsv_path = tmp_path / "TEST0001.vert.tsv"
    with tsv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=VERT_FIELDNAMES, delimiter="\t")
        writer.writeheader()
        for i, r in enumerate(rows):
            writer.writerow(_vert_row(
                token_id=f"0-{i}", speaker="SPK0", tu_id="0", id=str(i),
                type="linguistic", align="Begin=0.0|End=1.0", **r))
    out = io.StringIO()
    convert_file(
        str(tsv_path), {}, {}, [], out,
        base_url="/corpus", artifacts_base_url="/artifacts",
        artifacts_module="TEST", issues_base_url="", translations_dir=None,
    )
    return out.getvalue()


def _token_lines(vertical):
    return [l.split("\t") for l in vertical.splitlines() if "\t" in l]


class TestVarietyAttributes:
    """Every token carries `variety` and `nonce` (the last two positional
    attributes); the unit carries contains_variation when any token has a
    variety. The word keeps its "#"/"#*"/"$" prefix, so the original marker
    can be read back from the vertical alone."""

    def test_variety_and_nonce_columns_and_word_prefixes(self, tmp_path):
        v = _vertical(tmp_path, [
            dict(form="ciao", **{"code-variation": "ContainsVariation=Yes"}),
            dict(form="hola", **{"code-variation": "ContainsVariation=Yes|Code=Other|Language=NO_ISO_CODE"}),
            dict(form="mah", **{"code-variation": "ContainsVariation=Yes|Code=Unsure|Language=NO_ISO_CODE"}),
            dict(form="boh", **{"code-variation": "ContainsVariation=Yes|Code=Underspecified"}),
            dict(form="forma", **{"code-variation": "ContainsVariation=Yes|Nonce=Yes"}),
        ])
        toks = {t[1]: t for t in _token_lines(v) if t[1].startswith("0-")}
        # (word, token_id, lemma, upos, variety, nonce)
        assert toks["0-0"][0] == "ciao" and toks["0-0"][-2:] == ["", ""]
        assert toks["0-1"][0] == "#hola" and toks["0-1"][-2:] == ["other", ""]
        assert toks["0-2"][0] == "#*mah" and toks["0-2"][-2:] == ["unsure", ""]
        assert toks["0-3"][0] == "boh" and toks["0-3"][-2:] == ["underspecified", ""]
        assert toks["0-4"][0] == "$forma" and toks["0-4"][-2:] == ["", "yes"]

    def test_unit_attribute_contains_variation(self, tmp_path):
        v = _vertical(tmp_path, [dict(form="hola", **{"code-variation": "ContainsVariation=Yes|Code=Other"})])
        assert 'contains_variation="yes"' in v
        assert "language_variation" not in v

    def test_unit_attribute_absent_without_variation(self, tmp_path):
        v = _vertical(tmp_path, [dict(form="ciao", **{"code-variation": "ContainsVariation=No"})])
        assert "contains_variation" not in v

    def test_nonce_alone_does_not_set_contains_variation(self, tmp_path):
        v = _vertical(tmp_path, [dict(form="forma", **{"code-variation": "ContainsVariation=No|Nonce=Yes"})])
        assert "contains_variation" not in v
