"""Command-line interface for kiparla-tools."""
from __future__ import annotations

import argparse
import collections
import json
import logging
import pathlib

import tqdm
import yaml

import args_check as ac
import serialize
import alignment as align_mod
from data import Transcript, TranscriptionUnit

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _eaf2csv(args):
    input_files = list(args.input_dir.glob("*.eaf")) if args.input_dir else list(args.input_files)

    annotations_map = {}
    if args.units_annotations_dir:
        for f in input_files:
            p = pathlib.Path(args.units_annotations_dir) / f"{f.stem}.yml"
            annotations_map[f.stem] = serialize.load_annotations(p) if p.is_file() else {}

    for filename in tqdm.tqdm(input_files, desc="eaf2csv"):
        output_fname = args.output_dir / f"{filename.stem}.csv"
        annotations = annotations_map.get(filename.stem, {})
        serialize.eaf2csv(filename, output_fname, annotations)

        if annotations and args.units_annotations_dir:
            out_yml = pathlib.Path(args.units_annotations_dir) / f"{filename.stem}.yml"
            with open(out_yml, "w", encoding="utf-8") as yf:
                yaml.dump(annotations, yf, indent=2)


def _csv2eaf(args):
    input_files = list(args.input_dir.glob("*.csv")) if args.input_dir else list(args.input_files)

    for filename in tqdm.tqdm(input_files, desc="csv2eaf"):
        basename = filename.stem
        if basename.endswith(".tus"):
            basename = basename[:-4]

        suffix = ".ids.eaf" if args.include_ids else ".eaf"
        output_fname = args.output_dir / f"{basename}{suffix}"
        audio_fname = f"{basename}.wav"
        if args.audio_dir:
            audio_fname = args.audio_dir / f"{basename}.wav"

        serialize.csv2eaf(filename, str(audio_fname), output_fname,
                          args.delimiter, args.multiplier_factor, args.include_ids)


def _process(args):
    input_files = list(args.input_dir.glob("*.csv")) if args.input_dir else list(args.input_files)

    annotations = collections.defaultdict(dict)
    if args.units_annotations_dir:
        for f in input_files:
            p = pathlib.Path(args.units_annotations_dir) / f"{f.stem}.yml"
            if p.is_file():
                annotations[f.stem] = serialize.load_annotations(p)

    output_json = args.output_dir / "summary.json"
    full_data = []
    transcripts = {}

    for filename in tqdm.tqdm(input_files, desc="process"):
        name = filename.stem
        transcript = _process_transcript(filename, annotations[name],
                                         duration_threshold=args.duration_threshold)
        transcripts[name] = transcript

        serialize.conversation_to_conll(transcript, args.output_dir / f"{name}.vert.tsv")
        serialize.conversation_to_linear(transcript, args.output_dir / f"{name}.csv")
        full_data.append(serialize.build_json(transcript))

    with open(output_json, "w", encoding="utf-8") as jf:
        print(json.dumps(full_data, indent=2, ensure_ascii=False), file=jf)

    if args.produce_stats:
        serialize.print_full_statistics(transcripts, args.output_dir / "stats.csv")


def _process_transcript(filename, annotations, duration_threshold=0.1,
                         tiers_to_ignore=("Traduzione",)):
    """Load, sort, tokenize and resolve overlaps for one transcript."""
    import itertools

    relations_to_ignore = []
    for element in annotations.get("ignore", []):
        relations_to_ignore.extend(
            itertools.combinations([int(x) for x in element.split()], 2)
        )

    transcript = Transcript(filename.stem)
    for tu_id, speaker, start, end, duration, annotation in serialize.read_csv(filename):
        if speaker not in tiers_to_ignore:
            transcript.add(TranscriptionUnit(tu_id, speaker, start, end, duration, annotation))

    transcript.sort()
    transcript.find_overlaps(duration_threshold=duration_threshold)
    for tu in transcript:
        tu.tokenize()
    transcript.check_overlaps(duration_threshold, relations_to_ignore)
    for tu in transcript:
        tu.add_token_features()
    return transcript


