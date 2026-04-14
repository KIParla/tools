#!/usr/bin/env python3
"""
linear2html.py – Convert KIParla linearized transcripts to a styled HTML page.

Produces a single self-contained HTML file with both orthographic and Jefferson
transcriptions embedded. Conversation metadata and participant table are always
visible; toggle buttons switch between transcript views and show/hide timings.

Usage:
    python3 tools/linear2html.py \\
        --orthographic  ParlaTO/linear-orthographic/PTA005.txt \\
        --jefferson     ParlaTO/linear-jefferson/PTA005.txt \\
        --tsv           ParlaTO/tsv/PTA005.vert.tsv \\
        --conversations ParlaTO/metadata/conversations.tsv \\
        --participants  ParlaTO/metadata/participants.tsv \\
        --output        NoSketchEngine/html/PTA005.html

Or write directly to the KIParla artifacts layout:
    python3 tools/linear2html.py \\
        --orthographic  ParlaTO/linear-orthographic/PTA005.txt \\
        --jefferson     ParlaTO/linear-jefferson/PTA005.txt \\
        --tsv           ParlaTO/tsv/PTA005.vert.tsv \\
        --conversations ParlaTO/metadata/conversations.tsv \\
        --participants  ParlaTO/metadata/participants.tsv \\
        --artifacts-root KIParla-artifacts \\
        --module        ParlaTO

The conversation code is inferred from the input filename stem.
Either --orthographic or --jefferson (or both) must be supplied.
--tsv is optional; when supplied, per-turn begin/end times are embedded
and can be toggled on/off in the page.
"""

import argparse
import csv
import html
import os
import re
import subprocess
import sys
from pathlib import Path


# ── colour palette assigned round-robin to speakers ───────────────────────────
SPEAKER_COLOURS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#bab0ac",
]

# ── Italian labels for metadata fields ────────────────────────────────────────
TYPE_LABELS = {
    "office-hours":             "ricevimento",
    "free-conversation":        "conversazione libera",
    "exam":                     "esame",
    "lecture":                  "lezione",
    "semistructured-interview": "intervista semistrutturata",
}
RELATIONSHIP_LABELS = {"asymmetric": "asimmetrico", "symmetric": "simmetrico"}
TOPIC_LABELS        = {"free": "libero", "fixed": "fisso"}
OCCUPATION_LABELS   = {
    "0-Students": "stud", "0-Retired": "pens", "0-Unemployed": "disocc",
    "1-Managers": "impr", "2-Professionals": "intell", "3-Technicians": "tec",
    "4-Clerical-Workers": "uff", "5-Service-Workers": "comm",
    "6-Craft-Workers": "artig", "7-Plant-Operators": "oper",
    "8-Elementary": "non-qualif",
}
STUDY_LABELS = {
    "liceo-diploma": "dip-lic", "middle-school": "med", "primary-school": "elem",
    "technical-vocational-diploma": "dip-tec-prof",
    "university-degree": "laurea", "university-degree-ongoing": "laurea-in-corso",
}


def tr(value, mapping):
    return mapping.get(value, value)


# ── loaders ───────────────────────────────────────────────────────────────────

def load_tsv(path):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {row["code"]: row for row in csv.DictReader(f, delimiter="\t")}


def infer_module_name(conversations_path):
    path = Path(conversations_path).resolve()
    if path.parent.name == "metadata":
        return path.parent.parent.name
    return path.parent.name


def load_turns(path):
    turns = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if "\t" in line:
                speaker, text = line.split("\t", 1)
            else:
                speaker, text = "???", line
            turns.append((speaker.strip(), text.strip()))
    return turns


def _append_unit_text(text_jefferson, text_orthographic, row):
    rtype = row.get("type", "")
    span = row.get("span", "")
    form = row.get("form", "")
    feats = row.get("jefferson_feats", "")

    if rtype in ["nonverbalbehavior", "shortpause"]:
        text_jefferson.append(span)
    elif rtype in ["unknown"]:
        text_jefferson.append(span)
        text_orthographic.append("".join(c for c in span if c == "x"))
    elif rtype in ["linguistic"]:
        text_jefferson.append(span)
        text_orthographic.append(form)
    elif rtype in ["error"]:
        text_jefferson.append(span)
        text_orthographic.append("".join(c for c in form if c.isalpha()))

    if "ProsodicLink=Yes" in feats:
        text_jefferson.append("=")
        text_orthographic.append(" ")
    elif "SpaceAfter=No" not in feats:
        text_jefferson.append(" ")
        text_orthographic.append(" ")


def _finalize_unit(tu_idx, speaker, begin_ms, end_ms, text_jefferson, text_orthographic):
    jeff_text = re.sub(r" +", " ", "".join(text_jefferson).strip())
    orth_text = re.sub(r" +", " ", "".join(text_orthographic).strip())
    return {
        "tu_idx": tu_idx,
        "speaker": speaker.strip(),
        "begin": begin_ms,
        "end": end_ms,
        "jeff_text": jeff_text,
        "orth_text": orth_text,
    }


