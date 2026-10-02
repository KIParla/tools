/* KIParla corpus explorer: interface. Needs core.js (KiparlaCore) and data.js (KIPARLA_DATA). */
(function () {
"use strict";
var DATA = window.KIPARLA_DATA;
var LINKS = DATA.links || { artifacts: "", search: "" };
var K = KiparlaCore;
var ix = K.buildIndex(DATA);
var UNKNOWN = K.UNKNOWN;
var state = K.emptyState();
var tab = "overview";
var ui = { dist: "overlap_pct", sx: "overlap_pct", sy: "rate", color: "type", sort: "code", dir: 1,
           formats: { tsv: 1, orthographic: 1, jefferson: 1 }, spkTop: {} };
var metricById = {};
DATA.metrics.forEach(function (m) { metricById[m.id] = m; });
var corpus = K.summarise(ix, DATA.conversations);
var sel = [], sum = null;

/* ------------------------------------------------------------- helpers */
function $(s, r) { return (r || document).querySelector(s); }
function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
function nf(v, d) { return v === null || v === undefined || isNaN(v) ? "–" : Number(v).toLocaleString("en", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
function withUnit(m, v) {
  if (v === null || v === undefined) return "–";
  var s = nf(v, m.decimals);
  return m.unit === "%" ? s + "%" : m.unit ? s + " " + m.unit : s;
}
function pct(x, d) { return x === null || x === undefined ? "–" : nf(100 * x, d === undefined ? 1 : d) + "%"; }
function pretty(facetId, v) {
  if (v === UNKNOWN) return facetId === "subtype" ? "none" : "unknown";
  if (facetId === "occupation") v = v.replace(/^\d-/, "");
  if (facetId === "region") return v.split("-").map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); }).join("-");
  if (facetId === "point" || facetId === "module" || facetId === "year" || facetId === "age") return v;
  v = v.replace(/-/g, " ");
  return v.charAt(0).toUpperCase() + v.slice(1);
}
function facetLabel(kind, id) {
  var list = kind === "cat" ? ix.convFacets : ix.spkFacets;
  for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].label;
  return id;
}
function htmlLink(c) { return LINKS.artifacts + c.modules[0] + "/html/" + c.code + ".html"; }
function download(name, text, mime) {
  var a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: mime || "text/plain" }));
  a.download = name; document.body.appendChild(a); a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
function copyText(text, done) {
  function fallback() {
    var t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); done(true); } catch (e) { done(false); }
    t.remove();
  }
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
  else fallback();
}
function niceTicks(lo, hi, n) {
  var span = hi - lo || 1, step = Math.pow(10, Math.floor(Math.log10(span / n)));
  var err = (span / n) / step;
  step *= err >= 7 ? 10 : err >= 3 ? 5 : err >= 1.5 ? 2 : 1;
  var out = [], t = Math.ceil(lo / step) * step;
  for (; t <= hi + step * 1e-9; t += step) out.push(+t.toFixed(10));
  return out;
}

