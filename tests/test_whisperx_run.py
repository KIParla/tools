import csv
import json

import pytest

from conftest import load_tool_module

run = load_tool_module("whisperx_run")

META = ["code", "type", "duration", "participants-number", "participants", "unknown-participant"]


@pytest.fixture
def module(tmp_path):
    mod = tmp_path / "Mod"
    (mod / "metadata").mkdir(parents=True)
    (mod / "tsv").mkdir()
    rows = [
        ["C1", "x", "00:01:00", "2", "A;B", "no"],
        ["C2", "x", "00:01:00", "4", "A;B;C;D", "yes"],
        ["C3", "x", "00:01:00", "1", "A", "no"],
        ["C4", "x", "00:01:00", "_", "A;B;C", "no"],      # count falls back to the list
        ["C5", "x", "00:01:00", "2", "A;B", "no"],        # no audio
    ]
    with open(mod / "metadata" / "conversations.tsv", "w", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(META)
        w.writerows(rows)
    audio = tmp_path / "audio"
    (audio / "Mod").mkdir(parents=True)
    for code in ("C1", "C2", "C3"):
        (audio / "Mod" / f"{code}.mp3").write_bytes(b"")
    (audio / "C4.wav").write_bytes(b"")
    return mod, audio, tmp_path / "out"


def test_speaker_range():
    assert run.speaker_range({"participants-number": "3", "unknown-participant": "no"}) == (3, 3, 3)
    assert run.speaker_range({"participants-number": "3", "unknown-participant": "yes"}, slack=1) == (3, 2, 4)
    assert run.speaker_range({"participants-number": "_", "participants": "A;B"}) == (2, 2, 2)
    assert run.speaker_range({"participants-number": "_", "participants": "_"}) == (None, None, None)
    assert run.speaker_range({"participants-number": "1", "unknown-participant": "yes"}) == (1, 1, 1)


def test_plan_jobs(module):
    mod, audio, out = module
    jobs = {j.code: j for j in run.plan_jobs(mod, audio, out)}
    assert (jobs["C1"].min_speakers, jobs["C1"].max_speakers) == (2, 2)
    assert (jobs["C2"].min_speakers, jobs["C2"].max_speakers) == (3, 4)
    assert jobs["C3"].diarize is False
    assert jobs["C4"].n_speakers == 3 and jobs["C4"].audio.name == "C4.wav"
    assert jobs["C5"].audio is None
    assert jobs["C1"].json_path == out / "Mod" / "C1.json"
    assert [j.code for j in run.plan_jobs(mod, audio, out, codes=["C2"])] == ["C2"]


def test_dry_run_prints_plan_and_writes_nothing(module, capsys):
    mod, audio, out = module
    assert run.main([str(mod), "--audio-dir", str(audio), "--output-dir", str(out), "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "Mod/C2\t4\t3-4\ttodo" in text and "Mod/C5\t2\t2-2\tNO AUDIO" in text
    assert not out.exists()


def test_run_skips_done_aligns_and_survives_failures(module, monkeypatch):
    mod, audio, out = module
    with open(mod / "tsv" / "C1.vert.tsv", "w") as f:
        f.write("token_id\tform\ttype\n0-0\tciao\tlinguistic\n")
    calls = []

    class FakeRunner:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, job):
            calls.append((job.code, job.min_speakers, job.max_speakers))
            if job.code == "C2":
                raise RuntimeError("boom")
            words = [{"word": "ciao", "start": 0.0, "end": 1.0, "score": 0.9}]
            return {"segments": [{"start": 0, "end": 1, "text": "ciao", "words": words}]}

    monkeypatch.setattr(run, "Runner", FakeRunner)
    args = [str(mod), "--audio-dir", str(audio), "--output-dir", str(out),
            "--device", "cpu", "--align", "--no-diarize"]
    assert run.main(args) == 1                       # C2 failed
    assert [c[0] for c in calls] == ["C1", "C2", "C3", "C4"]
    assert (out / "Mod" / "C1.json").exists() and not (out / "Mod" / "C2.json").exists()
    assert (out / "Mod" / "C1.aligned.tsv").exists()
    assert (out / "Mod" / "C3.json").exists() and not (out / "Mod" / "C3.aligned.tsv").exists()  # no tsv: align failed, json kept

    calls.clear()
    run.main(args)                                   # resumes: only the failed one is redone
    assert [c[0] for c in calls] == ["C2"]
    wer = list(csv.DictReader(open(out / "Mod" / "wer.tsv"), delimiter="\t"))
    assert [r["code"] for r in wer] == ["C1"] and wer[0]["wer"] == "0.0000"
    assert wer[0]["speaker_map"] == "_" and wer[0]["speaker_agreement"] == "_"  # no diarization here


def test_audio_in_module_audio_dir(module):
    mod, audio, out = module
    (audio / "Mod_audio").mkdir()
    (audio / "Mod_audio" / "C5.mp3").write_bytes(b"")
    jobs = {j.code: j for j in run.plan_jobs(mod, audio, out)}
    assert jobs["C5"].audio.parent.name == "Mod_audio"
