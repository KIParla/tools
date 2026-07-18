#!/usr/bin/env python3
"""
validate_csv.py — Validate a KIParla lemmatization CSV before running make_patch.py.

Usage:
    python validate_csv.py <wip_csv> [<wip_csv> ...]
    python validate_csv.py --batch <wip_subdir>

Checks performed:
  1. token_id         — must be non-empty for every row
  2. Subtoken IDs     — must use lowercase letter suffix (a, b, c …) after the
                        numeric part; parent multiword token must also be present
  3. UPOS             — must be a valid Universal Dependencies tag, or '_'
  4. Multiword tokens — parent rows (those with subtoken rows) must have
                        lemma='_' and upos='_'
  5. Pauses           — tokens with form='[PAUSE]' or span='(.)' must have
                        a bracket tag as lemma (e.g. '[PAUSE]') and upos='X'
  6. Nonverbal        — tokens whose span is enclosed in (( )) must
                        have a bracket tag as lemma (e.g. '[NVB]') and upos='X'
  7. Missing annotation — regular tokens (not multiword parents, pauses or
                        nonverbal) with lemma='_' or upos='_' are reported as
                        warnings (not errors)

Exit code: 0 if no errors, 1 if any errors found (warnings do not affect exit code).
"""

import csv
import re
import sys
from pathlib import Path

# All valid Universal Dependencies UPOS tags
UD_UPOS: frozenset[str] = frozenset({
    'ADJ', 'ADP', 'ADV', 'AUX', 'CCONJ', 'DET', 'INTJ',
    'NOUN', 'NUM', 'PART', 'PRON', 'PROPN', 'PUNCT',
    'SCONJ', 'SYM', 'VERB', 'X',
})

_MISSING = frozenset({'', '_'})


def _is_missing(v: str) -> bool:
    return v.strip() in _MISSING


def _is_subtoken(token_id: str) -> bool:
    """3-2a, 3-2b → True; 3-2, 3-2bis, 3-2ter → False.

    Only single-letter suffixes (a, b, c …) are true subtokens.
    Multi-letter suffixes like 'bis', 'ter' are KIParla positional
    variants and are treated as regular tokens.
    """
    return bool(re.fullmatch(r'\d+-\d+[A-Za-z]', token_id))


def _subtoken_base(token_id: str) -> str | None:
    """'3-2a' → '3-2'; '3-2', '3-2bis' → None."""
    m = re.fullmatch(r'(\d+-\d+)[A-Za-z]', token_id)
    return m.group(1) if m else None


def _is_pause(row: dict) -> bool:
    form = (row.get('form') or '').strip()
    span = (row.get('span') or '').strip()
    # Accept both the current (.) notation and the legacy {P} notation, since
    # wip/*.csv files not yet reprocessed through the tools/ pipeline may
    # still use the old form.
    return form in ('[PAUSE]', '(.)', '{P}') or span in ('(.)', '{P}')


def _is_nonverbal(row: dict) -> bool:
    span = (row.get('span') or '').strip()
    is_paren_form = span.startswith('((') and span.endswith('))')
    is_brace_form = span.startswith('{') and span.endswith('}')
    return (is_paren_form or is_brace_form) and not _is_pause(row)


