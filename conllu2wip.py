#!/usr/bin/env python3
"""
conllu2wip.py — Convert a KIParla CoNLL-U file into the WIP CSV format
used by the lemmatization project.

Usage:
    python conllu2wip.py <input.conllu> [<output.csv>]

If <output.csv> is omitted, the file is written to
    lemmatization-project/wip/KIP/<stem>.csv     (KIP corpus)
    lemmatization-project/wip/ParlaTO/<stem>.csv  (ParlaTO corpus)
based on the filename prefix.

Column mapping
--------------
token_id        KID field from CoNLL-U misc; interpolated for pause/nonverbal
speaker         # speaker_id comment; '_' for subtokens
tu_id           prefix of token_id (e.g. '33' from '33-1'); '_' for subtokens
unit            same prefix, always filled
id              CoNLL-U internal id verbatim ('4-5' for multiword, '4' for subtoken)
span            word from jefferson_text (space-split); '_' for subtokens
form            CoNLL-U form; '[PAUSE]' for pauses; '[TAG]' for nonverbals
lemma           CoNLL-U lemma; '_' for multiword parents; '[PAUSE]'/tag for specials
upos            CoNLL-U upos; '_' for multiword parents; 'X' for pause/nonverbal
jefferson_feats '_' (recomputed downstream from span)
"""

import csv
import re
import sys
from pathlib import Path

WIP_FIELDS = [
    'token_id', 'speaker', 'tu_id', 'unit', 'id',
    'span', 'form', 'lemma', 'upos', 'jefferson_feats',
]

_PARLATO_PREFIXES = ('PTA', 'PTB')

# ── CoNLL-U parser ────────────────────────────────────────────────────────────

def _parse_misc(misc: str) -> dict[str, str]:
    if not misc or misc == '_':
        return {}
    result: dict[str, str] = {}
    for part in misc.split('|'):
        if '=' in part:
            k, v = part.split('=', 1)
            result[k] = v
        else:
            result[part] = ''
    return result


