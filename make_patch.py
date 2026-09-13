#!/usr/bin/env python3
"""
make_patch.py — Generate a .patch file for a KIParla TSV from a lemmatization CSV.

Usage:
    python make_patch.py <wip_csv> <source_tsv> [<output_patch>]
    python make_patch.py --batch <wip_subdir> [<source_root>]

The lemmatization CSV (from wip/) carries corrected form/span alongside lemma/upos
annotations. This script extracts the transcription-level corrections and generates
a unified diff patch for the source TSV.

What is patched:
  - form, span columns (shared between CSV and TSV); values are stripped
  - missing speaker/tu_id values are auto-filled from CSV or neighboring tokens
  - jefferson_feats: only Lang=XXX entries from the CSV are merged in; if span
    changed, span-derived features (Intonation, Interrupted, Truncated, Volume)
    are recomputed from the new span; all other TSV features are preserved
  - Token deletions: TSV tokens absent from the CSV (e.g. second half of a merge)
    - Begin=/End= alignment is auto-transferred to neighboring tokens where possible
    - prolongations/pace/guesses/overlaps are auto-transferred to the preceding
      kept token when its form was extended by exactly the dropped token's form
      (a clean merge), with char positions shifted by the length gained
  - Token additions: CSV tokens absent from the TSV are listed in the recap only
  - `id`: recomputed as a monotonic 0-based position within each TU, from final
    row order — independent of `token_id`, whose numeric suffix stays stable/
    creation-order and can go out of sequence after a split or add
  - `unit` column: dropped from the output (pure duplicate of `tu_id`)

What is NOT patched:
  - `type` (annotation-only classification, not derived here)
  - Lemma/upos (annotation-only, not in source TSV)
  - Annotation sub-token rows (numeric token_id + letter suffix, e.g. 4-7a)

Output:
  patches/<CORPUS>/<FILE>.vert.tsv.patch   — apply with: git apply <patch>
  patches/<CORPUS>/<FILE>.vert.tsv.recap.md — manual follow-up actions, ordered by row

Validation:
  - Every token in both CSV and TSV must have non-empty span and form values

Batch mode:
  - Given a wip subfolder (e.g. wip/KIP), patches all *.csv files inside it
  - Source TSV paths are inferred as ../<CORPUS>/tsv/<FILE>.vert.tsv unless
    <source_root> is provided explicitly

The output patch can be applied with:
    cd <KIP-module-dir> && git apply ../lemmatization-project/patches/KIP/BOA1007.vert.tsv.patch
"""

import csv
import sys
import difflib
import re
from datetime import date
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from jefferson_feats import (
    SPAN_DERIVED_FEAT_KEYS,
    parse_feats,
    format_feats,
    feats_from_span,
    form_from_span,
)

PATCHABLE_COLS = ['span', 'form']
# TSV-only columns that may need manual attention after structural changes
MANUAL_COLS = ['prolongations', 'pace', 'guesses', 'overlaps']

# pace/guesses/overlaps encode zero-based char positions over `form` as
# `<start>-<end>`, optionally with a `Fast=`/`Slow=` prefix (pace) or a
# `(group_id)` suffix (pace/overlaps) — see README's vert.tsv column
# reference. prolongations uses `<char_id>x<count>`.
_POS_PAIR_RE = re.compile(r'(\d+)-(\d+)')
_PROLONG_RE = re.compile(r'(\d+)x(\d+)')


def _shift_positions(value: str, offset: int) -> str:
    """Shift every `<start>-<end>` char-position pair in *value* by *offset*."""
    return _POS_PAIR_RE.sub(lambda m: f'{int(m.group(1)) + offset}-{int(m.group(2)) + offset}', value)


def _shift_prolongations(value: str, offset: int) -> str:
    """Shift every `<char_id>x<count>` pair in *value* by *offset*."""
    return _PROLONG_RE.sub(lambda m: f'{int(m.group(1)) + offset}x{m.group(2)}', value)


_POSITIONAL_COL_SHIFTERS = {
    'pace': _shift_positions,
    'guesses': _shift_positions,
    'overlaps': _shift_positions,
    'prolongations': _shift_prolongations,
}


# One span entry: optional `Fast=`/`Slow=` prefix (pace), `<start>-<end>`,
# optional `(group_id)` suffix (pace/overlaps).
_SPAN_ENTRY_RE = re.compile(r'^(?P<prefix>[A-Za-z]+=)?(?P<start>\d+)-(?P<end>\d+)(?P<suffix>\(.*\))?$')


