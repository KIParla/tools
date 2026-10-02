"""Tests for audio_check.py: last annotation vs. recording length."""

from pathlib import Path

import pytest

import audio_check
from audio_check import evaluate, parse_duration


class _TU:
    def __init__(self, end, include=True):
        self.end, self.include = end, include


class _Transcript:
    def __init__(self, *ends):
        self.transcription_units = [_TU(e) for e in ends]


def _module(tmp_path, durations, vert_ends=None):
    """A minimal module dir: metadata/conversations.tsv (+ optional tsv/*.vert.tsv)."""
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata" / "conversations.tsv").write_text(
        "code\tduration\n" + "".join(f"{c}\t{d}\n" for c, d in durations.items()),
        encoding="utf-8")
    if vert_ends:
        (tmp_path / "tsv").mkdir()
        for code, end in vert_ends.items():
            (tmp_path / "tsv" / f"{code}.vert.tsv").write_text(
                "token_id\talign\n"
                "0-0\tBegin=1.0\n"
                f"0-1\tEnd={end}\n", encoding="utf-8")
    return tmp_path


class TestParseDuration:

    @pytest.mark.parametrize("text, expected", [
        ("01:02:03", 3723.0), ("02:03", 123.0), ("45", 45.0), ("0:46:42", 2802.0),
    ])
    def test_formats(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["", "_", None, "abc", "1:xx"])
    def test_unparseable_is_none(self, text):
        assert parse_duration(text) is None


class TestEvaluate:

    def test_ok_within_tolerances(self):
        assert evaluate(1000.0, 1010.0, "audio")["status"] == "ok"
        assert evaluate(1000.5, 1000.0, "audio")["status"] == "ok"   # < 1 s overrun

    def test_overrun_when_annotations_run_past_the_audio(self):
        r = evaluate(1005.0, 1000.0, "audio")
        assert r["status"] == "overrun"
        assert r["difference_seconds"] == -5.0

    def test_underrun_when_annotations_end_long_before_the_audio(self):
        assert evaluate(1000.0, 1000.0 + 121, "audio")["status"] == "underrun"
        assert evaluate(1000.0, 1000.0 + 119, "audio")["status"] == "ok"

    def test_metadata_source_allows_extra_overrun(self):
        # a duration rounded to the minute may be up to ~60 s short
        assert evaluate(1030.0, 1000.0, "metadata")["status"] == "ok"
        assert evaluate(1030.0, 1000.0, "audio")["status"] == "overrun"
        assert evaluate(1070.0, 1000.0, "metadata")["status"] == "overrun"

    def test_unavailable_without_length_or_annotations(self):
        assert evaluate(None, 1000.0, "audio")["status"] == "unavailable"
        assert evaluate(1000.0, None, None)["status"] == "unavailable"

    def test_thresholds_come_from_config(self):
        cfg = {"audio_check": {"max_underrun": 10.0}}
        assert evaluate(1000.0, 1020.0, "audio", cfg)["status"] == "underrun"


class TestAudioLength:

    def test_metadata_duration_used_without_audio_dir(self, tmp_path):
        m = _module(tmp_path, {"C1": "00:10:00"})
        assert audio_check.audio_length("C1", m, None) == (600.0, "metadata")

    def test_audio_file_wins_over_metadata(self, tmp_path, monkeypatch):
        mod = tmp_path / "mod"; mod.mkdir()
        m = _module(mod, {"C1": "00:10:00"})
        audio = tmp_path / "audio"; audio.mkdir()
        (audio / "C1.mp3").write_bytes(b"")
        monkeypatch.setattr(audio_check, "probe_duration", lambda p: 654.3)
        assert audio_check.audio_length("C1", m, audio) == (654.3, "audio")

    def test_falls_back_to_metadata_when_probe_fails(self, tmp_path, monkeypatch):
        mod = tmp_path / "mod"; mod.mkdir()
        m = _module(mod, {"C1": "00:10:00"})
        audio = tmp_path / "audio"; audio.mkdir()
        (audio / "C1.wav").write_bytes(b"")
        monkeypatch.setattr(audio_check, "probe_duration", lambda p: None)
        assert audio_check.audio_length("C1", m, audio) == (600.0, "metadata")

    def test_nothing_available(self, tmp_path):
        m = _module(tmp_path, {})
        assert audio_check.audio_length("C1", m, None) == (None, None)


