// Tests for explorer/core.js. Run: node tests/explorer_core.test.js
const assert = require("node:assert/strict");
const path = require("node:path");
const K = require(path.join(__dirname, "..", "explorer", "core.js"));

const metrics = [
  { id: "hours", label: "Duration", unit: "h", decimals: 2 },
  { id: "speech_hours", label: "Speech", unit: "h", decimals: 2 },
  { id: "overlap_pct", label: "Overlap", unit: "%", decimals: 1 },
  { id: "ann_overlap_pct", label: "Ann", unit: "%", decimals: 1 },
  { id: "ling", label: "Ling", unit: "", decimals: 0 },
  { id: "tokens", label: "Tokens", unit: "", decimals: 0 },
  { id: "units", label: "Units", unit: "", decimals: 0 },
];

function conv(code, over) {
  return Object.assign({
    code, modules: ["KIP"], type: "lecture", subtype: null, relationship: "asymmetric",
    moderator: "no", topic: "free", year: "2018", point: "BO", languages: null,
    hours: 1, speech_hours: 1, overlap_pct: 0, ann_overlap_pct: 0, ling: 100, tokens: 110, units: 10,
  }, over);
}
function spk(c, s, over) {
  return Object.assign({ conv: c, spk: s, known: true, gender: "F", age: "21-25", region: "emilia-romagna", mothertongue: null }, over);
}

const data = {
  metrics,
  conversation_facets: [
    { id: "module", label: "Module", multi: true }, { id: "type", label: "Type", multi: false },
    { id: "year", label: "Year", multi: false }, { id: "languages", label: "Languages", multi: true },
  ],
  speaker_facets: [
    { id: "gender", label: "Gender", multi: false }, { id: "age", label: "Age", multi: false },
    { id: "mothertongue", label: "Mother tongue", multi: true },
  ],
  conversations: [
    conv("A1", { type: "lecture", year: "2018", hours: 1, speech_hours: 1, overlap_pct: 2, ann_overlap_pct: 4, ling: 100 }),
    conv("A2", { type: "free-conversation", year: "2019", modules: ["KIP", "ParlaTO"], languages: "italian;dialect",
                 hours: 2, speech_hours: 1.5, overlap_pct: 20, ann_overlap_pct: 30, ling: 300 }),
    conv("A3", { type: "free-conversation", year: null, languages: null, hours: 0.5, speech_hours: 0.5,
                 overlap_pct: 10, ann_overlap_pct: null, ling: 50 }),
    conv("A4", { type: "exam", year: "2018", hours: 1, speech_hours: 1, overlap_pct: null, ling: 80 }),
  ],
  speakers: [
    spk("A1", "S1", { gender: "F" }), spk("A1", "S2", { gender: "M" }),
    spk("A2", "S1", { gender: "F" }), spk("A2", "S3", { gender: "F", mothertongue: "italiano; romeno" }),
    spk("A3", "S4", { gender: null }),
    spk("A4", "S5", { gender: "M" }), spk("A4", "???", { known: false, gender: null }),
  ],
};
const ix = K.buildIndex(data);
const codes = (st) => K.filter(ix, st).map((c) => c.code);
const st = (patch) => Object.assign(K.emptyState(), patch);

// --- filtering ---------------------------------------------------------
assert.deepEqual(codes(K.emptyState()), ["A1", "A2", "A3", "A4"]);
assert.deepEqual(codes(st({ cat: { type: ["free-conversation"] } })), ["A2", "A3"]);
assert.deepEqual(codes(st({ cat: { type: ["lecture", "exam"] } })), ["A1", "A4"], "values within a facet are OR");
assert.deepEqual(codes(st({ cat: { type: ["free-conversation"], year: ["2019"] } })), ["A2"], "facets are AND");
assert.deepEqual(codes(st({ cat: { year: ["unknown"] } })), ["A3"], "null is selectable as 'unknown'");
assert.deepEqual(codes(st({ cat: { module: ["ParlaTO"] } })), ["A2"], "multi-module conversation");
assert.deepEqual(codes(st({ cat: { module: ["KIP"] } })), ["A1", "A2", "A3", "A4"]);
assert.deepEqual(codes(st({ cat: { languages: ["dialect"] } })), ["A2"], "';'-separated values are split");
assert.deepEqual(codes(st({ cat: { languages: ["unknown"] } })), ["A1", "A3", "A4"]);