/* ------------------------------------------------------------- sidebar */
function buildSidebar() {
  var h = '<div class="search"><label class="sr" for="q">Search by conversation or speaker code</label><input id="q" type="search" placeholder="Conversation or speaker code" autocomplete="off"></div>';
  h += '<div class="group">Conversation</div>';
  ix.convFacets.forEach(function (f, i) { h += facetBlock("cat", f, i < 3); });
  h += '<div class="group">Speakers</div>';
  h += '<div class="seg" role="group" aria-label="Speaker matching"><button type="button" data-mode="any" aria-pressed="true" title="The conversation has at least one speaker with the chosen values">at least one speaker</button><button type="button" data-mode="all" aria-pressed="false" title="Every identified speaker of the conversation has the chosen values">every speaker</button></div>';
  ix.spkFacets.forEach(function (f, i) { h += facetBlock("spk", f, i === 0); });
  h += '<div class="group">Measured features</div>';
  var open = { overlap_pct: 1, ann_overlap_pct: 1, rate: 1 };
  DATA.metrics.forEach(function (m) { h += rangeBlock(m, open[m.id]); });
  $("#fbody").innerHTML = h;
  updateSidebar();
}
function facetBlock(kind, f, open) {
  var opts = K.facetOptions(ix, K.emptyState(), kind, f.id);
  var h = '<details class="fs"' + (open ? " open" : "") + ' data-fs="' + kind + ":" + f.id + '"><summary><span class="t">' + esc(f.label) + '</span><span class="badge" hidden></span></summary><div class="opts">';
  opts.forEach(function (o) {
    h += '<label class="opt"><input type="checkbox" data-kind="' + kind + '" data-facet="' + f.id + '" value="' + esc(o.value) + '"><span class="v">' + esc(pretty(f.id, o.value)) + '</span><span class="n"></span></label>';
  });
  return h + "</div></details>";
}
function rangeBlock(m, open) {
  return '<details class="rg"' + (open ? " open" : "") + ' data-metric="' + m.id + '"><summary><span class="t">' + esc(m.label) + (m.unit ? ' <span class="muted">(' + esc(m.unit) + ")</span>" : "") + '</span><span class="badge" hidden></span></summary><div class="rbody"><p class="desc">' + esc(m.description) + '</p><div class="hist"><svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"></svg><div class="slider"><input type="range" class="lo" min="0" max="1000" step="1" value="0" aria-label="' + esc(m.label) + ' minimum"><input type="range" class="hi" min="0" max="1000" step="1" value="1000" aria-label="' + esc(m.label) + ' maximum"></div></div><div class="nums"><input type="number" class="nmin" step="any" aria-label="' + esc(m.label) + ' from"><span class="u">to</span><input type="number" class="nmax" step="any" aria-label="' + esc(m.label) + ' to"></div></div></details>';
}
var BINS = 32, allHist = {};
DATA.metrics.forEach(function (m) {
  var d = ix.domains[m.id];
  allHist[m.id] = K.histogram(DATA.conversations.map(function (c) { return c[m.id]; }), d[0], d[1], BINS);
});
function t2v(m, t) { var d = ix.domains[m.id]; return d[0] + (d[1] - d[0]) * t / 1000; }
function v2t(m, v) { var d = ix.domains[m.id]; return d[1] > d[0] ? Math.round((v - d[0]) / (d[1] - d[0]) * 1000) : 0; }
function roundTo(m, v, up) {
  var f = Math.pow(10, m.decimals + (m.decimals ? 1 : 0));
  return (up ? Math.ceil(v * f - 1e-9) : Math.floor(v * f + 1e-9)) / f;
}
function updateSidebar() {
  $$("details.fs").forEach(function (d) {
    var p = d.getAttribute("data-fs").split(":"), kind = p[0], id = p[1];
    var opts = K.facetOptions(ix, state, kind, id), chosen = state[kind][id] || [];
    var byVal = {}; opts.forEach(function (o) { byVal[o.value] = o.count; });
    $$("label.opt", d).forEach(function (l) {
      var inp = $("input", l), v = inp.value, n = byVal[v] || 0;
      inp.checked = chosen.indexOf(v) >= 0;
      $(".n", l).textContent = n;
      l.classList.toggle("zero", n === 0);
    });
    var b = $(".badge", d); b.hidden = !chosen.length; b.textContent = chosen.length;
  });
  $$("button[data-mode]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-mode") === state.spkMode)); });
  var q = $("#q"); if (document.activeElement !== q) q.value = state.q;
  $$("details.rg").forEach(function (d) {
    var m = metricById[d.getAttribute("data-metric")], r = state.num[m.id], dom = ix.domains[m.id];
    var lo = $(".lo", d), hi = $(".hi", d), nmin = $(".nmin", d), nmax = $(".nmax", d);
    var tl = r && r[0] !== null ? v2t(m, r[0]) : 0, th = r && r[1] !== null ? v2t(m, r[1]) : 1000;
    if (document.activeElement !== lo) lo.value = tl;
    if (document.activeElement !== hi) hi.value = th;
    if (document.activeElement !== nmin) nmin.value = r && r[0] !== null ? r[0] : "";
    if (document.activeElement !== nmax) nmax.value = r && r[1] !== null ? r[1] : "";
    nmin.placeholder = nf(dom[0], m.decimals); nmax.placeholder = nf(dom[1], m.decimals);
    var b = $(".badge", d); b.hidden = !r; b.textContent = r ? "on" : "";
    var selH = K.histogram(sel.map(function (c) { return c[m.id]; }), dom[0], dom[1], BINS), all = allHist[m.id];
    var mx = Math.max.apply(null, all) || 1, bars = "";
    for (var i = 0; i < BINS; i++) {
      var w = 100 / BINS, ha = 38 * all[i] / mx, hs = 38 * selH[i] / mx;
      bars += '<rect x="' + (i * w + 0.3) + '" y="' + (39 - ha) + '" width="' + (w - 0.6) + '" height="' + ha + '" fill="var(--ghost)"/>';
      if (selH[i]) bars += '<rect x="' + (i * w + 0.3) + '" y="' + (39 - hs) + '" width="' + (w - 0.6) + '" height="' + hs + '" fill="var(--accent)"/>';
    }
    $("svg", d).innerHTML = bars + '<line x1="0" x2="100" y1="39.5" y2="39.5" stroke="var(--line)" stroke-width=".6" vector-effect="non-scaling-stroke"/>';
  });
}
function setRange(m, lo, hi) {
  var dom = ix.domains[m.id];
  lo = lo === null || lo <= dom[0] ? null : lo;
  hi = hi === null || hi >= dom[1] ? null : hi;
  if (lo === null && hi === null) delete state.num[m.id]; else state.num[m.id] = [lo, hi];
}
function onSidebar(e) {
  var t = e.target;
  if (t.matches("input[type=checkbox]")) {
    var k = t.getAttribute("data-kind"), f = t.getAttribute("data-facet"), arr = state[k][f] = state[k][f] || [];
    var i = arr.indexOf(t.value);
    if (t.checked && i < 0) arr.push(t.value); else if (!t.checked && i >= 0) arr.splice(i, 1);
    if (!arr.length) delete state[k][f];
    update();
  } else if (t.matches("input[type=range]")) {
    var d = t.closest("details.rg"), m = metricById[d.getAttribute("data-metric")];
    var lo = +$(".lo", d).value, hi = +$(".hi", d).value;
    if (t.classList.contains("lo") && lo > hi) { lo = hi; $(".lo", d).value = lo; }
    if (t.classList.contains("hi") && hi < lo) { hi = lo; $(".hi", d).value = hi; }
    setRange(m, lo <= 0 ? null : roundTo(m, t2v(m, lo), false), hi >= 1000 ? null : roundTo(m, t2v(m, hi), true));
    update();
  } else if (t.matches("input[type=number]")) {
    var d2 = t.closest("details.rg"), m2 = metricById[d2.getAttribute("data-metric")];
    var a = $(".nmin", d2).value, b = $(".nmax", d2).value;
    setRange(m2, a === "" ? null : +a, b === "" ? null : +b);
    update();
  } else if (t.id === "q") {
    state.q = t.value; update();
  }
}

