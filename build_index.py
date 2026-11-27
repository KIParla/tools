#!/usr/bin/env python3
"""
build_index.py - Generate the index page of KIParla-artifacts (index.html).

The page lists the HTML/PDF transcriptions of every module and lets the reader
filter the conversations by their metadata (module, type, year, collection point,
community, languages, unidentified speakers), by the metadata of their participants
(gender, age, region, ...) and by code. The metadata are read from each module's
`metadata/conversations.tsv` and `metadata/participants.tsv` and embedded in the page,
so it works as a static file; the links point to the pages that exist under
`<module>/html/`. When several participant filters are set, a conversation is listed
only if one same participant matches all of them.

Usage:
    python3 tools/build_index.py \\
        --modules-root modules \\
        --artifacts-root KIParla-artifacts

Writes <artifacts-root>/index.html (styles: <artifacts-root>/css/index.css).
"""

import argparse
import csv
import json
from html import escape
from pathlib import Path

from linear2html import PARTICIPANT_VALUE_LABELS, tr

MODULES = ["KIP", "KIPasti", "ParlaBO", "ParlaTO", "ParlaBZ", "Stra-ParlaBO", "Stra-ParlaTO"]

# metadata column -> key in the embedded JSON
FIELDS = {
    "type": "type",
    "year": "year",
    "collection-point": "point",
    "languages": "languages",
    "community": "community",
    "unknown-participant": "unknown",
    "duration": "duration",
    "participants-number": "speakers",
}
MISSING = {"", "_"}

# key in the embedded JSON -> participants.tsv columns (the first one present is read).
# Modules have different columns; KIP and ParlaBZ record the school region, which is
# used as the region, as in KIParla-collection.
PARTICIPANT_FIELDS = {
    "gender": ("gender",),
    "age": ("age-range",),
    "region": ("birth-region", "school-region"),
    "study": ("study-level",),
    "occupation": ("occupation",),
    "l1": ("L1",),
    "mothertongue": ("mothertongue",),
    "arrival": ("age-of-arrival-italy",),
    "years": ("years-in-italy",),
}


def participant_values(row: dict) -> dict:
    """{key: [values]} of one participants.tsv row; missing values are left out."""
    out = {}
    for key, columns in PARTICIPANT_FIELDS.items():
        column = next((c for c in columns if c in row), None)
        values = []
        for value in (row.get(column) or "").split(";"):
            value = value.strip()
            if key == "region":
                value = value.partition(":")[0]   # "estero:colombia" -> "estero"
            if value not in MISSING | {"N/A"}:
                values.append(tr(value, PARTICIPANT_VALUE_LABELS.get(column, {})))
        if values:
            out[key] = values
    return out