def _merge_positional_feature(prev_value: str, dropped_value: str, offset: int, col: str) -> str:
    """Shift *dropped_value* (from a token being merged away) by *offset*
    characters and append it to *prev_value*.

    For pace/guesses/overlaps, if the shifted span picks up exactly where
    the kept token's last span of the same kind (prefix + group id) left
    off — the normal case for a token that was itself one continuous
    slow/fast/overlap span — the two are collapsed into one continuous
    span instead of two adjacent comma-separated entries (`Slow=0-4(0)` +
    `Slow=4-7(0)` → `Slow=0-7(0)`, not `Slow=0-4(0),Slow=4-7(0)`).
    """
    shifted = _POSITIONAL_COL_SHIFTERS[col](dropped_value, offset)
    if prev_value in ('_', ''):
        return shifted
    if col == 'prolongations':
        return f'{prev_value},{shifted}'

    prev_entries = prev_value.split(',')
    shifted_entries = shifted.split(',')
    last = _SPAN_ENTRY_RE.match(prev_entries[-1])
    first_new = _SPAN_ENTRY_RE.match(shifted_entries[0])
    if (last and first_new
            and last.group('prefix') == first_new.group('prefix')
            and last.group('suffix') == first_new.group('suffix')
            and int(last.group('end')) == int(first_new.group('start'))):
        prev_entries[-1] = (
            f"{last.group('prefix') or ''}{last.group('start')}-{first_new.group('end')}"
            f"{last.group('suffix') or ''}"
        )
        shifted_entries = shifted_entries[1:]
    return ','.join(prev_entries + shifted_entries)

# Legacy curly-brace Jefferson notation: {P} for shortpause, {tag}/{multi_word_tag}
# for non-verbal-behavior (spaces stored as underscores). normalize.py's decision
# is to preserve literal (.)/((tag)) notation rather than rewrite it — see
# meta_tag()'s docstring. Some wip/*.csv rows a lemmatizer never touched still
# carry the legacy form copied verbatim from an older TSV pull; without this,
# comparing them against a (correctly literal) source TSV would look like a
# genuine edit and silently reintroduce the legacy notation into the corpus.
_LEGACY_TAG_RE = re.compile(r'^\{([^{}]+)\}$')


def normalize_legacy_pause_nvb(value: str) -> str:
    """Canonicalize legacy {P}/{tag} notation to literal (.)/((tag)).

    Leaves everything else untouched, including the lemmatization project's
    own CoNLL-U placeholder forms ([PAUSE]/[NVB]), which are a distinct,
    intentional convention for the `form` column and not a legacy notation.
    """
    if value == '{P}':
        return '(.)'
    m = _LEGACY_TAG_RE.match(value)
    if m:
        return '((' + m.group(1).replace('_', ' ') + '))'
    return value


def is_missing(value: str | None) -> bool:
    return value is None or value.strip() in {'', '_'}


def is_subtoken(token_id: str) -> bool:
    """Annotation sub-token rows (4-7a, 4-7b) are not source TSV tokens.

    Keep lexical token ids such as ``12bis``; only the numeric-hyphen-letter
    pattern is treated as an annotation-internal subtoken.
    """
    return bool(re.fullmatch(r'\d+-\d+[A-Za-z]', token_id))


def read_csv(path: Path) -> dict[str, dict]:
    """Return {token_id: row_dict} in insertion order, skipping sub-token rows.
    Handles BOM. All values are stripped of leading/trailing whitespace.
    """
    rows = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            tid = row['token_id'].strip()
            if not is_subtoken(tid):
                rows[tid] = {k: v.strip() for k, v in row.items()}
    return rows


def validate_required_token_fields(rows: list[dict] | dict[str, dict], source_name: str) -> None:
    """Fail if any token has an empty span/form after normalization."""
    items = rows.items() if isinstance(rows, dict) else (
        (row.get('token_id', '?'), row) for row in rows
    )
    for token_id, row in items:
        for field in ('span', 'form'):
            value = row.get(field, '')
            if not value.strip():
                raise ValueError(
                    f"{source_name}: token `{token_id}` has empty `{field}`"
                )


def read_tsv(path: Path) -> tuple[list[str], list[dict], list[str], str]:
    """Return (header_cols, row_dicts_ordered, raw_lines, eol).
    Opens in binary mode to preserve original line endings (CRLF or LF).
    """
    raw_bytes = path.read_bytes()
    eol = '\r\n' if b'\r\n' in raw_bytes else '\n'
    lines = raw_bytes.decode('utf-8').splitlines(keepends=True)
    header = lines[0].rstrip('\r\n').split('\t')
    rows = []
    for line in lines[1:]:
        parts = line.rstrip('\r\n').split('\t')
        rows.append(dict(zip(header, parts)))
    return header, rows, lines, eol


def parse_align(s: str) -> dict[str, str]:
    """'Begin=4.696|End=5.034' → {'Begin': '4.696', 'End': '5.034'}"""
    if not s or s == '_':
        return {}
    d = {}
    for part in s.split('|'):
        if '=' in part:
            k, v = part.split('=', 1)
            d[k] = v
    return d


