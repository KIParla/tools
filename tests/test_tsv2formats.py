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