/* ---------------------------------------------------------- selection bar */
function renderSelBar() {
  var s = sum;
  $("#selbar").innerHTML =
    '<div><span class="big">' + nf(s.conversations) + '</span> <span class="of">of ' + nf(DATA.conversations.length) + ' conversations</span></div>' +
    fig("Duration", nf(s.hours, 1) + " h") + fig("Tokens", nf(s.tokens)) + fig("Speakers", nf(s.speakers)) +
    fig("Overlap", pct(s.overlapShare)) + fig("Speech rate", s.rate === null ? "–" : nf(s.rate, 2) + " tok/s") +
    '<div class="actions"><button class="btn" type="button" id="copylink">Copy link</button><button class="btn primary" type="button" id="goexport">Export</button></div>';
}
function fig(l, v) { return '<div class="fig"><span>' + l + "</span><span>" + v + "</span></div>"; }
function renderChips() {
  var out = [];
  if (state.q) out.push(chip("Search: " + state.q, 'data-rm="q"'));
  ["cat", "spk"].forEach(function (k) {
    Object.keys(state[k]).forEach(function (f) {
      state[k][f].forEach(function (v) {
        out.push(chip((k === "spk" ? "Speaker " + facetLabel(k, f).toLowerCase() : facetLabel(k, f)) + ": " + pretty(f, v), 'data-rm="' + k + ":" + f + ":" + esc(v) + '"'));
      });
    });
  });
  Object.keys(state.num).forEach(function (id) {
    var m = metricById[id], r = state.num[id];
    var txt = r[0] !== null && r[1] !== null ? withUnit(m, r[0]).replace(/ ?%$/, "") + " – " + withUnit(m, r[1]) : r[0] !== null ? "≥ " + withUnit(m, r[0]) : "≤ " + withUnit(m, r[1]);
    out.push(chip(m.label + ": " + txt, 'data-rm="num:' + id + '"'));
  });
  if (state.exclude.length) out.push(chip(state.exclude.length + " removed by hand", 'data-rm="exclude"'));
  $("#chips").innerHTML = out.join("");
}
function chip(text, attr) { return '<span class="chip">' + esc(text) + '<button type="button" aria-label="Remove filter" ' + attr + ">×</button></span>"; }
function onChips(e) {
  var b = e.target.closest("button[data-rm]"); if (!b) return;
  var p = b.getAttribute("data-rm").split(":");
  if (p[0] === "q") state.q = "";
  else if (p[0] === "exclude") state.exclude = [];
  else if (p[0] === "num") delete state.num[p[1]];
  else { var v = p.slice(2).join(":"), arr = state[p[0]][p[1]]; arr.splice(arr.indexOf(v), 1); if (!arr.length) delete state[p[0]][p[1]]; }
  update();
}

/* ------------------------------------------------------------- overview */
function bars(kind, id) {
  var rows = K.breakdown(ix, sel, kind, id), all = K.breakdown(ix, DATA.conversations, kind, id);
  var selBy = {}; rows.forEach(function (r) { selBy[r.value] = r.count; });
  var mx = Math.max.apply(null, all.map(function (r) { return r.count; })) || 1;
  var show = ui.spkTop[kind + id] ? all.length : 10;
  var h = '<div class="bars"><h4>' + esc(facetLabel(kind, id)) + "</h4>";
  all.slice().sort(function (a, b) { return /year|age|occupation|study/.test(id) ? K.naturalOrder(a.value, b.value) : b.count - a.count; }).slice(0, show).forEach(function (r) {
    var n = selBy[r.value] || 0;
    h += '<div class="bar"><span class="lab" title="' + esc(pretty(id, r.value)) + '">' + esc(pretty(id, r.value)) + '</span><span class="track"><span class="ghost" style="width:' + (100 * r.count / mx) + '%"></span><span class="fill" style="width:' + (100 * n / mx) + '%"></span></span><span class="cnt">' + nf(n) + " / " + nf(r.count) + "</span></div>";
  });
  if (all.length > show) h += '<button class="btn" type="button" data-more="' + kind + id + '">Show all ' + all.length + "</button>";
  return h + "</div>";
}
function peopleBars(id) {
  var rows = K.breakdown(ix, sel, "spk", id), all = allSpk[id];
  var selBy = {}; rows.forEach(function (r) { selBy[r.value] = r.count; });
  var mx = Math.max.apply(null, all.map(function (r) { return r.count; })) || 1;
  var show = ui.spkTop["spk" + id] ? all.length : 8;
  var h = '<div class="bars"><h4>' + esc(facetLabel("spk", id)) + "</h4>";
  all.slice().sort(function (a, b) { return /age|occupation|study/.test(id) ? K.naturalOrder(a.value, b.value) : b.count - a.count; }).slice(0, show).forEach(function (r) {
    var n = selBy[r.value] || 0;
    h += '<div class="bar"><span class="lab" title="' + esc(pretty(id, r.value)) + '">' + esc(pretty(id, r.value)) + '</span><span class="track"><span class="ghost" style="width:' + (100 * r.count / mx) + '%"></span><span class="fill" style="width:' + (100 * n / mx) + '%"></span></span><span class="cnt">' + nf(n) + " / " + nf(r.count) + "</span></div>";
  });
  if (all.length > show) h += '<button class="btn" type="button" data-more="spk' + id + '">Show all ' + all.length + "</button>";
  return h + "</div>";
}
var allSpk = {};
ix.spkFacets.forEach(function (f) { allSpk[f.id] = K.breakdown(ix, DATA.conversations, "spk", f.id); });

