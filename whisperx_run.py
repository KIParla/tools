#!/usr/bin/env python3
"""
whisperx_run.py — run WhisperX (transcription + forced alignment + speaker
diarization) on the audio of KIParla conversations, telling the diarizer how
many speakers to expect, and optionally align each result with its .vert.tsv.

For every conversation of a module (rows of ``metadata/conversations.tsv``):

  1. the audio is looked up as ``<audio_dir>/<Module>/<code>.<ext>``, then
     ``<audio_dir>/<Module>_audio/<code>.<ext>``, then ``<audio_dir>/<code>.<ext>``;
  2. the number of speakers comes from the ``participants-number`` column
     (falling back to the length of ``participants``):
         max_speakers = n + slack
         min_speakers = n, or n-1 when ``unknown-participant`` is ``yes`` (the
                        placeholder speaker may not be an audible voice of its own)
     with one speaker, diarization is skipped;
  3. WhisperX writes ``<output_dir>/<Module>/<code>.json`` (segments with word
     timestamps and ``speaker`` labels, the format ``whisperx_align.py`` reads);
  4. with ``--align``, the result is aligned with ``<module>/tsv/<code>.vert.tsv``
     (``whisperx_align.py``): ``<code>.aligned.tsv`` next to the JSON, and one
     line per conversation in ``<output_dir>/<Module>/wer.tsv``.

The models are loaded once and reused for all conversations. Conversations
whose JSON already exists are skipped (``--force`` to redo them), so an
interrupted run can simply be restarted; a failing conversation is reported
and does not stop the others.

Speaker diarization needs a Hugging Face token (pyannote models; accept their
terms on huggingface.co first): ``--hf-token`` or the ``HF_TOKEN`` variable.

Usage:
    python whisperx_run.py moduli/KIP --audio-dir /data/audio --output-dir whisperx-out
    python whisperx_run.py moduli/KIP moduli/ParlaTO --audio-dir ... --output-dir ... --align
    python whisperx_run.py moduli/KIP --audio-dir ... --output-dir ... --dry-run
    python whisperx_run.py moduli/KIP --audio-dir ... --output-dir ... --codes BOA1001 BOA1002

Requires ``pip install whisperx`` (not needed for ``--dry-run``).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from audio_check import find_audio
from textutil import ENCODING_READ, ENCODING_WRITE


@dataclass
class Job:
    module: str
    code: str
    audio: Optional[Path]
    n_speakers: Optional[int]
    min_speakers: Optional[int]
    max_speakers: Optional[int]
    json_path: Path
    tsv_path: Path

    @property
    def diarize(self) -> bool:
        return bool(self.max_speakers and self.max_speakers > 1)


def speaker_range(row: dict, slack: int = 0) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(n, min_speakers, max_speakers) for a conversations.tsv row."""
    n = None
    value = (row.get("participants-number") or "").strip()
    if value.isdigit():
        n = int(value)
    else:
        listed = [p for p in (row.get("participants") or "").split(";") if p and p != "_"]
        n = len(listed) or None
    if not n:
        return None, None, None
    unknown = (row.get("unknown-participant") or "").strip().lower() == "yes"
    low = max(1, n - 1) if unknown and n > 1 else n
    return n, low, n + slack


def plan_jobs(module_dir: Path, audio_dir: Path, output_dir: Path,
              codes: Optional[list[str]] = None, slack: int = 0) -> list[Job]:
    module_dir = Path(module_dir)
    module = module_dir.name
    meta = module_dir / "metadata" / "conversations.tsv"
    with open(meta, encoding=ENCODING_READ, newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    jobs = []
    for row in rows:
        code = row["code"]
        if codes and code not in codes:
            continue
        audio = None
        for d in (Path(audio_dir) / module, Path(audio_dir) / f"{module}_audio", Path(audio_dir)):
            audio = find_audio(d, code)
            if audio:
                break
        n, low, high = speaker_range(row, slack)
        jobs.append(Job(
            module=module, code=code, audio=audio,
            n_speakers=n, min_speakers=low, max_speakers=high,
            json_path=Path(output_dir) / module / f"{code}.json",
            tsv_path=module_dir / "tsv" / f"{code}.vert.tsv",
        ))
    return jobs


# ---------------------------------------------------------------------------
# WhisperX
# ---------------------------------------------------------------------------

class Runner:
    """Loads the WhisperX models once and transcribes conversations."""

    def __init__(self, model: str, language: str, device: str, compute_type: str,
                 batch_size: int, hf_token: Optional[str], diarize: bool):
        import whisperx  # imported lazily: not needed for --dry-run

        self.whisperx = whisperx
        self.device = device
        self.language = language
        self.batch_size = batch_size
        self.model = whisperx.load_model(model, device, compute_type=compute_type,
                                         language=language)
        self.align_model, self.align_meta = whisperx.load_align_model(
            language_code=language, device=device)
        self.diarizer = self._load_diarizer(hf_token) if diarize else None

    def _load_diarizer(self, hf_token: Optional[str]):
        if not hf_token:
            raise SystemExit("speaker diarization needs a Hugging Face token: "
                             "pass --hf-token or set HF_TOKEN (or use --no-diarize)")
        wx = self.whisperx
        cls = getattr(wx, "DiarizationPipeline", None)
        if cls is None:  # newer releases moved it
            from whisperx.diarize import DiarizationPipeline as cls
        try:
            return cls(use_auth_token=hf_token, device=self.device)
        except TypeError:  # newer releases renamed the argument
            return cls(token=hf_token, device=self.device)

    def transcribe(self, job: Job) -> dict:
        wx = self.whisperx
        audio = wx.load_audio(str(job.audio))
        result = self.model.transcribe(audio, batch_size=self.batch_size,
                                       language=self.language)
        result = wx.align(result["segments"], self.align_model, self.align_meta,
                          audio, self.device, return_char_alignments=False)
        if self.diarizer is not None and job.diarize:
            kwargs = {"min_speakers": job.min_speakers, "max_speakers": job.max_speakers}
            diarization = self.diarizer(audio, **kwargs)
            result = wx.assign_word_speakers(diarization, result)
        return {"segments": result["segments"], "language": self.language,
                "min_speakers": job.min_speakers, "max_speakers": job.max_speakers}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding=ENCODING_WRITE) as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # never leave a half-written JSON that looks finished


