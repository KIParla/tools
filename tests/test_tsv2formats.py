from conftest import load_tool_module


tsv2formats = load_tool_module("tsv2formats")


def test_tsv2linear_writes_jefferson_and_orthographic_outputs(tmp_path):
    input_path = tmp_path / "sample.vert.tsv"
    input_path.write_text(
        "\n".join(
            [
                "tu_id\tspeaker\ttype\tspan\tform\tjefferson_feats",
                "0\tSPK1\tlinguistic\tciao\tciao\t_",
                "0\tSPK1\tlinguistic\tcome\tcome\tProsodicLink=Yes",
                "0\tSPK1\tlinguistic\tstai\tstai\t_",
                "1\tSPK2\tunknown\txxx\tx\t_",
                "1\tSPK2\terror\ta!b?\tab\tSpaceAfter=No",
                "1\tSPK2\tshortpause\t(.)\t_\t_",
            ]
        ),
        encoding="utf-8",
    )
    out_jeff = tmp_path / "jeff"
    out_ortho = tmp_path / "ortho"
    out_jeff.mkdir()
    out_ortho.mkdir()

    tsv2formats.tsv2linear([input_path], out_jeff, out_ortho)

    jefferson = (out_jeff / "sample.txt").read_text(encoding="utf-8")
    orthographic = (out_ortho / "sample.txt").read_text(encoding="utf-8")

    assert jefferson == "SPK1\tciao come=stai\nSPK2\txxx a!b?(.)\n"
    assert orthographic == "SPK1\tciao come stai\nSPK2\txxx ab\n"


def _linear(tmp_path, rows):
    """Run tsv2linear on vert rows given as (tu_id, speaker, type, span, form, variation_feats)."""
    header = "tu_id\tspeaker\ttype\tspan\tform\tvariation\tjefferson_feats"
    path = tmp_path / "v.vert.tsv"
    path.write_text(
        "\n".join([header] + ["\t".join(map(str, (*r, "_"))) for r in rows]),
        encoding="utf-8")
    jeff, ortho = tmp_path / "jeff", tmp_path / "ortho"
    jeff.mkdir()
    ortho.mkdir()
    tsv2formats.tsv2linear([path], jeff, ortho)
    return ((jeff / "v.txt").read_text(encoding="utf-8"),
            (ortho / "v.txt").read_text(encoding="utf-8"))


def test_unit_initial_hash_underscore_is_read_from_the_span(tmp_path):
    jeff, ortho = _linear(tmp_path, [
        (0, "S", "linguistic", "#_ hola", "hola", "ContainsVariation=Yes|Variety=Other|Language=NO_ISO_CODE"),
        (0, "S", "linguistic", "que", "que", "ContainsVariation=Yes|Variety=Other|Language=NO_ISO_CODE"),
    ])
    assert jeff == "S\t#_ hola que\n"
    # one "#_ " for the unit, no per-word "#"
    assert ortho == "S\t#_ hola que\n"


def test_unit_initial_hash_unsure_is_read_from_the_span(tmp_path):
    jeff, ortho = _linear(tmp_path, [
        (0, "S", "linguistic", "# hola", "hola", "ContainsVariation=Yes|Variety=Unsure"),
        (0, "S", "linguistic", "que", "que", "ContainsVariation=Yes|Variety=Unsure"),
    ])
    assert jeff == "S\t# hola que\n"
    assert ortho == "S\t# hola que\n"


def test_mid_unit_hash_underscore_orthographic(tmp_path):
    jeff, ortho = _linear(tmp_path, [
        (0, "S", "linguistic", "ciao", "ciao", "ContainsVariation=Yes"),
        (0, "S", "linguistic", "#_ hola", "hola", "ContainsVariation=Yes|Variety=Other|Language=NO_ISO_CODE"),
        (0, "S", "linguistic", "que", "que", "ContainsVariation=Yes|Variety=Other|Language=NO_ISO_CODE"),
    ])
    assert jeff == "S\tciao #_ hola que\n"
    assert ortho == "S\tciao #_ hola que\n"


def test_per_word_markers_orthographic(tmp_path):
    jeff, ortho = _linear(tmp_path, [
        (0, "S", "linguistic", "#hola", "hola", "ContainsVariation=Yes|Variety=Other|Language=NO_ISO_CODE"),
        (0, "S", "linguistic", "#*mah", "mah", "ContainsVariation=Yes|Variety=Unassignable|Language=NO_ISO_CODE"),
        (0, "S", "linguistic", "$forma", "forma", "ContainsVariation=Yes|Nonce=Yes"),
        (0, "S", "linguistic", "ciao", "ciao", "ContainsVariation=Yes"),
    ])
    assert jeff == "S\t#hola #*mah $forma ciao\n"
    assert ortho == "S\t#hola #*mah $forma ciao\n"


def test_orthographic_state_resets_between_units(tmp_path):
    # a "#_" in unit 0 must not suppress the per-word "#" in unit 1
    jeff, ortho = _linear(tmp_path, [
        (0, "S", "linguistic", "#_ hola", "hola", "ContainsVariation=Yes|Variety=Other"),
        (1, "S", "linguistic", "#hola", "hola", "ContainsVariation=Yes|Variety=Other"),
    ])
    assert ortho == "S\t#_ hola\nS\t#hola\n"


def test_old_format_vert_is_rejected(tmp_path):
    import pytest
    path = tmp_path / "old.vert.tsv"
    path.write_text("tu_id\tspeaker\ttype\tspan\tform\tvariation\tjefferson_feats\n"
                    "0\tS\tlinguistic\thola\thola\tall\t_\n", encoding="utf-8")
    (tmp_path / "j").mkdir()
    (tmp_path / "o").mkdir()
    with pytest.raises(SystemExit, match="old-format"):
        tsv2formats.tsv2linear([path], tmp_path / "j", tmp_path / "o")
