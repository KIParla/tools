"""
textutil.py — text encoding conventions shared by every tool.

Everything is UTF-8, no BOM, LF line endings, and Unicode Normalization
Form C — the same requirements Universal Dependencies puts on its files
(see the UD validator, level 1). NFC matters beyond style: a decomposed
"ṭ" (t + COMBINING DOT BELOW) is not a single letter to the tokenizer's
\\p{L} word regex, while the composed "ṭ" is.

Text is normalized once, where it enters the pipeline (eaf2csv, and
TranscriptionUnit for any other caller). Files are always opened with
ENCODING_READ / ENCODING_WRITE and, when written, newline="\\n".
"""

from __future__ import annotations

import unicodedata

# Accept (and drop) a UTF-8 BOM when reading; never write one.
ENCODING_READ = "utf-8-sig"
ENCODING_WRITE = "utf-8"


def nfc(text):
    """Return *text* in Unicode Normalization Form C (non-str passes through)."""
    if isinstance(text, str):
        return unicodedata.normalize("NFC", text)
    return text
