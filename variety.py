"""
variety.py — reading the `code_variation` column of a vert.tsv.

All variation information of a token row lives in ONE feature-list column,
``code_variation`` (pipe-separated ``Key=Value``, CoNLL-U style):

    ContainsVariation=Yes      unit level, repeated on every token row of the unit with
                               a Code (any token of the unit has one); absent otherwise
    Code=Other                 #word, or any token covered by a "#_" marker
    Code=Unsure                #*word (assignable to more than one language)
    Code=Underspecified        token inside a unit that starts with "# "
    Language=<ISO code>        the language, on tokens marked as another code
                               (NO_ISO_CODE when it wasn't identified)
    Nonce=Yes                  $word (a nonce form; not a code change)

``ContainsVariation`` is only written when Yes: an ordinary token has ``_`` (earlier
files also had ``ContainsVariation=No``, which is read the same). A ``$`` form alone
does not make ``ContainsVariation`` Yes: it is not a code change.

Every marker can be read back from the ``span`` column: the unit-initial
"#_ " / "# " is part of the first token's span, and a mid-unit "#_ " is part
of the span of the first token it covers. So no information about *where* a
marker was written is lost, even though the unit-level flag is only Yes/No.
"""

from __future__ import annotations

import csv

COLUMN = "code_variation"
# Column name used before the rename to `code-variation` (features: `Variety=`).
_LEGACY_COLUMN = "variation"
# The hyphenated spelling, renamed to match the other underscore-separated columns.
_HYPHENATED_COLUMN = "code-variation"
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


SYNTACTIC = "syntactic"


def is_syntactic(row: dict) -> bool:
    """True for a row of a syntactic word: one component of a multiword token.

    Such a row (``token_id`` = the surface token's id + a letter, e.g. ``5-7a``) carries
    the lemma/upos of one syntactic word and belongs to the surface token just above it.
    It is not part of the transcription, so nothing that rebuilds the transcription
    (linear files, eaf, NoSketch, counts) may see it.
    """
    return row.get("type") == SYNTACTIC


def iter_vert_rows(fobj, source="vert.tsv", delimiter="\t", syntactic=False):
    """Yield the rows of a vert.tsv as dicts, refusing files written before the
    ``code_variation`` column held feature lists.

    The ``syntactic`` rows (components of multiword tokens, see ``is_syntactic``) are
    left out unless ``syntactic=True``: a reader must ask for them explicitly.

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
    if _HYPHENATED_COLUMN in names:
        raise _old_format_error(
            source, f"column '{_HYPHENATED_COLUMN}' was renamed '{COLUMN}', and "
            f"'Volume=low/high' became 'Volume=Low/High'")
    first = True
    for row in reader:
        if first:
            first = False
            value = row.get(COLUMN)
            # an old bare label (none/yes/all...) instead of a Key=Value feature list
            if value not in (None, "", "_") and "=" not in value:
                raise _old_format_error(source, f"{COLUMN}='{value}'")
        if syntactic or not is_syntactic(row):
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
