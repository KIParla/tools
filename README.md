# tools

Standalone scripts to handle and transform KIParla data.

## Scripts

- `eaf2csv.py`: convert ELAN `.eaf` files into tab-separated transcript CSV.
- `make_patch.py`: generate a `.patch` file from a lemmatization CSV to fix transcription errors in a corpus `.vert.tsv`.
  It also supports `--batch <wip_subdir>` to process every CSV in a corpus folder.
- `cli.py vert2eaf` (`serialize.vert2eaf`): rebuild `.eaf` files from current-schema `.vert.tsv`
  output, reattaching translations from `translations/<name>.translations.json` the same way
  `csv2eaf` does.
- `tsv2formats.py`: generate linear enriched and orthographic text files from `.vert.tsv`.
- `linear2html.py`: generate publication HTML and PDF artifacts in the `KIParla-artifacts` layout.
- `merge_metadata.py`: merge metadata tables from module repositories.
- `check_participants.py`: cross-check each conversation's `metadata/conversations.tsv`
  `participants` list against the actual `speaker` values in its `tsv/<code>.vert.tsv`,
  flag transcript speakers missing from `metadata/participants.tsv`, and cross-check
  `conversations.tsv` `participants` against `participants.tsv` `conversations` in both
  directions. Runs across all modules by default (auto-discovered), or pass
  `--modules <dir> ...`. Pass `--add-unknown-participant-column` to add/refresh an
  `unknown-participant` column (`yes`/`no`) in each module's `conversations.tsv`.
- `summarize.py`: write per-conversation summaries and a module-level `snapshot.json` into
  the summaries repository, `KIParla-summaries/<Module>/` (token counts by type, speaking time and token/time rates per
  participant, overlap shares by time and by annotated tokens, variation, plus the pipeline's own
  warnings/errors), computed from `tsv/*.vert.tsv`. Run by hand after `sync.py` / `cli.py process`:
  `python summarize.py --output-dir <summaries_dir> <module_dir> ...`; `--check` reports summaries that are missing or older than
  their `tsv/` file. See `docs/modules/ROOT/pages/scripts.adoc`.
- `build_explorer.py`: build the corpus explorer, a small static site (`index.html`, `explorer.css`,
  `core.js`, `app.js`, `data.json`; in `KIParla-artifacts/explorer/`) from the snapshots in `KIParla-summaries`. It filters
  the conversations by metadata, speaker attributes and measured features (overlap shares, token/time
  rates, ...), shows figures for the resulting sub-corpus and exports it (code list, CSV, a script that
  copies the files). Run `summarize.py` first. See `docs/modules/ROOT/pages/scripts.adoc`.
- `generate_validation_report.py`: build the validation pages (published as part of the
  KIParla docs site), combining `check_participants.py`'s metadata-consistency results
  with per-conversation pipeline warnings/errors from each module's
  `tmp/process/json/summary.json`. One page per module,
  `docs/modules/ROOT/pages/validation-<module>.adoc`, with two sections:
  - *Errors*: an interactive, filterable table (filter by issue type, search by code) of
    only real errors, metadata cross-reference gaps, and missing transcripts — the
    actionable list, with GitHub links (`dev` branch) to jump straight to each file's
    `vert.tsv`/`eaf`.
  - *Warnings*: a diary of every warning the pipeline auto-fixed per conversation —
    informational, not actionable.

  `docs/modules/ROOT/pages/validation.adoc` is the index linking to every module page.
  Regenerating one module (e.g. from `sync.py`) only rewrites that module's page and keeps
  the other modules' rows in the index.

  Run again (and commit) after reprocessing a module, or via `sync.py`, to keep the pages current.
- `sync.py`: one-shot, one-directional sync for a single edited file — run manually
  after opening/saving a `.eaf` in ELAN, or hand-editing a `.vert.tsv`:
  - `python sync.py --from-eaf <path/to/X.eaf>`: eaf2csv → process (updates
    `tsv/X.vert.tsv`, `translations/`, `tmp/process/{csv,json}/`, `tmp/process/json/summary.json`)
    → `tsv2formats` (linear-enriched/orthographic) → `check_participants` (this module)
    → `generate_validation_report` (this module's page).
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
python -m pytest tests/test_tsv2formats.py
```

## Notes

- **Audio length check:** `audio_check.py` compares the end of each transcript's last annotation with the length of
  its recording (audio file via `--audio-dir`, else `duration` in `conversations.tsv`) and warns on
  `AUDIO_OVERRUN` / `AUDIO_UNDERRUN`. It runs in `sync.py --from-eaf` and `cli.py process`, and standalone:
  `python audio_check.py <module_dir> --audio-dir <dir>`. See PIPELINE.md.

- **Rebuilding EAFs from TSVs:** `python sync.py --from-vert <X.vert.tsv> --audio-ext mp3`
  overwrites `eaf/X.eaf` (translations reattached from `translations/`, audio linked by the
  relative path `X.mp3`, no empty `default` tier). Keep the transcribers' files elsewhere first
  (e.g. `original/`): the rebuilt EAF contains the *normalized* text. Re-processing rebuilt EAFs
  reproduces the TSVs except for a handful of tokens next to brackets/intonation marks
  (39 of 629,677 tokens in Stra-ParlaBO).
- **Validation report:** `sync.py` refreshes the synced module's page
  (`validation-<module>.adoc`) and the index; to rebuild several modules at once run
  `python generate_validation_report.py --modules <module dirs>`.

- **Encoding:** all inputs/outputs are UTF-8 (no BOM), LF line endings, Unicode NFC — the same
  requirements as Universal Dependencies. Text is NFC-normalized on entry (`eaf2csv`,
  `TranscriptionUnit`); see `textutil.py` and PIPELINE.md.
- **`#_`** means "non-Italian from this point to the end of the unit" (the whole unit when it is at
  the start); `[#_ w]` is rewritten to `#_ [w]` (`HASH_UNIT_SPACE`). See PIPELINE.md.

- `vert2eaf` reconstructs the pipeline's *normalized* enriched text (post accent-correction,
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
