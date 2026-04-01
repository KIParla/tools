#!/usr/bin/env python3
"""
make_patch.py — Generate a .patch file for a KIParla TSV from a lemmatization CSV.

Usage:
    python make_patch.py <wip_csv> <source_tsv> [<output_patch>]

The lemmatization CSV (from wip/) carries corrected form/span/jefferson_feats
alongside lemma/upos annotations. This script extracts the transcription-level
corrections and generates a unified diff patch for the source TSV.

What is patched:
  - form, span, jefferson_feats columns (shared between CSV and TSV); values are stripped
  - Token deletions: TSV tokens absent from the CSV (e.g. second half of a merge)
    - Begin=/End= alignment is auto-transferred to neighboring tokens where possible
  - Token additions: CSV tokens absent from the TSV are listed in the recap only

What is NOT patched:
  - TSV-only columns (align, prolongations, pace, guesses, overlaps, type) except
    for the automatic Begin/End transfer on structural changes
  - Lemma/upos (annotation-only, not in source TSV)
  - Sub-token rows (token_id ending in a letter, e.g. 4-7a)

Output:
  patches/<CORPUS>/<FILE>.vert.tsv.patch   — apply with: git apply <patch>
  patches/<CORPUS>/<FILE>.vert.tsv.recap.md — manual follow-up actions

The output patch can be applied with:
    cd <KIP-module-dir> && git apply ../lemmatization-project/patches/KIP/BOA1007.vert.tsv.patch
"""

import csv
import sys
import difflib
from datetime import date
from pathlib import Path


PATCHABLE_COLS = ['span', 'form', 'jefferson_feats']
# TSV-only columns that may need manual attention after structural changes
MANUAL_COLS = ['prolongations', 'pace', 'guesses', 'overlaps']


def is_subtoken(token_id: str) -> bool:
    """Sub-token rows (4-7a, 4-7b) are annotation-internal, not in source TSV."""
    return bool(token_id) and token_id[-1].isalpha()


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


def parse_feats(value: str) -> dict[str, str]:
    """Parse a pipe-separated feature string into an ordered dict-like mapping."""
    if not value or value == '_':
        return {}
    feats: dict[str, str] = {}
    for part in value.split('|'):
        if not part:
            continue
        if '=' in part:
            key, val = part.split('=', 1)
            feats[key] = val
        else:
            feats[part] = ''
    return feats


def format_feats(feats: dict[str, str]) -> str:
    """Serialize parsed features back to TSV/CSV form."""
    if not feats:
        return '_'
    parts = []
    for key, val in feats.items():
        if val == '':
            parts.append(key)
        else:
            parts.append(f'{key}={val}')
    return '|'.join(parts)


def merge_jefferson_feats(old_value: str, new_value: str) -> str:
    """
    Merge jefferson_feats from TSV and CSV.

    The CSV may add new features such as Lang=lat without repeating existing TSV
    features like Intonation=WeaklyRising. In that case we preserve TSV features
    and overlay CSV values by key.
    """
    old_feats = parse_feats(old_value)
    new_feats = parse_feats(new_value)
    if not new_feats:
        return old_value
    merged = dict(old_feats)
    merged.update(new_feats)
    return format_feats(merged)


def infer_type(csv_row: dict) -> str:
    """Infer a sensible TSV type for a newly inserted token."""
    span = csv_row.get('span', '')
    form = csv_row.get('form', '')
    if span == '{P}' or form == '{P}' or form == '[PAUSE]':
        return 'shortpause'
    if span.startswith('{') and span.endswith('}'):
        return 'nonverbalbehavior'
    if form == 'x':
        return 'unknown'
    return 'linguistic'


