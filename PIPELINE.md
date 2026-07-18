# KIParla Processing Pipeline

This document describes the full `process` pipeline that converts a per-conversation CSV
(produced by `eaf2csv`) into a `.vert.tsv` file.

It is the reference for porting this logic from `kiparla-tools` to `tools`, and for
deciding what should be configurable per module.

---

## Overview

```
eaf2csv output CSV
        │
        ▼
[1] Read CSV → build Transcript (unsorted TranscriptionUnits)
        │
        ▼
[2] Preprocess each TranscriptionUnit
    – detect language variation markers
    – apply normalization (warning) rules
    – apply error checks
    – extract span-level feature positions
        │
        ▼
[3] Sort TranscriptionUnits by start time
        │
        ▼
[4] Find time-based overlaps → build overlap graph
        │
        ▼
[5] Tokenize each TranscriptionUnit
    – split on spaces and prosodic links
    – classify each token
    – extract token-level features (intonation, truncation, prolongations, volume, …)
        │
        ▼
[6] Resolve overlaps
    – prune overlap graph
    – match annotated overlap spans to time-based overlap events
        │
        ▼
[7] Map span features to tokens (add_token_features)
        │
        ▼
[8] Serialize to vert.tsv  (conversation_to_conll)
    + JSON summary
```

---

## Input

A tab-separated CSV with columns:

| column        | type  | notes                                          |
|---------------|-------|------------------------------------------------|
| tu_id         | int   | sequential, 0-based                            |
| speaker       | str   | ELAN tier ID                                   |
| start         | float | seconds                                        |
| end           | float | seconds                                        |
| duration      | float | seconds                                        |
| text          | str   | raw Jefferson transcription                    |
| parent_tu_id  | int   | TU id of the parent annotation; empty for top-level tiers |

`parent_tu_id` is produced by `eaf2csv` for ELAN child tiers (e.g. `Traduzione`).

**Config:**

- `tiers_to_ignore` — TUs from these tiers are skipped entirely and never appear in
  the output.
- `tiers_to_extract` — TUs from these tiers bypass the main pipeline and are written
  to a separate `<name>.translations.tsv` (no normalization or tokenization).
  The `parent_tu_id` column links each translation to its source TU.

---

## Step 2 — TranscriptionUnit preprocessing

Runs inside `TranscriptionUnit.__post_init__` before any tokenization.

### 2a. Empty / exclude check

If the annotation is empty or `None`, the TU is marked `include=False` and skipped
in all downstream steps.

### 2b. Language variation markers

| prefix | meaning                        | action                        |
|--------|--------------------------------|-------------------------------|
| `# `   | some tokens are non-Italian, unspecified which | strip prefix, set `non_ita=unspecified` |
| `#_`   | entire TU is non-Italian       | strip prefix, set `non_ita=all`, **return early** (no further normalization) |

When `non_ita=all` the annotation is not normalized and tokenization assigns
`Language=NO_ISO_CODE` to every token.

`non_ita=unspecified` (step 2b) and `non_ita=yes` (derived bottom-up in step 5e from
individually-`#`/`$`/`#*`-marked tokens) are tracked as distinct `languagevariation`
values, not merged — `unspecified` is never downgraded to `yes` by step 5e, even if the
TU happens to also contain individually-marked tokens. This distinction is what lets
`vert2eaf` losslessly reconstruct the `"# "` prefix only when it was actually present in
the source (see `serialize.vert_to_linear_rows`): `unspecified` means "there's no other
record of this marker, reconstruct the prefix"; `yes` means "each contributing token
already carries its own marker in `span`, reconstructing the prefix would duplicate it."

### 2c. Normalization (warning) rules

Applied in order. Each rule returns a substitution count and the modified string.
The count is accumulated in `tu.warnings[RULE_NAME]`.

