"""Render frequency-table CSV artifacts into a single HTML validation report.

This is a validation aid: it reads the per-question ``frequency_tables/*.csv``
files produced by the frequency stage (and any ``open_text_outputs/*.csv``) and
renders them into one self-contained ``report.html`` so the computed counts and
percentages can be eyeballed for accuracy. The eventual presentation output is a
MS Word document produced elsewhere; this report deliberately favours a faithful,
complete rendering of every value over visual polish.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .frequencies import PCT_DECIMALS_MAX, STAT_KEYS

# Columns carried per question (constant across its rows) vs. per response row.
_QUESTION_LEVEL = ("question_key", "question_id", "question_text", "question_type")


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _natural_question_key(question_id: str, question_key: str) -> tuple:
    """Sort key approximating survey order from a data export tag like 'Q1.10'.

    'Q1.2' -> (0, [1, 2]); non-numeric tags (e.g. 'Q_DataPolicyViolations')
    sort after numbered ones, then alphabetically.
    """
    nums = re.findall(r"\d+", question_id or "")
    if nums:
        return (0, [int(n) for n in nums], question_key)
    return (1, [], question_id or question_key)


def _fmt_pct(value: str, decimals: int = 2) -> str:
    try:
        return f"{float(value):.{decimals}f}%"
    except (ValueError, TypeError):
        return html.escape(str(value))


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _json_script(data: Any, elem_id: str) -> str:
    """Embed JSON in a <script> tag, safe against '</script>' appearing in the data.

    json.dumps doesn't escape '<', '>', '&', so a survey response containing the
    literal text '</script>' would otherwise truncate the tag and corrupt the page.
    \\uXXXX escapes are transparent to JSON.parse.
    """
    text = json.dumps(data)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/json" id="{html.escape(elem_id, quote=True)}">{text}</script>'


# --- presentation: stat selection -----------------------------------------

_PCT_FIELD = {"valid": "valid_pct", "eligible": "eligible_pct", "total": "total_pct"}
_N_FIELD = {"valid": "valid_n", "eligible": "eligible_n", "total": "total_n"}
_STAT_LABEL = {
    "n": "n", "valid_n": "Valid n", "valid_pct": "Valid %", "eligible_n": "Eligible n",
    "eligible_pct": "Eligible %", "total_n": "Total n", "total_pct": "Total %",
    "pct": "%", "base_n": "Base n",
}
# Labels for the stat *toggles* only. "pct"/"base_n" are aliases that resolve to
# whichever base is featured, so labelling a toggle with _STAT_LABEL would repeat
# the concrete stat it resolves to ("Eligible %" twice, "Eligible n" twice). Column
# headers still use _STAT_LABEL -- a header should name the number it actually shows.
_STAT_CHIP_LABEL = {"pct": "Featured %", "base_n": "Featured base n"}
# What each column means, shown in the report's "statistic definitions" panel. The
# three bases mirror the semantics documented in frequencies.py.
_STAT_DEFINITION = {
    "n": "Respondents who chose this response option.",
    "valid_n": "Denominator: respondents who answered this question.",
    "valid_pct": "n ÷ Valid n — share of those who answered.",
    "eligible_n": (
        "Denominator: respondents shown this question per its display logic "
        "(all respondents when the question has no display logic)."
    ),
    "eligible_pct": "n ÷ Eligible n — share of those who were asked.",
    "total_n": "Denominator: all survey respondents.",
    "total_pct": "n ÷ Total n — prevalence across the whole sample.",
    "pct": "Whichever percentage this question's reporting base features.",
    "base_n": "Whichever base count this question's reporting base features.",
}
# One percentage, following the question's reporting base -- matching the crosstab
# default. The concrete per-base stats stay selectable for side-by-side comparison.
_DEFAULT_FLAT_STATS = ["n", "pct"]
_DEFAULT_CELL_STATS = ["n", "pct"]
_PRES_DEFAULT = {
    "show_code": True, "orientation": "columns",
    "overall": False, "response_total": False, "stats": None,
    "pct_decimals": 2, "hide_codes": [],
}
# Written by the frequencies stage, which owns them: they decide which rows exist
# rather than how existing rows look. The manifest keeps them in their own map;
# the report merges both into one per-table options dict, and the browser echoes
# them into the *question* config block rather than a table spec.
_FREQ_DEFAULT = {"include_empty_codes": False, "sort_by": "auto"}
# Selectable stats, in display order. "base_n" is deliberately absent: it resolves
# to whichever base is featured, which a crosstab already prints in its group
# header and a flat table can name outright, so it never earned a toggle. It stays
# accepted in config for back-compat.
_STAT_ORDER = ["n", "valid_n", "valid_pct", "eligible_n", "eligible_pct", "total_n", "total_pct", "pct"]
_REPORT_BASES = ["valid", "eligible", "total"]


def _num(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _is_mutually_exclusive(rows: list[dict]) -> bool:
    """Whether each respondent contributes to exactly one response option.

    Multi-select questions let one respondent pick several options, so their per
    -option counts sum past valid_n. question_type is "MC" for both kinds, so the
    counts themselves are the only signal available in the frequency table.
    """
    if not rows:
        return True
    return sum(_num(r.get("n")) for r in rows) <= _num(rows[0].get("valid_n")) + 0.5


_BASE_NAME = {"valid": "Valid", "eligible": "Eligible", "total": "Total"}


def _base_caption(rows: list[dict], report_base: str, hidden: int) -> str:
    """The '· Base: Eligible n = 101' clause of a question's caption line.

    With one percentage column this line is the only place the denominator is
    named, so it has to state the base in force and its size -- including the
    smaller size after rows are hidden. Mirrored by baseCaption() in the JS.
    """
    if not report_base or not rows:
        return ""
    name = _BASE_NAME.get(report_base, report_base)
    value = rows[0].get(_N_FIELD.get(report_base, "eligible_n"), "")
    if value == "":
        return ""
    note = f" ({hidden} hidden)" if hidden > 0 else ""
    return f" &middot; Base: {_esc(name)} n = {_esc(value)}{note}"


def _numeric_sort_key(value: str) -> tuple[int, float | str]:
    """Order codes as numbers where they are numbers, as text otherwise."""
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def _code_choices(rows: list[dict], universe: list[dict] | None = None) -> list[dict[str, str]]:
    """The response codes a table can show, for the hide picker.

    Offering the actual codes with their labels beats typing a sentinel from
    memory: "-1" is a genuine "Other" option in some exports, so what counts as
    N/A is a per-survey judgement rather than something to hardcode.

    Every defined code is listed, not just the ones somebody chose, so the list
    does not change out from under you when zero-response rows are switched on.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows:
        code = str(r.get("response_code", "")).strip()
        if code and code not in seen:
            seen.add(code)
            out.append({"code": code, "label": r.get("response_label", "")})
    for entry in universe or []:
        for code, label in entry.get("codes") or []:
            code = str(code).strip()
            if code and code not in seen:
                seen.add(code)
                out.append({"code": code, "label": label})
    # By code, not by the table's row order: the picker is a lookup ("hide -1"),
    # and a list that reorders itself whenever the table is re-sorted is one you
    # have to re-scan every time. Numbers sort numerically, so 10 follows 9;
    # write-in "codes" are verbatim text and sort after the coded ones.
    out.sort(key=lambda c: _numeric_sort_key(c["code"]))
    return out


def _apply_hidden(rows: list[dict], hide_codes: list[str]) -> list[dict]:
    """Drop hidden response codes and rebase Valid n onto what remains.

    Valid n is defined as the number who answered; once rows are hidden it should
    mean "chose one of the responses shown", so it becomes the sum of the visible
    counts and Valid % is recomputed against it. Eligible/Total are prevalence
    bases over people, not over the displayed options, so they are left alone.
    """
    if not hide_codes:
        return rows
    hidden = set(hide_codes)
    visible = [r for r in rows if str(r.get("response_code", "")).strip() not in hidden]
    if not visible or len(visible) == len(rows) or not _is_mutually_exclusive(rows):
        return visible
    new_valid = sum(_num(r.get("n")) for r in visible)
    out = []
    for r in visible:
        r = dict(r)
        r["valid_n"] = int(new_valid) if new_valid == int(new_valid) else new_valid
        r["valid_pct"] = round(_num(r.get("n")) / new_valid * 100.0, 10) if new_valid else 0.0
        out.append(r)
    return out


def _constants_blob() -> str:
    """Shared enums/labels/defaults for the in-browser JS, sourced from the
    same constants the Python renderers use so the two can't drift apart."""
    data = {
        "stat_labels": _STAT_LABEL,
        "stat_chip_labels": _STAT_CHIP_LABEL,
        "stat_definitions": _STAT_DEFINITION,
        "stat_order": _STAT_ORDER,
        "alias_stats": sorted(_STAT_CHIP_LABEL),
        "report_bases": _REPORT_BASES,
        "pct_decimals_max": PCT_DECIMALS_MAX,
        # Accepted values, so the browser validates a pasted config against the
        # same vocabulary the config validator uses.
        "stat_keys": sorted(STAT_KEYS),
        "orientations": ["columns", "rows"],
        # Row orders the browser can impose itself, in the order the control
        # lists them. Sourced from the frequencies stage's own vocabulary so a
        # new mode cannot be added there without appearing here.
        "row_orders": [
            ["survey_order", "Survey order"],
            ["code_asc", "Code (low to high)"],
            ["code_desc", "Code (high to low)"],
            ["count_desc", "Count (high to low)"],
            ["count_asc", "Count (low to high)"],
            ["response_order", "Custom order"],
        ],
        "positions": [False, "before", "after"],
        "pct_field": _PCT_FIELD,
        "n_field": _N_FIELD,
        "default_flat_stats": _DEFAULT_FLAT_STATS,
        "default_cell_stats": _DEFAULT_CELL_STATS,
    }
    return _json_script(data, "rr-constants")


def _stat_field(stat: str, report_base: str) -> str:
    """Resolve aliases 'pct'/'base_n' to the featured report_base field."""
    if stat == "pct":
        return _PCT_FIELD.get(report_base, "eligible_pct")
    if stat == "base_n":
        return _N_FIELD.get(report_base, "eligible_n")
    return stat


def _stat_label(stat: str, report_base: str) -> str:
    if stat in ("pct", "base_n"):
        return _STAT_LABEL.get(_stat_field(stat, report_base), stat)
    return _STAT_LABEL.get(stat, stat)


def _stat_value(row: dict | None, stat: str, report_base: str, decimals: int = 2) -> str:
    field = _stat_field(stat, report_base)
    val = (row or {}).get(field, "")
    return _fmt_pct(val, decimals) if field.endswith("_pct") else _esc(val)


def _aggregate_rows(rows: list[dict], report_base: str) -> dict:
    """Synthetic 'Total' row: sum n and percentages; keep base counts constant."""
    if not rows:
        return {}
    agg = dict(rows[0])

    def _sum(field: str) -> float:
        total = 0.0
        for r in rows:
            try:
                total += float(r.get(field, "") or 0)
            except (ValueError, TypeError):
                pass
        return total

    agg["n"] = int(_sum("n"))
    for f in ("valid_pct", "eligible_pct", "total_pct"):
        agg[f] = round(_sum(f), 2)
    return agg


def _cell_html(row: dict | None, stats: list[str], report_base: str, decimals: int = 2) -> str:
    if row is None:
        return '<td class="num">&mdash;</td>'
    if len(stats) == 1:
        return f'<td class="num">{_stat_value(row, stats[0], report_base, decimals)}</td>'
    parts = []
    for i, stat in enumerate(stats):
        val = _stat_value(row, stat, report_base, decimals)
        parts.append(
            val if i == 0
            else f'<span class="meta">{_esc(_stat_label(stat, report_base))}</span> {val}'
        )
    return '<td class="num">' + "<br>".join(parts) + "</td>"


