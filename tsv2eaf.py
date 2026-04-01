import csv
import sys
import re
import os
import pathlib

from pympi import Elan as EL

# --- regexes to read Begin/End from the 'align' column ---
BEGIN_RE = re.compile(r"\bBegin\s*=\s*([0-9]+(?:\.[0-9]+)?)")
END_RE   = re.compile(r"\bEnd\s*=\s*([0-9]+(?:\.[0-9]+)?)")
_FIELD = "tu_id"
_ALIGN_FIELD = "align"
# _FIELD = "iu_id"
# _ALIGN_FIELD = "iu_align"

def parse_begin(text):
	"""Return float or None from an align string."""
	if not text or text == "_":
		return None
	m = BEGIN_RE.search(text)
	return float(m.group(1)) if m else None

def parse_end(text):
	"""Return float or None from an align string."""
	if not text or text == "_":
		return None
	m = END_RE.search(text)
	return float(m.group(1)) if m else None

def extract_annotations(input_path):
	"""Read a .tsv file and return speakers plus grouped TU annotations."""
	to_print = []
	speakers = set()
	file_basename = pathlib.Path(input_path).stem

	with open(input_path, encoding="utf-8") as fin:
		csvFile = csv.DictReader(fin, delimiter='\t')
		tu_id = 0
		tu_speaker = None
		tu_begin = None
		tu_end = None
		text_jefferson = []

		def flush_tu():
			if len(text_jefferson):
				text_jefferson_str = re.sub(r' +', ' ', ''.join(text_jefferson).strip())
				if tu_begin is None:
					print(f"[WARN] {file_basename} TU {tu_id} ({tu_speaker}) missing Begin=", file=sys.stderr)
					raise SystemExit(1)
				if tu_end is None:
					print(f"[WARN] {file_basename} TU {tu_id} ({tu_speaker}) missing End=", file=sys.stderr)
					raise SystemExit(1)
				to_print.append((tu_speaker, tu_begin, tu_end, text_jefferson_str))

		for row in csvFile:
			current_id = int(row[_FIELD])
			current_speaker = row['speaker']
			align = row.get(_ALIGN_FIELD, '')

			if tu_speaker is None:
				tu_speaker = current_speaker
				speakers.add(tu_speaker)

			if current_id != tu_id:
				if len(text_jefferson):
					flush_tu()

				tu_id = current_id
				tu_speaker = current_speaker
				speakers.add(tu_speaker)
				text_jefferson = []
				tu_begin = None
				tu_end = None

			b = parse_begin(align)
			e = parse_end(align)
			if b is not None and tu_begin is None:
				tu_begin = b
			if e is not None:
				tu_end = e

			if row['type'] in ['nonverbalbehavior', 'shortpause', 'unknown', 'linguistic', 'error']:
				text_jefferson.append(row['span'])

			jf = row.get('jefferson_feats', '') or ''
			if 'ProsodicLink=Yes' in jf:
				text_jefferson.append('=')
			elif 'SpaceAfter=No' not in jf:
				text_jefferson.append(' ')

		if len(text_jefferson):
			flush_tu()

	return speakers, to_print


def convert_tsv_to_eaf(input_path, output_dir="eaf"):
	"""Convert a pipeline TSV file into EAF and return the output path."""
	input_path = pathlib.Path(input_path)
	output_dir = pathlib.Path(output_dir)
	output_dir.mkdir(parents=True, exist_ok=True)

	speakers, annotations = extract_annotations(input_path)
	doc = EL.Eaf(author="automatic_pipeline")
	for tier_id in speakers:
		doc.add_tier(tier_id=tier_id)

	for annotation in annotations:
		value = annotation[3]
		start = int(float(annotation[1]) * 1000)
		end = int(float(annotation[2]) * 1000)
		if end - start < 0:
			print(
				f"[WARN] {input_path.stem} Negative duration for {annotation[0]} {start} {end} {value}",
				file=sys.stderr,
			)
			raise SystemExit(1)

		doc.add_annotation(
			id_tier=annotation[0],
			start=start,
			end=end,
			value=value,
		)

	output_path = output_dir / f"{input_path.stem}.eaf"
	doc.to_file(output_path)
	return output_path


def main(argv=None):
	import argparse
	parser = argparse.ArgumentParser(
		description="Convert .vert.tsv file(s) to ELAN .eaf format."
	)
	parser.add_argument(
		"-i", "--input",
		required=True,
		help="Input .vert.tsv file or folder containing such files.",
	)
	parser.add_argument(
		"-o", "--output",
		default="eaf",
		help="Output folder for .eaf files (default: eaf/).",
	)
	args = parser.parse_args(sys.argv[1:] if argv is None else argv)

	p = pathlib.Path(args.input)
	if p.is_dir():
		files = sorted(p.glob("*.vert.tsv"))
		if not files:
			print(f"No *.vert.tsv files found in {p}", file=sys.stderr)
			raise SystemExit(1)
	elif p.is_file():
		files = [p]
	else:
		print(f"Input path does not exist: {p}", file=sys.stderr)
		raise SystemExit(1)

	for f in files:
		print(f"Processing {f}", file=sys.stderr)
		convert_tsv_to_eaf(f, output_dir=args.output)


if __name__ == "__main__":
	main()