def read_participants(modules_root: Path, module: str) -> dict[str, list[dict]]:
    """{conversation code: [participant_values, ...]}.

    The link comes from the `conversations` column of participants.tsv: the
    `participants` column of conversations.tsv does not list every speaker.
    """
    path = modules_root / module / "metadata" / "participants.tsv"
    by_conversation: dict[str, list[dict]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            values = participant_values(row)
            for code in (row.get("conversations") or "").split(";"):
                by_conversation.setdefault(code.strip(), []).append(values)
    return by_conversation


def read_module(modules_root: Path, artifacts_root: Path, module: str) -> list[dict]:
    path = modules_root / module / "metadata" / "conversations.tsv"
    html_dir = artifacts_root / module / "html"
    pdf_dir = artifacts_root / module / "pdf"
    participants = read_participants(modules_root, module)
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            code = row["code"]
            if not (html_dir / f"{code}.html").is_file():
                continue
            conv = {"code": code}
            for column, key in FIELDS.items():
                value = (row.get(column) or "").strip()
                conv[key] = "" if value in MISSING else value
            # "type:subtype" -> two facets
            conv["type"], _, conv["subtype"] = conv["type"].partition(":")
            conv["pdf"] = [
                kind for kind in ("orthographic", "enriched")
                if (pdf_dir / f"{code}-{kind}.pdf").is_file()
            ]
            conv["people"] = participants.get(code, [])
            rows.append(conv)
    return sorted(rows, key=lambda c: c["code"])


PAGE = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KIParla Artifacts</title>
  <link rel="stylesheet" href="css/index.css">
</head>
<body>
  <main>
    <header>
      <h1>KIParla Artifacts</h1>
      <p>
        Indice delle trascrizioni HTML e dei PDF pubblicati per i moduli del corpus:
        __MODULE_CODES__.
        Usa i filtri per cercare le conversazioni in base ai metadati.
      </p>
    </header>

    <section class="filters" aria-labelledby="filters-title">
      <h2 id="filters-title">Filtri</h2>
      <form id="filters" autocomplete="off">
        <label class="filter">Codice
          <input type="search" id="f-code" placeholder="es. BOA10" aria-label="Cerca per codice">
        </label>
        <label class="filter">Modulo<select id="f-module"></select></label>
        <label class="filter">Tipo<select id="f-type"></select></label>
        <label class="filter">Sottotipo<select id="f-subtype"></select></label>
        <label class="filter">Anno<select id="f-year"></select></label>
        <label class="filter">Punto di raccolta<select id="f-point"></select></label>
        <label class="filter">Comunit&agrave;<select id="f-community"></select></label>
        <label class="filter">Lingue<select id="f-languages"></select></label>
        <label class="filter">Parlanti non identificati<select id="f-unknown"></select></label>
        <fieldset class="participant-filters">
          <legend>Partecipanti</legend>
          <p>Con pi&ugrave; filtri, almeno un partecipante deve soddisfarli tutti.</p>
          <label class="filter">Genere<select id="f-gender"></select></label>
          <label class="filter">Et&agrave;<select id="f-age"></select></label>
          <label class="filter">Regione<select id="f-region"></select></label>
          <label class="filter">Titolo di studio<select id="f-study"></select></label>
          <label class="filter">Occupazione<select id="f-occupation"></select></label>
          <label class="filter">L1<select id="f-l1"></select></label>
          <label class="filter">Lingua madre<select id="f-mothertongue"></select></label>
          <label class="filter">Et&agrave; di arrivo in Italia<select id="f-arrival"></select></label>
          <label class="filter">Anni in Italia<select id="f-years"></select></label>
        </fieldset>
        <button type="reset" id="reset">Azzera i filtri</button>
      </form>
      <p class="result" id="result" role="status" aria-live="polite"></p>
    </section>

    <section class="summary" aria-labelledby="summary-title">
      <h2 id="summary-title">Moduli</h2>
      <ul id="summary"></ul>
    </section>

    <section class="modules" id="modules"></section>

    <footer id="footer"></footer>
  </main>

  <script id="data" type="application/json">__DATA__</script>
  <script>
    (function () {
      var data = JSON.parse(document.getElementById("data").textContent);
      var modules = data.modules;
      var FACETS = [
        {id: "module", label: "tutti"}, {id: "type", label: "tutti"}, {id: "subtype", label: "tutti"},
        {id: "year", label: "tutti"}, {id: "point", label: "tutti"}, {id: "community", label: "tutte"},
        {id: "languages", label: "tutte"}, {id: "unknown", label: "tutti"}
      ];
      var PARTICIPANT_FACETS = [
        {id: "gender", label: "tutti"}, {id: "age", label: "tutte"}, {id: "region", label: "tutte"},
        {id: "study", label: "tutti"}, {id: "occupation", label: "tutte"}, {id: "l1", label: "tutte"},
        {id: "mothertongue", label: "tutte"}, {id: "arrival", label: "tutte"}, {id: "years", label: "tutti"}
      ];
      var UNKNOWN = {yes: "presenti", no: "assenti"};
      var all = [];
      modules.forEach(function (m) {
        m.conversations.forEach(function (c) {
          var item = Object.assign({module: m.name}, c);
          item.langList = c.languages ? c.languages.split(";") : [];
          all.push(item);
        });
      });

      function values(id) {
        var seen = {};
        all.forEach(function (c) {
          var v = id === "languages" ? c.langList : [c[id]];
          v.forEach(function (x) { if (x) seen[x] = true; });
        });
        return Object.keys(seen).sort();
      }

      function participantValues(id) {
        var seen = {};
        all.forEach(function (c) {
          c.people.forEach(function (p) { (p[id] || []).forEach(function (x) { seen[x] = true; }); });
        });
        return Object.keys(seen).sort(function (a, b) { return a.localeCompare(b, "it", {numeric: true}); });
      }

      PARTICIPANT_FACETS.forEach(function (f) {
        var sel = document.getElementById("f-" + f.id);
        var opts = participantValues(f.id);
        if (opts.length === 0) { sel.parentNode.hidden = true; return; }
        sel.appendChild(new Option(f.label, ""));
        opts.forEach(function (v) { sel.appendChild(new Option(v, v)); });
      });

      FACETS.forEach(function (f) {
        var sel = document.getElementById("f-" + f.id);
        var opts = values(f.id);
        // facets that carry no information in the data are hidden
        if (f.id !== "module" && opts.length === 0) { sel.parentNode.hidden = true; return; }
        sel.appendChild(new Option(f.label, ""));
        opts.forEach(function (v) {
          sel.appendChild(new Option(f.id === "unknown" ? UNKNOWN[v] || v : v, v));
        });
      });

      var form = document.getElementById("filters");
      var codeInput = document.getElementById("f-code");

      function state() {
        var s = {code: codeInput.value.trim().toLowerCase()};
        FACETS.concat(PARTICIPANT_FACETS).forEach(function (f) { s[f.id] = document.getElementById("f-" + f.id).value; });
        return s;
      }

      function matches(c, s) {
        if (s.code && c.code.toLowerCase().indexOf(s.code) === -1) return false;
        for (var i = 0; i < FACETS.length; i++) {
          var id = FACETS[i].id;
          if (!s[id]) continue;
          if (id === "languages") { if (c.langList.indexOf(s[id]) === -1) return false; }
          else if (c[id] !== s[id]) return false;
        }
        var wanted = PARTICIPANT_FACETS.filter(function (f) { return s[f.id]; });
        // one same participant has to match every participant filter
        return !wanted.length || c.people.some(function (p) {
          return wanted.every(function (f) { return (p[f.id] || []).indexOf(s[f.id]) !== -1; });
        });
      }

      function link(href, text) {
        var a = document.createElement("a");
        a.href = href; a.textContent = text;
        return a;
      }

      function render() {
        var s = state();
        var shown = 0;
        var summary = document.getElementById("summary");
        var root = document.getElementById("modules");
        summary.textContent = ""; root.textContent = "";
        modules.forEach(function (m) {
          var list = all.filter(function (c) { return c.module === m.name && matches(c, s); });
          shown += list.length;
          if (!list.length) return;
          var id = m.name.toLowerCase();
          var li = document.createElement("li");
          var a = link("#" + id, m.name + " ");
          var n = document.createElement("span");
          n.className = "count"; n.textContent = list.length + (list.length === 1 ? " conversazione" : " conversazioni");
          a.appendChild(n); li.appendChild(a); summary.appendChild(li);

          var art = document.createElement("article");
          art.className = "module"; art.id = id;
          var hd = document.createElement("header");
          var h2 = document.createElement("h2"); h2.textContent = m.name;
          var cnt = document.createElement("span"); cnt.className = "count"; cnt.textContent = n.textContent;
          hd.appendChild(h2); hd.appendChild(cnt); art.appendChild(hd);
          var ol = document.createElement("ol"); ol.className = "conversation-list";
          list.forEach(function (c) {
            var item = document.createElement("li");
            item.appendChild(link(m.name + "/html/" + c.code + ".html", c.code));
            var meta = [c.type, c.subtype, c.year, c.point, c.community].filter(Boolean).join(" · ");
            if (meta) {
              var small = document.createElement("span"); small.className = "meta"; small.textContent = meta;
              item.appendChild(small);
            }
            c.pdf.forEach(function (kind) {
              item.appendChild(document.createTextNode(" "));
              var p = link(m.name + "/pdf/" + c.code + "-" + kind + ".pdf", kind === "orthographic" ? "PDF ort." : "PDF enr.");
              p.className = "pdf"; item.appendChild(p);
            });
            ol.appendChild(item);
          });
          art.appendChild(ol); root.appendChild(art);
        });
        var res = document.getElementById("result");
        res.textContent = shown === all.length
          ? all.length + " conversazioni"
          : shown + " conversazioni su " + all.length;
        if (!shown) {
          var empty = document.createElement("p");
          empty.className = "empty"; empty.textContent = "Nessuna conversazione corrisponde ai filtri.";
          root.appendChild(empty);
        }
        document.getElementById("footer").textContent =
          "Totale conversazioni HTML pubblicate: " + all.length + " (le conversazioni condivise tra due moduli compaiono in entrambi).";
      }

      form.addEventListener("input", render);
      form.addEventListener("change", render);
      form.addEventListener("reset", function () { setTimeout(render, 0); });
      form.addEventListener("submit", function (e) { e.preventDefault(); });
      render();
    })();
  </script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules-root", required=True, type=Path,
                    help="Directory containing the module repositories (KIP, ParlaBO, ...)")
    ap.add_argument("--artifacts-root", required=True, type=Path,
                    help="Root of KIParla-artifacts")
    args = ap.parse_args()

    modules = []
    for name in MODULES:
        convs = read_module(args.modules_root, args.artifacts_root, name)
        modules.append({"name": name, "conversations": convs})
        print(f"{name}: {len(convs)} conversations")

    codes = [f"<code>{escape(m['name'])}</code>" for m in modules]
    module_codes = ", ".join(codes[:-1]) + " e " + codes[-1]
    # "</" must not appear in the inline JSON
    payload = json.dumps({"modules": modules}, ensure_ascii=False,
                         separators=(",", ":")).replace("</", "<\\/")
    page = PAGE.replace("__MODULE_CODES__", module_codes).replace("__DATA__", payload)
    out = args.artifacts_root / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
