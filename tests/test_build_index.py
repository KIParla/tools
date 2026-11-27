from conftest import load_tool_module

bi = load_tool_module("build_index")


def test_participant_values_normalizes_and_drops_missing():
    row = {"school-region": "estero:colombia", "gender": "_", "age-range": "N/A",
           "L1": "italian;dialect", "study-level": "primary-school", "occupation": "0-Students "}
    assert bi.participant_values(row) == {
        "region": ["estero"], "l1": ["italian", "dialect"], "study": ["elem"],
        "occupation": ["stud"]}
