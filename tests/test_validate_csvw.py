import importlib.util
import pathlib
import sys

import pytest

pytest.importorskip("csvw")

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tools_validate_csvw", TOOLS_DIR / "validate_csvw.py")
validate_csvw = importlib.util.module_from_spec(_spec)
sys.modules["tools_validate_csvw"] = validate_csvw
_spec.loader.exec_module(validate_csvw)

HEADER = ["token_id", "speaker", "tu_id", "span", "form", "type"]


def _module(root, rows):
    (root / "metadata").mkdir(parents=True)
    (root / "tsv").mkdir()
    (root / "metadata" / "conversations.tsv").write_text(
        "code\tparticipants\nC1\tP1\n", encoding="utf-8")
    (root / "metadata" / "participants.tsv").write_text(
        "code\tgender\tconversations\nP1\tF\tC1\n??\t_\tC1\n", encoding="utf-8")
    body = "\n".join("\t".join(r) for r in rows)
    (root / "tsv" / "C1.vert.tsv").write_text("\t".join(HEADER) + "\n" + body + "\n", encoding="utf-8")
    return root


def test_valid_module_has_no_errors(tmp_path):
    root = _module(tmp_path, [
        ["0-0", "P1", "0", "ciao", "ciao", "linguistic"],
        ["1-0", "??", "1", "ehm", "ehm", "linguistic"],
    ])
    assert validate_csvw.validate(root) == []


def test_unknown_speaker_and_empty_required_value_are_reported_once_each(tmp_path):
    root = _module(tmp_path, [
        ["0-0", "P1", "0", "ciao", "", "linguistic"],
        ["1-0", "ZZZ", "1", "ehm", "ehm", "linguistic"],
    ])
    errors = validate_csvw.validate(root)
    assert any("C1.vert.tsv:2" in e and "form" in e for e in errors)
    assert any("ZZZ" in e for e in errors)
    assert len(errors) == len(set(errors))


def test_validation_does_not_write_into_the_module(tmp_path):
    root = _module(tmp_path, [["0-0", "P1", "0", "ciao", "ciao", "linguistic"]])
    before = sorted(p.name for p in root.iterdir())
    validate_csvw.validate(root)
    assert sorted(p.name for p in root.iterdir()) == before


def test_report_lists_files_and_truncates():
    errors = [f"tsv/C1.vert.tsv:{i}:3 form: required column value is missing" for i in range(5)]
    report = validate_csvw.render_report("M", errors, max_errors=2)
    assert "5 validation error(s) in 1 file(s)" in report
    assert "`tsv/C1.vert.tsv` | 5" in report
    assert "and 3 more" in report
    assert "valid" in validate_csvw.render_report("M", [], 2)
