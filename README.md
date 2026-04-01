# tools

Standalone scripts to handle and transform KIParla data.

## Scripts

- `eaf2csv.py`: convert ELAN `.eaf` files into tab-separated transcript CSV.
- `make_patch.py`: generate a `.patch` file from a lemmatization CSV to fix transcription errors in a corpus `.vert.tsv`.
- `tsv2eaf.py`: rebuild `.eaf` files from pipeline TSV output.
- `tsv2formats.py`: generate linear Jefferson and orthographic text files from `.vert.tsv`.
- `merge_metadata.py`: merge metadata tables from module repositories.

## Tests

The test suite lives in [tests](/Users/ludovica/Documents/KIParla/tools/tests) and uses `pytest`.

From the `tools/` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
python -m pytest
```

If you only want to run one file:

```bash
python -m pytest tests/test_tsv2eaf.py
```

## Notes

- `tsv2eaf.py` expects the historical TSV column names `iu_id` and `iu_align`.
- The tests mostly target importable functions rather than shell-level CLI behavior.