| # | rule name             | function                  | what it does |
|---|-----------------------|---------------------------|--------------|
| 1 | `SYMBOL_NOT_ALLOWED`  | `clean_non_jefferson_symbols` | Remove characters not in `allowed_symbols` (config), `\w`, `\s`, or active variation prefixes (`$`, `#*`). |
| 2 | `META_TAGS`           | `meta_tag`                | Normalize spacing inside NVB spans: spaces inside `((…))` replaced with `_` so the tag is treated as a single token downstream. Shortpause `(.)` and NVB `((…))` stay literal Jefferson notation (not rewritten). |
| 3 | `UNEVEN_SPACES`       | `check_spaces`            | Remove spaces immediately inside `[ ]` and `( )`, before `. , : ?`, and around `(.)`/`((…))` tags. |
| 4 | `TRIM_PAUSES`         | `remove_pauses`           | Remove leading/trailing `(.)`. |
| 5 | `TRIM_PROSODICLINKS`  | `remove_prosodiclinks`    | Remove leading/trailing `=`. |
| 6 | `UNEVEN_SPACES`       | `space_prosodiclink`      | Remove spaces around `=`. |
| 7 | `OVERLAP_PROLONGATION`| `overlap_prolongations`   | **Default off.** Move `[` before the last character + its colons: `word:*[:` → `wor[d:*:`. |
| 8 | `MULTIPLE_SPACES`     | `remove_spaces`           | Remove tabs, newlines, double spaces. |
| 9 | `ACCENTS`             | `apply_accent_corrections` | Apply word-boundary-aware substitutions from `accent_corrections` (config list). |
| 10| `NUMBERS`             | `check_numbers`           | Replace Arabic numerals with Italian words (`num2words`). |
| 11| `HASH_UNIT_SPACE`     | `normalize_hash_unit_space` | **Default off.** Ensure `#_` at start of unit has a space: `#_word` → `#_ word`. (StraParlaBO, StraParlaTO) |
| 12| `HASH_PREFIX_SPACE`   | `normalize_hash_prefix`    | **Default off.** Move `#` to front of unit with a space. (KIPasti) |

**Config:** rules are toggled per module; `allowed_symbols`, `accent_corrections`, and
`reduction_words` are lists in `defaults.yml` that modules may override.

### Variation markers

Token-level and TU-level variation is signalled by prefixes on tokens or units.
The prefix is stripped during tokenization and a `Variation=` label is written to
`jefferson_feats` (token level) or the `variation` column (TU level).

| prefix | scope | label | modules |
|--------|-------|-------|---------|
| `# ` at start of TU | TU | `Variation=Unit` | KIPasti, StraParlaBO, StraParlaTO |
| `#_ ` at start of TU | TU | `Variation=Unit` (all tokens) | StraParlaBO, StraParlaTO |
| `#word` | token | `Variation=Token` | StraParlaBO, StraParlaTO |
| `$word` | token | `Variation=Emerging` | StraParlaBO, StraParlaTO |
| `#*word` | token | `Variation=Doubtful` | StraParlaBO, StraParlaTO |

The dataflags enum `tokenvariation` carries `token`, `emerging`, `doubtful` values.
TU-level variation (`languagevariation.yes` / `.unspecified` / `.all`) is rendered as-is
(`tu.non_ita.name`) in the `variation` output column.

### 2d. Error checks

Applied after normalization. Each check returns `True` (valid) or `False` (error).
Stored in `tu.errors[RULE_NAME]`.

| rule name          | function                     | what it checks |
|--------------------|------------------------------|----------------|
| `UNBALANCED_DOTS`  | `check_even_dots`            | Number of `°` characters is even |
| `UNBALANCED_PACE`  | `check_angular_parentheses`  | `< >` pairs are balanced (handles both `<…>` slow and `>…<` fast) |
| `UNBALANCED_GUESS` | `check_normal_parentheses`   | `( )` pairs are balanced and non-nested |
| `UNBALANCED_OVERLAP`| `check_normal_parentheses`  | `[ ]` pairs are balanced and non-nested |

### 2e. Conditional fixes (run only if no error in their bracket type)

- If `°` present and `UNBALANCED_DOTS=False`: fix spaces inside `°…°` spans.
- If `<` present and `UNBALANCED_PACE=False`: fix spaces inside `<…>` / `>…<` spans.

### 2f. Span position extraction

Stored as character offset pairs on the TU for use in step 7.