// hand-removed conversations
assert.deepEqual(codes(st({ cat: { type: ["free-conversation"] }, exclude: ["A3"] })), ["A2"], "excluded codes are dropped");
assert.deepEqual(K.facetOptions(ix, st({ cat: { type: ["free-conversation"] }, exclude: ["A3"] }), "cat", "year")
  .map((o) => o.value), ["2018", "2019", "unknown"], "options come from the filters, not the exclusions");

// numeric ranges: inclusive, open ends, null never matches an active range
assert.deepEqual(codes(st({ num: { overlap_pct: [10, 20] } })), ["A2", "A3"]);
assert.deepEqual(codes(st({ num: { overlap_pct: [null, 2] } })), ["A1"]);
assert.deepEqual(codes(st({ num: { overlap_pct: [3, null] } })), ["A2", "A3"]);
assert.deepEqual(codes(st({ num: { hours: [1, 1] } })), ["A1", "A4"]);

// speaker attributes: any / all, unknown, unidentified tiers ignored
assert.deepEqual(codes(st({ spk: { gender: ["M"] } })), ["A1", "A4"], "any speaker");
assert.deepEqual(codes(st({ spk: { gender: ["F"] }, spkMode: "all" })), ["A2"], "every identified speaker");
assert.deepEqual(codes(st({ spk: { gender: ["M"] }, spkMode: "all" })), ["A4"], "'???' tier does not break 'all'");
assert.deepEqual(codes(st({ spk: { gender: ["unknown"] } })), ["A3"]);
assert.deepEqual(codes(st({ spk: { mothertongue: ["romeno"] } })), ["A2"], "multi-valued speaker attribute");
assert.deepEqual(codes(st({ spk: { gender: ["F"] }, cat: { type: ["lecture"] } })), ["A1"]);

// text search over code and speaker codes
assert.deepEqual(codes(st({ q: "a2" })), ["A2"]);
assert.deepEqual(codes(st({ q: "s1" })), ["A1", "A2"]);
assert.deepEqual(codes(st({ q: "s1 s3" })), ["A2"], "all terms must match");
assert.deepEqual(codes(st({ q: "???" })), [], "unidentified tiers are not searchable");

// --- facet options (counts ignore the facet's own filter) --------------
let opts = K.facetOptions(ix, st({ cat: { type: ["lecture"] } }), "cat", "type");
assert.deepEqual(opts, [{ value: "exam", count: 1 }, { value: "free-conversation", count: 2 }, { value: "lecture", count: 1 }],
  "selecting one type still shows what the others would give");
opts = K.facetOptions(ix, st({ cat: { type: ["lecture"], year: ["2019"] } }), "cat", "type");
assert.deepEqual(opts.map((o) => [o.value, o.count]), [["exam", 0], ["free-conversation", 1], ["lecture", 0]],
  "other facets still narrow the counts");
opts = K.facetOptions(ix, K.emptyState(), "cat", "year");
assert.deepEqual(opts.map((o) => o.value), ["2018", "2019", "unknown"], "unknown sorts last");
opts = K.facetOptions(ix, K.emptyState(), "spk", "gender");
assert.deepEqual(opts, [{ value: "F", count: 2 }, { value: "M", count: 2 }, { value: "unknown", count: 1 }],
  "speaker options count conversations having such a speaker");
opts = K.facetOptions(ix, st({ cat: { type: ["lecture"] } }), "cat", "module");
assert.deepEqual(opts, [{ value: "KIP", count: 1 }, { value: "ParlaTO", count: 0 }]);