_STYLE = """
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 1000px; color: #1a1a1a; line-height: 1.4; }
h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 0.25rem; border-bottom: 2px solid #ddd; padding-bottom: 0.2rem; }
.summary { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px; padding: 0.75rem 1rem; margin: 1rem 0; }
.meta { color: #555; font-size: 0.85rem; margin-bottom: 0.5rem; }
.qtext { font-weight: 400; color: #333; }
.badge { display: inline-block; background: #fff3cd; color: #7a5c00; border: 1px solid #ffe69c;
         border-radius: 4px; padding: 0 0.4rem; font-size: 0.75rem; margin-left: 0.4rem; }
table { border-collapse: collapse; width: 100%; margin-bottom: 0.5rem; font-size: 0.9rem; }
th, td { border: 1px solid #d0d7de; padding: 0.3rem 0.55rem; text-align: left; vertical-align: top; }
th { background: #f0f3f6; }
td.num, th.num { text-align: right; white-space: nowrap; }
tbody tr:nth-child(even) { background: #fafbfc; }
nav ol { columns: 2; font-size: 0.9rem; }
nav a { text-decoration: none; }
a.top { font-size: 0.75rem; color: #888; margin-left: 0.5rem; }
details { margin: 0.5rem 0; }
summary { cursor: pointer; font-weight: 600; }
table.writein { width: auto; max-width: 100%; margin-bottom: 1.5rem; background: #fcfcfd; }
table.writein th { background: #eef1f4; }
.rr-tools { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px;
            padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; font-size: 0.85rem; }
.rr-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 1rem; margin: 0.25rem 0; }
.rr-row label.rr-field { display: flex; align-items: center; gap: 0.3rem; white-space: nowrap; }
.rr-chips { display: flex; flex-wrap: wrap; gap: 0.15rem 0.7rem; }
.rr-chips label { display: flex; align-items: center; gap: 0.25rem; white-space: nowrap; }
.rr-tools select { font-size: 0.85rem; }
.rr-tools .rr-note { color: #888; font-size: 0.78rem; }
.rr-panel > summary { font-size: 0.85rem; font-weight: 600; cursor: pointer; }
.rr-panel[open] > summary { margin-bottom: 0.35rem; }
.rr-tools input[type="checkbox"]:indeterminate { opacity: 0.6; }
.rr-snippet summary { font-size: 0.85rem; font-weight: 600; }
.rr-snippet-body { width: 100%; box-sizing: border-box; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                    font-size: 0.8rem; margin: 0.4rem 0; padding: 0.4rem; }
.rr-snippet-hint { font-size: 0.78rem; color: #555; margin-bottom: 0.3rem; }
.rr-copy-btn { font-size: 0.8rem; padding: 0.15rem 0.6rem; cursor: pointer; }
.rr-copy-btn:disabled { cursor: not-allowed; opacity: 0.6; }
.rr-note.rr-bad { color: #b3261e; }
.rr-chip-group { display: flex; align-items: center; gap: 0.15rem 0.7rem; flex-wrap: wrap; }
.rr-chip-group + .rr-chip-group { border-left: 1px solid #d0d7de; padding-left: 0.7rem; }
.rr-chip-group > .rr-group-name { color: #666; font-size: 0.78rem; text-transform: uppercase;
                                  letter-spacing: 0.03em; }
.rr-defs summary { font-size: 0.85rem; font-weight: 600; }
.rr-defs table { width: auto; max-width: 100%; margin: 0.4rem 0; font-size: 0.8rem; background: #fff; }
.rr-defs td:first-child { white-space: nowrap; font-weight: 600; }
.rr-defs .rr-defs-note { font-size: 0.78rem; color: #555; margin-top: 0.3rem; }
th.rr-sortable { cursor: pointer; user-select: none; }
th.rr-sortable:hover { background: #e4e9ef; }
th.rr-sortable:focus-visible { outline: 2px solid #0969da; outline-offset: -2px; }
th.rr-sortable .rr-arrow { color: #57606a; font-size: 0.75rem; margin-left: 0.2rem; }
.rr-snippet-part + .rr-snippet-part { margin-top: 0.6rem; padding-top: 0.5rem;
                                       border-top: 1px solid #e1e4e8; }
.rr-tools input.rr-num { width: 4rem; font-size: 0.85rem; }
.rr-hide summary { font-size: 0.85rem; font-weight: 600; cursor: pointer; }
/* Write-in responses arrive as their own "code", so a question can offer dozens
   of long verbatim choices; cap the list and clip each label to keep it usable. */
.rr-hide .rr-chips { margin-top: 0.3rem; max-height: 14rem; overflow-y: auto;
                     display: block; column-count: 2; column-gap: 1.2rem; }
.rr-hide .rr-chips label { display: flex; align-items: baseline; gap: 0.3rem;
                           break-inside: avoid; margin-bottom: 0.1rem; }
.rr-code-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 700px) { .rr-hide .rr-chips { column-count: 1; } }
#rr-global:empty { display: none; }
"""