def format_align(d: dict[str, str]) -> str:
    """{'Begin': '4.696', 'End': '5.034'} → 'Begin=4.696|End=5.034'"""
    if not d:
        return '_'
    parts = []
    for k in ('Begin', 'End'):
        if k in d:
            parts.append(f'{k}={d[k]}')
    for k, v in d.items():
        if k not in ('Begin', 'End'):
            parts.append(f'{k}={v}')
    return '|'.join(parts)


def row_to_line(row: dict, header: list[str], eol: str) -> str:
    return '\t'.join(row.get(c, '_') for c in header) + eol



def update_jefferson_feats(tsv_value: str, csv_value: str, span_changed: bool,
                           new_span: str, old_span: str = '') -> str:
    """Compute the patched jefferson_feats for a kept token.

    Rules:
    - Only Lang=XXX from the CSV is considered; all other CSV features are ignored.
    - If span changed: span-derived features (SPAN_DERIVED_FEAT_KEYS) are recomputed
      from new_span and replace their counterparts in the TSV value — *unless* a
      feature (Volume, in practice) wasn't derivable from old_span either, meaning
      it was inherited from a wider multi-token °...° span the edited token merely
      sits inside (only that span's boundary tokens carry a literal `°` — see
      feats_from_span); in that case this token-local edit tells us nothing new
      about it, so it's carried over rather than silently dropped.
    - Non-span features (ProsodicLink, SpaceAfter, Orthography, …) are always
      preserved from the TSV.
    - Lang from CSV is merged in last, overriding any Lang already present.
    """
    tsv_feats = parse_feats(tsv_value)

    # Extract only Lang=* from the CSV
    csv_feats = parse_feats(csv_value)
    lang_feats = {k: v for k, v in csv_feats.items() if k == 'Lang'}

    if span_changed:
        derived = feats_from_span(new_span)
        old_derived = feats_from_span(old_span)
        merged = {k: v for k, v in tsv_feats.items() if k not in SPAN_DERIVED_FEAT_KEYS}
        for key in SPAN_DERIVED_FEAT_KEYS:
            if key in derived:
                merged[key] = derived[key]
            elif key not in old_derived and key in tsv_feats:
                merged[key] = tsv_feats[key]
    else:
        merged = dict(tsv_feats)

    merged.update(lang_feats)
    return format_feats(merged)


def infer_type(csv_row: dict) -> str:
    """Infer a sensible TSV type for a newly inserted token."""
    span = csv_row.get('span', '')
    form = csv_row.get('form', '')
    # Accept both the current (.)/((...)) notation and the legacy {P}/{tag}
    # notation, since wip/*.csv files not yet reprocessed through the
    # tools/ pipeline may still use the old form.
    if span in ('(.)', '{P}') or form in ('(.)', '{P}', '[PAUSE]'):
        return 'shortpause'
    if (
        (span.startswith('((') and span.endswith('))'))
        or (span.startswith('{') and span.endswith('}'))
        or form in ('[NVB]',)
    ):
        return 'nonverbalbehavior'
    if form == 'x':
        return 'unknown'
    return 'linguistic'


def infer_missing_metadata(
    field: str,
    csv_row: dict,
    current_row: dict | None = None,
    prev_row: dict | None = None,
    next_row: dict | None = None,
) -> str:
    """Fill missing speaker/tu_id conservatively from CSV or neighboring rows."""
    current_row = current_row or {}

    csv_value = csv_row.get(field, '_')
    if not is_missing(csv_value):
        return csv_value

    current_value = current_row.get(field, '_')
    if not is_missing(current_value):
        return current_value

    prev_value = prev_row.get(field, '_') if prev_row is not None else '_'
    next_value = next_row.get(field, '_') if next_row is not None else '_'

    if field == 'speaker':
        current_tu = current_row.get('tu_id', '_')
        prev_same_tu = (
            prev_row is not None and not is_missing(current_tu)
            and prev_row.get('tu_id') == current_tu
            and not is_missing(prev_value)
        )
        next_same_tu = (
            next_row is not None and not is_missing(current_tu)
            and next_row.get('tu_id') == current_tu
            and not is_missing(next_value)
        )
        if prev_same_tu:
            return prev_value
        if next_same_tu:
            return next_value

    if not is_missing(prev_value) and prev_value == next_value:
        return prev_value
    if not is_missing(prev_value) and is_missing(next_value):
        return prev_value
    if not is_missing(next_value) and is_missing(prev_value):
        return next_value

    return '_'