def validate_csv(path: Path) -> tuple[list[str], list[str]]:
    """Run all checks on *path*.

    Returns (errors, warnings).  Errors block make_patch; warnings need review.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        return [f"cannot read file: {exc}"], []

    if not rows:
        return ['file is empty or has no data rows'], []

    # ── Pre-pass: collect all token IDs and infer multiword parents ──────────
    all_ids: set[str] = set()
    multiword_parents: set[str] = set()
    for row in rows:
        tid = (row.get('token_id') or '').strip()
        if tid:
            all_ids.add(tid)
        base = _subtoken_base(tid)
        if base is not None:
            multiword_parents.add(base)

    # ── Row-by-row checks ────────────────────────────────────────────────────
    for row in rows:
        raw_tid = (row.get('token_id') or '').strip()
        upos = (row.get('upos') or '').strip()
        lemma = (row.get('lemma') or '').strip()

        # 1. token_id must be present
        if not raw_tid:
            errors.append(f"row with empty token_id: {dict(row)}")
            continue

        tid = raw_tid
        loc = f"token `{tid}`"

        # 2a. Subtoken letter suffix must be lowercase
        if _is_subtoken(tid):
            letter = re.fullmatch(r'\d+-\d+([A-Za-z]+)', tid).group(1)
            if not letter.islower():
                errors.append(
                    f"{loc}: subtoken suffix '{letter}' must be lowercase (a, b, c …)"
                )

        # 2b. Parent multiword token must exist in the file
        base = _subtoken_base(tid)
        if base is not None and base not in all_ids:
            errors.append(
                f"{loc}: subtoken has no parent row — expected token_id '{base}'"
            )

        # 3. UPOS must be a valid UD tag or '_'
        if upos not in UD_UPOS and not _is_missing(upos):
            errors.append(f"{loc}: invalid upos '{upos}' — not a UD UPOS tag")

        # 4–6. Tokens that must carry lemma='_' and upos='_'
        if tid in multiword_parents:
            if not _is_missing(lemma):
                errors.append(
                    f"{loc}: multiword token lemma should be '_', got '{lemma}'"
                )
            if not _is_missing(upos):
                errors.append(
                    f"{loc}: multiword token upos should be '_', got '{upos}'"
                )
            continue  # no further annotation checks for multiword parents

        if _is_pause(row):
            if not re.fullmatch(r'\[.+\]', lemma):
                errors.append(
                    f"{loc}: pause lemma should be a bracket tag like '[PAUSE]', got '{lemma}'"
                )
            if upos != 'X':
                errors.append(
                    f"{loc}: pause upos should be 'X', got '{upos}'"
                )
            continue

        if _is_nonverbal(row):
            if not re.fullmatch(r'\[.+\]', lemma):
                errors.append(
                    f"{loc}: nonverbal token lemma should be a bracket tag like '[NVB]', got '{lemma}'"
                )
            if upos != 'X':
                errors.append(
                    f"{loc}: nonverbal token upos should be 'X', got '{upos}'"
                )
            continue

        # 7. Regular tokens: warn if annotation is missing
        if _is_missing(lemma):
            warnings.append(f"{loc}: missing lemma")
        if _is_missing(upos):
            warnings.append(f"{loc}: missing upos")

    return errors, warnings


def report(path: Path) -> tuple[int, int]:
    """Validate *path*, print results, return (n_errors, n_warnings)."""
    errors, warnings = validate_csv(path)
    if not errors and not warnings:
        print(f"  OK  {path.name}")
        return 0, 0

    label = "FAIL" if errors else "WARN"
    print(f"  {label}  {path.name}")
    for msg in errors:
        print(f"    ERROR: {msg}")
    for msg in warnings:
        print(f"    warn:  {msg}")
    return len(errors), len(warnings)


def run_single(paths: list[Path]) -> int:
    """Validate one or more CSVs. Return exit code."""
    total_errors = 0
    total_warnings = 0
    for p in paths:
        ne, nw = report(p)
        total_errors += ne
        total_warnings += nw
    _print_summary(len(paths), total_errors, total_warnings)
    return 1 if total_errors else 0


def run_batch(wip_dir: Path) -> int:
    """Validate every *.csv inside *wip_dir*. Return exit code."""
    csv_paths = sorted(wip_dir.glob('*.csv'))
    if not csv_paths:
        print(f"No CSV files found in `{wip_dir}`", file=sys.stderr)
        return 1

    total_errors = 0
    total_warnings = 0
    for p in csv_paths:
        ne, nw = report(p)
        total_errors += ne
        total_warnings += nw

    _print_summary(len(csv_paths), total_errors, total_warnings)
    return 1 if total_errors else 0


def _print_summary(n_files: int, n_errors: int, n_warnings: int) -> None:
    print()
    print(
        f"Validated {n_files} file(s): "
        f"{n_errors} error(s), {n_warnings} warning(s)"
    )
    if n_errors:
        print("Fix errors before running make_patch.py.")


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == '--batch':
        if len(args) < 2:
            print("Usage: validate_csv.py --batch <wip_subdir>", file=sys.stderr)
            sys.exit(1)
        sys.exit(run_batch(Path(args[1])))

    sys.exit(run_single([Path(a) for a in args]))