WER_COLUMNS = ["code", "reference_tokens", "match", "mismatch", "tsv_only",
               "whisper_only", "wer", "speaker_map", "speaker_agreement"]


def upsert_wer(path: Path, job: Job, stats: dict) -> None:
    """Write/replace this conversation's line in wer.tsv (re-runs don't duplicate)."""
    rows = {}
    if path.exists():
        with open(path, encoding=ENCODING_READ, newline="") as f:
            rows = {r["code"]: r for r in csv.DictReader(f, delimiter="\t")}
    rows[job.code] = {"code": job.code, "reference_tokens": stats["reference_tokens"],
                      "match": stats["match"], "mismatch": stats["mismatch"],
                      "tsv_only": stats["tsv_only"], "whisper_only": stats["whisper_only"],
                      "wer": f"{stats['wer']:.4f}",
                      "speaker_map": stats.get("speaker_map") or "_",
                      "speaker_agreement": (f"{stats['speaker_agreement']:.4f}"
                                            if stats.get("speaker_agreement") is not None else "_")}
    with open(path, "w", encoding=ENCODING_WRITE, newline="") as f:
        w = csv.DictWriter(f, fieldnames=WER_COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows[c] for c in sorted(rows))


def print_plan(jobs: list[Job], force: bool) -> None:
    print("code\tspeakers\tmin-max\tstatus")
    for j in jobs:
        if j.audio is None:
            status = "NO AUDIO"
        elif j.json_path.exists() and not force:
            status = "done"
        else:
            status = "todo"
        rng = f"{j.min_speakers}-{j.max_speakers}" if j.max_speakers else "?"
        print(f"{j.module}/{j.code}\t{j.n_speakers or '?'}\t{rng}\t{status}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modules", nargs="+", type=Path, help="module directories")
    ap.add_argument("--audio-dir", required=True, type=Path,
                    help="audio files, as <dir>/<code>.<ext>, <dir>/<Module>/<code>.<ext> or <dir>/<Module>_audio/<code>.<ext>")
    ap.add_argument("--output-dir", required=True, type=Path,
                    help="where <Module>/<code>.json is written")
    ap.add_argument("--codes", nargs="+", help="only these conversation codes")
    ap.add_argument("--align", action="store_true",
                    help="also align each result with the module's tsv (whisperx_align.py)")
    ap.add_argument("--force", action="store_true", help="redo conversations that have a JSON")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    ap.add_argument("--slack", type=int, default=0,
                    help="extra speakers allowed above participants-number (default 0)")
    ap.add_argument("--no-diarize", action="store_true", help="skip speaker diarization")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default="it")
    ap.add_argument("--device", default=None, help="cuda or cpu (default: cuda if available)")
    ap.add_argument("--compute-type", default=None,
                    help="default: float16 on cuda, int8 on cpu")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"))
    args = ap.parse_args(argv)

    jobs: list[Job] = []
    for module in args.modules:
        jobs.extend(plan_jobs(module, args.audio_dir, args.output_dir,
                              args.codes, args.slack))
    print_plan(jobs, args.force)
    if args.dry_run:
        return 0

    todo = [j for j in jobs if j.audio and (args.force or not j.json_path.exists())]
    if args.align:  # also align conversations whose JSON is already there
        todo_align = [j for j in jobs if j.json_path.exists() and j not in todo]
    else:
        todo_align = []
    if not todo and not todo_align:
        print("nothing to do", file=sys.stderr)
        return 0

    runner = None
    if todo:
        device = args.device
        if device is None:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = args.compute_type or ("float16" if device == "cuda" else "int8")
        runner = Runner(args.model, args.language, device, compute_type, args.batch_size,
                        args.hf_token, diarize=not args.no_diarize)

    failures = []
    for i, job in enumerate(todo + todo_align, 1):
        print(f"[{i}/{len(todo) + len(todo_align)}] {job.module}/{job.code}", file=sys.stderr)
        try:
            if job in todo:
                write_json(job.json_path, runner.transcribe(job))
            if args.align:
                from whisperx_align import align_files
                out = job.json_path.with_name(f"{job.code}.aligned.tsv")
                stats = align_files(str(job.json_path), str(job.tsv_path), str(out),
                                    verbose=False)
                upsert_wer(job.json_path.with_name("wer.tsv"), job, stats)
                print(f"  WER {stats['wer']:.2%}", file=sys.stderr)
        except Exception as exc:  # keep going: one bad file must not stop a long run
            failures.append((job, exc))
            print(f"  FAILED: {exc!r}", file=sys.stderr)

    missing = [j for j in jobs if j.audio is None]
    if missing:
        print(f"{len(missing)} conversations without audio: "
              + ", ".join(j.code for j in missing[:10]) + (" ..." if len(missing) > 10 else ""),
              file=sys.stderr)
    if failures:
        print(f"{len(failures)} failed: " + ", ".join(j.code for j, _ in failures),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
