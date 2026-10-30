import json

from conftest import load_tool_module

ga = load_tool_module("generate_artifacts")
l2h = load_tool_module("linear2html")


def test_write_version_json(tmp_path):
    path = ga.write_version_json(tmp_path / "KIP", "KIP@v3.0.1", "https://example.org/v3.0.1")
    assert json.loads(path.read_text()) == {"label": "KIP@v3.0.1", "url": "https://example.org/v3.0.1"}


def test_write_version_json_without_url(tmp_path):
    path = ga.write_version_json(tmp_path / "KIP", "KIP@v3.0.1", None)
    assert json.loads(path.read_text())["url"] == ""


def test_footer_label_is_overridable_from_version_json():
    # the generated label is only a fallback: the script reads ../version.json
    assert "version.json" in l2h.JS
    assert "release-link" in l2h.JS
