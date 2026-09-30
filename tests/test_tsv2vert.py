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
