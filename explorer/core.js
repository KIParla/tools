/* KIParla explorer — filtering and statistics (no DOM). Tested under Node
 * (tests/explorer_core.test.js); inlined into the page by build_explorer.py. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.KiparlaCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var UNKNOWN = "unknown";

  /* ---------------------------------------------------------------- state */

  function emptyState() {
    return { q: "", cat: {}, spk: {}, spkMode: "any", num: {}, exclude: [] };
  }

  function isActive(state) {
    return !!(state.q || (state.exclude && state.exclude.length) ||
      Object.keys(state.cat).some(function (k) { return state.cat[k].length; }) ||
      Object.keys(state.spk).some(function (k) { return state.spk[k].length; }) ||
      Object.keys(state.num).length);
  }

  /* ------------------------------------------------------------- indexing */

  function splitMulti(v) {
    return String(v).split(";").map(function (x) { return x.trim(); })
      .filter(function (x) { return x && x !== "_" && x !== "N/A"; });
  }

  function valuesOf(record, facet) {
    var v = facet.id === "module" ? record.modules : record[facet.id];
    if (v === null || v === undefined || v === "") return [UNKNOWN];
    if (Array.isArray(v)) return v.length ? v : [UNKNOWN];
    if (facet.multi) {
      var parts = splitMulti(v);
      return parts.length ? parts : [UNKNOWN];
    }
    return [String(v)];
  }

  function buildIndex(data) {
    var convFacets = [{ id: "module", label: "Module", multi: true }]
      .concat(data.conversation_facets.filter(function (f) { return f.id !== "module"; }));
    var spkByConv = {};
    data.speakers.forEach(function (s) {
      if (!s.known) return;
      (spkByConv[s.conv] = spkByConv[s.conv] || []).push(s);
    });
    var domains = {};
    data.metrics.forEach(function (m) {
      var vals = data.conversations.map(function (c) { return c[m.id]; })
        .filter(function (v) { return v !== null && v !== undefined; });
      domains[m.id] = [Math.min.apply(null, vals), Math.max.apply(null, vals)];
    });
    return {
      data: data, convFacets: convFacets, spkFacets: data.speaker_facets,
      metrics: data.metrics, spkByConv: spkByConv, domains: domains,
    };
  }

  /* ------------------------------------------------------------ filtering */

  function matchCat(ix, c, state, skip) {
    return ix.convFacets.every(function (f) {
      var want = state.cat[f.id];
      if (!want || !want.length || skip === "cat:" + f.id) return true;
      var have = valuesOf(c, f);
      return want.some(function (w) { return have.indexOf(w) >= 0; });
    });
  }

  function matchSpk(ix, c, state, skip) {
    var spk = ix.spkByConv[c.code] || [];
    return ix.spkFacets.every(function (f) {
      var want = state.spk[f.id];
      if (!want || !want.length || skip === "spk:" + f.id) return true;
      var test = function (s) {
        var have = valuesOf(s, f);
        return want.some(function (w) { return have.indexOf(w) >= 0; });
      };
      if (!spk.length) return false;
      return state.spkMode === "all" ? spk.every(test) : spk.some(test);
    });
  }

  function matchNum(ix, c, state, skip) {
    return ix.metrics.every(function (m) {
      var r = state.num[m.id];
      if (!r || skip === "num:" + m.id) return true;
      var v = c[m.id];
      if (v === null || v === undefined) return false;
      return (r[0] === null || v >= r[0]) && (r[1] === null || v <= r[1]);
    });
  }

  function matchQuery(ix, c, state) {
    var q = (state.q || "").trim().toLowerCase();
    if (!q) return true;
    var hay = [c.code].concat((ix.spkByConv[c.code] || []).map(function (s) { return s.spk; }))
      .join(" ").toLowerCase();
    return q.split(/\s+/).every(function (t) { return hay.indexOf(t) >= 0; });
  }

  /* `skip` leaves one facet/metric out, to count what its options would give. */
  function matches(ix, c, state, skip) {
    return matchCat(ix, c, state, skip) && matchSpk(ix, c, state, skip) &&
      matchNum(ix, c, state, skip) && matchQuery(ix, c, state);
  }

  /* The selection: conversations passing every filter, minus the ones
   * removed by hand (state.exclude). */
  function filter(ix, state) {
    var out = state.exclude || [];
    return ix.data.conversations.filter(function (c) {
      return out.indexOf(c.code) < 0 && matches(ix, c, state);
    });
  }

  /* Options of a facet with how many conversations each would select, given
   * every other filter. kind: "cat" (conversation) or "spk" (speaker). */
  function facetOptions(ix, state, kind, facetId) {
    var facet = (kind === "cat" ? ix.convFacets : ix.spkFacets)
      .filter(function (f) { return f.id === facetId; })[0];
    var counts = {};
    ix.data.conversations.forEach(function (c) {
      if (!matches(ix, c, state, kind + ":" + facetId)) return;
      var seen = {};
      if (kind === "cat") {
        valuesOf(c, facet).forEach(function (v) { seen[v] = true; });
      } else {
        (ix.spkByConv[c.code] || []).forEach(function (s) {
          valuesOf(s, facet).forEach(function (v) { seen[v] = true; });
        });
      }
      Object.keys(seen).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
    });
    var all = {};
    (kind === "cat" ? ix.data.conversations : ix.data.speakers).forEach(function (r) {
      if (kind === "spk" && !r.known) return;
      valuesOf(r, facet).forEach(function (v) { all[v] = true; });
    });
    (state[kind][facetId] || []).forEach(function (v) { all[v] = true; });
    return Object.keys(all).sort(naturalOrder).map(function (v) {
      return { value: v, count: counts[v] || 0 };
    });
  }

  function naturalOrder(a, b) {
    if (a === UNKNOWN) return 1;
    if (b === UNKNOWN) return -1;
    return a.localeCompare(b, "en", { numeric: true });
  }

  /* ---------------------------------------------------------- statistics */

  function sum(arr, f) {
    return arr.reduce(function (t, x) { var v = f(x); return t + (v || 0); }, 0);
  }

  function median(values) {
    if (!values.length) return null;
    var s = values.slice().sort(function (a, b) { return a - b; });
    var m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  function describe(values) {
    var v = values.filter(function (x) { return x !== null && x !== undefined; });
    if (!v.length) return { n: 0, min: null, median: null, mean: null, max: null };
    return {
      n: v.length, min: Math.min.apply(null, v), median: median(v),
      mean: v.reduce(function (a, b) { return a + b; }, 0) / v.length,
      max: Math.max.apply(null, v),
    };
  }

  /* Figures of a selection. Shares are pooled (total overlap time over total
   * speech time, etc.), not averages of per-conversation percentages. */
  function summarise(ix, convs) {
    var speechH = sum(convs, function (c) { return c.speech_hours; });
    var ling = sum(convs, function (c) { return c.ling; });
    var overlapH = sum(convs, function (c) { return c.speech_hours * (c.overlap_pct || 0) / 100; });
    var annTok = sum(convs, function (c) { return c.ling * (c.ann_overlap_pct || 0) / 100; });
    var codes = {};
    convs.forEach(function (c) { codes[c.code] = true; });
    var people = {};
    ix.data.speakers.forEach(function (s) {
      if (s.known && codes[s.conv]) people[s.spk] = true;
    });
    var perMetric = {};
    ix.metrics.forEach(function (m) {
      perMetric[m.id] = describe(convs.map(function (c) { return c[m.id]; }));
    });
    return {
      conversations: convs.length,
      speakers: Object.keys(people).length,
      hours: sum(convs, function (c) { return c.hours; }),
      speechHours: speechH,
      tokens: sum(convs, function (c) { return c.tokens; }),
      ling: ling,
      units: sum(convs, function (c) { return c.units; }),
      overlapShare: speechH ? overlapH / speechH : null,
      annOverlapShare: ling ? annTok / ling : null,
      rate: speechH ? ling / (speechH * 3600) : null,
      perMetric: perMetric,
    };
  }

  /* Counts of conversations (kind "cat") or distinct speakers (kind "spk")
   * per value of a facet, within a selection. */
  function breakdown(ix, convs, kind, facetId) {
    var facet = (kind === "cat" ? ix.convFacets : ix.spkFacets)
      .filter(function (f) { return f.id === facetId; })[0];
    var counts = {};
    if (kind === "cat") {
      convs.forEach(function (c) {
        valuesOf(c, facet).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
      });
    } else {
      var codes = {}, seen = {};
      convs.forEach(function (c) { codes[c.code] = true; });
      ix.data.speakers.forEach(function (s) {
        if (!s.known || !codes[s.conv] || seen[s.spk]) return;
        seen[s.spk] = true;
        valuesOf(s, facet).forEach(function (v) { counts[v] = (counts[v] || 0) + 1; });
      });
    }
    return Object.keys(counts).sort(naturalOrder).map(function (v) {
      return { value: v, count: counts[v] };
    });
  }

  function histogram(values, lo, hi, bins) {
    var out = [];
    for (var i = 0; i < bins; i++) out.push(0);
    var span = hi - lo;
    values.forEach(function (v) {
      if (v === null || v === undefined) return;
      var k = span > 0 ? Math.floor((v - lo) / span * bins) : 0;
      out[Math.min(bins - 1, Math.max(0, k))]++;
    });
    return out;
  }

  /* --------------------------------------------------------------- export */

  function csvCell(v) {
    if (v === null || v === undefined) return "";
    var s = Array.isArray(v) ? v.join(";") : String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function toCSV(convs, columns) {
    var lines = [columns.map(csvCell).join(",")];
    convs.forEach(function (c) {
      lines.push(columns.map(function (k) { return csvCell(c[k]); }).join(","));
    });
    return lines.join("\n") + "\n";
  }

  /* Where each format lives. {m} module, {c} code. `repo` says which checkout. */
  var FORMATS = [
    { id: "tsv", label: "Vertical (.vert.tsv)", repo: "module", path: "{m}/tsv/{c}.vert.tsv" },
    { id: "eaf", label: "ELAN (.eaf)", repo: "module", path: "{m}/eaf/{c}.eaf" },
    { id: "orthographic", label: "Linear, orthographic", repo: "module", path: "{m}/linear-orthographic/{c}.txt" },
    { id: "jefferson", label: "Linear, Jefferson", repo: "module", path: "{m}/linear-jefferson/{c}.txt" },
    { id: "summary", label: "Summary (.json)", repo: "summaries", path: "{m}/{c}.json" },
    { id: "html", label: "HTML page", repo: "artifacts", path: "{m}/html/{c}.html" },
    { id: "pdf", label: "PDF (both)", repo: "artifacts", path: "{m}/pdf/{c}-orthographic.pdf", extra: ["{m}/pdf/{c}-jefferson.pdf"] },
  ];

  function shQuote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }

  /* A bash script copying the chosen formats of the selected conversations
   * from local checkouts into one folder. */
  function copyScript(convs, formatIds, info) {
    var chosen = FORMATS.filter(function (f) { return formatIds.indexOf(f.id) >= 0; });
    var out = ["#!/usr/bin/env bash",
      "# " + convs.length + " conversations selected in the KIParla explorer.",
      "# Set KIPARLA to the folder holding the module checkouts (KIP, ParlaBO, ...),",
      "# ARTIFACTS to your KIParla-artifacts checkout and SUMMARIES to your",
      "# KIParla-summaries checkout, then run it.",
      "set -euo pipefail",
      'KIPARLA="${KIPARLA:-.}"', 'ARTIFACTS="${ARTIFACTS:-./KIParla-artifacts}"',
      'SUMMARIES="${SUMMARIES:-./KIParla-summaries}"',
      'OUT="${OUT:-subcorpus}"', ""];
    if (info && info.note) out.splice(2, 0, "# " + info.note);
    chosen.forEach(function (f) { out.push('mkdir -p "$OUT/' + f.id + '"'); });
    out.push("");
    convs.forEach(function (c) {
      var m = c.modules[0];
      chosen.forEach(function (f) {
        [f.path].concat(f.extra || []).forEach(function (tpl) {
          var rel = tpl.replace("{m}", m).replace("{c}", c.code);
          var base = f.repo === "module" ? "$KIPARLA" : f.repo === "summaries" ? "$SUMMARIES" : "$ARTIFACTS";
          out.push('cp "' + base + "/" + rel + '" "$OUT/' + f.id + '/"');
        });
      });
    });
    return out.join("\n") + "\n";
  }

  function codeList(convs) {
    return convs.map(function (c) { return c.code; }).join("\n") + "\n";
  }

  /* -------------------------------------------------------- shareable URL */

  function encodeState(state) {
    var slim = {};
    if (state.q) slim.q = state.q;
    ["cat", "spk"].forEach(function (k) {
      var o = {};
      Object.keys(state[k]).forEach(function (f) { if (state[k][f].length) o[f] = state[k][f]; });
      if (Object.keys(o).length) slim[k] = o;
    });
    if (Object.keys(state.num).length) slim.num = state.num;
    if (state.spkMode !== "any") slim.mode = state.spkMode;
    if (state.exclude && state.exclude.length) slim.x = state.exclude;
    var json = JSON.stringify(slim);
    if (json === "{}") return "";
    var bytes = unescape(encodeURIComponent(json));
    var b64 = (typeof btoa === "function" ? btoa(bytes) : Buffer.from(bytes, "binary").toString("base64"));
    return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function decodeState(text) {
    var state = emptyState();
    if (!text) return state;
    try {
      var b64 = text.replace(/-/g, "+").replace(/_/g, "/");
      while (b64.length % 4) b64 += "=";
      var bytes = typeof atob === "function" ? atob(b64) : Buffer.from(b64, "base64").toString("binary");
      var slim = JSON.parse(decodeURIComponent(escape(bytes)));
      state.q = typeof slim.q === "string" ? slim.q : "";
      state.cat = slim.cat || {};
      state.spk = slim.spk || {};
      state.num = slim.num || {};
      state.spkMode = slim.mode === "all" ? "all" : "any";
      state.exclude = Array.isArray(slim.x) ? slim.x : [];
    } catch (e) { return emptyState(); }
    return state;
  }

  return {
    UNKNOWN: UNKNOWN, FORMATS: FORMATS, emptyState: emptyState, isActive: isActive,
    buildIndex: buildIndex, valuesOf: valuesOf, filter: filter, matches: matches,
    facetOptions: facetOptions, summarise: summarise, breakdown: breakdown,
    histogram: histogram, describe: describe, toCSV: toCSV, copyScript: copyScript,
    codeList: codeList, encodeState: encodeState, decodeState: decodeState,
    naturalOrder: naturalOrder,
  };
});