def make_added_row(
    csv_row: dict,
    tsv_header: list[str],
    prev_row: dict | None = None,
    next_row: dict | None = None,
) -> dict:
    """Build a new TSV row for a token present in CSV but absent in TSV."""
    row = {c: '_' for c in tsv_header}
    row['token_id'] = csv_row['token_id']
    row['tu_id'] = csv_row.get('tu_id', '_') or '_'
    row['span'] = csv_row.get('span', '_') or '_'
    row['form'] = csv_row.get('form', '_') or '_'
    row['jefferson_feats'] = csv_row.get('jefferson_feats', '_') or '_'
    if 'type' in row:
        row['type'] = infer_type(csv_row)

    prev_same_tu = prev_row is not None and prev_row.get('tu_id') == row['tu_id']
    next_same_tu = next_row is not None and next_row.get('tu_id') == row['tu_id']

    if prev_same_tu:
        row['speaker'] = prev_row.get('speaker', '_')
    elif next_same_tu:
        row['speaker'] = next_row.get('speaker', '_')
    else:
        row['speaker'] = csv_row.get('speaker', '_') or '_'

    return row


def build_new_rows(
    csv_rows: dict[str, dict],
    tsv_header: list[str],
    tsv_rows: list[dict],
    eol: str,
) -> tuple[list[dict], list[str]]:
    """
    Produce corrected row dicts and a recap of structural changes.

    Returns:
        new_rows: list of dicts in TSV column order (with corrections applied)
        recap_items: list of strings describing changes needing manual attention
    """
    col_set = set(tsv_header)
    recap_items: list[str] = []

    # Process TSV rows in order
    # We keep a mutable list so we can retroactively fix the previous row's align
    new_rows: list[dict] = []
    kept_rows_by_id: dict[str, dict] = {}
    # pending_begin: Begin value to apply to the next kept token
    pending_begin: str | None = None

    tsv_ids_seen: set[str] = set()

    for tsv_row in tsv_rows:
        tid = tsv_row.get('token_id', '')

        if tid not in csv_rows:
            # ── DROPPED token ────────────────────────────────────────────────
            align_d = parse_align(tsv_row.get('align', '_'))
            transferred: list[str] = []
            conflicts: list[str] = []

            # End= → transfer to the last kept row (previous neighbor)
            if 'End' in align_d:
                if new_rows:
                    prev = new_rows[-1]
                    prev_align = parse_align(prev.get('align', '_'))
                    if 'End' not in prev_align:
                        prev_align['End'] = align_d['End']
                        prev['align'] = format_align(prev_align)
                        transferred.append(f"End={align_d['End']} → {prev['token_id']}")
                    else:
                        conflicts.append(
                            f"End conflict: dropped {tid} has End={align_d['End']} "
                            f"but {prev['token_id']} already has End={prev_align['End']}"
                        )
                else:
                    conflicts.append(f"End={align_d['End']} has no previous token to receive it")

            # Begin= → defer to the next kept row
            if 'Begin' in align_d:
                if pending_begin is not None:
                    conflicts.append(
                        f"Begin conflict: pending Begin={pending_begin} from earlier drop "
                        f"overwritten by Begin={align_d['Begin']} from {tid}"
                    )
                pending_begin = align_d['Begin']

            # Collect non-_ values in manual-attention columns
            manual_losses: dict[str, str] = {
                c: tsv_row[c] for c in MANUAL_COLS if c in tsv_row and tsv_row[c] != '_'
            }

            # Build recap entry
            entry_lines = [f"**DROP** `{tid}` (tu_id={tsv_row.get('tu_id','?')})"]
            entry_lines.append(
                f"  - span=`{tsv_row.get('span','_')}` "
                f"form=`{tsv_row.get('form','_')}` "
                f"jefferson_feats=`{tsv_row.get('jefferson_feats','_')}`"
            )
            if transferred:
                entry_lines.append(f"  - ✅ auto-transferred: {'; '.join(transferred)}")
            if pending_begin and 'Begin' in align_d:
                entry_lines.append(f"  - ⏳ Begin={align_d['Begin']} will transfer to next kept token")
            if manual_losses:
                for c, v in manual_losses.items():
                    entry_lines.append(f"  - ⚠️  {c}=`{v}` — **manual check needed**")
            if conflicts:
                for c in conflicts:
                    entry_lines.append(f"  - ❌ {c}")
            recap_items.append('\n'.join(entry_lines))
            continue

        # ── KEPT token ────────────────────────────────────────────────────────
        new_row = dict(tsv_row)
        tsv_ids_seen.add(tid)
        csv_row = csv_rows[tid]

        # Apply patchable columns (already stripped in read_csv)
        changed: list[str] = []
        for col in PATCHABLE_COLS:
            if col in col_set and col in csv_row:
                new_val = csv_row[col]
                old_val = new_row.get(col, '')
                if col == 'jefferson_feats':
                    new_val = merge_jefferson_feats(old_val, new_val)
                if new_val and new_val != old_val:
                    changed.append(f"`{col}`: {old_val!r} → {new_val!r}")
                    new_row[col] = new_val

        if changed:
            recap_items.append(
                f"**CHANGE** `{tid}`: {'; '.join(changed)}"
            )

        # Apply pending Begin from a previously dropped token
        if pending_begin is not None:
            align_d = parse_align(new_row.get('align', '_'))
            if 'Begin' not in align_d:
                align_d['Begin'] = pending_begin
                new_row['align'] = format_align(align_d)
                # Find the matching recap item and annotate it
                recap_items.append(
                    f"  ✅ (continued) Begin={pending_begin} transferred to `{tid}`"
                )
            else:
                recap_items.append(
                    f"  ❌ Begin conflict: pending Begin={pending_begin} could not be applied "
                    f"to `{tid}` which already has Begin={align_d['Begin']}"
                )
            pending_begin = None

        new_rows.append(new_row)
        kept_rows_by_id[tid] = new_row

    # Tokens in CSV but not TSV
    desired_ids = [tid for tid in csv_rows if tid not in tsv_ids_seen]

    final_rows: list[dict] = []
    final_ids = [tid for tid in csv_rows]
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
        transfers: list[str] = []

        if prev_row is not None and prev_row.get('tu_id') == new_row.get('tu_id'):
            prev_align = parse_align(prev_row.get('align', '_'))
            if 'End' in prev_align:
                new_align = parse_align(new_row.get('align', '_'))
                new_align['End'] = prev_align.pop('End')
                prev_row['align'] = format_align(prev_align)
                new_row['align'] = format_align(new_align)
                transfers.append(f"End={new_align['End']} ← `{prev_row['token_id']}`")

        if next_row is not None and next_row.get('tu_id') == new_row.get('tu_id'):
            next_align = parse_align(next_row.get('align', '_'))
            new_align = parse_align(new_row.get('align', '_'))
            if 'Begin' in next_align and 'Begin' not in new_align:
                new_align['Begin'] = next_align.pop('Begin')
                next_row['align'] = format_align(next_align)
                new_row['align'] = format_align(new_align)
                transfers.append(f"Begin={new_align['Begin']} ← `{next_row['token_id']}`")

        final_rows.append(new_row)

        entry = (
            f"**ADD** `{tid}` (tu_id={new_row.get('tu_id','?')})\n"
            f"  - span=`{new_row.get('span','_')}` form=`{new_row.get('form','_')}` "
            f"jefferson_feats=`{new_row.get('jefferson_feats','_')}` type=`{new_row.get('type','_')}`"
        )
        if transfers:
            entry += f"\n  - ✅ auto-transferred: {'; '.join(transfers)}"
        else:
            entry += "\n  - ⚠️  alignment and placement should be checked manually"
        recap_items.append(entry)

    return final_rows, recap_items


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
    new_rows, recap_items = build_new_rows(csv_rows, tsv_header, tsv_rows, eol)

    # Serialize new rows back to lines
    new_lines = [tsv_lines[0]] + [row_to_line(r, tsv_header, eol) for r in new_rows]

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
    if recap_items:
        recap_md = [
            f"# Patch recap — {tsv_p.name}",
            f"Generated: {date.today()}  |  CSV: `{csv_p.name}`\n",
            "Items marked ⚠️ or ❌ require manual follow-up in the source TSV.",
            "Items marked ✅ were handled automatically.\n",
            "---\n",
        ]
        recap_md += [item + '\n' for item in recap_items]
        recap_p.write_text('\n'.join(recap_md), encoding='utf-8')
        print(f"Recap written: {recap_p}  ({len(recap_items)} items)")
    else:
        print("No structural changes — no recap written.")

    return bool(diff)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    make_patch(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
