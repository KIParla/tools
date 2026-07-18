"""jefferson_feats.py — Utilities for KIParla jefferson_feats strings.

Shared by make_patch.py and other tools in this directory.

Public API
----------
SPAN_DERIVED_FEAT_KEYS : frozenset
    Feature keys whose values are derived from the token's span string.
parse_feats(value)     : str → dict[str, str]
format_feats(feats)    : dict[str, str] → str
feats_from_span(span)  : str → dict[str, str]
form_from_span(span)   : str → str | None
    Derive the canonical form from a span string, mirroring
    kiparla_tools/data.py Token.__post_init__. Returns None for special
    tokens ({P}, {…}, x) where form cannot be predicted automatically.
"""

import re

# jefferson_feats keys whose value is derived from the token's span string.
# When span changes, these are recomputed; all other keys are preserved from the TSV.
SPAN_DERIVED_FEAT_KEYS: frozenset[str] = frozenset(
    {'Intonation', 'Interrupted', 'Truncated', 'Volume'}
)


def parse_feats(value: str) -> dict[str, str]:
    """Parse a pipe-separated feature string into an ordered dict.

    >>> parse_feats('Intonation=Falling|ProsodicLink=Yes')
    {'Intonation': 'Falling', 'ProsodicLink': 'Yes'}
    >>> parse_feats('_')
    {}
    """
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
    """Serialize parsed features back to TSV/CSV form.

    >>> format_feats({'Intonation': 'Falling', 'Lang': 'ita'})
    'Intonation=Falling|Lang=ita'
    >>> format_feats({})
    '_'
    """
    if not feats:
        return '_'
    parts = []
    for key, val in feats.items():
        parts.append(key if val == '' else f'{key}={val}')
    return '|'.join(parts)


def feats_from_span(span: str) -> dict[str, str]:
    """Derive jefferson features that depend only on a token's span string.

    Mirrors the logic in kiparla_tools/data.py (Token.__post_init__) applied
    at word level, without requiring the full kiparla_tools data model.

    Returns a dict whose keys are a subset of SPAN_DERIVED_FEAT_KEYS:
      - Intonation: Falling / WeaklyRising / Rising  (trailing . , ?)
      - Interrupted: Yes   (leading/trailing - or ~)
      - Truncated:   Yes   (leading/trailing ')
      - Volume:      Low / High  (° → Low; uppercase letters → High)
    """
    if not span or span == '_':
        return {}

    feats: dict[str, str] = {}

    # Volume=Low: ° present anywhere in span
    if '°' in span:
        feats['Volume'] = 'Low'

    # Strip ° and overlap brackets for further analysis
    core = re.sub(r'°', '', span)
    core = re.sub(r'[\[\]]', '', core)

    # Volume=High: uppercase alphabetic letters (overrides Low; shouldn't coexist)
    if any(c.isupper() for c in core if c.isalpha()):
        feats['Volume'] = 'High'

    # Remove prolongation colons before checking endings
    core = re.sub(r':+', '', core)

    # Intonation from final punctuation
    if core.endswith('.'):
        feats['Intonation'] = 'Falling'
    elif core.endswith(','):
        feats['Intonation'] = 'WeaklyRising'
    elif core.endswith('?'):
        feats['Intonation'] = 'Rising'

    # Strip intonation punctuation to check for truncation/interruption markers
    core_base = core.rstrip('.,?')

    if (core_base.endswith('-') or core_base.endswith('~') or
            core_base.startswith('-') or core_base.startswith('~')):
        feats['Interrupted'] = 'Yes'
    elif core_base.endswith("'") or core_base.startswith("'"):
        alpha = ''.join(c for c in core_base if c.isalpha())
        if alpha not in ('po',):      # "po'" is not a truncation
            feats['Truncated'] = 'Yes'

    return feats


def form_from_span(span: str) -> str | None:
    """Derive the canonical form from a span string.

    Mirrors the normalisation in kiparla_tools/data.py Token.__post_init__:
      1. Return None for special tokens: (.), ((...)), all-x (unknown)
      2. Strip bracket chars: [ ] ( ) < > °
      3. Strip leading $ or # sigils
      4. Remove trailing intonation punctuation: . , ?
      5. Remove elongation colons (:+)
      6. Lowercase

    Returns None when the form cannot be automatically derived (special tokens).
    """
    if not span or span == '_':
        return None

    # Special tokens: form is not predictable. Checked before the bracket
    # strip below, since (.) and ((...)) use the same parens as guess/pace
    # spans.
    stripped = span.strip()
    if stripped == '(.)' or (stripped.startswith('((') and stripped.endswith('))')):
        return None

    text = span
    for ch in '[]()<>°':
        text = text.replace(ch, '')

    text = text.strip()
    if not text:
        return None

    if all(c == 'x' for c in text):
        return None

    # Strip sigils
    if text.startswith('$') or text.startswith('#'):
        text = text[1:]

    # Remove trailing intonation marker
    if text.endswith(('.', ',', '?')):
        text = text[:-1]

    # Remove elongation colons
    text = re.sub(r':+', '', text)

    return text.lower()