| field              | condition                  | regex              |
|--------------------|----------------------------|--------------------|
| `slow_pace_spans`  | `<` present, no pace error | `<…>`              |
| `fast_pace_spans`  | `<` present, no pace error | `>…<`              |
| `low_volume_spans` | `°` present, no dot error  | `°[^°]+°`          |
| `high_volume_spans`| always                     | runs of uppercase letters |
| `overlapping_spans`| `[` present, no overlap error | `\[[^\]]+\]`    |
| `guessing_spans`   | `(` present, no guess error | `\([^)]+\)` (run against the annotation with `(.)`/`((…))` masked out first, so pause/NVB spans aren't mistaken for guess spans). Each match is then split by `is_reduction_candidate_span` into `guessing_spans` (genuine "hard to understand" spans) or `reduction_spans` (reduction candidate: letters immediately before `(` and after `)` — covers both the single-token case `c(io)è` and the multi-token case `m(e l)o`, since the parens content may contain whitespace). `reduction_spans` are a *candidate* list only — step 7 decides the final classification against the `reduction_words` config whitelist, matching the space-joined form of every token the span touches. |

### 2g. Symbol order corrections

- `switch_symbols`: fix `[.,?][:-~]` → `[:-~][.,?]` (punctuation must follow, not precede, prosodic/interruption markers).
- `switch_NVB`: fix `[((TAG))` → `((TAG))[` and `((TAG))]` → `]((TAG))` (NVB tag must be outside overlap brackets).

Counts accumulated in `tu.warnings["SWITCHES"]`.

### 2h. All-symbol exclusion

If the annotation contains only non-alphabetic characters after normalization
(`[ ] ( ) ° > < - ' #`), mark the TU `include=False`.

---

## Step 3 — Sort

TUs are sorted by `start` time. `Transcript.tot_length` is set to the `end` of the
last TU.

---

## Step 4 — Find time-based overlaps

`Transcript.find_overlaps(duration_threshold)` builds an `nx.Graph` where:
- Nodes = TU ids (with `speaker` and `overlapping_spans` attributes)
- Edges = pairs of TUs whose time intervals intersect (`tu1.end > tu2.start` and `tu2.end > tu1.start`)

Edge attributes: `start`, `end`, `duration` of the intersection.

Only TUs with `include=True` are considered.

---

## Step 5 — Tokenize

`TranscriptionUnit.tokenize()` splits the normalized annotation on spaces and `=`.

### 5a. Split

`re.split(r"( |=)", annotation)` — keeps the delimiters as separate items.

### 5b. Per-item processing

| item | action |
|------|--------|
| `" "` (space) | skip (advance position counter) |
| `"="` (prosodic link) | add `ProsodicLink` feature to the **previous** token |
| token containing `'` with letters on both sides | split into two sub-tokens at the apostrophe; first gets `SpaceAfter=No` |
| anything else | create a `Token` object |

### 5c. Token classification (`Token.__post_init__`)

In order of priority:

| condition | token_type | notes |
|-----------|------------|-------|
| starts with `@` | `anonymized` | |
| `(.)` | `shortpause` | |
| starts with `((` and ends with `))` | `nonverbalbehavior` | checked before the generic `[]()<>°` marker strip, since `(.)`/`((…))` reuse guess/pace-span parens |
| all `x` characters | `unknown` | form kept as-is; `Syllables=N` added (N = number of `x`s) |
| starts with `$` | `linguistic` + `non_ortho=True` | strip `$` prefix |
| starts with `#` | `linguistic` + `non_ita=True`, `iso_code=NO_ISO_CODE` | strip `#` prefix |
| matches word regex | `linguistic` | see below |
| `po'` variant | `linguistic` | special case: `pò` not caught by normalization |
| otherwise | `error` or `warning` | see below |

**Config:** the `$` marker for non-orthographic tokens is module-specific.
Modules that do not use `$` should not have it stripped.

**Variation downgrade:** if a token falls through to `error` and it is in a variation
context — either the token itself carried a `#` or `$` prefix (`Variation=Token`,
`Variation=Emerging`, `Variation=Doubtful`) or the TU is marked `non_ita=all`
(`Variation=Unit`) — the `error` is downgraded to a `warning`. Foreign-variety tokens
may legitimately not match the Italian word regex.

Word regex: `r"['~-]?(\p{L}+:*[-]?)*\p{L}+:*[-'~]?[.,?]?"` (Unicode letters, optional
leading/trailing apostrophe/dash/tilde, optional trailing punctuation; `-` may also
appear inside a token as a hyphen in compound words).

### 5d. Token feature extraction

Features are extracted from `linguistic`, `unknown`, and `anonymized` tokens.

**Unknown tokens** — `Syllables=N` where N = number of `x` characters. Form is kept
as transcribed (not normalized to a single `x`).

**Intonation** — from the final character (consumed, not kept in `form`):

| char | intonation |
|------|-----------|
| `.`  | `falling` |
| `,`  | `weakly_rising` |
| `?`  | `rising` |

**Interruption** — `True` if token starts or ends with `-` or `~`. Internal `-` (hyphen
in compound words) does not trigger interruption.

**Truncation** — `True` if token starts or ends with `'`, unless the stem is `po`.
(If `SpaceAfter=No` is later added, truncation is reset to `False`.)

**Prolongations** — each `:` run is recorded as `{char_position: length}`.
Colons are stripped from the form. (Linguistic tokens only.)

**High volume** — `True` if any character is uppercase. Form is lowercased.

**SpaceAfter=No** — set on the first sub-token produced by an apostrophe split (e.g.
`l'` from `l'albero`). Set during tokenization (step 5b), not re-derived here.

**PauseAfter=Yes** — set on any token immediately followed by a `shortpause` token in
the token list. Assigned in a post-tokenization pass over the token sequence.

### 5e. Post-tokenize: language variation

If the TU was marked `non_ita=all` in step 2b, every token gets `Language=NO_ISO_CODE`.
Otherwise, `non_ita` on the TU is updated to `all` (every token carries the `non_ita`
flag) or `yes` (some do) — unless it's already `unspecified` (explicit TU-level `"# "`
prefix from step 2b), which is preserved rather than downgraded to `yes`.

