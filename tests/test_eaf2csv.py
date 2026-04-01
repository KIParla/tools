import csv

from conftest import load_tool_module


eaf2csv = load_tool_module("eaf2csv")


class FakeTimestamp:
    def __init__(self, sec):
        self.sec = sec


class FakeAnnotation:
    def __init__(self, value, start, end):
        self.value = value
        self.from_ts = FakeTimestamp(start)
        self.to_ts = FakeTimestamp(end)
        self.duration = end - start


class FakeTier:
    def __init__(self, tier_id, annotations):
        self.ID = tier_id
        self.annotations = annotations


def test_convert_sorts_rows_and_remaps_ignore_ids(tmp_path, monkeypatch):
    fake_doc = [
        FakeTier("SPK2", [FakeAnnotation("id:15 later row", 2.0, 2.5)]),
        FakeTier("SPK1", [FakeAnnotation("id:10 first row", 1.0, 1.3)]),
    ]
    monkeypatch.setattr(eaf2csv.elan, "read_eaf", lambda _: fake_doc)

    output_path = tmp_path / "out.csv"
    annotations = {"ignore": ["15 10", "999 10"]}

    eaf2csv.convert(tmp_path / "input.eaf", output_path, annotations)

    with open(output_path, encoding="utf-8", newline="") as fobj:
        rows = list(csv.DictReader(fobj, delimiter="\t"))

    assert rows == [
        {
            "tu_id": "0",
            "speaker": "SPK1",
            "start": "1.000",
            "end": "1.300",
            "duration": "0.300",
            "text": "first row",
        },
        {
            "tu_id": "1",
            "speaker": "SPK2",
            "start": "2.000",
            "end": "2.500",
            "duration": "0.500",
            "text": "later row",
        },
    ]
    assert annotations["ignore"] == ["1 0", "999 0"]


def test_convert_handles_annotations_without_prefixed_id(tmp_path, monkeypatch):
    fake_doc = [
        FakeTier("SPK1", [FakeAnnotation("plain value", 0.0, 0.2)]),
    ]
    monkeypatch.setattr(eaf2csv.elan, "read_eaf", lambda _: fake_doc)

    output_path = tmp_path / "out.csv"
    annotations = {"ignore": ["42 7"]}

    eaf2csv.convert(tmp_path / "input.eaf", output_path, annotations)

    with open(output_path, encoding="utf-8", newline="") as fobj:
        rows = list(csv.DictReader(fobj, delimiter="\t"))

    assert rows[0]["text"] == "plain value"
    assert annotations["ignore"] == ["42 7"]