def parse_conllu(path: Path) -> list[dict]:
    sentences: list[dict] = []
    cur: dict | None = None

    with open(path, encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            if line.startswith('# sent_id'):
                if cur is not None:
                    sentences.append(cur)
                cur = {
                    'sent_id': line.split('=', 1)[1].strip(),
                    'jefferson_text': '',
                    'speaker_id': '',
                    'tokens': [],
                }
            elif line.startswith('# jefferson_text'):
                if cur is not None:
                    cur['jefferson_text'] = line.split('=', 1)[1].strip()
            elif line.startswith('# speaker_id'):
                if cur is not None:
                    cur['speaker_id'] = line.split('=', 1)[1].strip()
            elif line.startswith('#') or not line.strip():
                pass
            elif cur is not None:
                parts = line.split('\t')
                if len(parts) < 10:
                    continue
                cur['tokens'].append({
                    'conllu_id': parts[0],
                    'form':      parts[1],
                    'lemma':     parts[2],
                    'upos':      parts[3],
                    'misc':      _parse_misc(parts[9]),
                })

    if cur is not None:
        sentences.append(cur)
    return sentences


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_mw_range(s: str) -> bool:
    return bool(re.fullmatch(r'\d+-\d+', s))


def _range_covers(rng: str, n: int) -> bool:
    a, b = map(int, rng.split('-'))
    return a <= n <= b


def _kid_numeric(kid: str) -> tuple[str, int] | None:
    """'27-5' → ('27', 5);  '27-5a' or None → None."""
    m = re.fullmatch(r'(\d+)-(\d+)', kid) if kid else None
    return (m.group(1), int(m.group(2))) if m else None


def _kid_base(kid: str) -> str | None:
    """'5-3a' → '5-3';  '5-3', '5-3bis' → None.

    Only single-letter suffixes (a, b, c …) are true subtokens.
    Multi-letter suffixes like 'bis', 'ter' are positional variants.
    """
    m = re.fullmatch(r'(\d+-\d+)[a-z]', kid) if kid else None
    return m.group(1) if m else None


def _jt_approx_form(word: str) -> str:
    """Normalise a jefferson word to approximately match a CoNLL-U form.

    Strips Jefferson decoration that is absent from CoNLL-U forms:
      - overlap brackets  [ ] ( ) < > °
      - speed markers     > <
      - trailing intonation  . , ?
      - elongation colons :+
      - trailing latching =  (latch end; CoNLL-U form omits it)

    Does NOT strip leading = (latching onset, part of the word),
    apostrophes, or interruption tildes/dashes (present in CoNLL-U too).
    """
    w = re.sub(r'[\[\]()<>°]', '', word)
    w = w.replace('>', '').replace('<', '')
    w = w.rstrip('.,?')
    w = re.sub(r':+', '', w)
    w = w.rstrip('=')
    w = w.lower()
    # Multiple x's represent unintelligible speech and map to a single 'x' in CoNLL-U
    if re.fullmatch(r'x+', w):
        w = 'x'
    return w


def _split_jefferson_word(word: str) -> list[str]:
    """Split a single space-delimited jefferson word on mid-word ' or =.

    The separator stays attached to the LEFT part:
        c'era         → ["c'", "era"]
        dell'italiano → ["dell'", "italiano"]
        santo=è       → ["santo=", "è"]
        seduta=sul=eh → ["seduta=", "sul=", "eh"]

    No split when the separator is at the very start or very end:
        po'   → ["po'"]     (apostrophe at end)
        'sti  → ["'sti"]    (apostrophe at start)
        =ehi  → ["=ehi"]    (= at start, latching continuation)
    """
    parts = [word]
    changed = True
    while changed:
        changed = False
        new_parts: list[str] = []
        for w in parts:
            split = False
            for sep in ("'", '='):
                idx = w.find(sep)
                if 0 < idx < len(w) - 1:   # strictly in the middle
                    new_parts.append(w[:idx + 1])   # left keeps separator
                    new_parts.append(w[idx + 1:])    # right is new token
                    split = True
                    changed = True
                    break
            if not split:
                new_parts.append(w)
        parts = new_parts
    return parts


def _jt_span_words(jefferson_text: str) -> list[str]:
    """Split jefferson_text into per-token span words.

    Skips non-token markers:
      - '|'        turn-boundary separator in overlapping sentences
      - '((…))'    paralinguistic annotation with no CoNLL-U token

    Also splits space-delimited words on mid-word ' or = so that
    clitics/articles written without spaces (c'era, dell'altro, santo=è)
    are expanded into separate span tokens matching the CoNLL-U tokens.
    """
    result = []
    for w in (jefferson_text.split() if jefferson_text else []):
        if w == '|':
            continue
        if re.fullmatch(r'\(\(.*\)\)', w):
            continue
        result.extend(_split_jefferson_word(w))
    return result


# ── Sentence converter ────────────────────────────────────────────────────────

def sentence_to_rows(sent: dict) -> tuple[list[dict], list[str]]:
    """Convert one sentence to WIP rows.

    Returns (rows, warnings).
    """
    tokens = sent['tokens']
    speaker = sent['speaker_id']
    jt_words = _jt_span_words(sent['jefferson_text'])
    warnings: list[str] = []

    # CoNLL-U multiword range ids (e.g. '4-5')
    mw_ranges = [t['conllu_id'] for t in tokens if _is_mw_range(t['conllu_id'])]

    # ── Pass 1: assign KIDs to tokens missing them ───────────────────────────
    kids: list[str | None] = [t['misc'].get('KID') for t in tokens]

    # Special case: CoNLL-U range rows (multiword parents) sometimes have no KID.
    # Collect the subtokens within the range and derive the parent KID.
    for i, (t, kid) in enumerate(zip(tokens, kids)):
        if kid is None and _is_mw_range(t['conllu_id']):
            mw_end = int(t['conllu_id'].split('-')[1])
            # Gather indices of tokens within this range (the subtokens)
            sub_indices = []
            for j in range(i + 1, len(tokens)):
                try:
                    n = int(tokens[j]['conllu_id'])
                    if n > mw_end:
                        break
                    if _range_covers(t['conllu_id'], n):
                        sub_indices.append(j)
                except ValueError:
                    break

            if not sub_indices:
                continue

            first_sub_kid = kids[sub_indices[0]]
            if not first_sub_kid:
                continue

            base = _kid_base(first_sub_kid)
            if base:
                # Subtokens already have letter-suffixed KIDs (177-0a, 177-0b)
                kids[i] = base
            else:
                # Subtokens share a plain numeric KID (193-3, 193-3).
                # Use that KID as the parent and synthesise letter suffixes for subtokens.
                kids[i] = first_sub_kid
                letters = 'abcdefghijklmnopqrstuvwxyz'
                for k, j in enumerate(sub_indices):
                    kids[j] = f'{first_sub_kid}{letters[k]}'
                warnings.append(
                    f"  sent {sent['sent_id']}: multiword '{t['conllu_id']}' "
                    f"subtokens share KID '{first_sub_kid}' — "
                    f"synthetic letter suffixes assigned, please verify"
                )

    # Remaining None KIDs are pauses/nonverbals: interpolate from surrounding numeric KIDs.
    last_prefix = sent['sent_id'].split('_')[0]
    last_pos = -1
    none_run = 0
    for i, kid in enumerate(kids):
        if kid is not None:
            parts = _kid_numeric(kid)
            if parts:
                last_prefix, last_pos = parts
            none_run = 0
        else:
            none_run += 1
            kids[i] = f'{last_prefix}-{last_pos + none_run}'

    # ── Pass 2: identify orphan subtokens (letter KID, no CoNLL-U range row) ─
    # These occur when the original TSV had a multiword but the CoNLL-U
    # did not include a range header row.
    existing_numeric_kids = {
        kid for kid in kids if kid and re.fullmatch(r'\d+-\d+', kid)
    }

    # Map: base_kid → list of (token_index, token, kid)
    orphan_groups: dict[str, list[tuple[int, dict, str]]] = {}
    for i, (t, kid) in enumerate(zip(tokens, kids)):
        if not kid:
            continue
        base = _kid_base(kid)
        if base is None:
            continue  # not a letter-suffixed KID
        # Check Case 1: already covered by a CoNLL-U range row
        try:
            n = int(t['conllu_id'])
            if any(_range_covers(r, n) for r in mw_ranges):
                continue
        except ValueError:
            pass
        # Check if numeric parent already exists in this sentence
        if base in existing_numeric_kids:
            continue
        # This is an orphan subtoken
        orphan_groups.setdefault(base, []).append((i, t, kid))

    if orphan_groups:
        for base, members in orphan_groups.items():
            warnings.append(
                f"  sent {sent['sent_id']}: orphan subtokens for '{base}' "
                f"(no CoNLL-U range row) — {[m[2] for m in members]} — "
                f"synthetic parent inserted, please verify"
            )

    # Build a flat list of (token, kid, is_synthetic, is_subtoken) in emission order.
    # For orphan groups, emit a synthetic parent just before the first group member.
    emitted: list[tuple[dict, str, bool, bool]] = []
    synthetic_done: set[str] = set()

    for i, (t, kid) in enumerate(zip(tokens, kids)):
        base = _kid_base(kid) if kid else None

        # Is this an orphan subtoken?
        is_orphan_sub = (
            base is not None
            and base in orphan_groups
            and base not in existing_numeric_kids
        )

        # Emit synthetic parent before the first orphan subtoken in each group
        if is_orphan_sub and base not in synthetic_done:
            group = orphan_groups[base]
            group_cids = []
            for _, gt, _ in group:
                try:
                    group_cids.append(int(gt['conllu_id']))
                except ValueError:
                    pass
            synth_cid = (
                f'{min(group_cids)}-{max(group_cids)}'
                if len(group_cids) > 1
                else (str(group_cids[0]) if group_cids else '?')
            )
            synth_token = {
                'conllu_id': synth_cid,
                'form': '_',   # filled from jefferson_text below
                'lemma': '_',
                'upos': '_',
                'misc': {'KID': base},
            }
            emitted.append((synth_token, base, True, False))  # synthetic parent
            synthetic_done.add(base)

        # Determine is_subtoken
        is_sub = False
        # Case 1: CoNLL-U range row
        try:
            n = int(t['conllu_id'])
            if any(_range_covers(r, n) for r in mw_ranges):
                is_sub = True
        except ValueError:
            pass
        # Case 2: orphan subtoken
        if not is_sub and is_orphan_sub:
            is_sub = True

        emitted.append((t, kid, False, is_sub))

    # ── Pass 3: generate WIP rows, consuming jt_words ────────────────────────
    rows: list[dict] = []
    jt_idx = 0
    prev_pause_after = False   # True after a token whose misc has PauseAfter=Yes

    _JT_TAG_RE = re.compile(r'\{[A-Za-z][A-Za-z0-9]*\}')

    for t, kid, is_synth, is_sub in emitted:
        cid   = t['conllu_id']
        form  = t['form']
        lemma = t['lemma']
        upos  = t['upos']
        misc  = t['misc']

        mw = _is_mw_range(cid) or is_synth

        is_pause = (
            form == '{P}'
            or misc.get('Type') in ('shortpause', 'longpause')
        )
        is_nonverbal = (
            not is_pause
            and form.startswith('{') and form.endswith('}')
        )

        # Skip {P}/shortpause tokens when the preceding token's PauseAfter=Yes
        # already generated a synthetic pause row.  Both notations encode the
        # same pause; emitting the explicit token would cause span misalignment.
        if is_pause and not is_sub and prev_pause_after:
            prev_pause_after = False
            continue

        # Span: subtokens never consume a jt_word
        if is_sub:
            span = '_'
        else:
            # Skip annotation-only {TAG} words (e.g. {ride}, {borbotta}) that
            # appear in jefferson_text without a corresponding CoNLL-U token.
            # When the current token's form IS a {TAG}, it should consume the
            # matching word — so we only skip when the forms don't match.
            if not (form.startswith('{') and form.endswith('}')):
                while jt_idx < len(jt_words) and _JT_TAG_RE.fullmatch(jt_words[jt_idx]):
                    jt_idx += 1

            # Soft alignment: skip a jt_word that has no matching CoNLL-U token
            # (e.g. a word removed from CoNLL-U but not from jefferson_text).
            # Heuristic: if current jt_word doesn't approximately match the form
            # but the NEXT one does, skip current and emit a warning.
            if (not is_synth
                    and not is_pause and not is_nonverbal
                    and jt_idx < len(jt_words)
                    and jt_idx + 1 < len(jt_words)):
                cur_norm  = _jt_approx_form(jt_words[jt_idx])
                next_norm = _jt_approx_form(jt_words[jt_idx + 1])
                form_lower = form.lower()
                if cur_norm != form_lower and next_norm == form_lower:
                    warnings.append(
                        f"  sent {sent['sent_id']}: skipped jt_word "
                        f"'{jt_words[jt_idx]}' (no matching CoNLL-U token before "
                        f"'{form}') — possible jefferson_text inconsistency"
                    )
                    jt_idx += 1  # discard the orphan word

            span = jt_words[jt_idx] if jt_idx < len(jt_words) else '_'
            if is_synth:
                form = span  # back-fill synthetic parent's form from span
            jt_idx += 1

        unit = kid.split('-')[0] if (kid and '-' in kid) else ''

        # ── Row by type ───────────────────────────────────────────────────
        if mw:
            row = dict(
                token_id=kid, speaker=speaker,
                tu_id=unit, unit=unit, id=cid,
                span=span, form=form,
                lemma='_', upos='_', jefferson_feats='_',
            )
        elif is_sub:
            row = dict(
                token_id=kid, speaker='_',
                tu_id='_', unit=unit, id=cid,
                span='_', form=form,
                lemma=lemma, upos=upos, jefferson_feats='_',
            )
        elif is_pause:
            row = dict(
                token_id=kid, speaker=speaker,
                tu_id=unit, unit=unit, id=cid,
                span=span, form='[PAUSE]',
                lemma='[PAUSE]', upos='X', jefferson_feats='_',
            )
        elif is_nonverbal:
            tag = f'[{form[1:-1].upper()}]'
            row = dict(
                token_id=kid, speaker=speaker,
                tu_id=unit, unit=unit, id=cid,
                span=span, form=tag,
                lemma=tag, upos='X', jefferson_feats='_',
            )
        else:
            row = dict(
                token_id=kid, speaker=speaker,
                tu_id=unit, unit=unit, id=cid,
                span=span, form=form,
                lemma=lemma, upos=upos, jefferson_feats='_',
            )

        rows.append(row)

        # Track PauseAfter flag for the next iteration (see skip logic above)
        prev_pause_after = not is_sub and misc.get('PauseAfter') == 'Yes'

        # ── Implicit pause: PauseAfter=Yes on a non-subtoken token ───────────
        # The pause appears in jefferson_text as the next word (e.g. '(.)').
        # Insert a synthetic pause row and consume that jt_word.
        if not is_sub and not is_pause and misc.get('PauseAfter') == 'Yes':
            _PAUSE_RE = re.compile(r'\(\.+\)|\(\d+[\.,]\d+\)')
            next_word = jt_words[jt_idx] if jt_idx < len(jt_words) else ''
            if next_word and _PAUSE_RE.fullmatch(next_word):
                pause_span = next_word
                jt_idx += 1
            else:
                pause_span = '(.)'   # fallback if not in jefferson_text

            # Derive pause KID: current KID position + 1
            parts = _kid_numeric(kid)
            if parts:
                pause_kid = f'{parts[0]}-{parts[1] + 1}'
                pause_unit = parts[0]
            else:
                pause_kid = f'{unit}-?'
                pause_unit = unit

            rows.append(dict(
                token_id=pause_kid, speaker=speaker,
                tu_id=pause_unit, unit=pause_unit, id='_',
                span=pause_span, form='[PAUSE]',
                lemma='[PAUSE]', upos='X', jefferson_feats='_',
            ))

    return rows, warnings


# ── Sorting ───────────────────────────────────────────────────────────────────

def _sort_key(row: dict) -> tuple:
    tid = row.get('token_id') or ''
    m = re.fullmatch(r'(\d+)-(\d+)([a-z]*)', tid)
    if m:
        return (int(m.group(1)), int(m.group(2)), m.group(3))
    return (0, 0, tid)


# ── Entry point ───────────────────────────────────────────────────────────────

def convert(input_path: Path, output_path: Path) -> None:
    sentences = parse_conllu(input_path)

    all_rows: list[dict] = []
    all_warnings: list[str] = []
    for sent in sentences:
        rows, warns = sentence_to_rows(sent)
        all_rows.extend(rows)
        all_warnings.extend(warns)

    all_rows.sort(key=_sort_key)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=WIP_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Converted {len(sentences)} sentences / {len(all_rows)} rows → {output_path}")
    if all_warnings:
        print(f"\n{len(all_warnings)} warning(s) — manual review needed:")
        for w in all_warnings:
            print(w)


def _default_output(input_path: Path) -> Path:
    stem = input_path.stem
    corpus = 'ParlaTO' if any(stem.startswith(p) for p in _PARLATO_PREFIXES) else 'KIP'
    base = Path(__file__).parent.parent / 'lemmatization-project' / 'wip' / corpus
    return base / f'{stem}.csv'


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    inp = Path(args[0])
    out = Path(args[1]) if len(args) > 1 else _default_output(inp)
    convert(inp, out)