function renderOverview() {
  var s = sum, c = corpus, h = "<h2>This selection</h2>";
  if (!sel.length) return h + '<p class="empty">No conversation matches these filters. Remove one of the chips above to widen the selection.</p>';
  function row(label, a, b) { return "<tr><td>" + label + '</td><td class="r">' + a + '</td><td class="r delta">' + b + "</td></tr>"; }
  h += '<div class="scroll"><table class="fig-table"><thead><tr><th>Totals</th><th class="r">Selection</th><th class="r">Whole corpus</th></tr></thead><tbody>' +
    row("Conversations", nf(s.conversations), nf(c.conversations)) +
    row("Duration", nf(s.hours, 1) + " h", nf(c.hours, 1) + " h") +
    row("Speech time", nf(s.speechHours, 1) + " h", nf(c.speechHours, 1) + " h") +
    row("Tokens", nf(s.tokens), nf(c.tokens)) +
    row("Linguistic tokens", nf(s.ling), nf(c.ling)) +
    row("Speakers (identified)", nf(s.speakers), nf(c.speakers)) +
    row("Overlap, share of speech time", pct(s.overlapShare), pct(c.overlapShare)) +
    row("Overlap, share of linguistic tokens", pct(s.annOverlapShare), pct(c.annOverlapShare)) +
    row("Speech rate (linguistic tokens per second)", s.rate === null ? "–" : nf(s.rate, 2), nf(c.rate, 2)) +
    '</tbody></table></div><p class="note">Shares are pooled: total overlap time over total speech time, not an average of per-conversation percentages.</p>';

  h += "<h3>What it is made of</h3><p class=\"note\">Solid bar: conversations in the selection. Full length: all conversations in the corpus.</p><div class=\"cols\">";
  ["module", "type", "year", "point", "relationship", "languages"].forEach(function (id) { h += bars("cat", id); });
  h += "</div><h3>Who speaks</h3><p class=\"note\">Distinct identified speakers: in the selection / in the corpus.</p><div class=\"cols\">";
  ix.spkFacets.forEach(function (f) { h += peopleBars(f.id); });
  h += "</div>";

  h += '<h3>Conversation-level values</h3><div class="scroll"><table><thead><tr><th>Feature</th><th class="r">min</th><th class="r">median</th><th class="r">mean</th><th class="r">max</th><th class="r">corpus median</th></tr></thead><tbody>';
  DATA.metrics.forEach(function (m) {
    var d = s.perMetric[m.id], cm = c.perMetric[m.id].median;
    h += "<tr><td>" + esc(m.label) + '</td><td class="r num">' + nf(d.min, m.decimals) + '</td><td class="r num">' + nf(d.median, m.decimals) + '</td><td class="r num">' + nf(d.mean, m.decimals) + '</td><td class="r num">' + nf(d.max, m.decimals) + '</td><td class="r num muted">' + nf(cm, m.decimals) + "</td></tr>";
  });
  h += "</tbody></table></div>";

  h += '<h3>Distribution</h3><div class="ctrl"><label>Feature <select id="distSel">' + DATA.metrics.map(function (m) { return '<option value="' + m.id + '"' + (m.id === ui.dist ? " selected" : "") + ">" + esc(m.label) + "</option>"; }).join("") + "</select></label></div>" + distSVG() +
    '<div class="legend"><span><i style="background:var(--ghost)"></i>whole corpus</span><span><i style="background:var(--accent)"></i>selection</span></div>';
  return h;
}
function distSVG() {
  var m = metricById[ui.dist], d = ix.domains[m.id], B = 40, W = 720, H = 190, L = 40, R = 10, T = 8, Bt = 24;
  var all = K.histogram(DATA.conversations.map(function (c) { return c[m.id]; }), d[0], d[1], B);
  var sl = K.histogram(sel.map(function (c) { return c[m.id]; }), d[0], d[1], B);
  var mx = Math.max.apply(null, all) || 1, bw = (W - L - R) / B, g = "";
  niceTicks(0, mx, 4).forEach(function (t) { var y = H - Bt - (H - Bt - T) * t / mx; g += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y + '" y2="' + y + '"/><text x="' + (L - 5) + '" y="' + (y + 3) + '" text-anchor="end">' + t + "</text>"; });
  for (var i = 0; i < B; i++) {
    var x = L + i * bw, ha = (H - Bt - T) * all[i] / mx, hs = (H - Bt - T) * sl[i] / mx;
    g += '<rect x="' + (x + 1) + '" y="' + (H - Bt - ha) + '" width="' + (bw - 2) + '" height="' + ha + '" fill="var(--ghost)"/>';
    if (sl[i]) g += '<rect x="' + (x + 1) + '" y="' + (H - Bt - hs) + '" width="' + (bw - 2) + '" height="' + hs + '" fill="var(--accent)"/>';
  }
  niceTicks(d[0], d[1], 6).forEach(function (t) { var x = L + (t - d[0]) / (d[1] - d[0] || 1) * (W - L - R); g += '<text x="' + x + '" y="' + (H - 7) + '" text-anchor="middle">' + nf(t, m.decimals > 1 ? 1 : m.decimals) + "</text>"; });
  var med = sum.perMetric[m.id].median;
  if (med !== null) { var mxp = L + (med - d[0]) / (d[1] - d[0] || 1) * (W - L - R); g += '<line x1="' + mxp + '" x2="' + mxp + '" y1="' + T + '" y2="' + (H - Bt) + '" stroke="var(--ink)" stroke-width="1.2" stroke-dasharray="4 3"/><text x="' + (mxp + 4) + '" y="' + (T + 10) + '">median ' + nf(med, m.decimals) + "</text>"; }
  return '<svg class="plot" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Distribution of ' + esc(m.label) + '">' + g + '<line class="axis" x1="' + L + '" x2="' + (W - R) + '" y1="' + (H - Bt) + '" y2="' + (H - Bt) + '"/></svg>';
}