---

## Step 6 — Resolve overlaps

`Transcript.check_overlaps(duration_threshold, relations_to_ignore)`

### 6a. Remove NVB edges *(configurable)*

**Config: `nvb_participates_in_overlaps` (default `false`)**

When `false`: for every edge `(u, v)` in the overlap graph, if **all** tokens of `u`
or **all** tokens of `v` are `nonverbalbehavior`, the edge is removed.

When `true`: NVB TUs participate in overlaps like any other TU — NVB edges are kept
and NVB-adjacent mismatches are not silently discarded.

### 6b. Remove manually ignored pairs

Edges listed in `relations_to_ignore` (loaded from per-file YAML annotation files)
are removed unconditionally.

### 6c. Remove short unannotated overlaps

For each edge where `duration < duration_threshold` AND neither TU has annotated
`overlapping_spans`:
- Shrink `tu1.end` and `tu2.start` inward by `duration/2` each.
- Record a `MOVED_BOUNDARIES` warning.
- Remove the edge.

**Config:** `duration_threshold` (default `0.1`).

### 6d. Find cliques and assign overlap events

`nx.find_cliques` on the pruned graph. Each clique becomes one overlap event with an
`overlap_start` = max of TU starts, `overlap_end` = min of TU ends.

A flag `nvb_in_clique` is set if any TU in the clique contains any NVB token.

### 6e. Match annotated spans to overlap events

For each TU, compare the count of `overlapping_spans` (bracket annotations in text)
with the count of `overlapping_times` (graph-based events):

| spans | times | outcome |
|-------|-------|---------|
| equal | equal | match by time order → `overlapping_matches` ✓ |
| 0     | > 0   | check each event: if `nvb_in_clique` **or** `duration < threshold` → removable *(see note)* |
|       |       | if all events removable → `MISMATCHING_OVERLAPS` warning |
|       |       | else → `OVERLAPS:MISSING_ANNOTATION` error |
| > 0   | 0     | `OVERLAPS:MISSING_TIME` error; spans get `?` match id |
| times > spans | — | try to remove NVB/short events to close the gap → warning or error |
| otherwise | — | `MISMATCHING_OVERLAPS` error |

**Config: `nvb_participates_in_overlaps` (same toggle as 6a)**

When `true`, `nvb_in_clique` is never treated as a reason to make an event removable,
so missing annotations on NVB-adjacent overlaps become errors rather than warnings.

---

## Step 7 — Map span features to tokens

`TranscriptionUnit.add_token_features()`

Each span position pair collected in step 2f (slow/fast pace, low/high volume, guesses,
overlapping) is mapped to the tokens it covers, by walking the character-to-token index
built from `Token.orig_text`.

For each feature, each covered token receives `(span_id, char_start, char_end)` so the
feature can be rendered precisely in the vert.tsv output.

First and last tokens of each TU get `position.start` / `position.end` flags (used to
write `Begin=` / `End=` alignment in the output).

### Sub-token overlap position encoding (`P+N`)

Overlap spans can start or end mid-token (when `[` falls inside a word, or on a
prolongation colon). The position within a token is encoded as `P` or `P+N`:

- **`P`** — 0-based index of the form character at the bracket boundary.
  "Form character" = a letter or apostrophe in the normalized form (colons stripped).
