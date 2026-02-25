import csv
import sys
import re
import os
import pathlib

# orthographic = []
# jefferson = []

def tsv2linear(input_files_paths, output_jefferson_path, output_orthographic_path):

	for filename in input_files_paths:
		p = pathlib.Path(filename)
		basename = p.name.replace(".vert.tsv", "")

		with open(filename) as fin, \
			open(output_jefferson_path / f"{basename}.txt", "w") as fout_jeff, \
			open(output_orthographic_path / f"{basename}.txt", "w") as fout_ortho:

			csvFile = csv.DictReader(fin, delimiter='\t')
			tu_id = 0
			tu_speaker = None
			text_jefferson = []
			text_orthographic = []

			for row in csvFile:
				current_id = int(row['tu_id'])
				current_speaker = row['speaker']
				if tu_speaker is None:
					tu_speaker = current_speaker

				if not current_id == tu_id:
					if len(text_jefferson):
						text_jefferson = re.sub(r' +', ' ', ''.join(text_jefferson).strip())
						if len(text_jefferson.strip()):
							# print(f"{current_speaker}\t{text_jefferson}", file=fout_jeff)
							print(f"{tu_speaker}\t{text_jefferson}", file=fout_jeff)
					if len(text_orthographic):
						text_orthographic = re.sub(r' +', ' ', ''.join(text_orthographic).strip())
						if len(text_orthographic.strip()):
							# print(f"{current_speaker}\t{text_orthographic}", file=fout_ortho)
							print(f"{tu_speaker}\t{text_orthographic}", file=fout_ortho)

					tu_id = current_id
					tu_speaker = current_speaker
					text_jefferson = []
					text_orthographic = []

				if row['type'] in ['nonverbalbehavior', 'shortpause']:
					text_jefferson.append(row['span'])

				elif row['type'] in ['unknown']:
					text_jefferson.append(row['span'])
					text_orthographic.append(''.join([c for c in row['span'] if c == 'x']))

				elif row['type'] in ['linguistic']:
					text_jefferson.append(row['span'])
					text_orthographic.append(row['form'])

				elif row['type'] in ['error']:
					text_jefferson.append(row['span'])
					text_orthographic.append(''.join(c for c in row['form'] if c.isalpha()))

				if 'ProsodicLink=Yes' in row['jefferson_feats']:
					text_jefferson.append('=')
					text_orthographic.append(' ')
				elif 'SpaceAfter=No' not in row['jefferson_feats']:
					text_jefferson.append(' ')
					text_orthographic.append(' ')

			if len(text_jefferson):
				text_jefferson = re.sub(r' +', ' ', ''.join(text_jefferson).strip())
				if len(text_jefferson.strip()):
					print(f"{tu_speaker}\t{text_jefferson}", file=fout_jeff)
			if len(text_orthographic):
				text_orthographic = re.sub(r' +', ' ', ''.join(text_orthographic).strip())
				if len(text_orthographic.strip()):
					print(f"{tu_speaker}\t{text_orthographic}", file=fout_ortho)


if __name__ == "__main__":
	import argparse

	def parse_args():
		parser = argparse.ArgumentParser(
			description="Process .vert.tsv files from a folder or a single file."
		)

		# Either a single file or a folder
		parser.add_argument(
			"-i", "--input",
			required=True,
			help="Input file (*.vert.tsv) or folder containing such files."
		)

		parser.add_argument(
			"--out_orthographic",
			help="Output folder for transformation into linear orthographic.",
			default="linear-orthographic"
		)

		parser.add_argument(
			"--out_jefferson",
			help="Output folder for transformation into linear jefferson.",
			default="linear-jefferson"
		)

		return parser.parse_args()


	def resolve_input_files(input_path: str):
		p = pathlib.Path(input_path)

		if p.is_file():
			if not p.name.endswith(".vert.tsv"):
				raise ValueError(f"Input file must end with .vert.tsv: {p}")
			return [p]

		if p.is_dir():
			files = sorted(p.glob("*.vert.tsv"))
			if not files:
				raise ValueError(f"No *.vert.tsv files found in folder: {p}")
			return files

		raise ValueError(f"Input path does not exist: {p}")

	args = parse_args()

	# Resolve inputs
	input_files = resolve_input_files(args.input)

	# Ensure output folders exist
	out_orth = pathlib.Path(args.out_orthographic)
	out_jeff = pathlib.Path(args.out_jefferson)
	out_orth.mkdir(parents=True, exist_ok=True)
	out_jeff.mkdir(parents=True, exist_ok=True)

	print("Files to process:", input_files)
	print("Output A folder:", out_orth)
	print("Output B folder:", out_jeff)

	tsv2linear(input_files, out_jeff, out_orth)