// --- statistics --------------------------------------------------------
const all = K.filter(ix, K.emptyState());
const s = K.summarise(ix, all);
assert.equal(s.conversations, 4);
assert.equal(s.speakers, 5, "distinct identified speakers (S1 appears twice, '???' excluded)");
assert.equal(s.hours, 4.5);
assert.equal(s.speechHours, 4);
assert.equal(s.ling, 530);
// pooled overlap share: (1*2% + 1.5*20% + 0.5*10% + 1*0) / 4 h = 0.02+0.3+0.05 = 0.37 / 4
assert.ok(Math.abs(s.overlapShare - 0.37 / 4) < 1e-12);
// annotated overlap share: tokens in overlap / linguistic tokens = (4+90+0+0)/530
assert.ok(Math.abs(s.annOverlapShare - 94 / 530) < 1e-12);
assert.ok(Math.abs(s.rate - 530 / (4 * 3600)) < 1e-12);
assert.equal(s.perMetric.overlap_pct.n, 3, "missing values are left out of per-metric figures");
assert.equal(s.perMetric.overlap_pct.median, 10);
assert.equal(K.summarise(ix, []).overlapShare, null);

let b = K.breakdown(ix, all, "cat", "type");
assert.deepEqual(b, [{ value: "exam", count: 1 }, { value: "free-conversation", count: 2 }, { value: "lecture", count: 1 }]);
b = K.breakdown(ix, all, "spk", "gender");
assert.deepEqual(b, [{ value: "F", count: 2 }, { value: "M", count: 2 }, { value: "unknown", count: 1 }],
  "speakers are counted once however many conversations they are in");

assert.deepEqual(K.histogram([0, 1, 2, 3, 4, 10, null], 0, 10, 5), [2, 2, 1, 0, 1], "10 is clamped into the last bin");
assert.deepEqual(K.histogram([5, 5], 5, 5, 3), [2, 0, 0], "degenerate range");

// --- export --------------------------------------------------------------
const csv = K.toCSV([conv("X,1", { languages: 'say "hi"', modules: ["KIP", "ParlaTO"], year: null })],
  ["code", "languages", "modules", "year"]);
assert.equal(csv, 'code,languages,modules,year\n"X,1","say ""hi""",KIP;ParlaTO,\n');
assert.equal(K.codeList(all.slice(0, 2)), "A1\nA2\n");
const sh = K.copyScript([conv("A2", { modules: ["KIP", "ParlaTO"] })], ["tsv", "html", "pdf"], { note: "test" });
assert.match(sh, /cp "\$KIPARLA\/KIP\/tsv\/A2\.vert\.tsv" "\$OUT\/tsv\/"/);
assert.match(sh, /cp "\$ARTIFACTS\/KIP\/html\/A2\.html" "\$OUT\/html\/"/);
assert.match(sh, /A2-orthographic\.pdf/);
assert.match(sh, /A2-jefferson\.pdf/);
assert.doesNotMatch(sh, /eaf/);
assert.match(sh, /set -euo pipefail/);

// --- shareable state -------------------------------------------------------
assert.equal(K.encodeState(K.emptyState()), "");
const state = st({ q: "città è", cat: { type: ["free-conversation"], year: [] }, spk: { gender: ["F"] },
                   num: { overlap_pct: [5, null] }, spkMode: "all" });
const enc = K.encodeState(state);
assert.match(enc, /^[A-Za-z0-9_-]+$/, "URL-safe");
const dec = K.decodeState(enc);
assert.equal(dec.q, "città è");
assert.deepEqual(dec.cat, { type: ["free-conversation"] }, "empty facets are dropped");
assert.deepEqual(dec.num, { overlap_pct: [5, null] });
assert.equal(dec.spkMode, "all");
assert.deepEqual(K.decodeState(K.encodeState(st({ exclude: ["A1", "A9"] }))).exclude, ["A1", "A9"]);
assert.equal(K.isActive(st({ exclude: ["A1"] })), true);
assert.deepEqual(K.decodeState("%%%garbage"), K.emptyState(), "bad input gives the empty state");
assert.equal(K.isActive(K.emptyState()), false);
assert.equal(K.isActive(dec), true);

console.log("explorer core: all assertions passed");