# JS port of the presentation logic above (_stat_field/_stat_label/_stat_value,
# _aggregate_rows, _cell_html, the flat-question renderer, and the grouped-table
# pivot), driven by the per-section JSON data blobs. Rendering is done via DOM
# APIs (createElement/textContent) rather than string concatenation so no JS-side
# HTML escaper is needed for survey response text embedded in the data.
_SCRIPT = """
function rrInit() {
  var constNode = document.getElementById("rr-constants");
  if (!constNode) { return; }
  var constants = JSON.parse(constNode.textContent);
  var POSITION_OPTS = [["", "None"], ["before", "Before"], ["after", "After"]];
  var BASE_OPTS = constants.report_bases.map(function (b) {
    return [b, b.charAt(0).toUpperCase() + b.slice(1)];
  });

  // Every section registers what it contributes to the report-wide config.
  var configSections = [];
  function registerSection(entry) { configSections.push(entry); }
  var refreshFullConfig = function () {};

  // Report-level controls drive every table at once. A section registers one
  // accessor per option it can be told to change; the control reads them to see
  // whether the tables agree and writes to all of them. Options that only make
  // sense for one table (hide_codes names this question's codes) are not
  // registered, so no global control appears for them.
  var globalSubs = {};
  var globalInputs = {};
  function registerGlobal(key, setter, getter) {
    (globalSubs[key] || (globalSubs[key] = [])).push({ set: setter, get: getter });
  }
  function sameValue(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) { return JSON.stringify(a) === JSON.stringify(b); }
    return a === b;
  }
  // The value every table agrees on, or undefined when they differ -- which the
  // controls show as "(mixed)" rather than silently claiming one of them.
  function globalConsensus(key) {
    var subs = globalSubs[key] || [];
    if (!subs.length) { return undefined; }
    var first = subs[0].get();
    return subs.every(function (s) { return sameValue(s.get(), first); }) ? first : undefined;
  }
  function setGlobal(key, value) {
    (globalSubs[key] || []).forEach(function (s) { s.set(value); });
  }
  // Called whenever one table changes on its own, so the report-level control
  // stops claiming a value the tables no longer share.
  var syncGlobals = function () {};

  function statField(stat, reportBase) {
    if (stat === "pct") { return constants.pct_field[reportBase] || "eligible_pct"; }
    if (stat === "base_n") { return constants.n_field[reportBase] || "eligible_n"; }
    return stat;
  }
  function statLabel(stat, reportBase) {
    var field = (stat === "pct" || stat === "base_n") ? statField(stat, reportBase) : stat;
    return constants.stat_labels[field] || stat;
  }
  function isAlias(stat) { return constants.alias_stats.indexOf(stat) !== -1; }
  // Toggle label. Aliases get their own name so they don't read as a duplicate of the
  // concrete stat they currently resolve to; column headers keep using statLabel().
  function chipLabel(stat, reportBase) {
    return constants.stat_chip_labels[stat] || statLabel(stat, reportBase);
  }
  function chipTitle(stat, reportBase) {
    if (!isAlias(stat)) { return constants.stat_definitions[stat] || ""; }
    return "Tracks this question's reporting base (currently " +
      statLabel(stat, reportBase) + ").";
  }
  function fmtPct(raw, decimals) {
    if (raw === null || raw === undefined || raw === "") { return ""; }
    var n = Number(raw);
    if (Number.isNaN(n)) { return String(raw); }
    return n.toFixed(decimals === undefined ? 2 : decimals) + "%";
  }
  function statValue(row, stat, reportBase, decimals) {
    var field = statField(stat, reportBase);
    var val = row ? row[field] : undefined;
    if (val === undefined || val === null) { val = ""; }
    return field.slice(-4) === "_pct" ? fmtPct(val, decimals) : String(val);
  }
  function round2(x) { return Math.round(x * 100) / 100; }
  function sumField(rows, field) {
    var total = 0;
    for (var i = 0; i < rows.length; i++) {
      var raw = rows[i][field];
      if (raw === undefined || raw === null || raw === "") { continue; }
      var v = Number(raw);
      if (!Number.isNaN(v)) { total += v; }
    }
    return total;
  }
  // Port of _is_mutually_exclusive / _apply_hidden. Multi-select questions let a
  // respondent pick several options, so their counts sum past valid_n and the
  // "chose a shown response" count isn't derivable -- rebasing is skipped there.
  function isMutuallyExclusive(rows) {
    if (!rows.length) { return true; }
    var total = 0;
    rows.forEach(function (r) { total += Number(r.n) || 0; });
    return total <= (Number(rows[0].valid_n) || 0) + 0.5;
  }
  function dropEmpty(rows, includeEmpty) {
    if (includeEmpty) { return rows; }
    var kept = rows.filter(function (r) { return (Number(r.n) || 0) !== 0; });
    return kept.length ? kept : rows;
  }
  var K = "\u0001";
  // A CSV describes answers people gave, so a code nobody chose leaves no trace in
  // it. The universe blob carries what the question *defines*, which is what lets
  // the checkbox add these rows here rather than only asking for a re-run.
  //
  // Position matters: appending the missing codes would scatter a rating scale.
  // So when a column's rows already run in survey order, the block is rebuilt in
  // that order; when they don't (a count sort), the zeros go last, which is where
  // a count sort puts them anyway.
  function fillEmpty(rows, universe, includeEmpty) {
    if (!includeEmpty || !universe || !universe.length) { return rows; }
    var byBlock = {}, blockOrder = [];
    var groups = [], groupSeen = {};
    rows.forEach(function (r) {
      var g = r.group_codes || "";
      if (!(g in groupSeen)) { groupSeen[g] = true; groups.push(g); }
      var key = g + K + (r.column || "");
      if (!(key in byBlock)) { byBlock[key] = []; blockOrder.push(key); }
      byBlock[key].push(r);
    });
    function blockFor(g, u) {
      var existing = byBlock[g + K + u.column] || [];
      var idx = {}, have = {};
      u.codes.forEach(function (c, i) { idx[c[0]] = i; });
      existing.forEach(function (r) { have[String(r.response_code || "")] = r; });
      var missing = u.codes.filter(function (c) { return !(c[0] in have); });
      if (!missing.length) { return existing; }
      // Bases are respondent counts, so they are copied from a sibling row -- one
      // in the same column where there is one, else any row of the same group.
      var sibling = existing[0] || null;
      if (!sibling) {
        for (var i = 0; i < blockOrder.length && !sibling; i++) {
          if (blockOrder[i].indexOf(g + K) === 0) { sibling = byBlock[blockOrder[i]][0]; }
        }
      }
      function synth(code, label) {
        var r = {};
        if (sibling) { for (var k in sibling) { r[k] = sibling[k]; } }
        r.column = u.column;
        r.attribute = u.attribute || "";
        r.response_code = code;
        r.response_label = label;
        r.n = 0;
        r.valid_pct = 0; r.eligible_pct = 0; r.total_pct = 0;
        // A single-answer column with no rows at all had no answers, so its Valid n
        // is genuinely zero; a multi-select's is the question-level total, which
        // the sibling already carries.
        if (!existing.length && !u.multi_select) { r.valid_n = 0; }
        return r;
      }
      var known = existing.filter(function (r) { return String(r.response_code || "") in idx; });
      var inOrder = known.every(function (r, i) {
        return i === 0 || idx[String(known[i - 1].response_code)] < idx[String(r.response_code)];
      });
      if (!inOrder) {
        return existing.concat(missing.map(function (c) { return synth(c[0], c[1]); }));
      }
      var merged = u.codes.map(function (c) { return have[c[0]] || synth(c[0], c[1]); });
      // Codes outside the defined set (a write-in sharing the column) keep their
      // place at the end rather than being dropped.
      existing.forEach(function (r) {
        if (!(String(r.response_code || "") in idx)) { merged.push(r); }
      });
      return merged;
    }
    var out = [], emitted = {};
    groups.forEach(function (g) {
      universe.forEach(function (u) {
        emitted[g + K + u.column] = true;
        out = out.concat(blockFor(g, u));
      });
      blockOrder.forEach(function (key) {
        if (key.indexOf(g + K) === 0 && !emitted[key]) { out = out.concat(byBlock[key]); }
      });
    });
    return out.length ? out : rows;
  }
  // Row order the browser can impose on its own. survey_order needs the universe
  // -- the CSV carries no notion of the designer's choice order -- and count_* are
  // the same comparison the n column header does.
  function applyRowOrder(rows, presentation, universe) {
    var order = presentation.row_order;
    if (!order || order === "auto") { return rows; }
    if (order === "response_order") {
      var pos = {};
      (presentation.response_order || []).forEach(function (c, i) { pos[String(c)] = i; });
      // Codes the list does not name keep their existing order, after the ones
      // it does -- the same rule the frequencies stage applies.
      var listed = [], rest = [];
      rows.forEach(function (r) {
        (String(r.response_code || "") in pos ? listed : rest).push(r);
      });
      listed.sort(function (a, b) {
        return pos[String(a.response_code || "")] - pos[String(b.response_code || "")];
      });
      return listed.concat(rest);
    }
    if (order === "count_desc" || order === "count_asc") {
      var sign = order === "count_asc" ? 1 : -1;
      return rows.slice().sort(function (a, b) {
        return sign * ((Number(a.n) || 0) - (Number(b.n) || 0));
      });
    }
    // Code order is the response code itself, which for most questions is not
    // the order the choices appear in: a choice keeps its recode value when the
    // designer moves it, so a scale can run 1, 2, 3, 5, 4 in the survey.
    if (order === "code_desc" || order === "code_asc") {
      var csign = order === "code_asc" ? 1 : -1;
      var isNum = function (r) { return !Number.isNaN(Number(r.response_code)); };
      // Non-numeric codes go last either way -- a write-in's "code" is the
      // verbatim answer, and mirroring the sort would open a descending table
      // with a block of sentences.
      var numeric = rows.filter(isNum).sort(function (a, b) {
        return csign * (Number(a.response_code) - Number(b.response_code));
      });
      var other = rows.filter(function (r) { return !isNum(r); }).sort(function (a, b) {
        return compareValues(a.response_code, b.response_code, false);
      });
      return numeric.concat(other);
    }
    if (order !== "survey_order" || !universe || !universe.length) { return rows; }
    var rank = {};
    universe.forEach(function (u) {
      u.codes.forEach(function (c, i) { rank[u.column + K + c[0]] = i; });
    });
    return rows.slice().sort(function (a, b) {
      var ra = rank[(a.column || "") + K + String(a.response_code || "")];
      var rb = rank[(b.column || "") + K + String(b.response_code || "")];
      if (ra === undefined && rb === undefined) { return 0; }
      if (ra === undefined) { return 1; }
      if (rb === undefined) { return -1; }
      return ra - rb;
    });
  }
  // The rows a table shows: fill in, order, drop, hide -- in that sequence, so the
  // Total row and any column sort operate on exactly what is displayed.
  function shownRows(allRows, presentation, universe) {
    var rows = fillEmpty(allRows, universe, presentation.include_empty_codes);
    rows = applyRowOrder(rows, presentation, universe);
    return applyHidden(dropEmpty(rows, presentation.include_empty_codes), presentation.hide_codes);
  }
  function applyHidden(rows, hideCodes) {
    if (!hideCodes || !hideCodes.length) { return rows; }
    var hidden = {};
    hideCodes.forEach(function (c) { hidden[String(c)] = true; });
    var visible = rows.filter(function (r) { return !hidden[String(r.response_code || "").trim()]; });
    if (!visible.length || visible.length === rows.length || !isMutuallyExclusive(rows)) { return visible; }
    var newValid = 0;
    visible.forEach(function (r) { newValid += Number(r.n) || 0; });
    return visible.map(function (r) {
      var copy = {};
      for (var k in r) { copy[k] = r[k]; }
      copy.valid_n = newValid;
      copy.valid_pct = newValid ? (Number(r.n) || 0) / newValid * 100 : 0;
      return copy;
    });
  }
  function aggregateRow(rows) {
    if (!rows || !rows.length) { return null; }
    var agg = {};
    for (var k in rows[0]) { agg[k] = rows[0][k]; }
    agg.n = Math.trunc(sumField(rows, "n"));
    ["valid_pct", "eligible_pct", "total_pct"].forEach(function (f) {
      agg[f] = round2(sumField(rows, f));
    });
    return agg;
  }
  function th(text, cls) {
    var el = document.createElement("th");
    if (cls) { el.className = cls; }
    el.textContent = text;
    return el;
  }
  function td(text, cls) {
    var el = document.createElement("td");
    if (cls) { el.className = cls; }
    el.textContent = text;
    return el;
  }
  function cellNode(row, stats, reportBase, decimals) {
    var cell = document.createElement("td");
    cell.className = "num";
    if (!row) {
      cell.textContent = "\\u2014";
      return cell;
    }
    stats.forEach(function (stat, i) {
      if (i > 0) { cell.appendChild(document.createElement("br")); }
      if (stats.length > 1 && i > 0) {
        var span = document.createElement("span");
        span.className = "meta";
        span.textContent = statLabel(stat, reportBase);
        cell.appendChild(span);
        cell.appendChild(document.createTextNode(" "));
      }
      cell.appendChild(document.createTextNode(statValue(row, stat, reportBase, decimals)));
    });
    return cell;
  }

  // ---- sorting ----
  // Sort state is {key: <column id>, dir: "asc"|"desc"}, or null for the
  // config-driven order the CSV was written in. Headers cycle asc -> desc -> off
  // so that original order stays reachable.
  function sameKey(a, b) {
    if (!a || !b || a.kind !== b.kind) { return false; }
    return a.kind === "stat" ? a.stat === b.stat : true;
  }
  function nextSort(current, key) {
    if (!current || !sameKey(current.key, key)) { return { key: key, dir: "asc" }; }
    return current.dir === "asc" ? { key: key, dir: "desc" } : null;
  }
  // Missing values sort last in both directions rather than clumping at one end.
  function compareValues(a, b, numeric) {
    var aMissing = a === null || a === undefined || a === "";
    var bMissing = b === null || b === undefined || b === "";
    if (aMissing || bMissing) { return aMissing && bMissing ? 0 : (aMissing ? 1 : -1); }
    if (numeric) {
      var na = Number(a), nb = Number(b);
      var naBad = Number.isNaN(na), nbBad = Number.isNaN(nb);
      if (naBad || nbBad) { return naBad && nbBad ? 0 : (naBad ? 1 : -1); }
      return na === nb ? 0 : (na < nb ? -1 : 1);
    }
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  }
  // Array.prototype.sort is stable, so equal keys keep the config-driven order.
  function sortItems(items, sort, valueOf) {
    if (!sort) { return items; }
    var numeric = sort.key.kind === "stat";
    var out = items.slice();
    out.sort(function (x, y) {
      var c = compareValues(valueOf(x), valueOf(y), numeric);
      return sort.dir === "desc" ? -c : c;
    });
    return out;
  }
  // Turns an already-built <th> into a sort control, appending the active arrow.
  function makeSortable(cell, key, sort, onSort) {
    cell.classList.add("rr-sortable");
    cell.tabIndex = 0;
    cell.setAttribute("role", "button");
    var active = sort && sameKey(sort.key, key);
    cell.setAttribute("aria-sort", active ? (sort.dir === "asc" ? "ascending" : "descending") : "none");
    if (active) {
      var arrow = document.createElement("span");
      arrow.className = "rr-arrow";
      arrow.textContent = sort.dir === "asc" ? "\\u25b2" : "\\u25bc";
      cell.appendChild(arrow);
    }
    cell.title = "Sort by this column";
    function activate() { onSort(key); }
    cell.addEventListener("click", activate);
    cell.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); activate(); }
    });
    return cell;
  }
  function sortableTh(text, cls, key, sort, onSort) {
    return makeSortable(th(text, cls), key, sort, onSort);
  }

  // ---- flat (ungrouped question) table ----
  function hasAttributeIn(rows) {
    return rows.some(function (r) { return (r.attribute || "").trim() !== ""; });
  }
  function flatValueOf(row, key, reportBase) {
    if (key.kind === "attr") { return row.attribute || ""; }
    if (key.kind === "code") { return row.response_code || ""; }
    if (key.kind === "label") { return row.response_label || ""; }
    return row[statField(key.stat, reportBase)];
  }
  function buildFlatHeader(theadRow, hasAttr, showCode, stats, reportBase, sort, onSort) {
    theadRow.innerHTML = "";
    if (hasAttr) { theadRow.appendChild(sortableTh("Attribute", "", { kind: "attr" }, sort, onSort)); }
    if (showCode) { theadRow.appendChild(sortableTh("Code", "", { kind: "code" }, sort, onSort)); }
    theadRow.appendChild(sortableTh("Label", "", { kind: "label" }, sort, onSort));
    var featured = constants.pct_field[reportBase];
    // The star distinguishes one percentage column from the others; with a single
    // one it always lands on it and says nothing.
    var starBases = stats.filter(function (st) {
      return statField(st, reportBase).slice(-4) === "_pct";
    }).length > 1;
    stats.forEach(function (stat) {
      var mark = (starBases && statField(stat, reportBase) === featured) ? " \\u2605" : "";
      theadRow.appendChild(
        sortableTh(statLabel(stat, reportBase) + mark, "num", { kind: "stat", stat: stat }, sort, onSort)
      );
    });
  }
  function buildFlatTotalRow(rows, hasAttr, showCode, stats, reportBase, decimals) {
    var agg = aggregateRow(rows);
    if (!agg) { return null; }
    var tr = document.createElement("tr");
    var span = (hasAttr ? 1 : 0) + (showCode ? 1 : 0);
    if (span > 0) {
      var spacer = document.createElement("td");
      spacer.colSpan = span;
      tr.appendChild(spacer);
    }
    var labelTd = document.createElement("td");
    var strong = document.createElement("strong");
    strong.textContent = "Total";
    labelTd.appendChild(strong);
    tr.appendChild(labelTd);
    stats.forEach(function (stat) { tr.appendChild(cellNode(agg, [stat], reportBase, decimals)); });
    return tr;
  }
  function renderFlatTable(tableEl, allRows, reportBase, presentation, sort, onSort, universe) {
    var thead = tableEl.querySelector("thead tr");
    var tbody = tableEl.querySelector("tbody");
    var decimals = presentation.pct_decimals;
    var rows = shownRows(allRows, presentation, universe);
    if (!rows.length) { rows = allRows; }
    var hasAttr = hasAttributeIn(rows);
    var stats = (presentation.stats && presentation.stats.length) ? presentation.stats : constants.default_flat_stats;
    var showCode = !!presentation.show_code;
    buildFlatHeader(thead, hasAttr, showCode, stats, reportBase, sort, onSort);
    tbody.innerHTML = "";
    // The Total row is synthesized from the full, unsorted row set and was never a
    // member of it, so sorting can neither reorder nor alter it.
    if (presentation.response_total === "before") {
      var totalBefore = buildFlatTotalRow(rows, hasAttr, showCode, stats, reportBase, decimals);
      if (totalBefore) { tbody.appendChild(totalBefore); }
    }
    sortItems(rows, sort, function (row) { return flatValueOf(row, sort.key, reportBase); }).forEach(function (row) {
      var tr = document.createElement("tr");
      if (hasAttr) { tr.appendChild(td(row.attribute || "")); }
      if (showCode) { tr.appendChild(td(row.response_code || "")); }
      tr.appendChild(td(row.response_label || ""));
      stats.forEach(function (stat) { tr.appendChild(cellNode(row, [stat], reportBase, decimals)); });
      tbody.appendChild(tr);
    });
    if (presentation.response_total === "after") {
      var totalAfter = buildFlatTotalRow(rows, hasAttr, showCode, stats, reportBase, decimals);
      if (totalAfter) { tbody.appendChild(totalAfter); }
    }
    return sortItems(rows, sort, function (row) { return flatValueOf(row, sort.key, reportBase); });
  }

  // ---- grouped (crosstab) table ----
  function pivotGrouped(rows, overallRows, presentation, reportBase, universe) {
    var nField = constants.n_field[reportBase] || "eligible_n";
    // Filling and ordering happen per group, so they run before the pivot; the
    // Overall column is its own single-group table and gets the same treatment.
    rows = applyRowOrder(dropEmpty(fillEmpty(rows, universe, presentation.include_empty_codes),
                                   presentation.include_empty_codes), presentation, universe);
    if (overallRows) {
      overallRows = applyRowOrder(dropEmpty(fillEmpty(overallRows, universe, presentation.include_empty_codes),
                                            presentation.include_empty_codes), presentation, universe);
    }
    var groupRows = {};
    var groupOrder = [];
    rows.forEach(function (r) {
      var gc = r.group_codes || "";
      if (!(gc in groupRows)) { groupRows[gc] = []; groupOrder.push(gc); }
      groupRows[gc].push(r);
    });
    var hide = presentation.hide_codes;
    if (hide && hide.length) {
      // Each group column is its own table, so Valid n rebases within the group.
      // Falling back to the unfiltered rows keeps a group that hid everything visible.
      var keep = function (rs) { var k = applyHidden(rs, hide); return k.length ? k : rs; };
      rows = [];
      groupOrder.forEach(function (gc) {
        groupRows[gc] = keep(groupRows[gc]);
        rows = rows.concat(groupRows[gc]);
      });
      if (overallRows && overallRows.length) { overallRows = keep(overallRows); }
    }
    var groups = groupOrder.map(function (gc) {
      return { code: gc, label: groupRows[gc][0].group_labels || "", base: groupRows[gc][0][nField] || "" };
    });
    overallRows = overallRows || [];
    if (presentation.overall && overallRows.length) {
      var ov = { code: "__overall__", label: "Overall", base: overallRows[0][nField] || "" };
      groups = presentation.overall === "before" ? [ov].concat(groups) : groups.concat([ov]);
    }
    var opts = [];
    var seen = {};
    rows.concat(overallRows).forEach(function (r) {
      var key = (r.attribute || "") + "\\u0001" + (r.response_code || "");
      if (!(key in seen)) {
        seen[key] = true;
        opts.push({ attr: r.attribute || "", code: r.response_code || "", label: r.response_label || "" });
      }
    });
    var hasAttr = opts.some(function (o) { return !!o.attr; });

    var groupedIdx = {};
    rows.forEach(function (r) {
      groupedIdx[(r.group_codes || "") + "\\u0001" + (r.attribute || "") + "\\u0001" + (r.response_code || "")] = r;
    });
    var overallIdx = {};
    overallRows.forEach(function (r) {
      overallIdx[(r.attribute || "") + "\\u0001" + (r.response_code || "")] = r;
    });
    var groupTotal = {};
    groupOrder.forEach(function (gc) { groupTotal[gc] = aggregateRow(groupRows[gc]); });
    var overallTotal = overallRows.length ? aggregateRow(overallRows) : null;

    function data(gcode, attr, code, isTotal) {
      if (gcode === "__overall__") { return isTotal ? overallTotal : overallIdx[attr + "\\u0001" + code]; }
      return isTotal ? groupTotal[gcode] : groupedIdx[gcode + "\\u0001" + attr + "\\u0001" + code];
    }

    var respAxis = opts.slice();
    var TOTAL = { attr: "__total__", code: "", label: "Total" };
    if (presentation.response_total === "before") { respAxis = [TOTAL].concat(respAxis); }
    else if (presentation.response_total === "after") { respAxis = respAxis.concat([TOTAL]); }

    return { groups: groups, respAxis: respAxis, hasAttr: hasAttr, data: data };
  }
  function respLabelText(opt, showCode) {
    var isTotal = opt.attr === "__total__";
    if (isTotal) { return "Total"; }
    if (showCode && opt.code !== "") { return opt.code + " \\u2014 " + opt.label; }
    return opt.label;
  }
  function groupHeaderCell(label, base) {
    var cell = document.createElement("th");
    cell.className = "num";
    cell.appendChild(document.createTextNode(label));
    cell.appendChild(document.createElement("br"));
    var span = document.createElement("span");
    span.className = "meta";
    span.textContent = "n=" + base;
    cell.appendChild(span);
    return cell;
  }
  // Aggregate rows (the response Total, and the Overall group) are pinned at the
  // position their config option puts them; only real data rows take part in a sort.
  function isAggregateOpt(opt) { return opt.attr === "__total__"; }
  function isAggregateGroup(g) { return g.code === "__overall__"; }
  // Sorts `items` while leaving pinned entries at their original indices.
  function sortAroundPinned(items, isPinned, sort, valueOf) {
    if (!sort) { return items; }
    var sorted = sortItems(items.filter(function (it) { return !isPinned(it); }), sort, valueOf);
    var next = 0;
    return items.map(function (it) { return isPinned(it) ? it : sorted[next++]; });
  }
  // The value a cell sorts by. Multi-stat cells sort on the first selected stat,
  // which is the primary value shown.
  function cellSortValue(row, stats, reportBase) {
    return row ? row[statField(stats[0], reportBase)] : "";
  }
  function renderGroupedTable(tableEl, pivot, stats, showCode, orientation, reportBase, decimals, sort, onSort) {
    var thead = tableEl.querySelector("thead tr");
    var tbody = tableEl.querySelector("tbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";
    var groups = pivot.groups, respAxis = pivot.respAxis, hasAttr = pivot.hasAttr, data = pivot.data;

    if (orientation === "rows") {
      // Rows are groups; sort them by group label or by a response column's value.
      thead.appendChild(sortableTh("Group", "", { kind: "group" }, sort, onSort));
      respAxis.forEach(function (opt) {
        var isTotal = isAggregateOpt(opt);
        var extra = (hasAttr && opt.attr !== "" && !isTotal) ? (opt.attr + ": ") : "";
        var text = extra + respLabelText(opt, showCode);
        thead.appendChild(
          sortableTh(text, "num", { kind: "stat", stat: "opt:" + opt.attr + "\\u0001" + opt.code }, sort, onSort)
        );
      });
      sortAroundPinned(groups, isAggregateGroup, sort, function (g) {
        if (sort.key.kind === "group") { return g.label; }
        var parts = String(sort.key.stat).slice(4).split("\\u0001");
        return cellSortValue(data(g.code, parts[0], parts[1], parts[0] === "__total__"), stats, reportBase);
      }).forEach(function (g) {
        var tr = document.createElement("tr");
        tr.appendChild(groupHeaderCell(g.label, g.base));
        respAxis.forEach(function (opt) {
          tr.appendChild(cellNode(data(g.code, opt.attr, opt.code, isAggregateOpt(opt)), stats, reportBase, decimals));
        });
        tbody.appendChild(tr);
      });
    } else {
      // Rows are response options; sort them by code/label or by a group column.
      if (hasAttr) { thead.appendChild(sortableTh("Attribute", "", { kind: "attr" }, sort, onSort)); }
      if (showCode) { thead.appendChild(sortableTh("Code", "", { kind: "code" }, sort, onSort)); }
      thead.appendChild(sortableTh("Response", "", { kind: "label" }, sort, onSort));
      groups.forEach(function (g) {
        thead.appendChild(
          makeSortable(groupHeaderCell(g.label, g.base), { kind: "stat", stat: "grp:" + g.code }, sort, onSort)
        );
      });
      sortAroundPinned(respAxis, isAggregateOpt, sort, function (opt) {
        if (sort.key.kind === "attr") { return opt.attr; }
        if (sort.key.kind === "code") { return opt.code; }
        if (sort.key.kind === "label") { return opt.label; }
        var gcode = String(sort.key.stat).slice(4);
        return cellSortValue(data(gcode, opt.attr, opt.code, false), stats, reportBase);
      }).forEach(function (opt) {
        var isTotal = isAggregateOpt(opt);
        var tr = document.createElement("tr");
        if (hasAttr) { tr.appendChild(td(isTotal ? "" : opt.attr)); }
        if (showCode) { tr.appendChild(td(isTotal ? "" : opt.code)); }
        var labelTd = document.createElement("td");
        if (isTotal) {
          var strong = document.createElement("strong");
          strong.textContent = "Total";
          labelTd.appendChild(strong);
        } else {
          labelTd.textContent = opt.label;
        }
        tr.appendChild(labelTd);
        groups.forEach(function (g) {
          tr.appendChild(cellNode(data(g.code, opt.attr, opt.code, isTotal), stats, reportBase, decimals));
        });
        tbody.appendChild(tr);
      });
    }
    return pivot;
  }
  function renderGrouped(tableEl, sdata, presentation, sort, onSort) {
    var base = presentation.percent_base || sdata.report_base;
    var pivot = pivotGrouped(sdata.rows, sdata.overall_rows, presentation, base, sdata.universe);
    var stats = (presentation.stats && presentation.stats.length) ? presentation.stats : constants.default_cell_stats;
    renderGroupedTable(tableEl, pivot, stats, !!presentation.show_code,
      presentation.orientation || "columns", base, presentation.pct_decimals, sort, onSort);
    return pivot;
  }

  // ---- control bar widgets ----
  function addSelect(row, labelText, value, options, onChange) {
    var label = document.createElement("label");
    label.className = "rr-field";
    label.appendChild(document.createTextNode(labelText));
    var select = document.createElement("select");
    options.forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt[0];
      o.textContent = opt[1];
      if (opt[2]) { o.disabled = true; }
      if (opt[0] === value) { o.selected = true; }
      select.appendChild(o);
    });
    select.addEventListener("change", function () { onChange(select.value); });
    label.appendChild(select);
    row.appendChild(label);
    return select;
  }
  function addCheckbox(row, labelText, checked, onChange) {
    var label = document.createElement("label");
    label.className = "rr-field";
    var input = document.createElement("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    label.appendChild(input);
    label.appendChild(document.createTextNode(labelText));
    row.appendChild(label);
    return input;
  }
  function addStatChips(container, statOrder, selected, reportBase, onChange) {
    var chipsRow = document.createElement("div");
    chipsRow.className = "rr-chips";
    // Concrete stats first, then the base-tracking aliases in their own labelled
    // group so the pair reads as a distinct choice rather than a repeat.
    var concreteGroup = document.createElement("div");
    concreteGroup.className = "rr-chip-group";
    var aliasGroup = document.createElement("div");
    aliasGroup.className = "rr-chip-group";
    var aliasName = document.createElement("span");
    aliasName.className = "rr-group-name";
    aliasName.textContent = "Featured";
    aliasGroup.appendChild(aliasName);

    var boxes = [];
    statOrder.forEach(function (stat) {
      var label = document.createElement("label");
      var input = document.createElement("input");
      input.type = "checkbox";
      input.checked = selected.indexOf(stat) !== -1;
      input.addEventListener("change", function () {
        var checkedNow = boxes.filter(function (b) { return b.input.checked; });
        if (checkedNow.length === 0) {
          input.checked = true; // never allow the last stat to be unchecked
          return;
        }
        onChange(checkedNow.map(function (b) { return b.stat; }));
      });
      label.appendChild(input);
      label.appendChild(document.createTextNode(chipLabel(stat, reportBase)));
      label.title = chipTitle(stat, reportBase);
      (isAlias(stat) ? aliasGroup : concreteGroup).appendChild(label);
      boxes.push({ stat: stat, input: input });
    });
    chipsRow.appendChild(concreteGroup);
    chipsRow.appendChild(aliasGroup);
    container.appendChild(chipsRow);
    return function (stats) {
      boxes.forEach(function (b) { b.input.checked = stats.indexOf(b.stat) !== -1; });
    };
  }

  function addNumber(row, labelText, value, min, max, onChange) {
    var label = document.createElement("label");
    label.className = "rr-field";
    label.appendChild(document.createTextNode(labelText));
    var input = document.createElement("input");
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.value = String(value);
    input.className = "rr-num";
    input.addEventListener("input", function () {
      var v = parseInt(input.value, 10);
      if (Number.isNaN(v) || v < min || v > max) { return; }  // ignore mid-edit states
      onChange(v);
    });
    label.appendChild(input);
    row.appendChild(label);
    return input;
  }
  // Codes are offered with their labels: "-1" is a real "Other" option in some
  // exports, so which code means N/A is a per-survey judgement, not a constant.
  function addHidePicker(container, codes, selected, onChange) {
    if (!codes || !codes.length) { return function () {}; }
    var inputsByCode = {};
    var details = document.createElement("details");
    details.className = "rr-hide";
    var summary = document.createElement("summary");
    details.appendChild(summary);
    var body = document.createElement("div");
    body.className = "rr-chips";
    details.appendChild(body);
    container.appendChild(details);

    var chosen = {};
    selected.forEach(function (c) { chosen[String(c)] = true; });
    function current() {
      return codes.map(function (c) { return c.code; }).filter(function (c) { return chosen[c]; });
    }
    function syncSummary() {
      var n = current().length;
      summary.textContent = n ? "Hidden rows (" + n + ")" : "Hide rows";
    }
    codes.forEach(function (c) {
      var label = document.createElement("label");
      var input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!chosen[c.code];
      input.addEventListener("change", function () {
        chosen[c.code] = input.checked;
        syncSummary();
        onChange(current());
      });
      label.appendChild(input);
      inputsByCode[c.code] = input;
      // A write-in's "code" is the verbatim text, so code and label are the same
      // string -- printing both would just repeat a sentence. The span is width
      // capped in CSS and carries the full text as its tooltip.
      var text = (c.label && c.label !== c.code) ? c.code + " \\u2014 " + c.label : c.code;
      var span = document.createElement("span");
      span.className = "rr-code-label";
      span.textContent = text;
      span.title = text;
      label.appendChild(span);
      body.appendChild(label);
    });
    syncSummary();
    return function (hide) {
      chosen = {};
      (hide || []).forEach(function (c) { chosen[String(c)] = true; });
      Object.keys(inputsByCode).forEach(function (c) { inputsByCode[c].checked = !!chosen[c]; });
      syncSummary();
    };
  }

  // Twin of Python's _base_caption: with one percentage column this is the only
  // place the denominator is named, so it must track the base and any hiding.
  var BASE_NAME = { valid: "Valid", eligible: "Eligible", total: "Total" };
  function baseCaption(rows, reportBase, hidden) {
    if (!reportBase || !rows.length) { return ""; }
    var value = rows[0][constants.n_field[reportBase] || "eligible_n"];
    if (value === undefined || value === null || value === "") { return ""; }
    return " \\u00b7 Base: " + (BASE_NAME[reportBase] || reportBase) + " n = " + value +
      (hidden > 0 ? " (" + hidden + " hidden)" : "");
  }

  // ---- statistic definitions ----
  function buildDefsPanel(container, getBase, kind, getStats) {
    var details = document.createElement("details");
    details.className = "rr-defs";
    var summary = document.createElement("summary");
    summary.textContent = "Show statistic definitions";
    details.appendChild(summary);
    var body = document.createElement("div");
    details.appendChild(body);
    container.appendChild(details);

    // The stats on show, plus the base count any displayed percentage divides by --
    // "n ÷ Valid n" is opaque if Valid n isn't itself a column.
    function statsToDefine() {
      var out = [];
      var reportBase = getBase();
      getStats().forEach(function (stat) {
        if (out.indexOf(stat) === -1) { out.push(stat); }
        var field = statField(stat, reportBase);
        if (field.slice(-4) === "_pct") {
          var base = field.slice(0, -4) + "_n";
          if (constants.stat_definitions[base] && out.indexOf(base) === -1) { out.push(base); }
        }
      });
      return out;
    }

    function refresh() {
      var reportBase = getBase();
      body.innerHTML = "";
      var table = document.createElement("table");
      var tbody = document.createElement("tbody");
      statsToDefine().forEach(function (stat) {
        var tr = document.createElement("tr");
        var name = chipLabel(stat, reportBase);
        if (isAlias(stat)) { name += " (" + statLabel(stat, reportBase) + ")"; }
        tr.appendChild(td(name));
        var def = constants.stat_definitions[stat] || "";
        if (isAlias(stat)) {
          def += " For this question that is " + statLabel(stat, reportBase) + ".";
        }
        tr.appendChild(td(def));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      body.appendChild(table);

      var notes = [
        "\\u2605 marks the column matching this question's reporting base.",
        "When rows are hidden, Valid n becomes the number who chose one of the " +
          "responses still shown and Valid % is recomputed against it; Eligible % " +
          "and Total % are unchanged. On multi-select questions one respondent can " +
          "pick several options, so that count isn't derivable and Valid n is left as is.",
        "The Total row sums n and the percentages across the response options shown; " +
          "the base counts (Valid/Eligible/Total n) are held constant, not summed. " +
          "Percentages can exceed 100% for multi-select questions.",
      ];
      if (kind === "grouped") {
        notes.push("Cells are computed within each group: the n= under a group heading " +
          "is that group's base. An Overall column/row is the same question ungrouped.");
        notes.push("Sorting by a group column uses the first selected statistic, " +
          "which is the primary value shown in each cell.");
      }
      notes.forEach(function (text) {
        var note = document.createElement("div");
        note.className = "rr-defs-note";
        note.textContent = text;
        body.appendChild(note);
      });
    }
    refresh();
    return refresh;
  }

  // ---- copy-config snippet ----
  function arraysEqual(a, b) {
    if (!a || !b) { return a === b; }
    if (a.length !== b.length) { return false; }
    for (var i = 0; i < a.length; i++) { if (a[i] !== b[i]) { return false; } }
    return true;
  }
  // The config for a table as it currently stands -- every applicable key, not
  // just what differs from the file, so an unmodified table still shows a
  // complete block you can paste or export.
  function tableConfig(kind, state, groupBy) {
    var cfg = {};
    if (kind === "grouped") { cfg.group_by = (groupBy || []).slice(); }
    cfg.show_code = state.show_code;
    if (kind === "grouped") { cfg.orientation = state.orientation; cfg.overall = state.overall; }
    cfg.response_total = state.response_total;
    cfg.stats = state.stats.slice();
    cfg.pct_decimals = state.pct_decimals;
    cfg.hide_codes = state.hide_codes.slice();
    return cfg;
  }
  // Keys the frequencies stage owns; they sit on the question, never on a
  // table spec, and only take effect after that stage is re-run.
  function questionConfig(state, sort, axis) {
    var cfg = {
      include: state.include !== false,
      percent_base: state.percent_base,
      include_empty_codes: !!state.include_empty_codes,
      sort_by: state.row_order || "count_desc",
    };
    // Emitted unconditionally, so exporting a config and running it back through
    // the frequencies stage reproduces the order you are looking at rather than
    // falling back to the default.
    if (cfg.sort_by === "response_order") {
      var codes = (state.response_order || []).slice();
      if (!codes.length && axis && axis.codes && !axis.hasAttr) { codes = axis.codes(); }
      if (codes.length) { cfg.response_order = codes; }
    }
    var sc = sortConfig(sort, axis);
    for (var k in sc.keys) { cfg[k] = sc.keys[k]; }
    return { cfg: cfg, note: sc.note };
  }
  // Translate a browser sort into config keys. sort_by/response_order are
  // question-level keys consumed when the frequency CSVs are written, so they are
  // emitted separately from the render-time presentation keys, and only when the
  // sorted axis is the response axis (group ordering has no config equivalent).
  function sortConfig(sort, axis) {
    if (!sort) { return { keys: {}, note: "" }; }
    if (!axis || !axis.sortsResponses) {
      return { keys: {}, note: "Group ordering isn't configurable, so this sort isn't included below." };
    }
    if (sort.key.kind === "stat" && sort.key.stat === "n") {
      return { keys: { sort_by: sort.dir === "asc" ? "count_asc" : "count_desc" }, note: "" };
    }
    if (axis.hasAttr) {
      return {
        keys: {},
        note: "This question has attributes, so a response code isn't unique and " +
          "response_order would be ambiguous; this sort isn't included below.",
      };
    }
    var codes = axis.codes();
    if (!codes.length) { return { keys: {}, note: "" }; }
    return { keys: { sort_by: "response_order", response_order: codes }, note: "" };
  }
  // ---- reading a config back in ----
  // Validates a pasted block against the same vocabulary the config validator
  // uses, so a typo is reported rather than silently ignored. Returns the values
  // that can be applied plus what was rejected and why.
  var TABLE_KEYS = ["show_code", "orientation", "overall", "response_total",
                    "stats", "pct_decimals", "hide_codes"];
  var QUESTION_KEYS = ["include", "percent_base", "sort_by", "response_order",
                       "include_empty_codes"];
  function inList(v, list) { return list.indexOf(v) !== -1; }
  function readConfig(obj, kind) {
    var out = { values: {}, errors: [], ignored: [] };
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      out.errors.push("expected a JSON object");
      return out;
    }
    Object.keys(obj).forEach(function (key) {
      var v = obj[key];
      if (key.charAt(0) === "_" || key === "group_by" || key === "tables") { return; }
      switch (key) {
        case "show_code":
        case "include":
          if (typeof v !== "boolean") { out.errors.push(key + " must be true or false"); }
          else { out.values[key] = v; }
          break;
        case "include_empty_codes":
          // Turning it off is just a filter, so it applies here; turning it on
          // needs rows this CSV does not have. Either way the value is recorded
          // so the config exported afterwards still says what was asked for.
          if (typeof v !== "boolean") { out.errors.push(key + " must be true or false"); break; }
          out.values.include_empty_codes = v;
          if (v) { out.ignored.push("include_empty_codes: zero-response rows are added when the CSV is written"); }
          break;
        case "orientation":
          if (!inList(v, constants.orientations)) { out.errors.push("orientation must be columns or rows"); }
          else { out.values.orientation = v; }
          break;
        case "overall":
        case "response_total":
          if (!inList(v, constants.positions)) { out.errors.push(key + " must be false, \\"before\\" or \\"after\\""); }
          else { out.values[key] = v; }
          break;
        case "percent_base":
          if (!inList(v, constants.report_bases)) { out.errors.push("percent_base must be one of " + constants.report_bases.join(", ")); }
          else { out.values.percent_base = v; }
          break;
        case "pct_decimals":
          if (typeof v !== "number" || v % 1 !== 0 || v < 0 || v > constants.pct_decimals_max) {
            out.errors.push("pct_decimals must be a whole number 0-" + constants.pct_decimals_max);
          } else { out.values.pct_decimals = v; }
          break;
        case "stats":
          if (!Array.isArray(v)) { out.errors.push("stats must be a list"); break; }
          var bad = v.filter(function (x) { return !inList(x, constants.stat_keys); });
          if (bad.length) { out.errors.push("unknown stats: " + bad.join(", ")); }
          else if (!v.length) { out.errors.push("stats must name at least one statistic"); }
          else { out.values.stats = v.slice(); }
          break;
        case "hide_codes":
          if (!Array.isArray(v)) { out.errors.push("hide_codes must be a list"); }
          else { out.values.hide_codes = v.map(String); }
          break;
        case "sort_by":
          // Every order in the vocabulary is one the browser can impose itself,
          // so this drives the Row order control and clears any header sort.
          if (constants.row_orders.some(function (o) { return o[0] === v; })) {
            out.values.row_order = v;
            out.values.sort = null;
          } else if (v === "auto") {
            out.ignored.push("sort_by: \\"auto\\" resolves to a concrete order when the CSV is written");
          } else { out.errors.push("unknown sort_by: " + v); }
          break;
        case "response_order":
          if (!Array.isArray(v)) { out.errors.push("response_order must be a list"); }
          else { out.values.response_order = v.map(String); }
          break;
        default:
          out.ignored.push("unknown key: " + key);
      }
    });
    if (kind !== "grouped") { delete out.values.orientation; delete out.values.overall; }
    return out;
  }
  function applyConfig(state, values) {
    var changed = 0;
    Object.keys(values).forEach(function (k) {
      if (k === "sort") { state.sort = values[k]; changed++; return; }
      if (k === "row_order") { state.sort = null; }
      state[k] = values[k];
      changed++;
    });
    return changed;
  }

  function snippetPart(parent, hintText, onApply) {
    var wrap = document.createElement("div");
    wrap.className = "rr-snippet-part";
    var hint = document.createElement("div");
    hint.className = "rr-snippet-hint";
    hint.textContent = hintText;
    wrap.appendChild(hint);
    var textarea = document.createElement("textarea");
    textarea.className = "rr-snippet-body";
    textarea.rows = 8;
    textarea.spellcheck = false;
    wrap.appendChild(textarea);
    var copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "rr-copy-btn";
    copyBtn.textContent = "Copy";
    wrap.appendChild(copyBtn);
    var applyBtn = null;
    if (onApply) {
      applyBtn = document.createElement("button");
      applyBtn.type = "button";
      applyBtn.className = "rr-copy-btn";
      applyBtn.textContent = "Apply";
      applyBtn.style.marginLeft = "0.4rem";
      applyBtn.title = "Read this JSON back and update the report to match";
      wrap.appendChild(applyBtn);
    }
    var resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.className = "rr-copy-btn";
    resetBtn.textContent = "Regenerate";
    resetBtn.style.marginLeft = "0.4rem";
    resetBtn.hidden = true;
    wrap.appendChild(resetBtn);
    var note = document.createElement("span");
    note.className = "rr-note";
    note.style.marginLeft = "0.5rem";
    wrap.appendChild(note);
    parent.appendChild(wrap);

    // The box is editable so you can adjust the JSON before copying. Once you
    // type in it, control changes stop overwriting your text -- otherwise a
    // stray toggle would silently discard the edit -- until you Regenerate.
    var dirty = false;
    var latest = "";
    textarea.addEventListener("input", function () {
      dirty = textarea.value !== latest;
      resetBtn.hidden = !dirty;
      note.textContent = dirty ? "Edited \\u2014 no longer tracking the controls." : "";
    });
    resetBtn.addEventListener("click", function () {
      textarea.value = latest;
      dirty = false;
      resetBtn.hidden = true;
      note.textContent = "";
    });

    if (applyBtn) {
      applyBtn.addEventListener("click", function () {
        var parsed;
        try {
          parsed = JSON.parse(textarea.value);
        } catch (e) {
          note.className = "rr-note rr-bad";
          note.textContent = "Not valid JSON: " + e.message;
          return;
        }
        var report = onApply(parsed);
        note.className = report.errors.length ? "rr-note rr-bad" : "rr-note";
        var parts = [];
        if (report.applied) { parts.push("Applied " + report.applied + " setting" + (report.applied === 1 ? "" : "s") + "."); }
        else if (!report.errors.length) { parts.push("Nothing to change."); }
        if (report.errors.length) { parts.push(report.errors.join("; ")); }
        if (report.ignored.length) { parts.push("Not applied here \\u2014 " + report.ignored.join("; ")); }
        note.textContent = parts.join(" ");
      });
    }

    copyBtn.addEventListener("click", function () {
      var text = textarea.value;
      function fallback() {
        textarea.select();
        textarea.setSelectionRange(0, text.length);
        note.textContent = "Press Ctrl+C to copy.";
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          note.textContent = "Copied!";
          setTimeout(function () { note.textContent = dirty ? "Edited \\u2014 no longer tracking the controls." : ""; }, 1500);
        }, fallback);
      } else {
        fallback();
      }
    });
    return function (obj) {
      var empty = !obj || Object.keys(obj).length === 0;
      wrap.style.display = empty ? "none" : "";
      latest = empty ? "" : JSON.stringify(obj, null, 2);
      if (!dirty) { textarea.value = latest; note.className = "rr-note"; }
      copyBtn.disabled = empty && !dirty;
      return !empty;
    };
  }
  function buildSnippetPanel(container, kind, questionId, groupBy, getEdited, getSort, getAxis, apply) {
    var details = document.createElement("details");
    details.className = "rr-snippet";
    var summary = document.createElement("summary");
    summary.textContent = "Show config snippet";
    details.appendChild(summary);
    container.appendChild(details);

    var groupByText = groupBy.map(function (g) { return '"' + g + '"'; }).join(", ");
    var presHint = kind === "flat"
      ? "This table's settings, for \\"" + questionId + "\\"'s block in qualtrics_frequency_config.json."
      : "This breakout's \\"tables\\" entry (group_by: [" + groupByText + "]) for \\"" + questionId + "\\".";
    var qHint = "Question-level keys for \\"" + questionId + "\\" (not the \\"tables\\" entry). " +
      "These are what the frequencies stage reads \\u2014 re-run it to put them in the CSVs.";

    // Applying reads the block back in: edits here drive the table, not just
    // the clipboard.
    var setPres = snippetPart(details, presHint, function (obj) { return apply(obj, kind); });
    var setQuestion = snippetPart(details, qHint, function (obj) { return apply(obj, "question"); });
    var note = document.createElement("div");
    note.className = "rr-snippet-hint";
    details.appendChild(note);

    function refresh() {
      var state = getEdited();
      setPres(tableConfig(kind, state, groupBy));
      var q = questionConfig(state, getSort(), getAxis());
      setQuestion(q.cfg);
      note.textContent = q.note;
    }
    return refresh;
  }

  // ---- option controls -------------------------------------------------
  // One description of every option a control can drive, used to build both a
  // table's own controls and the report-level ones. Keeping them in a single
  // list is what stops the two sets from drifting apart as options are added.
  var MIXED = "\u0000mixed";
  // "Custom order" is the explicit response_order list from the config. It is
  // offered only where there is such a list to apply, since picking it anywhere
  // else would be a control that visibly does nothing.
  function rowOrderOpts(hasCustom) {
    return constants.row_orders.map(function (o) {
      return o[0] === "response_order" && !hasCustom ? [o[0], o[1], true] : o;
    });
  }
  function optionSpecs(ctx) {
    var hasUniverse = !!(ctx.universe && ctx.universe.length);
    return [
      { key: "include", kind: "bool", label: "Show this table", global: "Show all tables" },
      { key: "show_code", kind: "bool", label: "Show code column", global: "Show code columns" },
      { key: "include_empty_codes", kind: "bool", label: "Zero-response codes",
        disabled: !hasUniverse,
        title: hasUniverse
          ? "Show a row for every code the survey defines, including the ones nobody chose."
          : "These answers are verbatim text, not a fixed set of codes, so there is nothing to fill in." },
      { key: "row_order", kind: "select", label: "Row order",
        options: rowOrderOpts(ctx.hasCustomOrder),
        title: "Survey order is the order the choices appear in the survey, which is " +
               "not the same as their code order. Custom order is the response_order " +
               "list from the config. Setting this clears a column-header sort." },
      { key: "orientation", kind: "select", label: "Orientation", groupedOnly: true,
        options: [["columns", "Columns"], ["rows", "Rows"]] },
      { key: "overall", kind: "position", label: "Overall column/row", groupedOnly: true,
        disabled: !ctx.hasOverall,
        title: ctx.hasOverall ? "" :
          "No ungrouped table was generated for this question, so there is no Overall data to show." },
      { key: "response_total", kind: "position", global: "Total row",
        label: ctx.grouped ? "Response total" : "Total row" },
      { key: "percent_base", kind: "select", label: "Reporting base", options: BASE_OPTS },
      { key: "pct_decimals", kind: "number", label: "% decimals", global: "Decimal places" },
    ].filter(function (sp) { return ctx.grouped || !sp.groupedOnly; });
  }
  // mixed=true builds the report-level variant, which has to be able to say the
  // tables disagree without picking one of their values for them.
  function buildOption(row, sp, initial, onSet, mixed) {
    var el;
    var label = mixed ? (sp.global || sp.label) : sp.label;
    if (sp.kind === "bool") {
      el = addCheckbox(row, label, !!initial, onSet);
    } else if (sp.kind === "number") {
      el = addNumber(row, label, initial === undefined ? "" : initial, 0,
        constants.pct_decimals_max, onSet);
    } else {
      var opts = (sp.kind === "position" ? POSITION_OPTS : sp.options).slice();
      if (mixed) { opts = [[MIXED, "(mixed)"]].concat(opts); }
      el = addSelect(row, label, "", opts, function (v) {
        if (v === MIXED) { return; }
        onSet(sp.kind === "position" ? (v || false) : v);
      });
    }
    if (sp.disabled) { el.disabled = true; }
    if (sp.title) { el.title = sp.title; }
    syncOption(el, sp, initial);
    return el;
  }
  function syncOption(el, sp, value) {
    if (sp.kind === "bool") {
      el.indeterminate = value === undefined;
      el.checked = value === undefined ? false : !!value;
    } else if (sp.kind === "number") {
      el.value = value === undefined ? "" : String(value);
      el.placeholder = value === undefined ? "mixed" : "";
    } else if (value === undefined) {
      el.value = MIXED;
    } else {
      el.value = sp.kind === "position" ? (value || "") : value;
    }
  }
  // A table's own control bar: every spec bound to that table's state, and
  // registered so a report-level control can drive it too.
  function buildOptionRow(row, specs, state, rerender) {
    var els = {};
    function write(sp, v) {
      state[sp.key] = v;
      // Switching orientation swaps which axis the rows are, so a sort key from
      // the previous orientation no longer addresses a real column.
      // A column-header sort runs on top of the row order, so leaving one in
      // place makes this control look broken -- you pick an order and the table
      // does not move. Choosing an order here is the newer instruction, so it
      // takes over.
      if (sp.key === "orientation" || sp.key === "row_order") { state.sort = null; }
    }
    specs.forEach(function (sp) {
      els[sp.key] = buildOption(row, sp, state[sp.key], function (v) {
        write(sp, v);
        rerender();
        syncGlobals();
      }, false);
      // A disabled control has nothing to offer a global one, and registering it
      // would let "apply to all" write a value this table cannot honor.
      if (!sp.disabled) {
        registerGlobal(sp.key, function (v) {
          write(sp, v);
          // The table's own control has to move too, or it goes on reporting the
          // value it had before the report-level control overrode it.
          syncOption(els[sp.key], sp, v);
          rerender();
        }, function () { return state[sp.key]; });
      }
    });
    return function () {
      specs.forEach(function (sp) { syncOption(els[sp.key], sp, state[sp.key]); });
    };
  }
  // Controls start folded away: a report is for reading, and an always-open bar
  // above every table is noise until you want to change something.
  function buildPanel(host, summaryText) {
    var details = document.createElement("details");
    details.className = "rr-panel";
    var summary = document.createElement("summary");
    summary.textContent = summaryText;
    details.appendChild(summary);
    host.appendChild(details);
    return details;
  }

  // ---- per-section wiring ----
  function initFlatSection(toolsEl, slug) {
    var dataNode = document.getElementById(slug + "-data");
    var tableEl = document.getElementById(slug + "-table");
    if (!dataNode || !tableEl) { return; }
    var sdata = JSON.parse(dataNode.textContent);
    var pres = sdata.presentation || {};
    var effective = {
      show_code: !!pres.show_code,
      stats: (pres.stats && pres.stats.length) ? pres.stats.slice() : constants.default_flat_stats.slice(),
      response_total: pres.response_total || false,
      pct_decimals: pres.pct_decimals === undefined ? 2 : pres.pct_decimals,
      hide_codes: (pres.hide_codes || []).slice(),
      percent_base: sdata.report_base,
      include_empty_codes: !!pres.include_empty_codes,
      // What the CSV is already sorted by, so the control starts by describing
      // the table rather than proposing a change to it.
      row_order: pres.sort_by || "count_desc",
    };
    var state = {
      show_code: effective.show_code,
      stats: effective.stats.slice(),
      response_total: effective.response_total,
      pct_decimals: effective.pct_decimals,
      hide_codes: effective.hide_codes.slice(),
      percent_base: effective.percent_base,
      include_empty_codes: effective.include_empty_codes,
      row_order: effective.row_order,
      response_order: [],
      include: true,
      sort: null,
    };
    var hasAttr = hasAttributeIn(sdata.rows);
    // Hiding a table leaves its heading and controls in place, so it can be
    // brought back; only the data itself goes.
    var hideables = [tableEl].concat(Array.prototype.slice.call(
      tableEl.parentNode.querySelectorAll("table.writein, div.meta + table")));
    var metaEl = toolsEl.previousElementSibling;
    if (!metaEl || !metaEl.classList || !metaEl.classList.contains("meta")) { metaEl = null; }
    function onSort(key) { state.sort = nextSort(state.sort, key); rerender(); }
    function rerender() {
      hideables.forEach(function (el) { el.hidden = !state.include; });
      renderFlatTable(tableEl, sdata.rows, state.percent_base, state, state.sort, onSort, sdata.universe);
      if (metaEl) {
        // Counted against the filled-in row set, and deliberately not dropEmpty'd:
        // a zero-response row holds no respondents, so removing it moves no base
        // and calling it "hidden" would imply answers went missing.
        var filled = fillEmpty(sdata.rows, sdata.universe, state.include_empty_codes);
        var shown = applyHidden(filled, state.hide_codes);
        if (!shown.length) { shown = filled; }
        metaEl.textContent = sdata.meta_prefix +
          baseCaption(shown, state.percent_base, filled.length - shown.length);
      }
      refreshDefs();
      refreshSnippet();
      refreshFullConfig();
    }

    var panel = buildPanel(toolsEl, "Modify this table");
    var row1 = document.createElement("div");
    row1.className = "rr-row";
    var specs = optionSpecs({ grouped: false, universe: sdata.universe,
                              hasCustomOrder: state.row_order === "response_order" });
    var syncOpts = buildOptionRow(row1, specs, state, rerender);
    panel.appendChild(row1);

    var row2 = document.createElement("div");
    row2.className = "rr-row";
    var syncChips = addStatChips(row2, constants.stat_order, state.stats, state.percent_base, function (stats) { state.stats = stats; rerender(); syncGlobals(); });
    panel.appendChild(row2);
    registerGlobal("stats", function (v) { state.stats = v.slice(); syncChips(state.stats); rerender(); }, function () { return state.stats; });
    var syncHide = addHidePicker(panel, sdata.codes, state.hide_codes, function (codes) { state.hide_codes = codes; rerender(); });

    // A config read back in has to move the controls too, or they would
    // misreport the table they are supposed to be driving.
    function syncControls() {
      syncOpts();
      syncChips(state.stats);
      syncHide(state.hide_codes);
      syncGlobals();
    }

    var refreshDefs = buildDefsPanel(panel, function () { return state.percent_base; }, "flat",
      function () { return state.stats; });
    function flatState() { return state; }
    function applyToSection(obj, kind) {
      var r = readConfig(obj, kind === "question" ? "flat" : kind);
      var applied = applyConfig(state, r.values);
      if (applied) { syncControls(); rerender(); }
      return { applied: applied, errors: r.errors, ignored: r.ignored };
    }
    var refreshSnippet = buildSnippetPanel(panel, "flat", sdata.question_id, [],
      flatState,
      function () { return state.sort; },
      function () {
        return {
          sortsResponses: true,
          hasAttr: hasAttr,
          codes: function () {
            return sortItems(sdata.rows, state.sort, function (row) {
              return flatValueOf(row, state.sort.key, sdata.report_base);
            }).map(function (row) { return row.response_code || ""; });
          },
        };
      }, applyToSection);
    registerSection({
      qkey: sdata.question_key, kind: "flat", groupBy: [],
      apply: applyToSection,
      state: flatState, sort: function () { return state.sort; },
      axis: function () {
        return { sortsResponses: true, hasAttr: hasAttr, codes: function () {
          return sortItems(sdata.rows, state.sort, function (row) {
            return flatValueOf(row, state.sort.key, sdata.report_base);
          }).map(function (row) { return row.response_code || ""; });
        } };
      },
    });
    rerender(); // re-render once so the headers become sort controls
  }

  function initGroupedSection(toolsEl, slug) {
    var dataNode = document.getElementById(slug + "-data");
    var tableEl = document.getElementById(slug + "-table");
    if (!dataNode || !tableEl) { return; }
    var sdata = JSON.parse(dataNode.textContent);
    var pres = sdata.presentation || {};
    var hasOverall = !!(sdata.overall_rows && sdata.overall_rows.length);
    var effective = {
      show_code: !!pres.show_code,
      orientation: pres.orientation || "columns",
      overall: hasOverall ? (pres.overall || false) : false,
      response_total: pres.response_total || false,
      stats: (pres.stats && pres.stats.length) ? pres.stats.slice() : constants.default_cell_stats.slice(),
      pct_decimals: pres.pct_decimals === undefined ? 2 : pres.pct_decimals,
      hide_codes: (pres.hide_codes || []).slice(),
      percent_base: sdata.report_base,
      include_empty_codes: !!pres.include_empty_codes,
      // What the CSV is already sorted by, so the control starts by describing
      // the table rather than proposing a change to it.
      row_order: pres.sort_by || "count_desc",
    };
    var state = {
      show_code: effective.show_code,
      orientation: effective.orientation,
      overall: effective.overall,
      response_total: effective.response_total,
      stats: effective.stats.slice(),
      pct_decimals: effective.pct_decimals,
      hide_codes: effective.hide_codes.slice(),
      percent_base: effective.percent_base,
      include_empty_codes: effective.include_empty_codes,
      row_order: effective.row_order,
      response_order: [],
      include: true,
      sort: null,
    };
    var metaEl = toolsEl.previousElementSibling;
    if (!metaEl || !metaEl.classList || !metaEl.classList.contains("meta")) { metaEl = null; }
    var lastPivot = null;
    // Switching orientation swaps which axis the rows are, so a sort key from the
    // previous orientation no longer addresses a real column.
    function onSort(key) { state.sort = nextSort(state.sort, key); rerender(); }
    function rerender() {
      tableEl.hidden = !state.include;
      lastPivot = renderGrouped(tableEl, sdata, state, state.sort, onSort);
      if (metaEl) {
        var statNames = state.stats.map(function (s) { return statLabel(s, state.percent_base); }).join(", ");
        metaEl.textContent = "Grouped by " + sdata.group_keys + " \\u00b7 orientation: " + state.orientation +
          " \\u00b7 cells show " + statNames + " (within group)";
      }
      refreshDefs();
      refreshSnippet();
      refreshFullConfig();
    }

    var panel = buildPanel(toolsEl, "Modify this table");
    var row1 = document.createElement("div");
    row1.className = "rr-row";
    var specs = optionSpecs({ grouped: true, universe: sdata.universe, hasOverall: hasOverall,
                              hasCustomOrder: state.row_order === "response_order" });
    var syncOpts = buildOptionRow(row1, specs, state, function () {
      rerender();
    });
    panel.appendChild(row1);

    var row2 = document.createElement("div");
    row2.className = "rr-row";
    var syncChips = addStatChips(row2, constants.stat_order, state.stats, state.percent_base, function (stats) { state.stats = stats; rerender(); syncGlobals(); });
    panel.appendChild(row2);
    registerGlobal("stats", function (v) { state.stats = v.slice(); syncChips(state.stats); rerender(); }, function () { return state.stats; });
    var syncHide = addHidePicker(panel, sdata.codes, state.hide_codes, function (codes) { state.hide_codes = codes; rerender(); });

    // A config read back in has to move the controls too, or they would
    // misreport the table they are supposed to be driving.
    function syncControls() {
      syncOpts();
      syncChips(state.stats);
      syncHide(state.hide_codes);
      syncGlobals();
    }

    var refreshDefs = buildDefsPanel(panel, function () { return state.percent_base; }, "grouped",
      function () { return state.stats; });
    var groupByList = sdata.group_by || [];
    function groupedState() { return state; }
    function applyToSection(obj, kind) {
      var r = readConfig(obj, kind === "question" ? "grouped" : kind);
      var applied = applyConfig(state, r.values);
      if (applied) { syncControls(); rerender(); }
      return { applied: applied, errors: r.errors, ignored: r.ignored };
    }
    var refreshSnippet = buildSnippetPanel(panel, "grouped", sdata.question_id, groupByList,
      groupedState,
      function () { return state.sort; },
      function () {
        // Only the columns orientation puts response options on the row axis, so
        // only then does a sort correspond to a response_order.
        return {
          sortsResponses: state.orientation !== "rows",
          hasAttr: !!(lastPivot && lastPivot.hasAttr),
          codes: function () {
            if (!lastPivot) { return []; }
            return sortAroundPinned(lastPivot.respAxis, isAggregateOpt, state.sort, function (opt) {
              if (state.sort.key.kind === "attr") { return opt.attr; }
              if (state.sort.key.kind === "code") { return opt.code; }
              if (state.sort.key.kind === "label") { return opt.label; }
              var gcode = String(state.sort.key.stat).slice(4);
              return cellSortValue(lastPivot.data(gcode, opt.attr, opt.code, false), state.stats, state.percent_base);
            }).filter(function (opt) { return !isAggregateOpt(opt); })
              .map(function (opt) { return opt.code || ""; });
          },
        };
      }, applyToSection);
    function groupedAxis() {
      return {
        sortsResponses: state.orientation !== "rows",
        hasAttr: !!(lastPivot && lastPivot.hasAttr),
        codes: function () { return []; },
      };
    }
    registerSection({
      qkey: sdata.question_key, kind: "grouped", groupBy: groupByList,
      state: groupedState, sort: function () { return state.sort; }, axis: groupedAxis,
      apply: applyToSection,
    });
    rerender(); // re-render once so the headers become sort controls
  }

  document.querySelectorAll(".rr-tools[data-slug]").forEach(function (el) {
    var slug = el.getAttribute("data-slug");
    if (el.getAttribute("data-kind") === "grouped") { initGroupedSection(el, slug); }
    else { initFlatSection(el, slug); }
  });

  // The whole report as one config: question-level keys from each question's
  // ungrouped table, and a "tables" entry per breakout. A question with only an
  // ungrouped table keeps its keys at question level, where they already apply,
  // rather than carrying a redundant {"group_by": []} spec.
  // Distribute a whole config file across the sections: question-level keys go
  // to every table of that question, and each "tables" entry to the breakout
  // whose group_by matches ({"group_by": []} being the ungrouped one).
  function applyReportConfig(obj) {
    var errors = [], ignored = [], applied = 0;
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      return { applied: 0, errors: ["expected a JSON object"], ignored: [] };
    }
    var questions = obj.questions;
    if (!questions || typeof questions !== "object") {
      return { applied: 0, errors: ['expected a "questions" object'], ignored: [] };
    }
    var byKey = {};
    configSections.forEach(function (sec) {
      (byKey[sec.qkey] = byKey[sec.qkey] || []).push(sec);
    });
    Object.keys(questions).forEach(function (qkey) {
      var secs = byKey[qkey];
      if (!secs) { ignored.push(qkey + " is not in this report"); return; }
      var block = questions[qkey] || {};
      secs.forEach(function (sec) {
        var r = sec.apply(block, sec.kind);
        applied += r.applied;
        r.errors.forEach(function (e) { errors.push(qkey + ": " + e); });
      });
      var specs = Array.isArray(block.tables) ? block.tables : [];
      specs.forEach(function (spec) {
        var gb = (spec && Array.isArray(spec.group_by)) ? spec.group_by : [];
        var target = secs.filter(function (sec) {
          return sec.groupBy.length === gb.length &&
            sec.groupBy.every(function (g, i) { return g === gb[i]; });
        })[0];
        if (!target) {
          ignored.push(qkey + ": no table with group_by [" + gb.join(", ") + "]");
          return;
        }
        var r2 = target.apply(spec, target.kind);
        applied += r2.applied;
        r2.errors.forEach(function (e) { errors.push(qkey + ": " + e); });
      });
    });
    return { applied: applied, errors: errors, ignored: ignored };
  }

  function buildReportConfig() {
    var questions = {};
    var order = [];
    configSections.forEach(function (sec) {
      var qkey = sec.qkey;
      if (!(qkey in questions)) { questions[qkey] = { flat: null, tables: [] }; order.push(qkey); }
      if (sec.kind === "flat") { questions[qkey].flat = sec; }
      else { questions[qkey].tables.push(sec); }
    });
    var out = {};
    order.forEach(function (qkey) {
      var q = questions[qkey];
      var lead = q.flat || q.tables[0];
      var st = lead.state();
      var block = questionConfig(st, lead.sort(), lead.axis()).cfg;
      if (q.flat) {
        var flatCfg = tableConfig("flat", q.flat.state(), []);
        for (var k in flatCfg) { block[k] = flatCfg[k]; }
      }
      var specs = q.tables.filter(function (t) { return t.state().include !== false; })
        .map(function (t) { return tableConfig("grouped", t.state(), t.groupBy); });
      // An ungrouped table alongside breakouts needs its own spec, or the
      // "tables" list would replace it rather than sit beside it.
      if (specs.length && q.flat) { specs.unshift({ group_by: [] }); }
      if (specs.length) { block.tables = specs; }
      out[qkey] = block;
    });
    return { questions: out };
  }

  // Report-level controls, wired after the sections so every setter is registered.
  var globalHost = document.getElementById("rr-global");
  if (globalHost && (globalSubs.percent_base || []).length) {
    var gPanel = buildPanel(globalHost, "Modify all tables");
    // Built from the same specs the tables use, then narrowed to the options at
    // least one table registered -- which is how orientation and the Overall
    // position stay out of a report with no crosstabs, and how a per-question
    // option like hide_codes never turns up here at all.
    var anyCustom = (globalSubs.row_order || []).some(function (sub) {
      return sub.get() === "response_order";
    });
    var gspecs = optionSpecs({ grouped: true, universe: [1], hasOverall: true,
                               hasCustomOrder: anyCustom })
      .filter(function (sp) { return (globalSubs[sp.key] || []).length; });
    var row = document.createElement("div");
    row.className = "rr-row";
    var gEls = {};
    gspecs.forEach(function (sp) {
      gEls[sp.key] = buildOption(row, sp, globalConsensus(sp.key), function (v) {
        setGlobal(sp.key, v);
        syncGlobals();
      }, true);
    });
    gPanel.appendChild(row);

    var row2 = document.createElement("div");
    row2.className = "rr-row";
    var statSeed = globalConsensus("stats") || (globalSubs.stats[0] && globalSubs.stats[0].get()) || [];
    var syncGChips = addStatChips(row2, constants.stat_order, statSeed,
      globalConsensus("percent_base") || constants.report_bases[0], function (stats) {
        setGlobal("stats", stats);
        syncGlobals();
      });
    gPanel.appendChild(row2);

    var note = document.createElement("div");
    note.className = "rr-note";
    gPanel.appendChild(note);
    // Only hide_codes stays per-table: it names this question's own response
    // codes, which mean nothing to the next question.
    var NOTE = "Applies to every table at once; each table can then override it in " +
      "its own controls. Hiding specific rows stays per-table, since the codes are " +
      "the question's own.";
    syncGlobals = function () {
      gspecs.forEach(function (sp) { syncOption(gEls[sp.key], sp, globalConsensus(sp.key)); });
      var st = globalConsensus("stats");
      if (st) { syncGChips(st); }
      var mixed = gspecs.filter(function (sp) { return globalConsensus(sp.key) === undefined; })
        .map(function (sp) { return (sp.global || sp.label).toLowerCase(); });
      if (st === undefined) { mixed.push("statistics"); }
      note.textContent = mixed.length
        ? NOTE + " Tables currently differ on: " + mixed.join(", ") + "."
        : NOTE;
    };
    syncGlobals();

    // The whole report as one config file, tracking every control on the page.
    var full = document.createElement("details");
    full.className = "rr-snippet";
    var fullSummary = document.createElement("summary");
    fullSummary.textContent = "Show config for the whole report";
    full.appendChild(fullSummary);
    globalHost.appendChild(full);
    var setFull = snippetPart(full,
      "Every question as currently shown \\u2014 paste over qualtrics_frequency_config.json, " +
      "or paste a config in and press Apply to rearrange the report to match. " +
      "include, percent_base, sort_by, response_order and include_empty_codes are what " +
      "the frequencies stage reads when it writes the CSVs; the report applies all of " +
      "them here too, so re-run that stage only when you want the CSVs to match.",
      applyReportConfig);
    refreshFullConfig = function () { setFull(buildReportConfig()); };
    refreshFullConfig();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", rrInit);
} else {
  rrInit();
}
"""