class TestAnnotateSummary:

    def test_overrun_adds_warning_and_check(self, tmp_path):
        m = _module(tmp_path, {"C1": "00:10:00"})            # 600 s
        summary = {"transcript": "C1", "WARNINGS": {}}
        audio_check.annotate_summary(summary, _Transcript(100.0, 700.0), m)
        assert summary["WARNINGS"] == {"AUDIO_OVERRUN": 1}
        assert summary["AUDIO_CHECK"]["status"] == "overrun"
        assert summary["AUDIO_CHECK"]["source"] == "metadata"

    def test_underrun_adds_warning(self, tmp_path):
        m = _module(tmp_path, {"C1": "01:00:00"})
        summary = {"transcript": "C1", "WARNINGS": {}}
        audio_check.annotate_summary(summary, _Transcript(2100.0), m)
        assert summary["WARNINGS"] == {"AUDIO_UNDERRUN": 1}

    def test_ok_adds_only_the_check(self, tmp_path):
        m = _module(tmp_path, {"C1": "00:10:00"})
        summary = {"transcript": "C1", "WARNINGS": {}}
        audio_check.annotate_summary(summary, _Transcript(590.0), m)
        assert summary["WARNINGS"] == {}
        assert summary["AUDIO_CHECK"]["status"] == "ok"

    def test_excluded_units_are_ignored(self, tmp_path):
        m = _module(tmp_path, {"C1": "00:10:00"})
        t = _Transcript(590.0); t.transcription_units.append(_TU(9999.0, include=False))
        summary = {"transcript": "C1", "WARNINGS": {}}
        audio_check.annotate_summary(summary, t, m)
        assert summary["AUDIO_CHECK"]["status"] == "ok"

    def test_disabled_by_config(self, tmp_path):
        m = _module(tmp_path, {"C1": "00:10:00"})
        summary = {"transcript": "C1", "WARNINGS": {}}
        audio_check.annotate_summary(summary, _Transcript(9999.0), m,
                                     cfg={"audio_check": {"enabled": False}})
        assert "AUDIO_CHECK" not in summary and summary["WARNINGS"] == {}


class TestCheckModule:

    def test_reads_last_end_from_vert_files(self, tmp_path):
        m = _module(tmp_path, {"A": "00:10:00", "B": "00:10:00", "C": "00:30:00"},
                    vert_ends={"A": 590.0, "B": 700.0, "C": 600.0})
        got = {r["code"]: r["status"] for r in audio_check.check_module(m)}
        assert got == {"A": "ok", "B": "overrun", "C": "underrun"}


def test_default_config_has_audio_check_block():
    import config
    cfg = config.load_config(None)
    assert cfg["audio_check"]["enabled"] is True
    assert cfg["audio_check"]["max_underrun"] == 120.0


def test_update_summary_writes_check_and_warnings(tmp_path):
    import json
    m = _module(tmp_path, {"A": "00:10:00", "B": "00:10:00"},
                vert_ends={"A": 590.0, "B": 700.0})
    out = tmp_path / "tmp" / "process" / "json"
    out.mkdir(parents=True)
    (out / "summary.json").write_text(json.dumps([
        {"transcript": "A", "WARNINGS": {"AUDIO_OVERRUN": 1, "ACCENTS": 2}},   # stale rule
        {"transcript": "B", "WARNINGS": {}},
    ]), encoding="utf-8")
    assert audio_check.update_summary(m, audio_check.check_module(m)) == 2
    data = {e["transcript"]: e for e in json.loads((out / "summary.json").read_text())}
    assert data["A"]["AUDIO_CHECK"]["status"] == "ok"
    assert data["A"]["WARNINGS"] == {"ACCENTS": 2}          # stale AUDIO_* removed, others kept
    assert data["B"]["WARNINGS"] == {"AUDIO_OVERRUN": 1}
