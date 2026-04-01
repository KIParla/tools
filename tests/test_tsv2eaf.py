from conftest import load_tool_module


tsv2eaf = load_tool_module("tsv2eaf")


def test_parse_begin_and_end_handle_missing_values():
    assert tsv2eaf.parse_begin("Begin=1.25|End=2.50") == 1.25
    assert tsv2eaf.parse_end("Begin=1.25|End=2.50") == 2.5
    assert tsv2eaf.parse_begin("_") is None
    assert tsv2eaf.parse_end("") is None


def test_extract_annotations_groups_rows_and_preserves_spacing_rules(tmp_path):
    input_path = tmp_path / "sample.tsv"
    input_path.write_text(
        "\n".join(
            [
                "tu_id\tspeaker\talign\ttype\tspan\tjefferson_feats",
                "0\tSPK1\tBegin=1.0\tlinguistic\tciao\t_",
                "0\tSPK1\tEnd=1.2\tlinguistic\tcome\tProsodicLink=Yes",
                "0\tSPK1\t_\tlinguistic\tstai\t_",
                "1\tSPK2\tBegin=2.0\tunknown\txxx\t_",
                "1\tSPK2\tEnd=2.3\terror\ta!b?\tSpaceAfter=No",
            ]
        ),
        encoding="utf-8",
    )

    speakers, annotations = tsv2eaf.extract_annotations(input_path)

    assert speakers == {"SPK1", "SPK2"}
    assert annotations == [
        ("SPK1", 1.0, 1.2, "ciao come=stai"),
        ("SPK2", 2.0, 2.3, "xxx a!b?"),
    ]


def test_extract_annotations_fails_when_begin_or_end_is_missing(tmp_path):
    input_path = tmp_path / "broken.tsv"
    input_path.write_text(
        "\n".join(
            [
                "tu_id\tspeaker\talign\ttype\tspan\tjefferson_feats",
                "0\tSPK1\tBegin=1.0\tlinguistic\tciao\t_",
            ]
        ),
        encoding="utf-8",
    )

    try:
        tsv2eaf.extract_annotations(input_path)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected extract_annotations to abort on missing End=")
