import importlib.util
import json
import pathlib
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tools_generate_csvw", TOOLS_DIR / "csvw" / "generate_csvw.py")
generate_csvw = importlib.util.module_from_spec(_spec)
sys.modules["tools_generate_csvw"] = generate_csvw
_spec.loader.exec_module(generate_csvw)


def _write_module(root, vert_headers):
    (root / "metadata").mkdir(parents=True)
    (root / "tsv").mkdir()
    (root / "metadata" / "conversations.tsv").write_text(
        "code\ttype\tparticipants\nC1\tfree\tP1\n", encoding="utf-8")
    (root / "metadata" / "participants.tsv").write_text(
        "code\tgender\nP1\tF\n", encoding="utf-8")
    for name, header in vert_headers.items():
        (root / "tsv" / f"{name}.vert.tsv").write_text(
            "\t".join(header) + "\n", encoding="utf-8")


def test_every_vert_table_carries_its_own_complete_schema(tmp_path):
    base = ["token_id", "speaker", "tu_id", "span", "form", "type"]
    _write_module(tmp_path, {"C1": base, "C2": base, "C3": base + ["extra"]})

    out = generate_csvw.generate_for_module(tmp_path)
    tables = json.loads(out.read_text(encoding="utf-8"))["tables"]

    vert = [t for t in tables if t["url"].startswith("tsv/")]
    assert [t["url"] for t in vert] == ["tsv/C1.vert.tsv", "tsv/C2.vert.tsv", "tsv/C3.vert.tsv"]
    for table in vert:
        schema = table["tableSchema"]
        # no back-reference to another table's schema: CSVW tools would see zero columns
        assert "@id" not in schema
        assert schema["columns"], table["url"]
        assert schema["primaryKey"] == "token_id"
        assert schema["foreignKeys"][0]["columnReference"] == "speaker"
    assert [c["name"] for c in vert[0]["tableSchema"]["columns"]] == base
    assert [c["name"] for c in vert[2]["tableSchema"]["columns"]] == base + ["extra"]


def test_all_known_modules_include_parlabz():
    assert "ParlaBZ" in generate_csvw.MODULES