/* -------------------------------------------------------------- explore */
var PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#c9b800", "#8a8a8a"];
function colorKey(c) { return ui.color === "none" ? "all" : K.valuesOf(c, ix.convFacets.filter(function (f) { return f.id === ui.color; })[0])[0]; }
function renderExplore() {
  var h = "<h2>Explore</h2><p class=\"note\">Each point is a conversation. Drag a rectangle on the plot to keep only the conversations inside it (it sets the two feature ranges). Click a point to open the transcription.</p>";
  function opts(cur) { return DATA.metrics.map(function (m) { return '<option value="' + m.id + '"' + (m.id === cur ? " selected" : "") + ">" + esc(m.label) + (m.unit ? " (" + esc(m.unit) + ")" : "") + "</option>"; }).join(""); }
  h += '<div class="ctrl"><label>Horizontal <select id="sx">' + opts(ui.sx) + '</select></label><label>Vertical <select id="sy">' + opts(ui.sy) + '</select></label><label>Colour by <select id="sc">' +
    ["type", "module", "year", "point", "relationship", "none"].map(function (id) { return '<option value="' + id + '"' + (id === ui.color ? " selected" : "") + ">" + (id === "none" ? "nothing" : esc(facetLabel("cat", id))) + "</option>"; }).join("") + "</select></label></div>";
  h += scatter();
  return h;
}
var plot = null;
function scatter() {
  var mx = metricById[ui.sx], my = metricById[ui.sy], dx = ix.domains[mx.id], dy = ix.domains[my.id];
  var W = 760, H = 470, L = 54, R = 14, T = 12, B = 40;
  function px(v) { return L + (v - dx[0]) / (dx[1] - dx[0] || 1) * (W - L - R); }
  function py(v) { return H - B - (v - dy[0]) / (dy[1] - dy[0] || 1) * (H - B - T); }
  plot = { W: W, H: H, L: L, R: R, T: T, B: B, dx: dx, dy: dy, mx: mx, my: my };
  var inSel = {}; sel.forEach(function (c) { inSel[c.code] = 1; });
  var keys = {}, order = [];
  DATA.conversations.forEach(function (c) { var k = colorKey(c); if (!(k in keys)) { keys[k] = 0; order.push(k); } keys[k]++; });
  order.sort(function (a, b) { return keys[b] - keys[a]; });
  var colorOf = {}; order.forEach(function (k, i) { colorOf[k] = ui.color === "none" ? "var(--accent)" : PALETTE[Math.min(i, PALETTE.length - 1)]; });
  var g = "";
  niceTicks(dx[0], dx[1], 7).forEach(function (t) { g += '<line class="grid" x1="' + px(t) + '" x2="' + px(t) + '" y1="' + T + '" y2="' + (H - B) + '"/><text x="' + px(t) + '" y="' + (H - B + 15) + '" text-anchor="middle">' + nf(t, mx.decimals > 1 ? 1 : mx.decimals) + "</text>"; });
  niceTicks(dy[0], dy[1], 6).forEach(function (t) { g += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + py(t) + '" y2="' + py(t) + '"/><text x="' + (L - 6) + '" y="' + (py(t) + 3) + '" text-anchor="end">' + nf(t, my.decimals > 1 ? 1 : my.decimals) + "</text>"; });
  g += '<text x="' + (L + (W - L - R) / 2) + '" y="' + (H - 4) + '" text-anchor="middle">' + esc(mx.label + (mx.unit ? " (" + mx.unit + ")" : "")) + '</text><text transform="translate(12 ' + (T + (H - B - T) / 2) + ') rotate(-90)" text-anchor="middle">' + esc(my.label + (my.unit ? " (" + my.unit + ")" : "")) + "</text>";
  var pts = DATA.conversations.filter(function (c) { return c[mx.id] !== null && c[my.id] !== null; });
  pts.filter(function (c) { return !inSel[c.code]; }).forEach(function (c) { g += '<circle class="out" data-code="' + c.code + '" cx="' + px(c[mx.id]).toFixed(1) + '" cy="' + py(c[my.id]).toFixed(1) + '" r="3.4" fill="' + colorOf[colorKey(c)] + '"/>'; });
  pts.filter(function (c) { return inSel[c.code]; }).forEach(function (c) { g += '<circle data-code="' + c.code + '" cx="' + px(c[mx.id]).toFixed(1) + '" cy="' + py(c[my.id]).toFixed(1) + '" r="4" fill="' + colorOf[colorKey(c)] + '"/>'; });
  g += '<line class="axis" x1="' + L + '" x2="' + (W - R) + '" y1="' + (H - B) + '" y2="' + (H - B) + '"/><line class="axis" x1="' + L + '" x2="' + L + '" y1="' + T + '" y2="' + (H - B) + '"/><rect id="brush" class="brush" width="0" height="0" style="display:none"/>';
  var h = '<svg class="plot" id="scatter" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Scatter plot of ' + esc(my.label) + " against " + esc(mx.label) + '">' + g + "</svg>";
  h += '<div class="legend">' + (ui.color === "none" ? "" : order.map(function (k) { return '<span><i style="background:' + colorOf[k] + ';border-radius:50%"></i>' + esc(pretty(ui.color, k)) + " (" + keys[k] + ")</span>"; }).join("")) + '<span style="margin-left:auto">faded points are outside the selection</span></div>';
  return h;
}
function bindScatter() {
  var svg = $("#scatter"); if (!svg) return;
  var tip = $("#tip"), brush = $("#brush"), start = null;
  var byCode = {}; DATA.conversations.forEach(function (c) { byCode[c.code] = c; });
  function pt(e) { var r = svg.getBoundingClientRect(); return { x: (e.clientX - r.left) * plot.W / r.width, y: (e.clientY - r.top) * plot.H / r.height }; }
  function clamp(p) { return { x: Math.max(plot.L, Math.min(plot.W - plot.R, p.x)), y: Math.max(plot.T, Math.min(plot.H - plot.B, p.y)) }; }
  svg.addEventListener("pointerdown", function (e) { if (e.button !== 0) return; start = clamp(pt(e)); start.t = e.target.closest("circle[data-code]"); brush.style.display = ""; brush.setAttribute("x", start.x); brush.setAttribute("y", start.y); brush.setAttribute("width", 0); brush.setAttribute("height", 0); try { svg.setPointerCapture(e.pointerId); } catch (x) {} tip.style.display = "none"; });
  svg.addEventListener("pointermove", function (e) {
    if (start) {
      var p = clamp(pt(e));
      brush.setAttribute("x", Math.min(start.x, p.x)); brush.setAttribute("y", Math.min(start.y, p.y));
      brush.setAttribute("width", Math.abs(p.x - start.x)); brush.setAttribute("height", Math.abs(p.y - start.y)); return;
    }
    var t = e.target.closest("circle[data-code]");
    if (!t) { tip.style.display = "none"; return; }
    var c = byCode[t.getAttribute("data-code")];
    tip.innerHTML = "<b>" + esc(c.code) + "</b> · " + esc(c.modules[0]) + "<br>" + esc(pretty("type", c.type || UNKNOWN)) + (c.year ? ", " + c.year : "") + "<br>" + esc(plot.mx.label) + ": " + withUnit(plot.mx, c[plot.mx.id]) + "<br>" + esc(plot.my.label) + ": " + withUnit(plot.my, c[plot.my.id]);
    tip.style.display = "block"; tip.style.left = Math.min(window.innerWidth - 290, e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
  });
  svg.addEventListener("pointerleave", function () { tip.style.display = "none"; });
  svg.addEventListener("pointerup", function (e) {
    if (!start) return;
    var p = clamp(pt(e)), x0 = Math.min(start.x, p.x), x1 = Math.max(start.x, p.x), y0 = Math.min(start.y, p.y), y1 = Math.max(start.y, p.y);
    var moved = x1 - x0 > 6 && y1 - y0 > 6, t = start.t; start = null; brush.style.display = "none";
    if (!moved) { if (t) window.open(htmlLink(byCode[t.getAttribute("data-code")]), "_blank", "noopener"); return; }
    function vx(x) { return plot.dx[0] + (x - plot.L) / (plot.W - plot.L - plot.R) * (plot.dx[1] - plot.dx[0]); }
    function vy(y) { return plot.dy[0] + (plot.H - plot.B - y) / (plot.H - plot.B - plot.T) * (plot.dy[1] - plot.dy[0]); }
    setRange(plot.mx, roundTo(plot.mx, vx(x0), false), roundTo(plot.mx, vx(x1), true));
    setRange(plot.my, roundTo(plot.my, vy(y1), false), roundTo(plot.my, vy(y0), true));
    update();
  });
}

/* ---------------------------------------------------------------- table */
var COLS = [["code", "Code", 0], ["module", "Module", 0], ["type", "Type", 0], ["year", "Year", 0], ["point", "Point", 0],
  ["hours", "Duration (h)", 2], ["speakers_n", "Speakers", 0], ["tokens", "Tokens", 0], ["overlap_pct", "Overlap time %", 1], ["ann_overlap_pct", "Overlap tokens %", 1], ["rate", "tok/s", 2]];
function sortVal(c, k) { var v = k === "module" ? c.modules[0] : c[k]; return v === null || v === undefined ? null : v; }
function renderTable() {
  var h = "<h2>Conversations in the selection</h2>";
  if (!sel.length) return h + '<p class="empty">No conversation matches these filters.</p>';
  var rows = sel.slice().sort(function (a, b) {
    var x = sortVal(a, ui.sort), y = sortVal(b, ui.sort);
    if (x === null && y === null) return 0; if (x === null) return 1; if (y === null) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), "en", { numeric: true })) * ui.dir;
  });
  h += '<p class="note">' + nf(rows.length) + ' conversations. Use × to take one out of the selection; it stays out of the statistics and the export until you restore it.' + (state.exclude.length ? ' <button class="btn" type="button" id="restore">Restore ' + state.exclude.length + " removed</button>" : "") + '</p><div class="scroll"><table><thead><tr>';
  COLS.forEach(function (c) { var on = ui.sort === c[0]; h += '<th class="' + (c[2] || c[0] === "speakers_n" || c[0] === "tokens" ? "r" : "") + '"' + (on ? ' aria-sort="' + (ui.dir > 0 ? "ascending" : "descending") + '"' : "") + '><button type="button" data-sort="' + c[0] + '">' + esc(c[1]) + (on ? (ui.dir > 0 ? " ↑" : " ↓") : "") + "</button></th>"; });
  h += "<th></th></tr></thead><tbody>";
  rows.forEach(function (c) {
    h += '<tr><td><a href="' + htmlLink(c) + '">' + c.code + "</a></td><td>" + esc(c.modules.join(", ")) + "</td><td>" + esc(c.type ? pretty("type", c.type) + (c.subtype ? " · " + c.subtype : "") : "–") + "</td><td>" + (c.year || "–") + "</td><td>" + esc(c.point || "–") + "</td>";
    COLS.slice(5).forEach(function (k) { h += '<td class="r num">' + nf(c[k[0]], k[2]) + "</td>"; });
    h += '<td><button class="rm" type="button" data-exclude="' + c.code + '" title="Remove from the selection" aria-label="Remove ' + c.code + '">×</button></td></tr>';
  });
  return h + "</tbody></table></div>";
}