def _render_writein_table(text_rows: list[dict[str, str]]) -> str:
    """Render write-in / 'Other' responses for a question as a separate table.

    Verbatim responses are aggregated to (response, count) so duplicates
    collapse, and shown apart from the parent question's choice frequencies.
    """
    counts: Counter = Counter()
    for r in text_rows:
        value = (r.get("text_response") or "").strip()
        if value:
            counts[value] += 1
    if not counts:
        return ""
    body = "".join(
        f"<tr><td>{_esc(text)}</td><td class=\"num\">{n}</td></tr>"
        for text, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    total = sum(counts.values())
    return (
        f'<div class="meta">Write-in responses ({total})</div>'
        '<table class="writein"><thead><tr><th>Write-in response</th>'
        '<th class="num">n</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _render_question_section(
    qkey: str,
    rows: list[dict[str, str]],
    conditional: bool,
    writein_rows: list[dict[str, str]],
    presentation: dict,
    slug: str,
    universe: list[dict] | None = None,
) -> str:
    first = rows[0]
    question_id = first.get("question_id") or qkey
    question_text = first.get("question_text", "")
    qtype = first.get("question_type", "")
    scale = first.get("scale_type", "")
    report_base = first.get("report_base", "")

    show_code = presentation.get("show_code", True)
    stats = presentation.get("stats") or _DEFAULT_FLAT_STATS
    response_total = presentation.get("response_total", False)
    decimals = presentation.get("pct_decimals", 2)
    all_rows = rows
    rows = _apply_hidden(rows, presentation.get("hide_codes") or [])
    if not rows:  # every option hidden; fall back to the unfiltered table
        rows = all_rows
    has_attribute = any((r.get("attribute") or "").strip() for r in rows)
    featured = _PCT_FIELD.get(report_base)
    star_bases = sum(1 for s in stats if _stat_field(s, report_base).endswith("_pct")) > 1

    header_cells = []
    if has_attribute:
        header_cells.append("<th>Attribute</th>")
    if show_code:
        header_cells.append("<th>Code</th>")
    header_cells.append("<th>Label</th>")
    for stat in stats:
        # Star the column that matches the featured reporting base.
        # With a single percentage column the star would always sit on it, saying
        # nothing; it only earns its place when several bases are side by side.
        mark = " &#9733;" if (star_bases and _stat_field(stat, report_base) == featured) else ""
        header_cells.append(f'<th class="num">{_esc(_stat_label(stat, report_base))}{mark}</th>')

    def _row_html(label_cells: str, datarow: dict) -> str:
        cells = label_cells + "".join(
            _cell_html(datarow, [stat], report_base, decimals) for stat in stats
        )
        return f"<tr>{cells}</tr>"

    def _label_cells(r: dict) -> str:
        out = f"<td>{_esc(r.get('attribute'))}</td>" if has_attribute else ""
        if show_code:
            out += f"<td>{_esc(r.get('response_code'))}</td>"
        out += f"<td>{_esc(r.get('response_label'))}</td>"
        return out

    def _total_row() -> str:
        agg = _aggregate_rows(rows, report_base)
        span = (1 if has_attribute else 0) + (1 if show_code else 0)
        lead = (f'<td colspan="{span}"></td>' if span else "") + "<td><strong>Total</strong></td>"
        return _row_html(lead, agg)

    body = []
    if response_total == "before":
        body.append(_total_row())
    body.extend(_row_html(_label_cells(r), r) for r in rows)
    if response_total == "after":
        body.append(_total_row())

    badge = '<span class="badge">conditional</span>' if conditional else ""
    meta = (
        f"Type: {_esc(qtype)} &middot; Scale: {_esc(scale)}"
        f"{_base_caption(rows, report_base, len(all_rows) - len(rows))}"
    )
    writein = _render_writein_table(writein_rows) if writein_rows else ""
    tools_div = f'<div class="rr-tools" data-kind="flat" data-slug="{_esc(slug)}"></div>'
    data_blob = _json_script(
        {
            "kind": "flat",
            "question_id": question_id,
            "question_key": qkey,
            "meta_prefix": f"Type: {qtype} \u00b7 Scale: {scale}",
            "report_base": report_base,
            # Unfiltered, so the browser can re-apply or undo hide_codes itself.
            "rows": all_rows,
            "codes": _code_choices(all_rows, universe),
            # Every code the question defines, so the browser can show the ones
            # nobody chose and offer survey order -- neither is derivable from
            # rows that only describe answers people gave.
            "universe": universe or [],
            "presentation": presentation,
        },
        f"{slug}-data",
    )
    return (
        f'<section id="{_esc(qkey)}">'
        f'<h2>{_esc(question_id)}{badge}<a class="top" href="#top">top</a><br>'
        f'<span class="qtext">{_esc(question_text)}</span></h2>'
        f'<div class="meta">{meta}</div>'
        f"{tools_div}{data_blob}"
        f'<table id="{_esc(slug)}-table" class="rr-table"><thead><tr>{"".join(header_cells)}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
        f"{writein}"
        f"</section>"
    )


def _render_grouped_section(
    slug: str,
    rows: list[dict[str, str]],
    conditional: bool,
    presentation: dict,
    overall_rows: list[dict[str, str]] | None,
    universe: list[dict] | None = None,
) -> str:
    """Pivot a long grouped frequency table into a wide crosstab.

    Honors presentation options: orientation (group levels as columns or rows),
    an optional Overall column/row, an optional Total over response options,
    show_code, and which stats appear in each cell.
    """
    first = rows[0]
    question_id = first.get("question_id") or slug
    question_text = first.get("question_text", "")
    report_base = first.get("report_base", "eligible")
    group_keys = first.get("group_keys", "")
    n_field = _N_FIELD.get(report_base, "eligible_n")

    stats = presentation.get("stats") or _DEFAULT_CELL_STATS
    show_code = presentation.get("show_code", True)
    orientation = presentation.get("orientation", "columns")
    overall_opt = presentation.get("overall", False)
    response_total = presentation.get("response_total", False)
    decimals = presentation.get("pct_decimals", 2)
    hide_codes = presentation.get("hide_codes") or []
    # Kept unfiltered for the embedded blob, so the browser can undo hiding.
    all_rows = rows
    all_overall_rows = overall_rows

    # Per-group row lists (in appearance order) and their base sizes.
    group_rows: dict[str, list[dict]] = {}
    group_order: list[str] = []
    for r in rows:
        gc = r.get("group_codes", "")
        if gc not in group_rows:
            group_rows[gc] = []
            group_order.append(gc)
        group_rows[gc].append(r)
    if hide_codes:
        # Each group column is its own little table, so Valid n rebases within
        # the group rather than across the crosstab.
        group_rows = {gc: (_apply_hidden(rs, hide_codes) or rs) for gc, rs in group_rows.items()}
        if overall_rows:
            overall_rows = _apply_hidden(overall_rows, hide_codes) or overall_rows
        rows = [r for gc in group_order for r in group_rows[gc]]

    # Group axis: (code, label, base_n). Optionally inject an Overall level.
    groups = [
        (gc, group_rows[gc][0].get("group_labels", ""), group_rows[gc][0].get(n_field, ""))
        for gc in group_order
    ]
    overall_rows = overall_rows or []
    if overall_opt and overall_rows:
        ov = ("__overall__", "Overall", overall_rows[0].get(n_field, ""))
        groups = [ov, *groups] if overall_opt == "before" else [*groups, ov]

    # Response axis: (attr, code, label). First-seen across grouped (then overall).
    opts: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in list(rows) + overall_rows:
        key = (r.get("attribute", ""), r.get("response_code", ""))
        if key not in seen:
            seen.add(key)
            opts.append((r.get("attribute", ""), r.get("response_code", ""), r.get("response_label", "")))
    has_attr = any(a for a, _c, _l in opts)

    grouped_idx = {
        (r.get("group_codes", ""), r.get("attribute", ""), r.get("response_code", "")): r
        for r in rows
    }
    overall_idx = {(r.get("attribute", ""), r.get("response_code", "")): r for r in overall_rows}
    group_total = {gc: _aggregate_rows(group_rows[gc], report_base) for gc in group_order}
    overall_total = _aggregate_rows(overall_rows, report_base) if overall_rows else None

    def _data(gcode: str, attr: str, code: str, is_total: bool) -> dict | None:
        if gcode == "__overall__":
            return overall_total if is_total else overall_idx.get((attr, code))
        return group_total.get(gcode) if is_total else grouped_idx.get((gcode, attr, code))

    # Build the ordered response axis with optional Total marker.
    TOTAL = ("__total__", "", "Total")
    resp_axis = list(opts)
    if response_total == "before":
        resp_axis = [TOTAL, *resp_axis]
    elif response_total == "after":
        resp_axis = [*resp_axis, TOTAL]

    def _resp_label(attr: str, code: str, label: str) -> str:
        text = "Total" if attr == "__total__" else label
        if show_code and code not in ("", "__total__"):
            text = f"{code} &mdash; {_esc(label)}"
            return text  # already escaped label
        return _esc(text)

    def _group_header(label: str, base: str) -> str:
        return f'{_esc(label)}<br><span class="meta">n={_esc(base)}</span>'

    if orientation == "rows":
        # Rows = group levels; columns = response options.
        head = "<th>Group</th>"
        for attr, code, label in resp_axis:
            extra = f"{_esc(attr)}: " if (has_attr and attr not in ("", "__total__")) else ""
            head += f'<th class="num">{extra}{_resp_label(attr, code, label)}</th>'
        body = []
        for gcode, glabel, gbase in groups:
            cells = f"<td>{_group_header(glabel, gbase)}</td>"
            for attr, code, _label in resp_axis:
                is_total = attr == "__total__"
                cells += _cell_html(_data(gcode, attr, code, is_total), stats, report_base, decimals)
            body.append(f"<tr>{cells}</tr>")
    else:
        # Rows = response options; columns = group levels (default).
        head = ("<th>Attribute</th>" if has_attr else "") + (
            "<th>Code</th>" if show_code else ""
        ) + "<th>Response</th>"
        for _gc, glabel, gbase in groups:
            head += f'<th class="num">{_group_header(glabel, gbase)}</th>'
        body = []
        for attr, code, label in resp_axis:
            is_total = attr == "__total__"
            lead = f"<td>{'' if is_total else _esc(attr)}</td>" if has_attr else ""
            if show_code:
                lead += f"<td>{'' if is_total else _esc(code)}</td>"
            lead += f"<td>{'<strong>Total</strong>' if is_total else _esc(label)}</td>"
            cells = lead
            for gcode, _glabel, _gbase in groups:
                cells += _cell_html(_data(gcode, attr, code, is_total), stats, report_base, decimals)
            body.append(f"<tr>{cells}</tr>")

    badge = '<span class="badge">conditional</span>' if conditional else ""
    stat_names = ", ".join(_stat_label(s, report_base) for s in stats)
    meta = (
        f"Grouped by {_esc(group_keys)} &middot; orientation: {_esc(orientation)} &middot; "
        f"cells show {_esc(stat_names)} (within group)"
    )
    tools_div = f'<div class="rr-tools" data-kind="grouped" data-slug="{_esc(slug)}"></div>'
    data_blob = _json_script(
        {
            "kind": "grouped",
            "question_id": question_id,
            "question_key": first.get("question_key") or slug,
            "group_by": [g for g in group_keys.split(" | ") if g],
            "report_base": report_base,
            "group_keys": group_keys,
            # Unfiltered, so the browser can re-apply or undo hide_codes itself.
            "rows": all_rows,
            "overall_rows": all_overall_rows or None,
            "codes": _code_choices(all_rows, universe),
            # Every code the question defines, so the browser can show the ones
            # nobody chose and offer survey order -- neither is derivable from
            # rows that only describe answers people gave.
            "universe": universe or [],
            "presentation": presentation,
        },
        f"{slug}-data",
    )
    return (
        f'<section id="{_esc(slug)}">'
        f'<h2>{_esc(question_id)} &mdash; by {_esc(group_keys)}{badge}'
        f'<a class="top" href="#top">top</a><br>'
        f'<span class="qtext">{_esc(question_text)}</span></h2>'
        f'<div class="meta rr-meta">{meta}</div>'
        f"{tools_div}{data_blob}"
        f'<table id="{_esc(slug)}-table" class="rr-table crosstab"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
        f"</section>"
    )


def _load_writeins(text_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Group open-text / write-in responses by their parent question_key."""
    grouped: dict[str, list[dict[str, str]]] = {}
    if not text_dir.is_dir():
        return grouped
    for f in sorted(text_dir.glob("*_open_text.csv")):
        for r in load_csv_rows(f):
            qkey = r.get("question_key") or f.stem
            grouped.setdefault(qkey, []).append(r)
    return grouped


def generate_html_report(run_dir: str | Path, out_path: str | Path | None = None) -> Path:
    """Read a run directory's frequency CSVs and write report.html."""
    run_dir = Path(run_dir)
    freq_dir = run_dir / "frequency_tables"
    if not freq_dir.is_dir():
        raise SystemExit(f"No frequency_tables/ directory found in {run_dir}")

    manifest_path = run_dir / "frequency_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    conditional = set((manifest.get("conditional_questions") or {}).keys())
    presentation_map = manifest.get("table_presentation") or {}
    freq_opts_map = manifest.get("table_frequency_opts") or {}
    universe_map = manifest.get("table_code_universe") or {}
    data_path = manifest.get("data_path", "(unknown)")

    # Overall (ungrouped) rows by question_key, for the optional Overall column.
    overall_by_qkey: dict[str, list[dict[str, str]]] = {}
    blocks: list[tuple[tuple, str, str, bool, list[dict[str, str]]]] = []
    for csv_path in freq_dir.glob("*_frequencies.csv"):
        rows = load_csv_rows(csv_path)
        if not rows:
            continue
        stem = csv_path.stem
        slug = stem.removesuffix("_frequencies")
        qkey = rows[0].get("question_key") or slug
        is_grouped = bool((rows[0].get("group_keys") or "").strip())
        if not is_grouped:
            overall_by_qkey[qkey] = rows
        # Tiebreak by slug so the overall table sorts before its grouped variants.
        sort_key = _natural_question_key(rows[0].get("question_id", ""), slug)
        blocks.append((sort_key, slug, qkey, is_grouped, rows))
    blocks.sort(key=lambda b: b[0])

    def _presentation(slug: str) -> dict:
        return {
            **_PRES_DEFAULT,
            **_FREQ_DEFAULT,
            **(presentation_map.get(slug) or {}),
            **(freq_opts_map.get(slug) or {}),
        }

    def _index_label(rows: list[dict[str, str]]) -> str:
        qid = rows[0].get("question_id") or ""
        gk = (rows[0].get("group_keys") or "").strip()
        return f"{qid} — by {gk}" if gk else qid

    index_items = "".join(
        f'<li><a href="#{_esc(slug)}">{_esc(_index_label(rows))}</a> '
        f"&mdash; {_esc((rows[0].get('question_text') or '')[:60])}</li>"
        for _, slug, _qkey, _ig, rows in blocks
    )
    writeins = _load_writeins(run_dir / "open_text_outputs")
    sections = "".join(
        _render_grouped_section(
            slug, rows, qkey in conditional, _presentation(slug),
            overall_by_qkey.get(qkey), universe_map.get(slug),
        )
        if is_grouped
        else _render_question_section(
            qkey, rows, qkey in conditional, writeins.get(qkey, []), _presentation(slug), slug,
            universe_map.get(slug),
        )
        for _, slug, qkey, is_grouped, rows in blocks
    )
    # Render any write-ins whose parent question has no frequency table of its own.
    rendered_qkeys = {qkey for _, _slug, qkey, _ig, _rows in blocks}
    orphan = "".join(
        f'<section><h2>{_esc(qkey)} (write-in)<a class="top" href="#top">top</a></h2>'
        f"{_render_writein_table(rws)}</section>"
        for qkey, rws in sorted(writeins.items())
        if qkey not in rendered_qkeys
    )

    # astimezone() attaches the local offset: same wall-clock time as before, but
    # tz-aware, and %Z names the zone so a shared report's timestamp is unambiguous.
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    summary = (
        f'<div class="summary"><strong>{len(blocks)}</strong> question table(s) '
        f"from <code>{_esc(data_path)}</code>.<br>"
        "<span class=\"meta\">Each row carries three denominators: Valid % (of those who "
        "answered), Eligible % (of those shown the question per display logic), and Total % "
        "(of all respondents). The configured reporting base is marked &#9733;. Write-in / "
        "'Other' responses are shown in a separate table beneath each question. Grouped "
        "tables (crosstabs) show cells as n and the featured % within each group column. "
        'Questions gated by display logic are marked <span class="badge">conditional</span>. '
        "Use the controls above each table to try different formatting live in the browser; "
        "open “Show config snippet” to copy the JSON to paste into the frequency config."
        "</span></div>"
    )

    doc = (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Qualtrics Frequency Report</title>"
        f"<style>{_STYLE}</style></head><body><a id=\"top\"></a>"
        f"<h1>Qualtrics Frequency Report</h1>"
        f'<div class="meta">Generated {generated}</div>'
        f"{summary}"
        f'<div class="rr-tools" id="rr-global"></div>'
        f"<nav><h2>Questions</h2><ol>{index_items}</ol></nav>"
        f"{sections}{orphan}"
        f"{_constants_blob()}"
        f"<script>{_SCRIPT}</script>"
        "</body></html>"
    )

    out_path = Path(out_path) if out_path else run_dir / "report.html"
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render frequency tables to an HTML validation report")
    p.add_argument("--run-dir", required=True, help="Directory containing frequency_tables/")
    p.add_argument("--out", required=False, help="Output HTML path (default: <run-dir>/report.html)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out = generate_html_report(args.run_dir, args.out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