- **`N`** — number of colons belonging to that form character that appear **before**
  the bracket in the original text. Omitted (i.e. written as bare `P`) when `N = 0`.

`P+N` is always a direct index into the token's original `span` string when no form
character **before** position `P` in the same token carries prolongations (the common
case). When preceding prolongations exist (e.g. `sche::[rzavo]`) the `P+N` value
encodes the correct prolongation offset for character `P` but is not a direct string
index; this edge case is accepted as a limitation.

**Examples:**

| Jefferson fragment | bracket pos | encoding |
|--------------------|-------------|----------|
| `s[cherzavo]`      | `[` after `s` | start = `1` |
| `[scherzavo]`      | `[` before first char | start = `0` |
| `the:::[:`         | `[` on 3rd colon of `e` | start = `2+3` |
| `the[:` (1 colon)  | `[` on 1st colon of `e` | start = `2+1` |
| `s[cherzavo::]::` (end after 2 trailing colons of `o`) | end = `9+2` |

**In the `overlaps` column** the full token-level encoding is:
`START-END(OVERLAP_ID)` where `START` and `END` are either bare `P` or `P+N`.
Token-internal overlaps where both boundaries fall within the same token:
`s[cher]zavo` → `1-4(id)`.

For spans that cover whole tokens the boundary is the token edge: start = `0` (or the
first covered token's start edge), end = form length of the last covered token.

---

## Step 8 — Serialize

Output files always produced:

1. **`<name>.vert.tsv`** — token-per-line CoNLL-style format (`conversation_to_conll`)
2. **`<name>.json`** — summary of warnings, errors, and overlap events per TU

Produced when `tiers_to_extract` is non-empty:

3. **`<name>.translations.tsv`** — one row per extracted TU, columns: `tu_id`,
   `speaker`, `start`, `end`, `parent_tu_id`, `text`

### vert.tsv columns (tab-separated, `_` for missing values)

| column          | content |
|-----------------|---------|
| `token_id`      | `TU_ID-TOKEN_IDX` (e.g. `5-2`) |
| `speaker`       | tier ID |
| `tu_id`         | TU id |
| `unit`          | same as `tu_id` |
| `id`            | same as token index within TU |
| `span`          | raw Jefferson span (character slice of original annotation) |
| `form`          | normalized form (lowercase, no prolongations, no punctuation) |
| `lemma`         | `_` (filled by lemmatization step) |
| `upos`          | `_` (filled by lemmatization step) |
| `xpos`          | `_` |
| `feats`         | `_` |
| `deprel`        | `_` |
| `type`          | token type flag (`linguistic`, `shortpause`, `nonverbalbehavior`, …) |
| `meta_label`    | `_` |
| `variation`     | language variation flag on the TU |
| `jefferson_feats` | pipe-separated: `Intonation=X`, `Interrupted=Yes`, `Truncated=Yes`, `Reduced=Yes`, `ProsodicLink=Yes`, `SpaceAfter=No`, `PauseAfter=Yes`, `Language=ISO`, `Orthography=Yes`, `Volume=X`, `Variation=X`, `Syllables=N` |
| `align`         | `Begin=X` / `End=X` / `Begin=X\|End=X` for first/last token of TU |
| `prolongations` | e.g. `3x2,7x1` (char_pos × length pairs) |
| `pace`          | `Slow=0-5(0),…` / `Fast=…` |
| `guesses`       | `0-3(0),…` — genuine "hard to understand" spans only. A reduction-candidate span (single-token `c(io)è` or multi-token `m(e l)o`) whose reconstructed word/phrase is on the `reduction_words` config whitelist is *not* recorded here — every token it touches gets `Reduced=Yes` in `jefferson_feats` instead. If not whitelisted, it falls back to an ordinary entry in this column on each touched token, sharing one span id. |
| `overlaps`      | `0-3(0),…` |

---

## Known module differences (to drive config design)

| behavior | current default | modules needing different value |
|----------|-----------------|----------------------------------|
| `tiers_to_ignore` | `["Traduzione"]` | modules where `Traduzione` should be processed |
| `nvb_participates_in_overlaps` | `false` (prune NVB edges, treat NVB mismatches as removable) | ParlaBZ: `true` |
| `$` non-orthographic marker | kept / stripped to flag `non_ortho` | modules without `$` convention |
| normalization rules | all enabled | TBD per module |
| `duration_threshold` | `0.1` | TBD per module |
