"""Tests for registry_attrs.py and the aggregate filter of tsv2vert.py."""

import io

from registry_attrs import annotation_level, common_attributes, prune_registry, read_attributes
from tsv2vert import convert_file, corpus_has_variation
from test_tsv2vert import _vert_row, VERT_FIELDNAMES  # noqa: F401  (helpers)

import csv


def _registry(path, token=(), conversation=(), unit=()):
    def block(names, indent):
        return "".join(f'{indent}ATTRIBUTE {n} {{\n{indent}    LABEL "{n}"\n{indent}}}\n' for n in names)
    path.write_text(
        'NAME "X"\n\nATTRIBUTE word\n' + block(token, "")
        + "ATTRIBUTE lc {\n    LABEL \"lc\"\n}\n\n"
        + "STRUCTURE conversation {\n" + block(conversation, "    ") + "}\n\n"
        + "STRUCTURE transcription_unit {\n" + block(unit, "    ") + "}\n\n"
        + "STRUCTURE pause {\n}\n")
    return path


def test_common_attributes_are_the_intersection_without_variation_labels(tmp_path):
    a = _registry(tmp_path / "A", ["token_id", "variety", "nonce"], ["code", "year", "translation"],
                  ["begin", "contains_code_variation"])
    b = _registry(tmp_path / "B", ["token_id", "variety", "nonce"], ["code", "year"], ["begin"])
    common = common_attributes([a, b])
    assert common["token"] == ["word", "token_id", "lc"]          # variety / nonce excluded
    assert common["conversation"] == ["code", "year"]
    assert common["transcription_unit"] == ["begin"]


def test_prune_registry_removes_only_the_listed_blocks(tmp_path):
    a = _registry(tmp_path / "A", ["token_id", "variety"], ["code", "translation"], ["begin", "report_url"])
    keep = {"token": ["word", "token_id", "lc"], "conversation": ["code"], "transcription_unit": ["begin"]}
    pruned = prune_registry(a.read_text(), keep)
    (tmp_path / "P").write_text(pruned)
    attrs = read_attributes(tmp_path / "P")
    assert attrs == {"token": ["word", "token_id", "lc"], "conversation": ["code"],
                     "transcription_unit": ["begin"]}
    assert "STRUCTURE pause {" in pruned and 'NAME "X"' in pruned


def _tsv(tmp_path, code_variation):
    path = tmp_path / "TEST0001.vert.tsv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VERT_FIELDNAMES, delimiter="\t")
        w.writeheader()
        w.writerow(_vert_row(token_id="0-0", speaker="S0", tu_id="0", id="0", form="hola",
                             type="linguistic", align="Begin=0.0|End=1.0",
                             code_variation=code_variation))
    return path


def _convert(path, keep=None, has_variation=True):
    out = io.StringIO()
    convert_file(str(path), {}, {}, [], out, base_url="/c", artifacts_base_url="/a",
                 artifacts_module="T", issues_base_url="", translations_dir=None,
                 keep=keep, has_variation=has_variation)
    return out.getvalue()


def test_aggregate_filter_drops_unlisted_attributes_and_columns(tmp_path):
    path = _tsv(tmp_path, "ContainsVariation=Yes|Code=Other")
    keep = {"token": ["word", "token_id", "lemma", "upos"],
            "conversation": ["code"], "transcription_unit": ["begin"]}
    v = _convert(path, keep)
    assert "contains_code_variation" not in v and "full_conversation" not in v
    assert "linguistic_annotation" not in v and 'participant_code' not in v
    # the aggregate has no variety attribute, so no "#" marker on the word either
    assert [l for l in v.splitlines() if l.startswith("hola")] == ["hola\t0-0\t\t"]


def test_no_contains_code_variation_when_the_corpus_has_none(tmp_path):
    path = _tsv(tmp_path, "ContainsVariation=No")
    assert not corpus_has_variation([path])
    assert "contains_code_variation" not in _convert(path, has_variation=False)
    assert corpus_has_variation([_tsv(tmp_path, "ContainsVariation=Yes|Code=Other")])


def test_prune_registry_also_cleans_the_attribute_lists(tmp_path):
    a = _registry(tmp_path / "A", [], ["code", "year"], ["begin", "participant_gender"])
    text = a.read_text() + 'SUBCORPATTRS "conversation.year,transcription_unit.participant_gender,conversation.code"\n'
    keep = {"token": ["word", "lc"], "conversation": ["code", "year"], "transcription_unit": ["begin"]}
    assert 'SUBCORPATTRS "conversation.year,conversation.code"' in prune_registry(text, keep)


def test_annotation_level_follows_the_registry_attributes(tmp_path):
    base = _registry(tmp_path / "L0", [], ["code"], ["begin"])
    variation = _registry(tmp_path / "L1", ["variety"], ["code"], ["begin", "contains_code_variation"])
    translated = _registry(tmp_path / "L2", ["variety"], ["code"],
                           ["begin", "contains_code_variation", "translation"])
    assert [annotation_level(r) for r in (base, variation, translated)] == [0, 1, 2]
