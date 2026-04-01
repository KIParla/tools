import pandas as pd

from conftest import load_tool_module


merge_metadata = load_tool_module("merge_metadata")


def test_normalise_renames_and_fills_missing_columns():
    df = pd.DataFrame(
        [
            {
                "code": "P1",
                "occupation": "teacher",
                "gender": "F",
                "conversations": "C1",
                "school-region": "ER",
            }
        ]
    )

    out = merge_metadata.normalise(df, "KIP", merge_metadata.PARTICIPANTS_COLS)

    assert list(out.columns) == merge_metadata.PARTICIPANTS_COLS
    assert out.iloc[0].to_dict() == {
        "code": "P1",
        "occupation": "teacher",
        "gender": "F",
        "conversations": "C1",
        "birth-region": "ER",
        "age-range": "",
        "study-level": "",
    }


def test_merge_deduplicates_by_code_and_keeps_first_occurrence(tmp_path):
    module_a = tmp_path / "KIP"
    module_b = tmp_path / "ParlaBO"
    (module_a / "metadata").mkdir(parents=True)
    (module_b / "metadata").mkdir(parents=True)

    participants_a = pd.DataFrame(
        [
            {
                "code": "P1",
                "occupation": "teacher",
                "gender": "F",
                "conversations": "C1",
                "school-region": "ER",
                "age-range": "30-40",
                "study-level": "degree",
            }
        ]
    )
    participants_b = pd.DataFrame(
        [
            {
                "code": "P1",
                "occupation": "engineer",
                "gender": "M",
                "conversations": "C2",
                "birth-region": "LAZ",
                "age-range": "40-50",
                "study-level": "phd",
            },
            {
                "code": "P2",
                "occupation": "student",
                "gender": "F",
                "conversations": "C3",
                "birth-region": "TOS",
                "age-range": "20-30",
                "study-level": "ba",
            },
        ]
    )

    participants_a.to_csv(module_a / "metadata" / "participants.tsv", sep="\t", index=False)
    participants_b.to_csv(module_b / "metadata" / "participants.tsv", sep="\t", index=False)

    merged = merge_metadata.merge(
        [str(module_a), str(module_b)],
        "participants.tsv",
        merge_metadata.PARTICIPANTS_COLS,
    )

    assert merged.to_dict(orient="records") == [
        {
            "code": "P1",
            "occupation": "teacher",
            "gender": "F",
            "conversations": "C1",
            "birth-region": "ER",
            "age-range": "30-40",
            "study-level": "degree",
        },
        {
            "code": "P2",
            "occupation": "student",
            "gender": "F",
            "conversations": "C3",
            "birth-region": "TOS",
            "age-range": "20-30",
            "study-level": "ba",
        },
    ]
