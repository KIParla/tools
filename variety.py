"""
variety.py — reading the `code-variation` column of a vert.tsv.

All variation information of a token row lives in ONE feature-list column,
``code-variation`` (pipe-separated ``Key=Value``, CoNLL-U style):

    ContainsVariation=Yes|No   unit level, repeated on every token row of the unit:
                               Yes when any token of the unit has a Code
    Code=Other                 #word, or any token covered by a "#_" marker
    Code=Unsure                #*word (assignable to more than one language)
    Code=Underspecified        token inside a unit that starts with "# "
    Language=<ISO code>        the language, on tokens marked as another code
                               (NO_ISO_CODE when it wasn't identified)
    Nonce=Yes                  $word (a nonce form; not a code change)

``ContainsVariation`` is always present, so the column is never empty. A ``$``
form alone does not make ``ContainsVariation`` Yes: it is not a code change.

Every marker can be read back from the ``span`` column: the unit-initial
"#_ " / "# " is part of the first token's span, and a mid-unit "#_ " is part
of the span of the first token it covers. So no information about *where* a
marker was written is lost, even though the unit-level flag is only Yes/No.
"""

from __future__ import annotations

import csv

COLUMN = "code-variation"
# Column name used before the rename to `code-variation` (features: `Variety=`).
_LEGACY_COLUMN = "variation"
# Written by intermediate builds of the tools; never released.
_INTERIM_UNIT_COLUMN = "contains_variation"


def feat_value(feats: str | None, key: str) -> str:
    """Value of ``key`` in a pipe-separated ``Key=Value|...`` feature string, or ""."""
    prefix = key + "="
    for part in (feats or "").split("|"):
        if part.startswith(prefix):
            return part[len(prefix):]
    return ""


def feats_dict(feats: str | None) -> dict[str, str]:
    """All ``Key=Value`` pairs of a feature string ("_" / empty give {})."""
    out = {}
    for part in (feats or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def row_contains_variation(row: dict) -> bool:
    return feat_value(row.get(COLUMN), "ContainsVariation") == "Yes"


def row_variety(row: dict) -> str:
    """'other' | 'unsure' | 'underspecified' | '' (the ``Code`` feature) for a vert.tsv row."""
    return feat_value(row.get(COLUMN), "Code").lower()


def row_language(row: dict) -> str:
    """ISO code (or NO_ISO_CODE) of a token marked as another code, else ""."""
    return feat_value(row.get(COLUMN), "Language")


def row_nonce(row: dict) -> bool:
    return feat_value(row.get(COLUMN), "Nonce") == "Yes"


def _old_format_error(source, why):
    return SystemExit(
        f"{source}: old-format vert.tsv ({why}). Regenerate it with the current "
        f"tools (sync.py --from-eaf): its unit-initial '# ' / '#_ ' markers are "
        f"not in the spans, so reading it would silently drop them."
    )


def iter_vert_rows(fobj, source="vert.tsv", delimiter="\t"):
    """Yield the rows of a vert.tsv as dicts, refusing files written before the
    ``code-variation`` column held feature lists.

    Older files have a ``variation`` column with a bare label
    (none/yes/unspecified/all/some), or the interim ``contains_variation``
    column, plus ``Variation=…``/``Orthography=Yes`` features in
    ``jefferson_feats``. Reading them with the current code would silently lose
    every unit-initial "# " and "#_ " marker, so fail loudly instead.
    """
    reader = csv.DictReader(fobj, delimiter=delimiter)
    names = list(reader.fieldnames or [])
    if _INTERIM_UNIT_COLUMN in names:
        raise _old_format_error(source, f"column '{_INTERIM_UNIT_COLUMN}'")
    if _LEGACY_COLUMN in names:
        raise _old_format_error(
            source, f"column '{_LEGACY_COLUMN}' was renamed '{COLUMN}', and the "
            f"'Variety=' feature 'Code=' (Unassignable -> Unsure, Unsure -> Underspecified)")
    first = True
    for row in reader:
        if first:
            first = False
            value = row.get(COLUMN)
            if value not in (None, "", "_") and "ContainsVariation=" not in value:
                raise _old_format_error(source, f"{COLUMN}='{value}'")
        yield row


class OrthographicMarkers:
    """Re-creates the variation markers of the *orthographic* linear text,
    one unit at a time.

    ``next(row)`` returns ``(unit_prefix, word_prefix)`` for the next token row
    of the unit:

      * ``unit_prefix``: "#_ " or "# " when this token's span carries the
        marker (written once), else "".
      * ``word_prefix``: "#", "#*" or "$" for a linguistic token, else "".
        Tokens covered by a "#_" marker get no per-word "#" (the marker
        already covers them); "# "-unit tokens (Underspecified) get none either.
    """

    def __init__(self):
        self._hash_unit = False

    def next(self, row: dict) -> tuple[str, str]:
        span = row.get("span") or ""
        unit_prefix = ""
        if span.startswith("#_ "):
            self._hash_unit = True
            unit_prefix = "#_ "
        elif span.startswith("# "):
            unit_prefix = "# "

        word_prefix = ""
        if row.get("type") == "linguistic":
            variety = row_variety(row)
            if variety == "other" and not self._hash_unit:
                word_prefix = "#"
            elif variety == "unsure":
                word_prefix = "#*"
            elif row_nonce(row):
                word_prefix = "$"
        return unit_prefix, word_prefix
