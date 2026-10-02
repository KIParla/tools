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


class TestSite:

    def _dataset(self, tmp_path, **kw):
        (tmp_path / "mods").mkdir()
        return build_explorer.build_dataset(_modules(tmp_path / "mods"), **kw)

    def test_production_site_loads_data_json(self, tmp_path):
        d = self._dataset(tmp_path)
        files = build_explorer.write_site(d, tmp_path / "site")
        names = sorted(f.name for f in files)
        assert names == ["app.js", "core.js", "data.json", "explorer.css", "index.html"]
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        assert "data.js" not in html and "__DATA_SCRIPT__" not in html
        assert json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8")) == d
        assert "data.json" in (tmp_path / "site" / "app.js").read_text(encoding="utf-8")

    def test_index_references_only_files_that_exist(self, tmp_path):
        import re
        d = self._dataset(tmp_path)
        build_explorer.write_site(d, tmp_path / "site")
        html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="([^"#]+\.(?:js|css))"', html)
        assert sorted(refs) == ["app.js", "core.js", "explorer.css"]
        for ref in refs:
            assert (tmp_path / "site" / ref).is_file(), ref

    def test_single_file_has_nothing_external(self, tmp_path):
        import re
        html = build_explorer.single_file(self._dataset(tmp_path))
        assert "<style>" in html and html.count("<script>") == 3
        assert not re.search(r'<link[^>]+stylesheet', html)
        assert not re.search(r'<script[^>]+src=', html)
        assert "__DATA_SCRIPT__" not in html

    def test_single_file_embeds_the_dataset_before_the_app(self, tmp_path):
        d = self._dataset(tmp_path)
        html = build_explorer.single_file(d)
        start = html.index("window.KIPARLA_DATA = ") + len("window.KIPARLA_DATA = ")
        end = html.index(";</script>", start)
        assert json.loads(html[start:end].replace("<\\/", "</")) == d
        assert start < html.index("function run(DATA)")

    def test_data_cannot_close_the_script_tag(self, tmp_path):
        d = self._dataset(tmp_path)
        d["conversations"][0]["topic"] = "</script><b>x"
        html = build_explorer.single_file(d)
        assert "</script><b>" not in html
        assert "<\\/script><b>x" in html

    def test_transcription_links_default_to_the_published_site_and_can_be_relative(self, tmp_path):
        d = self._dataset(tmp_path)
        assert d["links"]["artifacts"] == "https://kiparla.github.io/KIParla-artifacts/"
        (tmp_path / "mods2").mkdir()
        d2 = build_explorer.build_dataset(_modules(tmp_path / "mods2"), "..")
        assert d2["links"]["artifacts"] == "../"

    def test_zip_holds_the_single_file_as_an_ordinary_file(self, tmp_path):
        import stat
        import zipfile
        d = self._dataset(tmp_path)
        z = tmp_path / "x.zip"
        build_explorer.write_zip(d, z)
        zf = zipfile.ZipFile(z)
        assert zf.namelist() == ["kiparla-explorer/index.html"]
        info = zf.infolist()[0]
        mode = info.external_attr >> 16
        assert stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o644, oct(mode)
        assert zf.read(info.filename).decode("utf-8") == build_explorer.single_file(d)

    def test_missing_source_file_or_tag_is_refused(self, tmp_path, monkeypatch):
        (tmp_path / "explorer").mkdir()
        monkeypatch.setattr(build_explorer, "EXPLORER_DIR", tmp_path / "explorer")
        with pytest.raises(SystemExit, match="is missing"):
            build_explorer.site_files({"conversations": []})
        for n in ("explorer.css", "core.js", "app.js"):
            (tmp_path / "explorer" / n).write_text("", encoding="utf-8")
        (tmp_path / "explorer" / "template.html").write_text("<html></html>", encoding="utf-8")
        with pytest.raises(SystemExit, match="has no"):
            build_explorer.single_file({"conversations": []})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_explorer_core_javascript_tests():
    """The filtering and statistics core is plain JS, tested under Node."""
    from conftest import TOOLS_DIR
    result = subprocess.run(["node", str(TOOLS_DIR / "tests" / "explorer_core.test.js")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "all assertions passed" in result.stdout