def load_tsv_units(tsv_path):
    """Return canonical TU records with Jefferson text, orthographic text, and timings."""
    units = []
    current_tu = None
    current_speaker = None
    begin_ms = None
    end_ms = None
    text_jefferson = []
    text_orthographic = []

    with open(tsv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            tid = row.get("tu_id", "")
            speaker = row.get("speaker", "").strip()
            if current_tu is None:
                current_tu = tid
                current_speaker = speaker

            if tid != current_tu:
                units.append(
                    _finalize_unit(
                        len(units), current_speaker, begin_ms, end_ms,
                        text_jefferson, text_orthographic,
                    )
                )
                current_tu = tid
                current_speaker = speaker
                begin_ms = None
                end_ms = None
                text_jefferson = []
                text_orthographic = []

            align = row.get("align", "")
            for part in align.split("|"):
                if part.startswith("Begin=") and begin_ms is None:
                    try:
                        begin_ms = round(float(part[6:]) * 1000)
                    except ValueError:
                        pass
                elif part.startswith("End="):
                    try:
                        end_ms = round(float(part[4:]) * 1000)
                    except ValueError:
                        pass

            _append_unit_text(text_jefferson, text_orthographic, row)

    if current_tu is not None:
        units.append(
            _finalize_unit(
                len(units), current_speaker, begin_ms, end_ms,
                text_jefferson, text_orthographic,
            )
        )

    return units


def align_turns_to_units(turns, units, text_key):
    """Align transcript turns to canonical TSV TUs, preserving shared TU indices."""
    candidate_indices = [i for i, u in enumerate(units) if u[text_key]]
    aligned = []
    pos = 0

    for speaker, text in turns:
        speaker = speaker.strip()
        text = text.strip()
        matched = None

        for offset in range(pos, len(candidate_indices)):
            idx = candidate_indices[offset]
            unit = units[idx]
            if unit["speaker"] == speaker and unit[text_key] == text:
                matched = offset
                break

        if matched is None:
            for offset in range(max(0, pos - 3), min(len(candidate_indices), pos + 4)):
                idx = candidate_indices[offset]
                unit = units[idx]
                if unit["speaker"] == speaker and unit[text_key] == text:
                    matched = offset
                    break

        if matched is None:
            raise ValueError(
                f"could not align turn for speaker {speaker!r}: {text[:80]!r}"
            )

        idx = candidate_indices[matched]
        unit = units[idx]
        aligned.append((unit["tu_idx"], unit["begin"], unit["end"]))
        pos = matched + 1

    return aligned


def fmt_ms(ms):
    """Format milliseconds as M:SS or H:MM:SS."""
    if ms is None:
        return ""
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


# ── Jefferson markup ──────────────────────────────────────────────────────────

def markup_jefferson(text):
    placeholders = {}

    def store(html_fragment):
        token = f"\uFFF0{len(placeholders)}\uFFF1"
        placeholders[token] = html_fragment
        return token

    t = text
    t = re.sub(
        r'\(\((.+?)\)\)',
        lambda m: store(f'<span class="para">(({html.escape(m.group(1))}))</span>'),
        t,
    )
    t = re.sub(
        r'\[([^\[\]]+)\]',
        lambda m: store(f'<span class="overlap">[{html.escape(m.group(1))}]</span>'),
        t,
    )
    t = re.sub(
        r'°([^°]+)°',
        lambda m: store(f'<span class="quiet">°{html.escape(m.group(1))}°</span>'),
        t,
    )
    t = re.sub(
        r'\(\.{1,3}\)',
        lambda m: store(f'<span class="pause">{html.escape(m.group(0))}</span>'),
        t,
    )
    t = re.sub(
        r'(\S+~)',
        lambda m: store(f'<span class="trunc">{html.escape(m.group(1))}</span>'),
        t,
    )
    t = re.sub(
        r'(\w)(:{2,})',
        lambda m: store(f'{html.escape(m.group(1))}<span class="length">{html.escape(m.group(2))}</span>'),
        t,
    )

    t = html.escape(t)
    for token, fragment in placeholders.items():
        t = t.replace(token, fragment)
    return t


# ── HTML builder ──────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --base-font-size: 15px;
    --transcript-font-size: 1rem;
    --timeline-handle-size: 14px;
}

body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: var(--base-font-size);
    line-height: 1.6;
    background: #f8f8f6;
    color: #222;
}

body.has-timeline {
    padding-bottom: 6.5rem;
}

body.timeline-collapsed {
    padding-bottom: 0;
}

.page {
    max-width: 980px;
    margin: 0 auto 0 max(.25rem, calc((100vw - 980px) / 2 - 2.4rem));
    padding: 2rem 1.5rem 4rem 18rem;
}

/* ── left rail ── */
.left-rail {
    position: fixed;
    top: 2rem;
    left: max(.2rem, calc((100vw - 980px) / 2 - 2.2rem));
    width: 14.5rem;
    z-index: 90;
    background: rgba(255, 255, 255, .9);
    border: 1px solid #e3e0da;
    border-radius: 16px;
    padding: 1rem;
    box-shadow: 0 12px 30px rgba(35, 25, 15, .08);
    backdrop-filter: blur(10px);
}

.left-rail h1 {
    font-size: 1.15rem;
    font-weight: 600;
    letter-spacing: .02em;
    color: #333;
    margin-bottom: .85rem;
    line-height: 1.25;
}

.left-rail .toggle-bar {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: .45rem;
    margin-bottom: 0;
}

.left-rail .toggle-bar button {
    width: 100%;
    text-align: left;
}

.left-rail .toggle-sep {
    display: none;
}

.download-links {
    display: flex;
    flex-direction: column;
    gap: .45rem;
    margin-top: .8rem;
}

.download-links a {
    display: block;
    padding: .45rem .7rem;
    border: 1px solid #d8d0c3;
    border-radius: 10px;
    background: #f8f4ed;
    color: #4f473d;
    text-decoration: none;
    font-size: .82rem;
}

.download-links a:hover {
    background: #f0e8dc;
}

/* ── sidebar ── */
.sidebar {
    position: fixed;
    top: 0;
    right: 0;
    width: 480px;
    height: 100vh;
    background: #fff;
    border-left: 1px solid #ddd;
    padding: 1.25rem 1.1rem 2rem;
    overflow-y: auto;
    z-index: 100;
    transform: translateX(100%);
    transition: transform .25s ease;
    box-shadow: -3px 0 12px rgba(0,0,0,.08);
}
.sidebar.open { transform: translateX(0); }