/* --------------------------------------------------------------- export */
function renderExport() {
  var h = "<h2>Export this sub-corpus</h2>";
  if (!sel.length) return h + '<p class="empty">Nothing to export: no conversation matches these filters.</p>';
  h += '<p class="note">' + nf(sel.length) + " conversations, " + nf(sum.tokens) + " tokens. Everything below is generated in your browser from the current selection.</p>";
  h += '<h3>Lists</h3><div class="actions-row"><button class="btn" type="button" data-dl="codes">Conversation codes (.txt)</button><button class="btn" type="button" data-dl="csv">Table with all features (.csv)</button><button class="btn" type="button" data-dl="json">Selection and filters (.json)</button></div>';
  h += '<p class="note">The .json file records the filters, the codes and the module versions the figures come from, so the selection can be described and reproduced.</p>';
  h += '<h3>Files</h3><p class="note">A shell script that copies the chosen formats of these conversations from your local checkouts into one folder.</p><div class="fmt">';
  K.FORMATS.forEach(function (f) { h += '<label><input type="checkbox" data-fmt="' + f.id + '"' + (ui.formats[f.id] ? " checked" : "") + ">" + esc(f.label) + "</label>"; });
  h += '</div><div class="actions-row"><button class="btn primary" type="button" data-dl="sh">Download copy-subcorpus.sh</button><button class="btn" type="button" data-dl="copysh">Copy script</button></div><pre id="shprev" tabindex="0"></pre>';
  var shared = sel.filter(function (c) { return c.modules.length > 1; }).length;
  if (shared) h += '<p class="note">' + shared + " selected conversations exist in more than one module (KIP and ParlaTO share 16); the script takes them from the first module listed.</p>";
  return h;
}
function fmtIds() { return K.FORMATS.filter(function (f) { return ui.formats[f.id]; }).map(function (f) { return f.id; }); }
function script() { return K.copyScript(sel, fmtIds(), { note: "Filters: " + (K.isActive(state) ? "see selection.json" : "none (whole corpus)") }); }
function updateScriptPreview() {
  var el = $("#shprev"); if (!el) return;
  var lines = script().split("\n");
  el.textContent = lines.slice(0, 26).join("\n") + (lines.length > 26 ? "\n… (" + (lines.length - 26) + " more lines)" : "");
}
var CSV_COLS = ["code", "modules", "type", "subtype", "relationship", "moderator", "topic", "year", "point", "languages"].concat(DATA.metrics.map(function (m) { return m.id; }));
function onPanel(e) {
  var t = e.target;
  var sortBtn = t.closest("button[data-sort]");
  if (sortBtn) { var k = sortBtn.getAttribute("data-sort"); ui.dir = ui.sort === k ? -ui.dir : 1; ui.sort = k; renderPanel(); return; }
  var ex = t.closest("button[data-exclude]");
  if (ex) { state.exclude.push(ex.getAttribute("data-exclude")); update(); return; }
  if (t.id === "restore") { state.exclude = []; update(); return; }
  var more = t.closest("button[data-more]");
  if (more) { ui.spkTop[more.getAttribute("data-more")] = 1; renderPanel(); return; }
  var dl = t.closest("button[data-dl]");
  if (dl) {
    var w = dl.getAttribute("data-dl");
    if (w === "codes") download("kiparla-codes.txt", K.codeList(sel));
    else if (w === "csv") download("kiparla-selection.csv", K.toCSV(sel, CSV_COLS), "text/csv");
    else if (w === "json") download("kiparla-selection.json", JSON.stringify({ schema: 1, sources: DATA.sources, filters: state, conversations: sel.map(function (c) { return c.code; }) }, null, 2), "application/json");
    else if (w === "sh") download("copy-subcorpus.sh", script(), "text/x-shellscript");
    else if (w === "copysh") copyText(script(), function (ok) { dl.textContent = ok ? "Copied" : "Copy failed: select the text below"; setTimeout(function () { dl.textContent = "Copy script"; }, 1800); });
  }
}
function onPanelChange(e) {
  var t = e.target;
  if (t.id === "distSel") { ui.dist = t.value; renderPanel(); }
  else if (t.id === "sx") { ui.sx = t.value; renderPanel(); }
  else if (t.id === "sy") { ui.sy = t.value; renderPanel(); }
  else if (t.id === "sc") { ui.color = t.value; renderPanel(); }
  else if (t.matches("input[data-fmt]")) { ui.formats[t.getAttribute("data-fmt")] = t.checked ? 1 : 0; updateScriptPreview(); }
}

