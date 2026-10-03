"""Tests for summarize.py: per-conversation summaries and the module snapshot."""

import json

import pytest

import summarize
from summarize import check_module, summarize_conversation, time_statistics, update_module

COLUMNS = ["token_id", "speaker", "tu_id", "id", "span", "form", "lemma", "upos",
           "xpos", "feats", "deprel", "type", "meta_label", "variation",
           "jefferson_feats", "align", "prolongations", "pace", "guesses", "overlaps"]


def _unit(tu, speaker, begin, end, words, overlaps=None, variation="ContainsVariation=No",
          types=None):
    """Rows of one unit: Begin on the first token, End on the last."""
    rows = []
    for i, w in enumerate(words):
        align = []
        if i == 0:
            align.append(f"Begin={begin}")
        if i == len(words) - 1:
            align.append(f"End={end}")
        rows.append({
            "token_id": f"{tu}-{i}", "speaker": speaker, "tu_id": str(tu), "id": str(i),
            "span": w, "form": w, "type": (types or {}).get(i, "linguistic"),
            "variation": variation, "align": "|".join(align) or "_",
            "overlaps": (overlaps or {}).get(i, "_"),
        })
    return rows


def _write_vert(path, units):
    lines = ["\t".join(COLUMNS)]
    for rows in units:
        for r in rows:
            lines.append("\t".join(r.get(c, "_") for c in COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _module(tmp_path, units_by_code, conversations=None, participants=None):
    (tmp_path / "tsv").mkdir()
    (tmp_path / "metadata").mkdir()
    for code, units in units_by_code.items():
        _write_vert(tmp_path / "tsv" / f"{code}.vert.tsv", units)
    (tmp_path / "metadata" / "conversations.tsv").write_text(
        "code\ttype\tduration\n" + "".join(
            f"{c}\t{t}\t{d}\n" for c, (t, d) in (conversations or {}).items()), encoding="utf-8")
    (tmp_path / "metadata" / "participants.tsv").write_text(
        "code\tgender\n" + "".join(f"{c}\t{g}\n" for c, g in (participants or {}).items()),
        encoding="utf-8")
    return tmp_path


def _out(mod):
    """The summaries repository root used in these tests (inside the module's tmp dir)."""
    return mod / "out"


def _sdir(mod):
    """Where the module's summaries land: <root>/<Module>/."""
    return _out(mod) / mod.name


def _u(key, speaker, begin, end):
    return {"key": key, "speaker": speaker, "begin": begin, "end": end}


class TestTimeStatistics:

    def test_partial_overlap(self):
        ts = time_statistics([_u("a", "A", 0, 10), _u("b", "B", 5, 8)])
        assert ts["speech"] == 10
        assert ts["overlap"] == 3
        assert ts["speaker_speech"] == {"A": 10, "B": 3}
        assert ts["speaker_overlap"] == {"A": 3, "B": 3}
        assert ts["overlapped_units"] == {"a", "b"}

    def test_touching_intervals_do_not_overlap(self):
        ts = time_statistics([_u("a", "A", 0, 5), _u("b", "B", 5, 10)])
        assert ts["overlap"] == 0
        assert ts["speech"] == 10
        assert ts["overlapped_units"] == set()

    def test_same_speaker_overlap_is_counted_once_and_is_not_an_overlap(self):
        ts = time_statistics([_u("a", "A", 0, 6), _u("b", "A", 4, 10)])
        assert ts["speech"] == 10
        assert ts["speaker_speech"] == {"A": 10}
        assert ts["overlap"] == 0
        assert ts["overlapped_units"] == set()

    def test_silence_between_units_is_not_speech(self):
        ts = time_statistics([_u("a", "A", 0, 2), _u("b", "B", 5, 6)])
        assert ts["speech"] == 3

    def test_three_speakers(self):
        ts = time_statistics([_u("a", "A", 0, 10), _u("b", "B", 2, 6), _u("c", "C", 4, 8)])
        assert ts["overlap"] == 6          # 2..8 has at least two speakers
        assert ts["speaker_overlap"]["A"] == 6


class TestSummarizeConversation:

    @pytest.fixture
    def summary(self, tmp_path):
        units = [
            _unit(0, "A", 0.0, 10.0, ["uno", "due", "tre", "quattro"]),
            _unit(1, "B", 5.0, 8.0, ["mh", "(.)"], overlaps={0: "0-2(0)"},
                  types={1: "shortpause"}),
            _unit(2, "A", 12.0, 14.0, ["ciao", "boh"], variation="ContainsVariation=Yes"),
        ]
        _write_vert(tmp_path / "X.vert.tsv", units)
        return summarize_conversation(
            tmp_path / "X.vert.tsv", code="X", module="M",
            metadata={"duration": "0:00:20"}, participants={"A": {"gender": "F"}})

    def test_token_counts(self, summary):
        assert summary["tokens"]["total"] == 8
        assert summary["tokens"]["linguistic"] == 7
        assert summary["tokens"]["by_type"] == {"linguistic": 7, "shortpause": 1}
        assert summary["speakers"]["A"]["tokens"] == 6
        assert summary["speakers"]["B"]["linguistic_tokens"] == 1

    def test_speaking_time_and_rates(self, summary):
        a, b = summary["speakers"]["A"], summary["speakers"]["B"]
        assert a["speech_seconds"] == 12.0          # 0-10 and 12-14
        assert b["speech_seconds"] == 3.0
        assert a["share_of_speech_time"] == 0.8
        assert a["linguistic_tokens_per_second"] == 0.5
        assert a["linguistic_tokens_per_minute"] == 30.0
        assert b["tokens_per_second"] == round(2 / 3, 4)

    def test_overlap_by_time(self, summary):
        t = summary["time"]
        assert t["overlap_seconds"] == 3.0
        assert t["speech_seconds"] == 12.0           # union 0-10, 12-14
        assert t["share_overlap_of_speech"] == 0.25
        assert summary["units"]["overlapped_in_time"] == 2
        assert summary["speakers"]["A"]["share_of_own_speech_overlapped"] == 0.25
        assert summary["speakers"]["B"]["share_of_own_speech_overlapped"] == 1.0

    def test_annotated_overlap(self, summary):
        o = summary["annotated_overlaps"]
        assert o["events"] == 1
        assert o["tokens"] == 1
        assert o["linguistic_tokens"] == 1
        assert o["share_of_linguistic_tokens"] == round(1 / 7, 4)
        assert summary["speakers"]["B"]["annotated_overlap_tokens"] == 1

    def test_span_silence_and_metadata_duration(self, summary):
        t = summary["time"]
        assert t["first_begin"] == 0.0 and t["last_end"] == 14.0
        assert t["span_seconds"] == 14.0
        assert t["silence_seconds"] == 2.0
        assert t["metadata_duration_seconds"] == 20.0

    def test_turns(self, summary):
        assert summary["units"]["turns"] == 3        # A, B, A
        assert summary["speakers"]["A"]["turns"] == 2

    def test_variation_and_participant_metadata(self, summary):
        assert summary["variation"]["units_with_variation"] == 1
        assert summary["speakers"]["A"]["participant"] == {"gender": "F"}
        assert summary["speakers"]["A"]["in_participants_metadata"] is True
        assert summary["speakers"]["B"]["in_participants_metadata"] is False

    def test_unmatched_overlap_spans(self, tmp_path):
        _write_vert(tmp_path / "X.vert.tsv", [
            _unit(0, "A", 0.0, 1.0, ["mh"], overlaps={0: "0-2(?)"})])
        s = summarize_conversation(tmp_path / "X.vert.tsv")
        assert s["annotated_overlaps"]["unmatched_spans"] == 1
        assert s["annotated_overlaps"]["events"] == 0

    def test_unit_without_time_is_reported_not_crashing(self, tmp_path):
        rows = _unit(0, "A", 0.0, 1.0, ["ciao"])
        rows[0]["align"] = "_"
        _write_vert(tmp_path / "X.vert.tsv", [rows])
        s = summarize_conversation(tmp_path / "X.vert.tsv")
        assert s["units"]["without_valid_time"] == 1
        assert s["time"]["speech_seconds"] == 0
        assert s["speakers"]["A"]["tokens_per_second"] is None


class TestModule:

    def _two_conversations(self, tmp_path):
        return _module(
            tmp_path,
            {"C1": [_unit(0, "A", 0.0, 10.0, ["a"] * 10), _unit(1, "B", 5.0, 8.0, ["b"] * 3)],
             "C2": [_unit(0, "A", 0.0, 5.0, ["a"] * 5)]},
            conversations={"C1": ("free-conversation:meal", "0:00:10"),
                           "C2": ("semistructured-interview:x", "0:00:05")},
            participants={"A": "F", "B": "M"})

    def test_snapshot_totals_and_tables(self, tmp_path):
        mod = self._two_conversations(tmp_path)
        update_module(mod, _out(mod))
        assert not (mod / "summaries").exists(), "nothing is written inside the module"
        assert (_sdir(mod) / "C1.json").is_file()
        snap = json.loads(update_module(mod, _out(mod)).read_text(encoding="utf-8"))
        t = snap["totals"]
        assert t["conversations"] == 2 and t["participants"] == 2
        assert t["tokens"] == 18 and t["linguistic_tokens"] == 18
        assert t["speech_seconds"] == 15.0
        assert t["overlap_seconds"] == 3.0
        assert snap["by_conversation_type"]["free-conversation"]["conversations"] == 1
        assert [c["code"] for c in snap["conversations"]] == ["C1", "C2"]
        assert snap["conversations"][0]["metadata"]["type"] == "free-conversation:meal"
        assert len(snap["speakers_in_conversations"]) == 3
        a = next(p for p in snap["participants"] if p["speaker"] == "A")
        assert a["conversations"] == ["C1", "C2"]
        assert a["speech_seconds"] == 15.0 and a["tokens"] == 15
        assert a["participant"] == {"gender": "F"}

    def test_stale_summaries_are_removed_and_single_code_refresh_keeps_others(self, tmp_path):
        mod = self._two_conversations(tmp_path)
        update_module(mod, _out(mod))
        (mod / "tsv" / "C2.vert.tsv").unlink()
        update_module(mod, _out(mod))
        assert not (_sdir(mod) / "C2.json").exists()
        snap = json.loads((_sdir(mod) / "snapshot.json").read_text(encoding="utf-8"))
        assert snap["totals"]["conversations"] == 1
        before = (_sdir(mod) / "C1.json").read_text(encoding="utf-8")
        update_module(mod, _out(mod), ["C1"])
        assert (_sdir(mod) / "C1.json").read_text(encoding="utf-8") == before

    def test_pipeline_report_is_embedded_without_speaker_counts(self, tmp_path):
        mod = self._two_conversations(tmp_path)
        (mod / "tmp" / "process" / "json").mkdir(parents=True)
        (mod / "tmp" / "process" / "json" / "C1.json").write_text(json.dumps({
            "transcript": "C1", "speakers": {"A": {}}, "WARNINGS": {"SWITCHES": 2},
            "ERRORS": {}, "AUDIO_CHECK": {"status": "ok"}}), encoding="utf-8")
        update_module(mod, _out(mod))
        c1 = json.loads((_sdir(mod) / "C1.json").read_text(encoding="utf-8"))
        assert c1["pipeline"]["WARNINGS"] == {"SWITCHES": 2}
        assert c1["pipeline"]["AUDIO_CHECK"] == {"status": "ok"}
        assert "speakers" not in c1["pipeline"]
        c2 = json.loads((_sdir(mod) / "C2.json").read_text(encoding="utf-8"))
        assert "pipeline" not in c2

    def test_output_is_deterministic(self, tmp_path):
        mod = self._two_conversations(tmp_path)
        first = update_module(mod, _out(mod)).read_bytes()
        assert update_module(mod, _out(mod)).read_bytes() == first


class TestCheck:

    def _fresh(self, tmp_path):
        mod = _module(tmp_path, {"C1": [_unit(0, "A", 0.0, 4.0, ["a"] * 4)],
                                 "C2": [_unit(0, "B", 0.0, 2.0, ["b"] * 2)]},
                      conversations={"C1": ("t", "0:00:04"), "C2": ("t", "0:00:02")})
        update_module(mod, _out(mod))
        return mod

    def test_up_to_date_has_no_problems(self, tmp_path):
        assert check_module(*(lambda m: (m, _out(m)))(self._fresh(tmp_path))) == []

    def test_edited_tsv_is_reported_stale(self, tmp_path):
        mod = self._fresh(tmp_path)
        _write_vert(mod / "tsv" / "C1.vert.tsv", [_unit(0, "A", 0.0, 4.0, ["a"] * 5)])
        assert check_module(mod, _out(mod)) == ["C1: summary is older than tsv/C1.vert.tsv"]

    def test_new_and_removed_conversations(self, tmp_path):
        mod = self._fresh(tmp_path)
        _write_vert(mod / "tsv" / "C3.vert.tsv", [_unit(0, "A", 0.0, 1.0, ["a"])])
        (mod / "tsv" / "C2.vert.tsv").unlink()
        problems = check_module(mod, _out(mod))
        assert "C3: no summary" in problems
        assert "C2: summary has no tsv/C2.vert.tsv" in problems
        assert any(p.startswith("snapshot.json: lists 2") for p in problems)

    def test_missing_snapshot(self, tmp_path):
        mod = self._fresh(tmp_path)
        (_sdir(mod) / "snapshot.json").unlink()
        assert check_module(mod, _out(mod)) == ["snapshot.json: missing"]
