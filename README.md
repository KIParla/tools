# tools

Standalone scripts to handle and transform KIParla data.

## Scripts

- `eaf2csv.py`: convert ELAN `.eaf` files into tab-separated transcript CSV.
- `make_patch.py`: generate a `.patch` file from a lemmatization CSV to fix transcription errors in a corpus `.vert.tsv`.
  It also supports `--batch <wip_subdir>` to process every CSV in a corpus folder.
- `cli.py vert2eaf` (`serialize.vert2eaf`): rebuild `.eaf` files from current-schema `.vert.tsv`
  output, reattaching translations from `translations/<name>.translations.json` the same way
  `csv2eaf` does. Supersedes `tsv2eaf.py` (deprecated, see Notes).
- `tsv2formats.py`: generate linear Jefferson and orthographic text files from `.vert.tsv`.
- `linear2html.py`: generate publication HTML and PDF artifacts in the `KIParla-artifacts` layout.
- `merge_metadata.py`: merge metadata tables from module repositories.
- `check_participants.py`: cross-check each conversation's `metadata/conversations.tsv`
  `participants` list against the actual `speaker` values in its `tsv/<code>.vert.tsv`,
  flag transcript speakers missing from `metadata/participants.tsv`, and cross-check
  `conversations.tsv` `participants` against `participants.tsv` `conversations` in both
  directions. Runs across all modules by default (auto-discovered), or pass
  `--modules <dir> ...`. Pass `--add-unknown-participant-column` to add/refresh an
  `unknown-participant` column (`yes`/`no`) in each module's `conversations.tsv`.
- `generate_validation_report.py`: build the validation report page (`docs/modules/ROOT/pages/validation-report.adoc`,
  published as part of the KIParla docs site) combining `check_participants.py`'s
  metadata-consistency results with per-conversation pipeline warnings/errors from
  each module's `tmp/process/json/summary.json`. Run again and commit after
  reprocessing a module to keep the report current.
- `sync.py`: one-shot, one-directional sync for a single edited file — run manually
  after opening/saving a `.eaf` in ELAN, or hand-editing a `.vert.tsv`:
  - `python sync.py --from-eaf <path/to/X.eaf>`: eaf2csv → process (updates
    `tsv/X.vert.tsv`, `translations/`, `tmp/process/{csv,json}/`, `tmp/process/json/summary.json`)
    → `tsv2formats` (linear-jefferson/orthographic) → `check_participants` (this module)
    → `generate_validation_report` (all modules).
  - `python sync.py --from-vert <path/to/X.vert.tsv>`: `vert2eaf` (overwrites `eaf/X.eaf`
    in place) → `tsv2formats` → `check_participants` → `generate_validation_report`.
    Pipeline warnings/errors in the report are *not* refreshed for this file (that would
    require reprocessing the freshly-written eaf — the reverse direction this command
    deliberately doesn't auto-trigger, to avoid ping-ponging between the two commands).
  - Module is inferred from the file's path (`<module>/eaf/X.eaf` or `<module>/tsv/X.vert.tsv`);
    override with `--module` if a module's directory name doesn't map to its config name
    (e.g. `Stra-ParlaBO` → `StraParlaBO`) by simply stripping `-`.

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

- `tsv2eaf.py` is deprecated: it expects the historical TSV column names `iu_id` and
  `iu_align`, which don't exist in the current `.vert.tsv` schema, and it has no knowledge
  of translations. Use `cli.py vert2eaf` instead.
- `vert2eaf` reconstructs the pipeline's *normalized* Jefferson text (post accent-correction,
  post number-to-words, etc.), not necessarily byte-identical pre-normalization source text.
  One documented, tested lossy case: a TU's TU-level `# ` variation marker (used when
  individual non-Italian tokens aren't decidable) is not reconstructed when `variation=some`,
  since vert.tsv can't distinguish that case from `some` arising purely from individually
  `#`/`$`-marked tokens (which round-trip correctly on their own). See `tests/test_vert2eaf.py`.
- Two more expected (non-bug) sources of diff on a real eaf -> vert.tsv -> eaf -> vert.tsv
  round-trip, verified on `Stra-ParlaBO/tsv/SBCA001.vert.tsv` (952/10322 lines differ, but the
  token multiset — speaker/form/type — is byte-identical; zero content loss):
  - **Millisecond rounding**: ELAN's native format stores integer milliseconds, so a `Begin=`/
    `End=` value with sub-millisecond precision (e.g. `166.7895`) gets truncated on write
    (`166.789`). Unavoidable given the file format.
  - **Tie-break order for simultaneous TUs**: when two+ different speakers have annotations at
    the *exact* same timestamp (e.g. everyone laughing at once), their relative order — and
    therefore their `tu_id` numbering — isn't preserved across a round-trip, since vert.tsv
    doesn't record the original eaf's tier ordering. `_csv2eaf_from_rows` creates tiers in
    first-appearance order (not a `set`) so re-running `vert2eaf` on the *same* vert.tsv is at
    least deterministic, but that order won't generally match an arbitrary pre-existing eaf's
    historical tier order.
- The tests mostly target importable functions rather than shell-level CLI behavior.
