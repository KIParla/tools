"""Tests for build_explorer.py: the dataset built from module snapshots and the page."""

import json
import shutil
import subprocess

import pytest

import build_explorer
import summarize
from test_summarize import _module, _unit


def _modules(tmp_path):
    """Two modules; conversation C2 exists in both (like KIP and ParlaTO)."""
    (tmp_path / "KIP").mkdir()
    (tmp_path / "ParlaTO").mkdir()
    a = _module(
        tmp_path / "KIP",
        {"C1": [_unit(0, "A", 0.0, 10.0, ["a"] * 10, overlaps={0: "0-1(0)"}),
                _unit(1, "B", 5.0, 8.0, ["b"] * 3, variation="ContainsVariation=Yes")],
         "C2": [_unit(0, "A", 0.0, 4.0, ["c"] * 4)]},
        conversations={"C1": ("free-conversation:meal", "0:00:10"),
                       "C2": ("semistructured-interview", "0:00:04")},
        participants={"A": "F", "B": "M"})
    b = _module(
        tmp_path / "ParlaTO",
        {"C2": [_unit(0, "A", 0.0, 4.0, ["c"] * 4)],
         "C3": [_unit(0, "X", 0.0, 2.0, ["d"] * 2)]},
        conversations={"C2": ("semistructured-interview", "0:00:04"),
                       "C3": ("lecture", "0:00:02")},
        participants={"A": "F", "X": "F"})
    # richer participant metadata for the attribute mapping
    (a / "metadata" / "participants.tsv").write_text(
        "code\tgender\tage-range\tschool-region\tbirth-region\n"
        "A\tF\t21-25\ttrentino-alto-adige:alto-adige\t_\n"
        "B\tM\t26-30\t_\temilia-romagna\n", encoding="utf-8")
    for m in (a, b):
        summarize.update_module(m)
    return [a, b]


class TestDataset:

    def test_conversations_are_unique_and_keep_all_modules(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        codes = [c["code"] for c in d["conversations"]]
        assert codes == ["C1", "C2", "C3"]
        c2 = d["conversations"][1]
        assert c2["modules"] == ["KIP", "ParlaTO"]
        assert d["sources"] == {"KIP": None, "ParlaTO": None}

    def test_shared_conversation_speakers_are_not_duplicated(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        assert sum(1 for s in d["speakers"] if s["conv"] == "C2") == 1

    def test_metadata_and_derived_metrics(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        c1 = d["conversations"][0]
        assert (c1["type"], c1["subtype"]) == ("free-conversation", "meal")
        assert c1["hours"] == round(10 / 3600, 4)
        assert c1["overlap_pct"] == round(100 * 3 / 10, 2)      # 3 s of 10 s of speech
        assert c1["silence_pct"] == 0.0
        assert c1["units_overlap_pct"] == 100.0
        assert c1["variation_pct"] == 50.0                       # 1 unit of 2
        assert c1["tokens"] == 13 and c1["ling"] == 13
        assert c1["tokens_per_unit"] == 6.5
        assert c1["speakers_n"] == 2
        assert c1["rate"] == round(13 / 10, 3)

    def test_missing_values_become_null(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        c3 = d["conversations"][2]
        assert c3["year"] is None and c3["point"] is None and c3["languages"] is None

    def test_speaker_attributes_fall_back_from_birth_to_school_region(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        sp = {(s["conv"], s["spk"]): s for s in d["speakers"]}
        assert sp[("C1", "A")]["region"] == "trentino-alto-adige"     # school-region, colon stripped
        assert sp[("C1", "B")]["region"] == "emilia-romagna"          # birth-region wins
        assert sp[("C1", "A")]["age"] == "21-25"
        assert sp[("C1", "A")]["known"] is True
        assert sp[("C3", "X")]["gender"] == "F"

    def test_multivalued_facets_are_flagged(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        multi = {f["id"] for f in d["conversation_facets"] + d["speaker_facets"] if f["multi"]}
        assert multi == {"languages", "mothertongue"}

    def test_missing_snapshot_is_a_clear_error(self, tmp_path):
        (tmp_path / "M").mkdir()
        with pytest.raises(SystemExit, match="run summarize.py first"):
            build_explorer.build_dataset([tmp_path / "M"])

    def test_every_declared_metric_exists_on_every_conversation(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        for m in d["metrics"]:
            assert all(m["id"] in c for c in d["conversations"]), m["id"]


class TestPage:

    def test_page_embeds_core_and_data(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        html = build_explorer.render_page(d)
        assert "__CORE__" not in html and "__DATA__" not in html
        assert "KiparlaCore" in html
        marker = '"conversations":[{"code":"C1"'
        assert marker in html

    def test_data_cannot_close_the_script_tag(self, tmp_path):
        d = build_explorer.build_dataset(_modules(tmp_path))
        d["conversations"][0]["topic"] = "</script><b>x"
        html = build_explorer.render_page(d)
        assert "</script><b>" not in html
        assert "<\\/script><b>x" in html

    def test_template_without_markers_is_refused(self, tmp_path, monkeypatch):
        (tmp_path / "explorer").mkdir()
        (tmp_path / "explorer" / "template.html").write_text("<html></html>", encoding="utf-8")
        (tmp_path / "explorer" / "core.js").write_text("", encoding="utf-8")
        monkeypatch.setattr(build_explorer, "EXPLORER_DIR", tmp_path / "explorer")
        with pytest.raises(SystemExit, match="marker"):
            build_explorer.render_page({"conversations": []})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_explorer_core_javascript_tests():
    """The filtering and statistics core is plain JS, tested under Node."""
    from conftest import TOOLS_DIR
    result = subprocess.run(["node", str(TOOLS_DIR / "tests" / "explorer_core.test.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "all assertions passed" in result.stdout