def make_added_row(
    csv_row: dict,
    tsv_header: list[str],
    prev_row: dict | None = None,
    next_row: dict | None = None,
) -> dict:
    """Build a new TSV row for a token present in CSV but absent in TSV."""
    row = {c: '_' for c in tsv_header}
    row['token_id'] = csv_row['token_id']
    row['span'] = normalize_legacy_pause_nvb(csv_row.get('span', '_') or '_')
    csv_form = csv_row.get('form', '_') or '_'
    if csv_form in ('[PAUSE]', '[NVB]'):
        row['form'] = row['span']  # CoNLL-U placeholder — mirror span, see PATCHABLE_COLS loop
    else:
        row['form'] = normalize_legacy_pause_nvb(csv_form)
    row['tu_id'] = infer_missing_metadata(
        field='tu_id',
        csv_row=csv_row,
        current_row=row,
        prev_row=prev_row,
        next_row=next_row,
    )

    # jefferson_feats for new tokens: span-derived features + Lang from CSV only
    span_feats = feats_from_span(row['span'])
    csv_jf = parse_feats(csv_row.get('jefferson_feats', '_'))
    lang_feats = {k: v for k, v in csv_jf.items() if k == 'Lang'}
    span_feats.update(lang_feats)
    row['jefferson_feats'] = format_feats(span_feats)
    if 'type' in row:
        row['type'] = infer_type(csv_row)

    row['speaker'] = infer_missing_metadata(
        field='speaker',
        csv_row=csv_row,
        current_row=row,
        prev_row=prev_row,
        next_row=next_row,
    )

    return row