.sidebar-close {
    position: absolute;
    top: .7rem;
    left: .8rem;
    background: none;
    border: none;
    font-size: 1.1rem;
    cursor: pointer;
    color: #888;
    line-height: 1;
    padding: .2rem .4rem;
}
.sidebar-close:hover { color: #333; }

.sidebar h2 {
    font-size: .78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: #888;
    margin-bottom: .7rem;
    margin-top: 1.5rem;
}
.sidebar h2:first-of-type { margin-top: 2rem; }

.meta-grid {
    display: flex;
    flex-direction: column;
    gap: .25rem;
    font-size: .84rem;
    color: #555;
}
.meta-grid span::before {
    content: attr(data-label) ": ";
    font-weight: 600;
    color: #333;
}

.sidebar-section + .sidebar-section {
    margin-top: 1.4rem;
}

/* ── sidebar toggle button (fixed, top-right) ── */
.sidebar-toggle {
    position: fixed;
    top: .85rem;
    right: .85rem;
    z-index: 200;
    background: #fff;
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: .3rem .55rem;
    cursor: pointer;
    font-size: .9rem;
    color: #555;
    line-height: 1;
    box-shadow: 0 1px 4px rgba(0,0,0,.1);
    transition: background .15s, color .15s;
}
.sidebar-toggle:hover,
.sidebar-toggle.active { background: #333; color: #fff; border-color: #333; }

/* ── participant table ── */
table {
    width: 100%;
    border-collapse: collapse;
    font-size: .76rem;
    table-layout: fixed;
}
thead th {
    text-align: left;
    padding: .35rem .5rem;
    background: #eee;
    font-weight: 600;
    color: #444;
    border-bottom: 2px solid #ddd;
    overflow-wrap: anywhere;
}
tbody tr:nth-child(odd) { background: #fafafa; }
tbody td {
    padding: .28rem .5rem;
    border-bottom: 1px solid #e8e8e8;
    vertical-align: middle;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.dot {
    display: inline-block;
    width: 9px; height: 9px;
    border-radius: 50%;
    margin-right: .35rem;
    vertical-align: middle;
    flex-shrink: 0;
}

/* ── toggle bar ── */
.toggle-bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: .4rem;
    margin-bottom: 1.25rem;
}
.toggle-bar button {
    padding: .3rem .85rem;
    border-radius: 4px;
    border: 1px solid #ccc;
    cursor: pointer;
    font-size: .84rem;
    background: #fff;
    color: #444;
    transition: background .15s, color .15s;
}
.toggle-bar button.active {
    background: #333;
    color: #fff;
    border-color: #333;
}

/* ── transcript container ── */
.transcript-shell {
    background: #fff;
    border: 1px solid #e3e0da;
    border-radius: 14px;
    padding: 1.1rem 1.2rem;
    box-shadow: 0 14px 36px rgba(35, 25, 15, .06);
}

/* ── transcript panels ── */
.transcript { font-size: var(--transcript-font-size); }
.panel { display: none; }
.panel.visible { display: block; }

.turn {
    display: grid;
    grid-template-columns: 5.5rem 1fr;
    gap: 0 1rem;
    padding: .28rem 0;
    border-bottom: 1px solid #efefef;
    align-items: baseline;
}
.turn:last-child { border-bottom: none; }
.turn.hover-linked {
    background: #f5efe5;
    border-radius: 8px;
}

.turn-meta {
    min-width: 0;
}
.turn-speaker {
    display: block;
    font-size: .74rem;
    font-weight: 700;
    letter-spacing: .04em;
    padding-top: .1rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.turn-text {
    line-height: 1.65;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
}

/* ── timings (hidden until .show-times on .transcript) ── */
.turn-time {
    display: none;
    white-space: nowrap;
    font-size: .7rem;
    color: #aaa;
    letter-spacing: .02em;
    font-variant-numeric: tabular-nums;
    margin-top: .1rem;
}
.show-times .turn-time { display: block; }

/* ── Jefferson notation ── */
.jtext { font: inherit; }
.overlap { color: #2060a0; }
.para    { color: #777; font-style: italic; }
.quiet   { opacity: .65; }
.pause   { color: #b05010; font-weight: 600; }
.trunc   { color: #999; }
.length  { color: #b05010; }

/* ── scroll timeline ── */
.timeline {
    position: fixed;
    left: 50%;
    bottom: 1rem;
    transform: translateX(-50%);
    width: min(960px, calc(100vw - 2rem));
    background: rgba(255, 255, 255, .96);
    border: 1px solid rgba(205, 199, 188, .95);
    border-radius: 16px;
    box-shadow: 0 14px 32px rgba(35, 25, 15, .12);
    padding: .8rem .95rem .9rem;
    z-index: 150;
    backdrop-filter: blur(10px);
}

.timeline.hidden {
    display: none;
}

.timeline-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 1rem;
    margin-bottom: .45rem;
    font-size: .78rem;
    color: #6c655d;
}

.timeline-current {
    font-weight: 700;
    color: #2f2a24;
    font-variant-numeric: tabular-nums;
}

.timeline-total {
    font-variant-numeric: tabular-nums;
}

.timeline-controls {
    display: inline-flex;
    align-items: center;
    gap: .35rem;
}

.timeline-controls button {
    min-width: 2rem;
    padding: .15rem .45rem;
    border: 1px solid #cfc6b9;
    border-radius: 999px;
    background: #fff;
    color: #5d5449;
    cursor: pointer;
    font-size: .78rem;
    line-height: 1.2;
}

.timeline-controls button:hover {
    background: #f4efe7;
}

.timeline-zoom-label {
    min-width: 2.6rem;
    text-align: center;
    font-variant-numeric: tabular-nums;
}

.timeline-track {
    position: relative;
    height: 18px;
    cursor: pointer;
    touch-action: none;
    margin-top: .35rem;
}

.timeline-track-inner {
    position: absolute;
    top: 0;
    bottom: 0;
    left: calc(var(--timeline-handle-size) / 2);
    right: calc(var(--timeline-handle-size) / 2);
}

.timeline-rail {
    position: absolute;
    top: 50%;
    left: 0;
    right: 0;
    height: 4px;
    transform: translateY(-50%);
    border-radius: 999px;
    background: linear-gradient(90deg, #e8e1d7 0%, #efeae1 100%);
}

.timeline-marker {
    position: absolute;
    top: 50%;
    width: 2px;
    height: 10px;
    transform: translate(-50%, -50%);
    border-radius: 999px;
    background: #b7afa3;
    opacity: .75;
    cursor: pointer;
}

.timeline-marker.active {
    height: 14px;
    background: #2d6c99;
    opacity: 1;
}

.timeline-playhead {
    position: absolute;
    top: 50%;
    width: var(--timeline-handle-size);
    height: var(--timeline-handle-size);
    transform: translate(-50%, -50%);
    border-radius: 50%;
    background: #2d6c99;
    border: 2px solid #fff;
    box-shadow: 0 0 0 3px rgba(45, 108, 153, .18);
    cursor: grab;
}

.timeline-playhead.dragging {
    cursor: grabbing;
}

.speaker-map {
    display: flex;
    flex-direction: column;
    gap: .28rem;
}

.speaker-lane {
    display: grid;
    grid-template-columns: 4.5rem 1fr;
    align-items: center;
    gap: .55rem;
}

.speaker-label {
    font-size: .7rem;
    font-weight: 700;
    letter-spacing: .04em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.speaker-track {
    position: relative;
    height: 14px;
    margin: 0 calc(var(--timeline-handle-size) / 2);
    border-radius: 999px;
    background: #f3eee7;
    overflow: hidden;
}

.speaker-segment {
    position: absolute;
    top: 1px;
    height: 12px;
    border-radius: 999px;
    opacity: .55;
    cursor: pointer;
}

.speaker-segment.active {
    opacity: 1;
    box-shadow: 0 0 0 1px rgba(255, 255, 255, .75) inset;
}

.timeline-marker.hover-linked {
    height: 16px;
    background: #1f4f73;
    opacity: 1;
}

.speaker-segment.hover-linked {
    opacity: 1;
    box-shadow: 0 0 0 2px rgba(31, 79, 115, .22);
}

/* ── footer ── */
footer {
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px solid #ddd;
    font-size: .78rem;
    color: #aaa;
    text-align: center;
}

@media (max-width: 720px) {
    .page {
        margin: 0;
        padding: 1rem .9rem 2rem;
    }

    .left-rail {
        position: static;
        width: auto;
        margin-bottom: 1rem;
        left: auto;
        top: auto;
    }

    .left-rail .toggle-bar button {
        text-align: center;
    }

    .turn {
        grid-template-columns: 1fr;
        gap: .15rem;
    }

    .turn-speaker {
        white-space: normal;
        overflow: visible;
        text-overflow: clip;
    }

    .turn-time {
        white-space: normal;
    }

    .sidebar {
        width: min(95vw, 480px);
    }

    .timeline {
        width: calc(100vw - 1rem);
        bottom: .5rem;
        padding: .7rem .75rem .8rem;
    }

    .timeline-head {
        font-size: .72rem;
    }

    .speaker-lane {
        grid-template-columns: 3.6rem 1fr;
        gap: .4rem;
    }
}
"""

JS = """
var currentFontSize = 15;
var isTimelineDragging = false;
var isTimelineVisible = true;
var timelineZoom = 1;
var timelineWindowCenter = 0.5;

function showPanel(id) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('visible'));
    document.querySelectorAll('.toggle-bar button[data-panel]').forEach(b => b.classList.remove('active'));
    document.getElementById(id).classList.add('visible');
    document.querySelector('[data-panel="' + id + '"]').classList.add('active');
    updateTimeline();
}
function toggleTimes(btn) {
    var t = document.querySelector('.transcript');
    var on = t.classList.toggle('show-times');
    btn.classList.toggle('active', on);
}
function toggleTimeline(btn) {
    isTimelineVisible = !isTimelineVisible;
    document.body.classList.toggle('timeline-collapsed', !isTimelineVisible);
    if (btn) btn.classList.toggle('active', isTimelineVisible);
    updateTimeline();
}
function toggleSidebar() {
    var sb = document.getElementById('sidebar');
    var btn = document.querySelector('.sidebar-toggle');
    var open = sb.classList.toggle('open');
    btn.classList.toggle('active', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}
function increaseFontSize() {
    currentFontSize = Math.min(currentFontSize + 1, 24);
    updateTranscriptFontSize();
}
function decreaseFontSize() {
    currentFontSize = Math.max(currentFontSize - 1, 11);
    updateTranscriptFontSize();
}
function updateTranscriptFontSize() {
    document.documentElement.style.setProperty('--transcript-font-size', currentFontSize + 'px');
}
function clearHoverLinked() {
    document.querySelectorAll('.hover-linked').forEach(function(node) {
        node.classList.remove('hover-linked');
    });
}
function setHoverLinked(tuIdx) {
    clearHoverLinked();
    if (tuIdx == null || tuIdx === '') return;
    document.querySelectorAll('[data-tu-idx="' + tuIdx + '"]').forEach(function(node) {
        node.classList.add('hover-linked');
    });
}
function setupHoverSync() {
    document.addEventListener('mouseover', function(event) {
        var target = event.target.closest('[data-tu-idx]');
        if (!target) return;
        setHoverLinked(target.dataset.tuIdx);
    });
    document.addEventListener('mouseout', function(event) {
        var target = event.target.closest('[data-tu-idx]');
        if (!target) return;
        var related = event.relatedTarget && event.relatedTarget.closest ? event.relatedTarget.closest('[data-tu-idx]') : null;
        if (related && related.dataset.tuIdx === target.dataset.tuIdx) return;
        clearHoverLinked();
    });
}
function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}
function getTimelineWindow() {
    var span = 1 / timelineZoom;
    var start = clamp(timelineWindowCenter - (span / 2), 0, 1 - span);
    return { start: start, end: start + span, span: span };
}
function ratioToWindowPercent(ratio) {
    var windowInfo = getTimelineWindow();
    return ((ratio - windowInfo.start) / windowInfo.span) * 100;
}
function updateTimelineGeometry(timeline, focusRatio) {
    if (!timeline) return;
    if (typeof focusRatio === 'number' && Number.isFinite(focusRatio)) {
        timelineWindowCenter = focusRatio;
    }
    var windowInfo = getTimelineWindow();
    timeline.querySelectorAll('.timeline-marker').forEach(function(marker) {
        var ratio = parseFloat(marker.dataset.ratio || '0');
        var visible = ratio >= windowInfo.start && ratio <= windowInfo.end;
        marker.style.display = visible ? 'block' : 'none';
        if (visible) {
            marker.style.left = ratioToWindowPercent(ratio) + '%';
        }
    });
    timeline.querySelectorAll('.speaker-segment').forEach(function(segment) {
        var startRatio = parseFloat(segment.dataset.startRatio || '0');
        var endRatio = parseFloat(segment.dataset.endRatio || startRatio);
        var clippedStart = Math.max(startRatio, windowInfo.start);
        var clippedEnd = Math.min(endRatio, windowInfo.end);
        var visible = clippedEnd > clippedStart;
        segment.style.display = visible ? 'block' : 'none';
        if (visible) {
            segment.style.left = ratioToWindowPercent(clippedStart) + '%';
            segment.style.width = Math.max(((clippedEnd - clippedStart) / windowInfo.span) * 100, 0.35) + '%';
        }
    });
    var zoomLabel = timeline.querySelector('.timeline-zoom-label');
    if (zoomLabel) {
        zoomLabel.textContent = timelineZoom.toFixed(1) + 'x';
    }
}
function zoomTimeline(factor) {
    var timeline = document.getElementById('timeline');
    if (!timeline) return;
    var playhead = timeline.querySelector('.timeline-playhead');
    var focusRatio = playhead ? parseFloat(playhead.dataset.ratio || '0') : timelineWindowCenter;
    timelineZoom = clamp(Math.round((timelineZoom * factor) * 10) / 10, 1, 12);
    updateTimelineGeometry(timeline, focusRatio);
    updateTimeline();
}
function getVisiblePanel() {
    return document.querySelector('.panel.visible');
}
function getTurnByTuIdx(tuIdx) {
    var panel = getVisiblePanel();
    return panel ? panel.querySelector('.turn[data-tu-idx="' + tuIdx + '"]') : null;
}
function scrollToTuIdx(tuIdx) {
    if (tuIdx == null || tuIdx === '') return;
    var turn = getTurnByTuIdx(tuIdx);
    if (!turn) return;
    setHoverLinked(tuIdx);
    scrollToTurn(turn);
}
function scrollToTimelineUnit(event) {
    event.stopPropagation();
    var target = event.currentTarget || event.target.closest('[data-tu-idx]');
    if (!target) return;
    scrollToTuIdx(target.dataset.tuIdx);
}
function getTimedTurns() {
    var panel = getVisiblePanel();
    return panel ? Array.from(panel.querySelectorAll('.turn[data-begin-ms], .turn[data-end-ms]')) : [];
}
function getTrackMetrics(turns) {
    if (!turns.length) return null;
    var maxEnd = 0;
    turns.forEach(function(turn) {
        var begin = parseInt(turn.dataset.beginMs || '0', 10);
        var end = parseInt(turn.dataset.endMs || turn.dataset.beginMs || '0', 10);
        if (end > maxEnd) maxEnd = end;
        if (begin > maxEnd) maxEnd = begin;
    });
    return maxEnd > 0 ? { maxEnd: maxEnd } : null;
}
function getCurrentTurnFromScroll(turns) {
    var current = turns[0];
    var targetY = window.innerHeight * 0.42;
    var bestDistance = Infinity;
    turns.forEach(function(turn) {
        var rect = turn.getBoundingClientRect();
        var center = rect.top + rect.height / 2;
        var distance = Math.abs(center - targetY);
        if (distance < bestDistance) {
            bestDistance = distance;
            current = turn;
        }
    });
    return current;
}
function setTimelineState(timeline, currentTurn, metrics) {
    if (!timeline || !currentTurn || !metrics) return;
    var begin = parseInt(currentTurn.dataset.beginMs || '0', 10);
    var end = parseInt(currentTurn.dataset.endMs || currentTurn.dataset.beginMs || '0', 10);
    var ratio = metrics.maxEnd ? Math.min(Math.max(begin / metrics.maxEnd, 0), 1) : 0;
    updateTimelineGeometry(timeline, ratio);
    var playhead = timeline.querySelector('.timeline-playhead');
    if (playhead) {
        playhead.dataset.ratio = ratio;
        playhead.style.left = ratioToWindowPercent(ratio) + '%';
    }

    var currentLabel = timeline.querySelector('.timeline-current');
    var totalLabel = timeline.querySelector('.timeline-total');
    if (currentLabel) {
        currentLabel.textContent = formatMsLabel(begin, end);
    }
    if (totalLabel) {
        totalLabel.textContent = formatSingleMs(metrics.maxEnd);
    }

    timeline.querySelectorAll('.timeline-marker').forEach(function(marker) {
        marker.classList.toggle('active', marker.dataset.tuIdx === currentTurn.dataset.tuIdx);
    });
    timeline.querySelectorAll('.speaker-segment').forEach(function(segment) {
        segment.classList.toggle('active', segment.dataset.tuIdx === currentTurn.dataset.tuIdx);
    });
}
function getTurnForRatio(turns, metrics, ratio) {
    if (!turns.length || !metrics) return null;
    var targetMs = ratio * metrics.maxEnd;
    var bestTurn = turns[0];
    var bestDistance = Infinity;
    turns.forEach(function(turn) {
        var begin = parseInt(turn.dataset.beginMs || turn.dataset.endMs || '0', 10);
        var end = parseInt(turn.dataset.endMs || turn.dataset.beginMs || '0', 10);
        var center = begin + ((end - begin) / 2);
        var distance = Math.abs(center - targetMs);
        if (distance < bestDistance) {
            bestDistance = distance;
            bestTurn = turn;
        }
    });
    return bestTurn;
}
function scrollToTurn(turn) {
    if (!turn) return;
    var rect = turn.getBoundingClientRect();
    var targetTop = window.scrollY + rect.top - (window.innerHeight * 0.28);
    window.scrollTo({ top: Math.max(targetTop, 0), behavior: 'smooth' });
}
function getRatioFromPointer(event, track) {
    var rect = track.getBoundingClientRect();
    var clientX = event.clientX;
    if (clientX == null && event.touches && event.touches[0]) {
        clientX = event.touches[0].clientX;
    }
    if (clientX == null) return 0;
    var handleSize = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--timeline-handle-size')) || 14;
    var inset = handleSize / 2;
    var usableWidth = Math.max(rect.width - (inset * 2), 1);
    var localRatio = Math.min(Math.max((clientX - rect.left - inset) / usableWidth, 0), 1);
    var windowInfo = getTimelineWindow();
    return windowInfo.start + (localRatio * windowInfo.span);
}
function syncTimelineToRatio(ratio, shouldScroll) {
    var timeline = document.getElementById('timeline');
    if (!timeline) return;
    var turns = getTimedTurns();
    var metrics = getTrackMetrics(turns);
    if (!turns.length || !metrics) return;
    var turn = getTurnForRatio(turns, metrics, ratio);
    setTimelineState(timeline, turn, metrics);
    if (shouldScroll) {
        scrollToTurn(turn);
    }
}
function handleTimelinePointer(event) {
    var timeline = document.getElementById('timeline');
    if (!timeline) return;
    var track = timeline.querySelector('.timeline-track');
    if (!track) return;
    var ratio = getRatioFromPointer(event, track);
    syncTimelineToRatio(ratio, true);
}
function startTimelineDrag(event) {
    var timeline = document.getElementById('timeline');
    if (!timeline) return;
    isTimelineDragging = true;
    var playhead = timeline.querySelector('.timeline-playhead');
    if (playhead) playhead.classList.add('dragging');
    handleTimelinePointer(event);
}
function stopTimelineDrag() {
    if (!isTimelineDragging) return;
    isTimelineDragging = false;
    var timeline = document.getElementById('timeline');
    if (!timeline) return;
    var playhead = timeline.querySelector('.timeline-playhead');
    if (playhead) playhead.classList.remove('dragging');
}
function updateTimeline() {
    var timeline = document.getElementById('timeline');
    if (!timeline) return;
    if (!isTimelineVisible) {
        timeline.classList.add('hidden');
        return;
    }
    var turns = getTimedTurns();
    if (!turns.length) {
        timeline.classList.add('hidden');
        return;
    }
    timeline.classList.remove('hidden');
    var metrics = getTrackMetrics(turns);
    if (!metrics) {
        timeline.classList.add('hidden');
        return;
    }
    if (isTimelineDragging) return;
    setTimelineState(timeline, getCurrentTurnFromScroll(turns), metrics);
}
function formatSingleMs(ms) {
    if (!Number.isFinite(ms) || ms < 0) return '';
    var totalSeconds = Math.floor(ms / 1000);
    var hours = Math.floor(totalSeconds / 3600);
    var minutes = Math.floor((totalSeconds % 3600) / 60);
    var seconds = totalSeconds % 60;
    return hours
        ? hours + ':' + String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0')
        : minutes + ':' + String(seconds).padStart(2, '0');
}
function formatMsLabel(begin, end) {
    var b = formatSingleMs(begin);
    var e = formatSingleMs(end);
    return b && e && b !== e ? (b + ' - ' + e) : (b || e);
}
window.addEventListener('scroll', updateTimeline, { passive: true });
window.addEventListener('resize', updateTimeline);
window.addEventListener('load', function() {
    setupHoverSync();
    updateTimeline();
});
window.addEventListener('pointermove', function(event) {
    if (!isTimelineDragging) return;
    handleTimelinePointer(event);
});
window.addEventListener('pointerup', stopTimelineDrag);
window.addEventListener('pointercancel', stopTimelineDrag);
"""


def render_turns(turns, colour_map, is_jefferson, timings=None):
    parts = []
    prev_spk = None
    for i, (spk, text) in enumerate(turns):
        colour = colour_map.get(spk, "#999")
        show_speaker = spk != prev_spk
        if is_jefferson:
            rendered = f'<span class="jtext">{markup_jefferson(text)}</span>'
        else:
            rendered = html.escape(text)

        time_html = ""
        data_tu = ""
        if timings and i < len(timings):
            tu_idx, b, e = timings[i]
            b_str, e_str = fmt_ms(b), fmt_ms(e)
            if b_str or e_str:
                label = f"{b_str}–{e_str}" if b_str and e_str else (b_str or e_str)
                time_html = f'<span class="turn-time">{html.escape(label)}</span>'
            data_tu = f' data-tu-idx="{tu_idx}"'
            data_begin = f' data-begin-ms="{b}"' if b is not None else ""
            data_end = f' data-end-ms="{e}"' if e is not None else ""
        else:
            b = e = None
            data_begin = ""
            data_end = ""

        parts.append(
            f'<div class="turn"{data_tu}{data_begin}{data_end}>'
            f'<div class="turn-meta">'
            f'<span class="turn-speaker" style="color:{colour}">{html.escape(spk) if show_speaker else ""}</span>'
            f'{time_html}'
            f'</div>'
            f'<span class="turn-text">{rendered}</span>'
            f'</div>'
        )
        prev_spk = spk
    return "\n".join(parts)


def _latex_escape(text):
    text = str(text or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _latex_cell(text):
    parts = [_latex_escape(part.strip()) for part in str(text or "").splitlines() if part.strip()]
    return r" \newline ".join(parts) if parts else ""


def _latex_key_value_table(rows):
    body = "\n".join(
        f"{_latex_cell(key)} & {_latex_cell(value)} \\\\"
        for key, value in rows
        if value
    )
    return rf"""
\begin{{longtable}}{{>{{\RaggedRight\arraybackslash}}p{{0.27\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.67\textwidth}}}}
\rowcolor{{kiparlaSand}}
\textbf{{Campo}} & \textbf{{Valore}} \\
\endfirsthead
\rowcolor{{kiparlaSand}}
\textbf{{Campo}} & \textbf{{Valore}} \\
\endhead
{body}
\end{{longtable}}
"""


def _latex_participants_table(rows):
    body = "\n".join(
        " & ".join(_latex_cell(cell) for cell in row) + r" \\"
        for row in rows
    )
    return rf"""
\small
\setlength{{\tabcolsep}}{{4pt}}
\begin{{longtable}}{{>{{\RaggedRight\arraybackslash}}p{{0.11\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.16\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.11\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.18\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.11\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.22\textwidth}}}}
\rowcolor{{kiparlaSand}}
\textbf{{Codice}} & \textbf{{Occupazione}} & \textbf{{Genere}} & \textbf{{Regione}} & \textbf{{Età}} & \textbf{{Titolo di studio}} \\
\endfirsthead
\rowcolor{{kiparlaSand}}
\textbf{{Codice}} & \textbf{{Occupazione}} & \textbf{{Genere}} & \textbf{{Regione}} & \textbf{{Età}} & \textbf{{Titolo di studio}} \\
\endhead
{body}
\end{{longtable}}
\normalsize
"""


def _latex_transcript_table(turns, timings):
    rows = []
    prev_speaker = None
    for idx, (speaker, text) in enumerate(turns):
        time_label = ""
        if timings and idx < len(timings):
            _, begin_ms, end_ms = timings[idx]
            b_str, e_str = fmt_ms(begin_ms), fmt_ms(end_ms)
            time_label = f"{b_str}–{e_str}" if b_str and e_str else (b_str or e_str)
        rows.append((speaker if speaker != prev_speaker else "", time_label, text))
        prev_speaker = speaker

    body = "\n".join(
        " & ".join(_latex_cell(cell) for cell in row) + r" \\"
        for row in rows
    )
    return rf"""
\small
\setlength{{\tabcolsep}}{{5pt}}
\begin{{longtable}}{{>{{\RaggedRight\arraybackslash}}p{{0.14\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.18\textwidth}} >{{\RaggedRight\arraybackslash}}p{{0.58\textwidth}}}}
\rowcolor{{kiparlaSand}}
\textbf{{Parlante}} & \textbf{{Tempo unità}} & \textbf{{Testo}} \\
\endfirsthead
\rowcolor{{kiparlaSand}}
\textbf{{Parlante}} & \textbf{{Tempo unità}} & \textbf{{Testo}} \\
\endhead
{body}
\end{{longtable}}
\normalsize
"""


def build_pdf_markdown(code, label, turns, timings, conv, participants_map, speaker_order):
    meta_items = [
        ("Codice", code),
        ("Tipo", tr(conv.get("type", ""), TYPE_LABELS)),
        ("Durata", conv.get("duration", "")),
        ("Partecipanti", conv.get("participants-number", "")),
        ("Rapporto", tr(conv.get("participants-relationship", ""), RELATIONSHIP_LABELS)),
        ("Moderatore", conv.get("moderator", "")),
        ("Argomento", tr(conv.get("topic", ""), TOPIC_LABELS)),
        ("Anno", conv.get("year", "")),
        ("Punto di raccolta", conv.get("collection-point", conv.get("point", ""))),
    ]
    participant_rows = []
    for spk in speaker_order:
        p = participants_map.get(spk, {})
        participant_rows.append([
            spk,
            tr(p.get("occupation", ""), OCCUPATION_LABELS),
            p.get("gender", ""),
            p.get("birth-region", p.get("school-region", "")),
            p.get("age-range", ""),
            tr(p.get("study-level", ""), STUDY_LABELS),
        ])

    lines = [
        "---",
        "geometry: margin=1.6cm",
        "header-includes:",
        "  - \\usepackage{longtable}",
        "  - \\usepackage{array}",
        "  - \\usepackage[table]{xcolor}",
        "  - \\usepackage{booktabs}",
        "  - \\usepackage{microtype}",
        "  - \\usepackage{ragged2e}",
        "  - \\definecolor{kiparlaInk}{HTML}{24211C}",
        "  - \\definecolor{kiparlaAccent}{HTML}{7A5C36}",
        "  - \\definecolor{kiparlaSand}{HTML}{F2E8DA}",
        "  - \\definecolor{kiparlaLine}{HTML}{D9CCBA}",
        "  - \\AtBeginDocument{\\setlength{\\parindent}{0pt}\\setlength{\\parskip}{0.45em}\\renewcommand{\\arraystretch}{1.2}\\arrayrulecolor{kiparlaLine}\\color{kiparlaInk}}",
        "---",
        "",
        r"\begin{center}",
        rf"{{\LARGE\bfseries {_latex_escape(code)}}}",
        "",
        rf"{{\large\color{{kiparlaAccent}} {_latex_escape(label)}}}",
        r"\end{center}",
        "",
        "## Conversazione",
        "",
        _latex_key_value_table(meta_items),
        "",
        "## Partecipanti",
        "",
        _latex_participants_table(participant_rows),
        "",
        "## Trascrizione",
        "",
        _latex_transcript_table(turns, timings),
    ]
    return "\n".join(lines) + "\n"


def ensure_pdfs(
    output_path, code, conv, participants_map, speaker_order,
    orth_turns=None, jeff_turns=None, orth_timings=None, jeff_timings=None,
):
    """Generate transcript PDFs next to the HTML output tree and return relative hrefs."""
    output_path = Path(output_path).resolve()
    html_dir = output_path.parent
    html_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = html_dir.parent / "pdf" if html_dir.name == "html" else html_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    pdf_links = {}
    jobs = [
        ("orthographic", "ortografico", orth_turns, orth_timings),
        ("jefferson", "jefferson", jeff_turns, jeff_timings),
    ]

    for slug, label, turns, timings in jobs:
        if not turns:
            continue
        md_path = pdf_dir / f"{code}-{slug}.md"
        pdf_path = pdf_dir / f"{code}-{slug}.pdf"
        md_path.write_text(
            build_pdf_markdown(code, label, turns, timings, conv, participants_map, speaker_order),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "pandoc",
                md_path.name,
                "--pdf-engine=xelatex",
                "-V", "fontsize=11pt",
                "-o", pdf_path.name,
            ],
            cwd=pdf_dir,
            check=True,
        )
        md_path.unlink(missing_ok=True)
        pdf_links[slug] = os.path.relpath(pdf_path, start=html_dir).replace(os.sep, "/")

    return pdf_links


def ensure_shared_assets(output_path):
    """Write shared CSS/JS assets next to the HTML output tree and return relative hrefs."""
    output_path = Path(output_path).resolve()
    html_dir = output_path.parent
    html_dir.mkdir(parents=True, exist_ok=True)
    if html_dir.name == "html" and html_dir.parent.parent != html_dir.parent:
        assets_root = html_dir.parent.parent
    else:
        assets_root = html_dir
    css_dir = assets_root / "css"
    js_dir = assets_root / "js"
    css_dir.mkdir(parents=True, exist_ok=True)
    js_dir.mkdir(parents=True, exist_ok=True)

    css_path = css_dir / "linear2html.css"
    js_path = js_dir / "linear2html.js"
    css_path.write_text(CSS, encoding="utf-8")
    js_path.write_text(JS, encoding="utf-8")

    css_href = os.path.relpath(css_path, start=html_dir).replace(os.sep, "/")
    js_href = os.path.relpath(js_path, start=html_dir).replace(os.sep, "/")
    return css_href, js_href


def build_html(
    code, conv, participants_map, all_turns, orth_turns, jeff_turns,
    orth_timings=None, jeff_timings=None, timeline_units=None,
    css_href="css/linear2html.css", js_href="js/linear2html.js", pdf_links=None,
):
    e = lambda s: html.escape(str(s)) if s else ""

    # colour map across all speakers from both transcripts
    colour_map = {}
    for spk, _ in all_turns:
        if spk not in colour_map:
            colour_map[spk] = SPEAKER_COLOURS[len(colour_map) % len(SPEAKER_COLOURS)]

    # ── metadata ──
    meta_items = [
        ("codice",          code),
        ("tipo",            tr(conv.get("type", ""), TYPE_LABELS)),
        ("durata",          conv.get("duration", "")),
        ("partecipanti",    conv.get("participants-number", "")),
        ("rapporto",        tr(conv.get("participants-relationship", ""), RELATIONSHIP_LABELS)),
        ("moderatore",      conv.get("moderator", "")),
        ("argomento",       tr(conv.get("topic", ""), TOPIC_LABELS)),
        ("anno",            conv.get("year", "")),
        ("punto di raccolta", conv.get("collection-point", conv.get("point", ""))),
    ]
    meta_html = "\n".join(
        f'<span data-label="{e(lbl)}">{e(val)}</span>'
        for lbl, val in meta_items if val
    )

    # ── participant table ──
    speaker_list = list(colour_map.keys())
    rows = []
    for spk in speaker_list:
        p = participants_map.get(spk, {})
        colour = colour_map[spk]
        rows.append(
            f'<tr>'
            f'<td><span class="dot" style="background:{colour}"></span><strong>{e(spk)}</strong></td>'
            f'<td>{e(tr(p.get("occupation",""), OCCUPATION_LABELS))}</td>'
            f'<td>{e(p.get("gender",""))}</td>'
            f'<td>{e(p.get("birth-region", p.get("school-region","")))}</td>'
            f'<td>{e(p.get("age-range",""))}</td>'
            f'<td>{e(tr(p.get("study-level",""), STUDY_LABELS))}</td>'
            f'</tr>'
        )
    table_html = (
        '<table><thead><tr>'
        '<th>Codice</th><th>Occupazione</th><th>Genere</th>'
        '<th>Regione</th><th>Età</th><th>Titolo di studio</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    ) if rows else ""

    pdf_links = pdf_links or {}
    downloads_html = ""
    download_items = []
    if pdf_links.get("orthographic"):
        download_items.append(f'<a href="{e(pdf_links["orthographic"])}" download>{e(code)} ortografico PDF</a>')
    if pdf_links.get("jefferson"):
        download_items.append(f'<a href="{e(pdf_links["jefferson"])}" download>{e(code)} Jefferson PDF</a>')
    if download_items:
        downloads_html = f'<div class="download-links">{"".join(download_items)}</div>'

    # ── panels ──
    panels = []
    default_panel = None

    if orth_turns is not None:
        panels.append(("panel-orth", "ortografico", render_turns(orth_turns, colour_map, False, orth_timings)))
        default_panel = default_panel or "panel-orth"

    if jeff_turns is not None:
        panels.append(("panel-jeff", "Jefferson", render_turns(jeff_turns, colour_map, True, jeff_timings)))
        default_panel = default_panel or "panel-jeff"

    toggle_html = ""
    panels_html = ""

    transcript_buttons = []
    if len(panels) > 1:
        transcript_buttons = [
            f'<button data-panel="{pid}" onclick="showPanel(\'{pid}\')">{lbl}</button>'
            for pid, lbl, _ in panels
        ]

    time_button = ""
    if orth_timings or jeff_timings:
        time_button = '<button onclick="toggleTimes(this)">tempi</button>'

    timeline_button = ""
    if timeline_units:
        timeline_button = '<button type="button" class="active" onclick="toggleTimeline(this)">timeline</button>'

    font_controls = (
        '<button type="button" onclick="decreaseFontSize()">A-</button>'
        '<button type="button" onclick="increaseFontSize()">A+</button>'
    )

    if transcript_buttons or time_button or timeline_button or font_controls:
        sep = '<span style="color:#ddd;margin:0 .3rem">|</span>' if transcript_buttons and time_button else ""
        toggle_html = (
            f'<div class="toggle-bar">'
            f'{"".join(transcript_buttons)}<span class="toggle-sep">{sep}</span>{time_button}{timeline_button}{font_controls}'
            f'</div>'
        )

    for pid, _, content in panels:
        visible = ' visible' if pid == default_panel else ''
        panels_html += f'<div class="panel{visible}" id="{pid}">\n{content}\n</div>\n'

    timeline_html = ""
    if timeline_units:
        valid_points = []
        max_end = 0
        for unit in timeline_units:
            begin_ms = unit["begin"]
            end_ms = unit["end"]
            point = begin_ms if begin_ms is not None else end_ms
            finish = end_ms if end_ms is not None else begin_ms
            if point is None or finish is None:
                continue
            valid_points.append((unit["tu_idx"], point))
            max_end = max(max_end, finish)

        if valid_points and max_end > 0:
            marker_html = "".join(
                f'<span class="timeline-marker" data-tu-idx="{tu_idx}" data-ratio="{(point / max_end):.6f}" '
                f'onpointerdown="event.stopPropagation()" onclick="scrollToTimelineUnit(event)"></span>'
                for tu_idx, point in valid_points
            )
            speaker_rows = []
            for spk in speaker_list:
                segments = []
                for unit in timeline_units:
                    if unit["speaker"] != spk:
                        continue
                    seg_start = unit["begin"] if unit["begin"] is not None else unit["end"]
                    seg_end = unit["end"] if unit["end"] is not None else unit["begin"]
                    if seg_start is None or seg_end is None:
                        continue
                    seg_width = max(((seg_end - seg_start) / max_end) * 100, 0.35)
                    segments.append(
                        f'<span class="speaker-segment" data-tu-idx="{unit["tu_idx"]}" '
                        f'data-start-ratio="{(seg_start / max_end):.6f}" '
                        f'data-end-ratio="{(seg_end / max_end):.6f}" '
                        f'style="background:{colour_map[spk]}" '
                        f'onpointerdown="event.stopPropagation()" onclick="scrollToTimelineUnit(event)"></span>'
                    )
                if segments:
                    speaker_rows.append(
                        f'<div class="speaker-lane">'
                        f'<span class="speaker-label" style="color:{colour_map[spk]}">{e(spk)}</span>'
                        f'<div class="speaker-track">{"".join(segments)}</div>'
                        f'</div>'
                    )
            speaker_map_html = f'<div class="speaker-map">{"".join(speaker_rows)}</div>' if speaker_rows else ""
            timeline_html = f"""
<div class="timeline" id="timeline">
  <div class="timeline-head">
    <span class="timeline-current">0:00</span>
    <span class="timeline-controls">
      <button type="button" onclick="zoomTimeline(0.8)">−</button>
      <span class="timeline-zoom-label">1.0x</span>
      <button type="button" onclick="zoomTimeline(1.25)">+</button>
      <span class="timeline-total">{e(fmt_ms(max_end))}</span>
    </span>
  </div>
  {speaker_map_html}
  <div class="timeline-track" onpointerdown="startTimelineDrag(event)">
    <div class="timeline-track-inner">
      <div class="timeline-rail"></div>
      {marker_html}
      <div class="timeline-playhead" style="left:0%" onpointerdown="startTimelineDrag(event)"></div>
    </div>
  </div>
</div>
"""

    sidebar_html = f"""
<aside class="sidebar" id="sidebar">
  <button class="sidebar-close" type="button" onclick="toggleSidebar()" aria-label="Chiudi informazioni">×</button>
  <section class="sidebar-section">
    <h2>Conversazione</h2>
    <div class="meta-grid">{meta_html}</div>
  </section>
  <section class="sidebar-section participants">
    <h2>Partecipanti</h2>
    {table_html}
  </section>
</aside>
"""

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(code)} – KIParla</title>
<link rel="stylesheet" href="{e(css_href)}">
</head>
<body{(' class="has-timeline"' if timeline_html else '')}>
<button class="sidebar-toggle" type="button" onclick="toggleSidebar()" aria-controls="sidebar" aria-expanded="false">info</button>
{sidebar_html}
<div class="page">
  <aside class="left-rail">
    <h1>{e(code)}</h1>
    {toggle_html}
    {downloads_html}
  </aside>

  <div class="transcript-shell">
    <section class="transcript">
      {panels_html}
    </section>
  </div>

  <footer>KIParla – <a href="https://www.kiparla.it/">kiparla.it</a></footer>

</div>
{timeline_html}
<script src="{e(js_href)}"></script>
</body>
</html>
"""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orthographic",  help="Orthographic linear .txt file")
    ap.add_argument("--jefferson",     help="Jefferson linear .txt file")
    ap.add_argument("--tsv",           help="vert.tsv file for per-turn begin/end timings (optional)")
    ap.add_argument("--conversations", required=True, help="conversations.tsv")
    ap.add_argument("--participants",  required=True, help="participants.tsv")
    ap.add_argument("--output",        help="Output .html file")
    ap.add_argument(
        "--artifacts-root",
        help="Root of KIParla-artifacts; writes to MODULE/html/CODE.html if --output is omitted",
    )
    ap.add_argument(
        "--module",
        help="Module name for artifacts layout, e.g. KIP or ParlaTO. Defaults to metadata directory name.",
    )
    args = ap.parse_args()

    if not args.orthographic and not args.jefferson:
        ap.error("at least one of --orthographic or --jefferson is required")
    if not args.output and not args.artifacts_root:
        ap.error("either --output or --artifacts-root is required")

    src = args.orthographic or args.jefferson
    code = Path(src).stem.split("_")[0]
    module_name = args.module or infer_module_name(args.conversations)
    output_path = args.output
    if not output_path:
        output_path = str(
            Path(args.artifacts_root).resolve() / module_name / "html" / f"{code}.html"
        )

    conversations = load_tsv(args.conversations)
    participants  = load_tsv(args.participants)
    conv          = conversations.get(code, {})
    if not conv:
        print(f"[warn] conversation {code} not found in {args.conversations}", file=sys.stderr)

    orth_turns = load_turns(args.orthographic) if args.orthographic else None
    jeff_turns = load_turns(args.jefferson)    if args.jefferson    else None

    # build unified turn list for colour assignment (preserve first-appearance order)
    all_turns: list[tuple[str, str]] = []
    seen: set[str] = set()
    for turns in filter(None, [orth_turns, jeff_turns]):
        for spk, text in turns:
            if spk not in seen:
                all_turns.append((spk, text))
                seen.add(spk)

    orth_timings = None
    jeff_timings = None
    timeline_units = None
    if args.tsv:
        timeline_units = load_tsv_units(args.tsv)
        if jeff_turns is not None:
            jeff_timings = align_turns_to_units(jeff_turns, timeline_units, "jeff_text")
        if orth_turns is not None:
            orth_timings = align_turns_to_units(orth_turns, timeline_units, "orth_text")

    css_href, js_href = ensure_shared_assets(output_path)
    speaker_order = list(dict.fromkeys(spk for spk, _ in all_turns))
    pdf_links = ensure_pdfs(
        output_path, code, conv, participants, speaker_order,
        orth_turns=orth_turns, jeff_turns=jeff_turns,
        orth_timings=orth_timings, jeff_timings=jeff_timings,
    )

    out = build_html(
        code, conv, participants, all_turns, orth_turns, jeff_turns,
        orth_timings=orth_timings, jeff_timings=jeff_timings, timeline_units=timeline_units,
        css_href=css_href, js_href=js_href, pdf_links=pdf_links,
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  wrote {output_path}")


if __name__ == "__main__":
    main()
