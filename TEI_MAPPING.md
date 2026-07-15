# KIParla → TEI P5 Mapping

This document describes how every field in the pipeline `vert.tsv` format is encoded in the TEI P5 XML output produced by `tsv2tei.py` (SpeechTEI / ISO 24624).

---

## Document Structure

```
<TEI>
  <teiHeader>          — metadata (see below)
  <text><body>
    <div type="interaction" xml:id="{corpus_id}">
      <timeline>       — all unique timestamps as <when> milestones
      <annotationBlock> × N   — one per transcription unit
      <spanGrp> × M    — one per active standoff annotation layer (future)
      <linkGrp type="overlaps">  — overlap anchor pairs
```

---

## TSV Columns

| Column            | TEI encoding                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------- |
| `token_id`        | `@xml:id` on `<w>` (prefixed `w`, e.g. `w0-0`)                                           |
| `speaker`         | `@who` on `<annotationBlock>` (prefixed `#`)                                              |
| `tu_id`           | `xml:id` on `<annotationBlock>` (`ab{N}`) and `<u>` (`u{N}`)                             |
| `unit`            | not encoded                                                                               |
| `id`              | not encoded                                                                               |
| `span`            | not encoded — raw Jefferson notation form; `form` is used instead                         |
| `form`            | text content of `<w>` (clean normalized form)                                             |
| `lemma`           | `@lemma` on `<w>` (when not `_`)                                                          |
| `upos`            | `<fs><f name="upos">…</f></fs>` inside `<w>` (when not `_`)                               |
| `xpos`            | `<fs><f name="xpos">…</f></fs>` inside `<w>` (when not `_`)                               |
| `feats`           | `<fs><f name="feats">…</f></fs>` inside `<w>` (when not `_`)                              |
| `deprel`          | `<fs><f name="deprel">…</f></fs>` inside `<w>` (when not `_`)                             |
| `type`            | determines element type (see **Token Types** below)                                       |
| `meta_label`      | `<fs><f name="meta_label">…</f></fs>` inside `<w>` (when not `_`)                         |
| `variation`       | `<fs><f name="variation">…</f></fs>` inside `<w>` (when not `_` or `none`)                |
| `jefferson_feats` | split by key — see **jefferson_feats Keys** below                                         |
| `align`           | `Begin` → `@start` on `<annotationBlock>`; `End` → `@end`; values reference `<timeline>`  |
| `prolongations`   | inline `<c type="prolongation" n="N"/>` milestones inside `<w>` (see below)               |
| `pace`            | `<shift feature="tempo" new="…"/>` in `<u>` (see **Prosodic Shifts**)                     |
| `guesses`         | `@cert="low"` on `<w>` (when not `_`)                                                     |
| `overlaps`        | `<anchor>` milestones + `<linkGrp type="overlaps">` (see **Overlaps**)                    |

---

## Token Types

The `type` column determines what element is produced instead of or around `<w>`:

| `type` value        | TEI element                                             |
| ------------------- | ------------------------------------------------------- |
| `linguistic`        | `<w xml:id="w{token_id}">`                              |
| `error`             | `<w type="error">`                                      |
| `shortpause`        | `<pause dur="short"/>`                                  |
| `nonverbalbehavior` | `<vocal><desc>…</desc></vocal>` (form stripped of `{}`) |
| `anonymized`        | `<w type="anonymized">`                                 |
| `mwt`               | `<w type="mwt">` containing `syntactic` children (see **Multi-word Tokens**) |
| `syntactic`         | `<w type="syntactic">` nested inside a `mwt` parent     |

---

## Multi-word Tokens (future)

Contractions like *del* (= *di* + *il*) follow the CoNLL-U MWT convention in the TSV: a surface-form row with `type="mwt"` and a range `token_id` (e.g. `5-6`) is immediately followed by the syntactic component rows (`type="syntactic"`, `token_id` `5` and `6`), all within the same `tu_id`.

TEI encoding uses nested `<w>`:

```xml
<w xml:id="w5-6" type="mwt">del
  <w xml:id="w5" type="syntactic" lemma="di" ...>di</w>
  <w xml:id="w6" type="syntactic" lemma="il" ...>il</w>
</w>
```

- The outer `<w type="mwt">` carries the surface form and all prosodic/spoken-language features (prolongation, intonation, `SpaceAfter`, etc.)
- The inner `<w type="syntactic">` elements carry `@lemma`, `upos`, `xpos`, `feats`, `deprel` in `<fs>`
- Span layer standoff annotations (`sent_id`, `iu_id`, etc.) reference the `syntactic` token ids, not the `mwt` id

---

## jefferson_feats Keys

Each entry in `jefferson_feats` (pipe-separated `key=value` pairs) is encoded as follows:

| Key            | Value(s)        | TEI encoding                                                            |
| -------------- | --------------- | ----------------------------------------------------------------------- |
| `Truncated`    | `Yes`           | `@type="interrupted"` on `<w>`                                          |
| `Volume`       | `low` / `high`  | `<shift feature="loud" new="p"/>` / `new="f"/>` in `<u>`                |
| `Language`     | language code   | `@xml:lang` on `<w>` (omitted when `ita`)                               |
| `Intonation`   | see table below | `<shift feature="pitch" new="…"/>` inside `<w>` (word-final)            |
| `SpaceAfter`   | `No`            | `@join="right"` on `<w>`                                                |
| `ProsodicLink` | `Yes`           | `@rend="prosodicLink"` on `<w>`                                         |
| all other keys | any             | `<fs><f name="jf.{key}"><string>{value}</string></f></fs>` inside `<w>` |

### Intonation → pitch mapping

| KIParla value    | TEI `@new`    |
| ---------------- | ------------- |
| `WeaklyRising`   | `weakly_asc`  |
| `Rising`         | `asc`         |
| `Falling`        | `desc`        |
| `WeaklyFalling`  | `weakly_desc` |
| `Level`          | `normal`      |

---

## Prosodic Shifts

`<shift>` (TEI SpeechTEI milestone) marks the point where a paralinguistic feature changes. It is placed in `<u>` before the token where the change begins; a closing `<shift … new="normal"/>` is emitted when the feature returns to baseline or at utterance end.

| Feature | `@new` values | Source field |
|---|---|---|
| `loud` | `p` (soft), `f` (loud), `normal` | `jefferson_feats` `Volume` |
| `tempo` | `l` (lento/slow), `aa` (accelerando/fast), `normal` | `pace` |
| `pitch` | `weakly_asc`, `asc`, `desc`, `weakly_desc`, `normal` | `jefferson_feats` `Intonation` |

Pitch shifts are placed **inside `<w>`** (word-final position) since intonation in Jefferson notation is a point marker, not a spanning feature. Loud and tempo shifts are placed **between tokens** in `<u>` since they span across words.

---

## Prolongations

The `prolongations` field (`posXcount[,posXcount…]`) is encoded as inline `<c>` milestone elements within `<w>`:

- `@type="prolongation"` marks the element as a prolongation marker
- `@n` gives the number of colons (duration)
- Position in the text content encodes which character is prolonged (the `<c>` follows that character)

Example: `prolongations=0x1` on form `eh` →
```xml
<w xml:id="w0_0">e<c type="prolongation" n="1"/>h</w>
```

---

## Overlaps

Simultaneous speech is encoded using milestone `<anchor>` elements so that overlap boundaries can fall anywhere (including mid-word). Anchors are placed inside `<u>` before and after the overlapping tokens:

```xml
<u xml:id="u5">
  <w>di</w>
  <anchor xml:id="OVS_0_5"/>   <!-- overlap clique 0, unit 5: start -->
  <w xml:id="w5_11">giugno</w>
  <anchor xml:id="OVE_0_5"/>   <!-- overlap clique 0, unit 5: end -->
</u>
<u xml:id="u6">
  <anchor xml:id="OVS_0_6"/>
  <w xml:id="w6_0">sì</w>
  <anchor xml:id="OVE_0_6"/>
</u>
```

A `<linkGrp type="overlaps">` at the end of the `<div>` links corresponding anchor pairs:

```xml
<linkGrp type="overlaps">
  <link targets="#OVS_0_5 #OVS_0_6"/>
  <link targets="#OVE_0_5 #OVE_0_6"/>
</linkGrp>
```

Anchor `xml:id` format: `OVS_{clique}_{tu_id}` (start) / `OVE_{clique}_{tu_id}` (end).

---

## Standoff Span Layers (future)

Additional annotation layers that do not align with transcription unit boundaries (syntactic sentences, intonation units, etc.) will be encoded as `<spanGrp>` elements with `<span from="#wX" to="#wY"/>`, referencing token `xml:id`s. Each layer is activated by adding an entry to `SPAN_LAYERS` in `tsv2tei.py`:

```python
SPAN_LAYERS: dict[str, str] = {
    "sent_id": "sentences",
    "iu_id":   "intonation-units",
}
```

When a column is present in the data, the output will include:

```xml
<spanGrp type="sentences">
  <span xml:id="se1" from="#wU1_0" to="#wU3_4"/>
</spanGrp>
```

---

## Metadata (teiHeader)

Populated from `conversations.tsv` and `participants.tsv` when `--metadata-dir` is supplied.

| Source field | TEI location |
|---|---|
| `duration` | `<recording @dur>` (ISO 8601 format) |
| `year` | `<bibl><date @when>` and `<setting><date @when>` |
| `type`, `collection-point`, `topic`, `participants-relationship`, `moderator` | `<bibl><note @type>` |
| `languages` | `<langUsage><language @ident>` (mapped to ISO 639-3) |
| speaker `gender` | `<person><sex @value>` (TEI: `1`=female, `2`=male) |
| speaker `age-range` | `<person><age>` |
| speaker `birth-region` | `<person><birth><region>` |
| speaker `occupation` | `<person><occupation>` |
| speaker `study-level` | `<person><education>` |
| moderator detection | `@role="moderator"` when speaker code has `R` as 3rd character (e.g. `TOR001`) |