/* ---------------------------------------------------------------- shell */
function renderPanel() {
  var p = $("#panel");
  p.innerHTML = tab === "overview" ? renderOverview() : tab === "explore" ? renderExplore() : tab === "table" ? renderTable() : renderExport();
  if (tab === "explore") bindScatter();
  if (tab === "export") updateScriptPreview();
  $$("#tabs button").forEach(function (b) { b.setAttribute("aria-selected", String(b.getAttribute("data-tab") === tab)); });
}
var persistTimer = null;
function persist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(function () {
    try {
      var e = K.encodeState(state), parts = [];
      if (e) parts.push("s=" + e);
      if (tab !== "overview") parts.push("t=" + tab);
      history.replaceState(null, "", parts.length ? "#" + parts.join("&") : location.pathname + location.search);
    } catch (x) { /* storage or history unavailable: the page still works */ }
  }, 250);
}
function update() {
  sel = K.filter(ix, state);
  sum = K.summarise(ix, sel);
  updateSidebar(); renderSelBar(); renderChips(); renderPanel(); persist();
}
function init() {
  try {
    var m = /[#&]s=([^&]+)/.exec(location.hash), t = /[#&]t=(overview|explore|table|export)/.exec(location.hash);
    if (m) state = K.decodeState(m[1]);
    if (t) tab = t[1];
  } catch (x) {}
  $("#nav-transcriptions").href = LINKS.artifacts + "index.html";
  $("#nav-search").href = LINKS.search;
  buildSidebar();
  $("#fbody").addEventListener("input", onSidebar);
  $("#fbody").addEventListener("change", function (e) { if (e.target.matches("input[type=checkbox]")) onSidebar(e); });
  $("#fbody").addEventListener("click", function (e) { var b = e.target.closest("button[data-mode]"); if (b) { state.spkMode = b.getAttribute("data-mode"); update(); } });
  $("#reset").addEventListener("click", function () { state = K.emptyState(); update(); });
  $("#ftoggle").addEventListener("click", function () { var f = $("#filters"), o = f.classList.toggle("open"); this.textContent = o ? "Hide" : "Show"; this.setAttribute("aria-expanded", String(o)); });
  $("#chips").addEventListener("click", onChips);
  $("#tabs").addEventListener("click", function (e) { var b = e.target.closest("button[data-tab]"); if (b) { tab = b.getAttribute("data-tab"); renderPanel(); persist(); } });
  $("#selbar").addEventListener("click", function (e) {
    if (e.target.id === "goexport") { tab = "export"; renderPanel(); persist(); $("#tabs").scrollIntoView({ block: "start" }); }
    if (e.target.id === "copylink") { var b = e.target; copyText(location.href, function (ok) { b.textContent = ok ? "Link copied" : "Copy the address bar"; setTimeout(function () { b.textContent = "Copy link"; }, 1800); }); }
  });
  var p = $("#panel"); p.addEventListener("click", onPanel); p.addEventListener("change", onPanelChange);
  var src = Object.keys(DATA.sources).map(function (k) { return k + (DATA.sources[k] ? " v" + DATA.sources[k] : ""); }).join(" · ");
  var defs = DATA.metrics.map(function (m) { return "<dt>" + esc(m.label) + "</dt><dd>" + esc(m.description) + "</dd>"; }).join("");
  $("#foot").innerHTML = '<details><summary>How the numbers are computed</summary><p class="note">Every figure comes from the <code>summaries/</code> folder of each module, built from the vertical files with <code>summarize.py</code>. Speech time counts a speaker as soon as one of their units is active; overlap time is when two or more speakers are active at once. Overlap in tokens uses the overlaps the transcribers marked with brackets. Speaker attributes come from <code>participants.tsv</code>; tiers that are not listed there (unidentified speakers, <code>environment</code>) are left out of speaker counts and filters.</p><dl class="defs">' + defs + "</dl></details><p>Built from " + esc(src) + ".</p>";
  update();
}
init();
})();