def _align(args):
    input_files = list(args.input_dir.glob("*.csv")) if args.input_dir else list(args.input_files)

    transcripts = {}
    for filename in tqdm.tqdm(input_files, desc="loading"):
        transcripts[filename.stem] = serialize.transcript_from_csv(filename)

    # Build ordered unique pairs sharing the same conversation basename
    ordered_pairs = []
    names = list(transcripts)
    for i, t1 in enumerate(names):
        for t2 in names[i + 1:]:
            base1 = t1.split(".")[0].split("_")[1] if "_" in t1 else t1
            base2 = t2.split(".")[0].split("_")[1] if "_" in t2 else t2
            if base1 != base2:
                continue
            try:
                n1, n2 = int(t1.split("_")[0]), int(t2.split("_")[0])
            except ValueError:
                n1, n2 = 0, 1
            pair = (t1, t2) if n1 > n2 else (t2, t1)
            if pair not in ordered_pairs:
                ordered_pairs.append(pair)

    for t1, t2 in tqdm.tqdm(ordered_pairs, desc="aligning"):
        tokens_a, tokens_b = align_mod.align_transcripts(transcripts[t1], transcripts[t2])
        out = pathlib.Path(args.output_dir) / f"{t1}_{t2}.tsv"
        serialize.print_aligned(tokens_a, tokens_b, out)


def _cicle(args):
    """Full cycle: corrected EAF → CSV → vert.tsv → new EAF."""
    for filename in tqdm.tqdm(list(args.eaf_dir.glob("*.eaf")), desc="eaf→csv"):
        serialize.eaf2csv(filename, args.csv_dir / f"{filename.stem}.csv", {})

    transcripts = {}
    for filename in tqdm.tqdm(list(args.csv_dir.glob("*.csv")), desc="process"):
        name = filename.stem
        transcript = _process_transcript(filename, {})
        transcripts[name] = transcript
        serialize.conversation_to_conll(transcript, args.output_dir / f"{name}.vert.tsv")
        serialize.conversation_to_linear(transcript, args.output_dir / f"{name}.tus.csv")

    for filename in tqdm.tqdm(list(args.output_dir.glob("*.csv")), desc="csv→eaf"):
        basename = filename.stem
        if basename.endswith(".tus"):
            basename = basename[:-4]
        serialize.csv2eaf(filename, "data/audio/PARLABOA.wav",
                          args.eaf_dir / f"{basename}.eaf", "\t", 1000, True)


def _conll2conllu(args):
    input_files = list(args.input_dir.glob("*.vert.tsv")) if args.input_dir else list(args.input_files)
    for filename in tqdm.tqdm(input_files, desc="conll2conllu"):
        serialize.conll2conllu(filename, args.output_dir / filename.name)


# ---------------------------------------------------------------------------
# NLP commands (optional — require spacy_udpipe / wtpsplit)
# ---------------------------------------------------------------------------

def _segment(args):
    try:
        from wtpsplit import SaT
        from linguistic_pipeline import segment
    except ImportError as e:
        raise SystemExit(f"segment command requires wtpsplit and linguistic_pipeline: {e}")

    input_files = list(args.input_dir.glob("*vert.csv")) if args.input_dir else list(args.input_files)
    sat_sm = SaT("sat-12l-sm")
    for filename in tqdm.tqdm(input_files, desc="segment"):
        segment(sat_sm, filename, args.output_dir / f"{filename.stem}.vert.tsv",
                args.remove_metalinguistic)