def build_new_rows(
    csv_rows: dict[str, dict],
    tsv_header: list[str],
    tsv_rows: list[dict],
    eol: str,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Produce corrected row dicts and structured recap data grouped by change type.

    Returns:
        final_rows:   list of dicts in TSV column order (with corrections applied)
        change_items: one dict per changed field  — {token_id, field, before, after}
        drop_items:   one dict per dropped token  — {token_id, tu_id, span, form,
                                                      jefferson_feats, notes: list[str]}
        add_items:    one dict per added token    — {token_id, tu_id, span, form,
                                                      jefferson_feats, type, notes: list[str]}
    """
    col_set = set(tsv_header)
    change_items: list[dict] = []
    drop_items: list[dict] = []
    add_items: list[dict] = []

    new_rows: list[dict] = []
    kept_rows_by_id: dict[str, dict] = {}
    pending_begin: str | None = None
    pending_begin_source: dict | None = None   # drop_item that owns the pending Begin

    tsv_ids_seen: set[str] = set()

    for idx, tsv_row in enumerate(tsv_rows):
        tid = tsv_row.get('token_id', '')

        if tid not in csv_rows:
            # ── DROPPED token ────────────────────────────────────────────────
            align_d = parse_align(tsv_row.get('align', '_'))
            notes: list[str] = []

            if 'End' in align_d:
                if new_rows:
                    prev = new_rows[-1]
                    prev_align = parse_align(prev.get('align', '_'))
                    if 'End' not in prev_align:
                        prev_align['End'] = align_d['End']
                        prev['align'] = format_align(prev_align)
                        notes.append(f"✅ End={align_d['End']} → `{prev['token_id']}`")
                    else:
                        notes.append(
                            f"❌ End conflict: End={align_d['End']} but "
                            f"`{prev['token_id']}` already has End={prev_align['End']}"
                        )
                else:
                    notes.append(f"❌ End={align_d['End']} — no previous token")

            if 'Begin' in align_d:
                if pending_begin is not None:
                    notes.append(
                        f"❌ Begin conflict: pending Begin={pending_begin} "
                        f"overwritten by Begin={align_d['Begin']}"
                    )
                pending_begin = align_d['Begin']

            manual_losses = {c: tsv_row[c] for c in MANUAL_COLS if c in tsv_row and tsv_row[c] != '_'}

            # If this token's content was appended onto the immediately
            # preceding kept token's form (a clean merge, e.g. "elle"+"due"
            # → "elledue" — the documented "second half of a merge" case),
            # transfer its positional features instead of just losing them,
            # shifting char positions by the length of the kept token's
            # original form.
            transferred: set[str] = set()
            if manual_losses and new_rows:
                prev = new_rows[-1]
                dropped_form = tsv_row.get('form', '')
                prev_form = prev.get('form', '')
                if dropped_form and prev_form.endswith(dropped_form) and len(prev_form) > len(dropped_form):
                    offset = len(prev_form) - len(dropped_form)
                    for col, value in manual_losses.items():
                        prev[col] = _merge_positional_feature(prev.get(col, '_'), value, offset, col)
                        notes.append(f"✅ {col}=`{value}` → `{prev['token_id']}` (shifted +{offset})")
                        transferred.add(col)

            for c, v in manual_losses.items():
                if c not in transferred:
                    notes.append(f"⚠️ {c}=`{v}`")

            drop_item: dict = {
                'token_id': tid,
                'tu_id': tsv_row.get('tu_id', '?'),
                'span': tsv_row.get('span', '_'),
                'form': tsv_row.get('form', '_'),
                'jefferson_feats': tsv_row.get('jefferson_feats', '_'),
                'old_row': dict(tsv_row),
                'notes': notes,
            }
            drop_items.append(drop_item)
            if 'Begin' in align_d:
                pending_begin_source = drop_item
            continue

        # ── KEPT token ────────────────────────────────────────────────────────
        new_row = dict(tsv_row)
        old_row_snapshot = dict(tsv_row)
        tsv_ids_seen.add(tid)
        csv_row = csv_rows[tid]

        any_content_changed = False
        span_changed = False
        form_changed = False
        for col in PATCHABLE_COLS:
            if col in col_set and col in csv_row:
                raw_val = csv_row[col]
                if col == 'form' and raw_val in ('[PAUSE]', '[NVB]'):
                    # [PAUSE]/[NVB] are the lemmatization project's own
                    # CoNLL-U placeholder forms (see conllu2wip.py) — not
                    # meant for the corpus `form` column. Per the
                    # normalize.py decision (literal notation, form==span
                    # for pause/NVB tokens), mirror the token's own span
                    # instead of writing the placeholder into the corpus.
                    # `span` is processed first (PATCHABLE_COLS order), so
                    # new_row['span'] already reflects this same update.
                    new_val = new_row.get('span', '')
                else:
                    new_val = normalize_legacy_pause_nvb(raw_val)
                old_val = new_row.get(col, '')
                if new_val and new_val != old_val:
                    new_row[col] = new_val
                    any_content_changed = True
                    if col == 'span':
                        span_changed = True
                    elif col == 'form':
                        form_changed = True

        if 'jefferson_feats' in col_set:
            old_jf = new_row.get('jefferson_feats', '_')
            new_jf = update_jefferson_feats(
                tsv_value=old_jf,
                csv_value=csv_row.get('jefferson_feats', '_'),
                span_changed=span_changed,
                new_span=new_row.get('span', '_'),
                old_span=old_row_snapshot.get('span', '_'),
            )
            if new_jf != old_jf:
                new_row['jefferson_feats'] = new_jf
                any_content_changed = True

        prev_kept_row = new_rows[-1] if new_rows else None
        next_kept_row = None
        for lookahead_row in tsv_rows[idx + 1:]:
            lookahead_tid = lookahead_row.get('token_id', '')
            if lookahead_tid in csv_rows:
                next_kept_row = lookahead_row
                break

        for field in ('tu_id', 'speaker'):
            if field in col_set and is_missing(new_row.get(field, '_')):
                inferred_value = infer_missing_metadata(
                    field=field,
                    csv_row=csv_row,
                    current_row=new_row,
                    prev_row=prev_kept_row,
                    next_row=next_kept_row,
                )
                if not is_missing(inferred_value):
                    new_row[field] = inferred_value
                    any_content_changed = True

        if pending_begin is not None:
            align_d = parse_align(new_row.get('align', '_'))
            if 'Begin' not in align_d:
                align_d['Begin'] = pending_begin
                new_row['align'] = format_align(align_d)
                if pending_begin_source is not None:
                    pending_begin_source['notes'].append(
                        f"✅ Begin={pending_begin} → `{tid}`"
                    )
            else:
                if pending_begin_source is not None:
                    pending_begin_source['notes'].append(
                        f"❌ Begin={pending_begin} conflict: `{tid}` already has "
                        f"Begin={align_d['Begin']}"
                    )
            pending_begin = None
            pending_begin_source = None

        new_rows.append(new_row)
        kept_rows_by_id[tid] = new_row

        # Snapshot deferred: new_row may still be modified by ADD section (align)
        if any_content_changed:
            change_notes: list[str] = []
            if form_changed and not span_changed:
                # Check whether the new form is derivable from the (unchanged) span
                span = new_row.get('span', '_')
                expected_form = form_from_span(span)
                if expected_form is not None and new_row.get('form') != expected_form:
                    change_notes.append('⚠️ forma non derivabile dallo span — verifica manuale')
            change_items.append({
                'token_id': tid,
                'tu_id': tsv_row.get('tu_id', '?'),
                'old_row': old_row_snapshot,
                'new_row': new_row,   # mutable ref — finalized after ADD section
                'notes': change_notes,
            })

    # Tokens in CSV but not in TSV (ADD)
    final_rows: list[dict] = []
    final_ids = list(csv_rows)
    for pos, tid in enumerate(final_ids):
        if tid in kept_rows_by_id:
            final_rows.append(kept_rows_by_id[tid])
            continue

        csv_row = csv_rows[tid]
        prev_row = final_rows[-1] if final_rows else None
        next_row = None
        for next_tid in final_ids[pos + 1:]:
            if next_tid in kept_rows_by_id:
                next_row = kept_rows_by_id[next_tid]
                break

        new_row = make_added_row(csv_row, tsv_header, prev_row=prev_row, next_row=next_row)
        notes = []

        if prev_row is not None and prev_row.get('tu_id') == new_row.get('tu_id'):
            prev_align = parse_align(prev_row.get('align', '_'))
            if 'End' in prev_align:
                new_align = parse_align(new_row.get('align', '_'))
                new_align['End'] = prev_align.pop('End')
                prev_row['align'] = format_align(prev_align)
                new_row['align'] = format_align(new_align)
                notes.append(f"✅ End={new_align['End']} ← `{prev_row['token_id']}`")

        if next_row is not None and next_row.get('tu_id') == new_row.get('tu_id'):
            next_align = parse_align(next_row.get('align', '_'))
            new_align = parse_align(new_row.get('align', '_'))
            if 'Begin' in next_align and 'Begin' not in new_align:
                new_align['Begin'] = next_align.pop('Begin')
                next_row['align'] = format_align(next_align)
                new_row['align'] = format_align(new_align)
                notes.append(f"✅ Begin={new_align['Begin']} ← `{next_row['token_id']}`")

        # Only the first/last token of a TU is expected to carry Begin=/End=
        # at all (see README's `align` column) — an added token strictly
        # inside a TU has correct (empty) alignment as-is and needs no
        # manual check.
        is_tu_first = prev_row is None or prev_row.get('tu_id') != new_row.get('tu_id')
        is_tu_last = next_row is None or next_row.get('tu_id') != new_row.get('tu_id')
        final_align = parse_align(new_row.get('align', '_'))
        if (is_tu_first and 'Begin' not in final_align) or (is_tu_last and 'End' not in final_align):
            notes.append("⚠️ verifica allineamento manualmente")

        final_rows.append(new_row)
        add_items.append({
            'token_id': tid,
            'tu_id': new_row.get('tu_id', '?'),
            'span': new_row.get('span', '_'),
            'form': new_row.get('form', '_'),
            'jefferson_feats': new_row.get('jefferson_feats', '_'),
            'type': new_row.get('type', '_'),
            'new_row': dict(new_row),
            'notes': notes,
        })

    # `id` is recomputed as a monotonic per-TU position, independent of
    # `token_id`'s suffix — splits/adds keep token_id stable (creation-order,
    # possibly out of sequence) while `id` always reflects true row order.
    if 'id' in col_set:
        next_id_by_tu: dict[str, int] = {}
        for row in final_rows:
            tu = row.get('tu_id', '_')
            row['id'] = str(next_id_by_tu.get(tu, 0))
            next_id_by_tu[tu] = int(row['id']) + 1

    return final_rows, change_items, drop_items, add_items


def _mc(value: str) -> str:
    """Escape | in a Markdown table cell (| breaks column parsing)."""
    return value.replace('|', '\\|')


def _row_to_tsv(row: dict, header: list[str]) -> str:
    return '\t'.join(row.get(c, '_') for c in header)


def _status_icon(notes: list[str]) -> str:
    if any(note.startswith('❌') for note in notes):
        return '❌'
    if any(note.startswith('⚠️') for note in notes):
        return '⚠️'
    return '✅'


# Plain-text tags, not emoji: emoji glyph width is inconsistent across
# fonts/renderers (variation selectors, ambiguous East Asian Width), which
# breaks monospace column alignment in the raw Markdown source. ASCII tags
# align predictably with any character-counting formatter.
LIVELLO_LABEL = {
    '✅': '[OK] gestito automaticamente',
    '⚠️': '[WARN] verifica manuale',
    '❌': '[FAIL] conflitto',
}

TIPO_LABEL = {
    'change': '[MOD] modifica',
    'drop':   '[DEL] eliminazione',
    'add':    '[ADD] aggiunta',
}

# Columns shown in their own dedicated table columns; everything else that
# differs lands in "altro".
_RECAP_OWN_COLS = {'token_id', 'id', 'span', 'form'}


def _cell(type_: str, col: str, old_row: dict | None, new_row: dict | None) -> str:
    """Render one field as a recap table cell: `prima` → `dopo` for a change,
    a single backticked value for add/drop, or '' if there's nothing to show."""
    if type_ == 'change':
        ov, nv = old_row.get(col, '_'), new_row.get(col, '_')
        if ov == nv:
            return f'`{_mc(ov)}`' if ov not in ('_', '') else ''
        return f'`{_mc(ov)}` → `{_mc(nv)}`'
    row = old_row if type_ == 'drop' else new_row
    v = row.get(col, '_')
    return f'`{_mc(v)}`' if v not in ('_', '') else ''


def _altro_cell(type_: str, old_row: dict | None, new_row: dict | None,
                 tsv_header: list[str], notes: list[str]) -> str:
    """Everything that changed (or is set) outside token_id/id/span/form,
    plus any ⚠️/❌ notes, as one cell — one `campo: prima → dopo` per line.

    ✅ notes (successful automatic Begin/End or positional-feature transfer)
    are dropped here: what they report is already visible from the field
    diffs themselves (the receiving token's own row shows the field
    changing), so repeating it as a note is just noise. ⚠️/❌ notes carry
    information the diffs don't (why something needs a manual look, or a
    conflict), so those stay.
    """
    parts: list[str] = []
    for col in tsv_header:
        if col in _RECAP_OWN_COLS or col == 'tu_id':
            continue
        if type_ == 'change':
            ov, nv = old_row.get(col, '_'), new_row.get(col, '_')
            if ov != nv:
                parts.append(f'{col}: `{_mc(ov)}` → `{_mc(nv)}`')
        else:
            row = old_row if type_ == 'drop' else new_row
            v = row.get(col, '_')
            if v not in ('_', ''):
                parts.append(f'{col}: `{_mc(v)}`')
    parts.extend(_mc(note) for note in notes if not note.startswith('✅'))
    return ' · '.join(parts)


def write_recap(
    recap_p: Path,
    tsv_name: str,
    csv_name: str,
    change_items: list[dict],
    drop_items: list[dict],
    add_items: list[dict],
    tsv_header: list[str],
    tsv_ids: list[str],
    csv_ids: list[str],
    final_rows: list[dict],
) -> bool:
    """Render structured recap data as a single Markdown table, ordered by row.

    Columns: token_id, id, tipo (modifica/aggiunta/eliminazione), livello
    (gestito automaticamente/verifica manuale/conflitto), span, form,
    altro (every other field that changed, plus notes). span/form/altro use
    `prima` → `dopo` for changes.

    Returns True if the file was written, False if there is nothing to report.
    """
    if not (change_items or drop_items or add_items):
        return False

    change_by_id = {item['token_id']: item for item in change_items}
    drop_by_id = {item['token_id']: item for item in drop_items}
    add_by_id = {item['token_id']: item for item in add_items}

    # `id` is recomputed on final_rows after change/add items are built (see
    # build_new_rows), and 'add' items only hold a snapshot copy — so the
    # authoritative final id per surviving token has to come from final_rows.
    final_id_by_token = {r['token_id']: r.get('id', '_') for r in final_rows}

    lines: list[str] = [
        f"# Patch recap — {tsv_name}",
        f"Generated: {date.today()}  |  CSV: `{csv_name}`",
        "",
        "| tipo | conteggio |",
        "|------|----------|",
        f"| modifiche | {len(change_items)} |",
        f"| eliminazioni | {len(drop_items)} |",
        f"| aggiunte | {len(add_items)} |",
        "",
        "| token_id | id | tipo | livello | span | form | altro |",
        "|----------|----|------|---------|------|------|-------|",
    ]

    # Build ordered list of (type, item) following TSV/CSV token order.
    ordered: list[tuple[str, dict]] = []
    matcher = difflib.SequenceMatcher(a=tsv_ids, b=csv_ids)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for token_id in tsv_ids[i1:i2]:
                item = change_by_id.get(token_id)
                if item is not None:
                    ordered.append(('change', item))
        if tag in ('delete', 'replace'):
            for token_id in tsv_ids[i1:i2]:
                item = drop_by_id.get(token_id)
                if item is not None:
                    ordered.append(('drop', item))
        if tag in ('insert', 'replace'):
            for token_id in csv_ids[j1:j2]:
                item = add_by_id.get(token_id)
                if item is not None:
                    ordered.append(('add', item))

    for type_, item in ordered:
        tid = item['token_id']
        old_row = item.get('old_row')
        new_row = item.get('new_row')
        notes = item.get('notes', [])

        livello = LIVELLO_LABEL[_status_icon(notes)]
        row_id = (old_row.get('id', '_') if type_ == 'drop'
                  else final_id_by_token.get(tid, '_'))
        span_cell = _cell(type_, 'span', old_row, new_row)
        form_cell = _cell(type_, 'form', old_row, new_row)
        altro_cell = _altro_cell(type_, old_row, new_row, tsv_header, notes)

        lines.append(
            f"| `{_mc(tid)}` | {_mc(row_id)} | {TIPO_LABEL[type_]} | {livello} | "
            f"{span_cell} | {form_cell} | {altro_cell} |"
        )

    recap_p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return True


def default_patch_path(csv_path: Path) -> Path:
    """wip/KIP/BOA1007.csv → patches/KIP/BOA1007.vert.tsv.patch"""
    parts = csv_path.parts
    try:
        wip_idx = parts.index('wip')
    except ValueError:
        return csv_path.parent / f'{csv_path.stem}.vert.tsv.patch'
    root = Path(*parts[:wip_idx])
    corpus = parts[wip_idx + 1]
    return root / 'patches' / corpus / f'{csv_path.stem}.vert.tsv.patch'


def infer_tsv_path(csv_path: Path, source_root: str | Path | None = None) -> Path:
    """Infer the source TSV for a CSV inside wip/CORPUS/FILE.csv."""
    parts = csv_path.parts
    try:
        wip_idx = parts.index('wip')
    except ValueError as exc:
        raise ValueError(f"Cannot infer TSV path from `{csv_path}`: missing `wip/`") from exc

    if wip_idx + 1 >= len(parts):
        raise ValueError(f"Cannot infer TSV path from `{csv_path}`: missing corpus folder")

    corpus = parts[wip_idx + 1]
    root = Path(*parts[:wip_idx])
    base = Path(source_root) if source_root is not None else root.parent
    return base / corpus / 'tsv' / f'{csv_path.stem}.vert.tsv'


def make_patch(csv_path: str, tsv_path: str, output_patch: str | None = None) -> bool:
    csv_p = Path(csv_path)
    tsv_p = Path(tsv_path)
    out_p = Path(output_patch) if output_patch else default_patch_path(csv_p)
    recap_p = out_p.parent / (out_p.stem + '.recap.md')  # e.g. BOA1007.vert.tsv.recap.md

    print(f"CSV:   {csv_p}")
    print(f"TSV:   {tsv_p}")
    print(f"Patch: {out_p}")
    print()

    csv_rows = read_csv(csv_p)
    tsv_header, tsv_rows, tsv_lines, eol = read_tsv(tsv_p)
    validate_required_token_fields(csv_rows, str(csv_p))
    validate_required_token_fields(tsv_rows, str(tsv_p))
    new_rows, change_items, drop_items, add_items = build_new_rows(
        csv_rows, tsv_header, tsv_rows, eol
    )

    # `unit` is a pure duplicate of `tu_id` (superseded by monotonic `id`,
    # see build_new_rows) — drop it from the output so patched files migrate
    # off the old schema as they're touched.
    output_header = [c for c in tsv_header if c != 'unit']

    # Serialize new rows back to lines
    header_line = '\t'.join(output_header) + eol
    new_lines = [header_line] + [row_to_line(r, output_header, eol) for r in new_rows]

    # Generate unified diff
    tsv_rel = f'tsv/{tsv_p.name}'
    diff = list(difflib.unified_diff(
        tsv_lines, new_lines,
        fromfile=f'a/{tsv_rel}',
        tofile=f'b/{tsv_rel}',
    ))

    out_p.parent.mkdir(parents=True, exist_ok=True)

    if diff:
        out_p.write_text(''.join(diff), encoding='utf-8')
        additions = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))
        print(f"Patch written: {out_p}  (+{additions} / -{deletions} lines)")
    else:
        print("No differences — no patch written.")

    # Write recap
    n_recap = len(change_items) + len(drop_items) + len(add_items)
    tsv_ids = [row.get('token_id', '') for row in tsv_rows]
    csv_ids = list(csv_rows)
    if write_recap(recap_p, tsv_p.name, csv_p.name,
                   change_items, drop_items, add_items, tsv_header, tsv_ids, csv_ids,
                   new_rows):
        print(f"Recap written: {recap_p}  ({n_recap} items)")
    else:
        print("No structural changes — no recap written.")

    return bool(diff)


def make_patches_in_dir(wip_dir: str, source_root: str | None = None) -> int:
    """Run make_patch on every CSV in a wip subfolder. Return number of failures."""
    wip_p = Path(wip_dir)
    csv_paths = sorted(wip_p.glob('*.csv'))
    if not csv_paths:
        raise ValueError(f"No CSV files found in `{wip_p}`")

    failures = 0
    written = 0
    for csv_path in csv_paths:
        try:
            tsv_path = infer_tsv_path(csv_path, source_root=source_root)
            if not tsv_path.exists():
                raise FileNotFoundError(f"Missing TSV: {tsv_path}")
            if make_patch(str(csv_path), str(tsv_path)):
                written += 1
        except Exception as exc:
            failures += 1
            print(f"ERROR {csv_path.name}: {exc}", file=sys.stderr)

    print(
        f"Batch completed: {len(csv_paths) - failures}/{len(csv_paths)} files processed, "
        f"{written} patch files written, {failures} failures"
    )
    return failures


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--batch':
        failures = make_patches_in_dir(
            sys.argv[2],
            sys.argv[3] if len(sys.argv) > 3 else None,
        )
        sys.exit(1 if failures else 0)

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    make_patch(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