def _parse(args):
    try:
        import spacy_udpipe
        import spacy_conll
        from linguistic_pipeline import parse
    except ImportError as e:
        raise SystemExit(f"parse command requires spacy_udpipe and linguistic_pipeline: {e}")

    input_files = list(args.input_dir.glob("*.vert.tsv")) if args.input_dir else list(args.input_files)
    nlp = spacy_udpipe.load_from_path(lang="it", path=args.udpipe_model,
                                       meta={"description": "Custom 'it' model"})
    nlp.add_pipe("conll_formatter", last=True)
    for filename in tqdm.tqdm(input_files, desc="parse"):
        parse(nlp, filename, args.output_dir / filename.name, args.remove_metalinguistic)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _input_group(parser):
    group = parser.add_argument_group("Input files")
    ex = group.add_mutually_exclusive_group(required=True)
    ex.add_argument("--input-files", nargs="+", type=ac.valid_filepath)
    ex.add_argument("--input-dir", type=ac.valid_dirpath)
    return parser


def main():
    root = argparse.ArgumentParser(prog="kiparla-tools")
    sub = root.add_subparsers(title="actions", dest="actions")

    # eaf2csv
    p = sub.add_parser("eaf2csv", help="convert EAF to CSV")
    p.add_argument("-o", "--output-dir", default="output/", type=ac.valid_dirpath)
    p.add_argument("--units-annotations-dir", type=ac.valid_dirpath)
    _input_group(p)
    p.set_defaults(func=_eaf2csv)

    # csv2eaf
    p = sub.add_parser("csv2eaf", help="convert CSV to EAF")
    p.add_argument("-o", "--output-dir", default="output_eaf/", type=ac.valid_dirpath)
    p.add_argument("-a", "--audio-dir", type=ac.valid_dirpath)
    p.add_argument("-d", "--delimiter", type=str, default="\t")
    p.add_argument("-m", "--multiplier-factor", type=int, default=1000)
    p.add_argument("--include-ids", action="store_true")
    _input_group(p)
    p.set_defaults(func=_csv2eaf)

    # process
    p = sub.add_parser("process", help="run full processing pipeline on transcripts")
    p.add_argument("-o", "--output-dir", default="output/", type=ac.valid_dirpath)
    p.add_argument("-t", "--duration-threshold", type=float, default=0.1)
    p.add_argument("-s", "--produce-stats", action="store_true")
    p.add_argument("--units-annotations-dir", type=ac.valid_dirpath)
    _input_group(p)
    p.set_defaults(func=_process)

    # align
    p = sub.add_parser("align", help="align pairs of transcripts")
    p.add_argument("-o", "--output-dir", default="output_aligned/", type=ac.valid_dirpath)
    _input_group(p)
    p.set_defaults(func=_align)

    # cicle
    p = sub.add_parser("cicle", help="full EAF→CSV→vert.tsv→EAF cycle")
    p.add_argument("-e", "--eaf-dir", required=True, type=ac.valid_dirpath)
    p.add_argument("-c", "--csv-dir", required=True, type=ac.valid_dirpath)
    p.add_argument("-o", "--output-dir", required=True, type=ac.valid_dirpath)
    p.set_defaults(func=_cicle)

    # conll2conllu
    p = sub.add_parser("conll2conllu", help="convert CoNLL TSV to CoNLL-U")
    p.add_argument("-o", "--output-dir", required=True, type=ac.valid_dirpath)
    _input_group(p)
    p.set_defaults(func=_conll2conllu)

    # segment (optional NLP)
    p = sub.add_parser("segment", help="segment into maximal units (requires wtpsplit)")
    p.add_argument("-o", "--output-dir", required=True, type=ac.valid_dirpath)
    p.add_argument("--remove-metalinguistic", action="store_true")
    _input_group(p)
    p.set_defaults(func=_segment)

    # parse (optional NLP)
    p = sub.add_parser("parse", help="parse with UDPipe (requires spacy_udpipe)")
    p.add_argument("-o", "--output-dir", required=True, type=ac.valid_dirpath)
    p.add_argument("--remove-metalinguistic", action="store_true")
    p.add_argument("--udpipe-model", required=True)
    _input_group(p)
    p.set_defaults(func=_parse)

    args = root.parse_args()
    if "func" not in args:
        root.print_usage()
        raise SystemExit(0)
    args.func(args)


if __name__ == "__main__":
    main()
