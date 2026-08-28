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


def _fmt_pct(value: str) -> str:
    try:
        return f"{float(value):.2f}%"
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
_DEFAULT_FLAT_STATS = ["n", "valid_pct", "eligible_pct", "total_pct"]
_DEFAULT_CELL_STATS = ["n", "pct"]
_PRES_DEFAULT = {
    "show_code": True, "orientation": "columns",
    "overall": False, "response_total": False, "stats": None,
}
_STAT_ORDER = ["n", "valid_n", "valid_pct", "eligible_n", "eligible_pct", "total_n", "total_pct", "pct", "base_n"]


def _constants_blob() -> str:
    """Shared enums/labels/defaults for the in-browser JS, sourced from the
    same constants the Python renderers use so the two can't drift apart."""
    data = {
        "stat_labels": _STAT_LABEL,
        "stat_chip_labels": _STAT_CHIP_LABEL,
        "stat_definitions": _STAT_DEFINITION,
        "stat_order": _STAT_ORDER,
        "alias_stats": sorted(_STAT_CHIP_LABEL),
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


def _stat_value(row: dict | None, stat: str, report_base: str) -> str:
    field = _stat_field(stat, report_base)
    val = (row or {}).get(field, "")
    return _fmt_pct(val) if field.endswith("_pct") else _esc(val)


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


def _cell_html(row: dict | None, stats: list[str], report_base: str) -> str:
    if row is None:
        return '<td class="num">&mdash;</td>'
    if len(stats) == 1:
        return f'<td class="num">{_stat_value(row, stats[0], report_base)}</td>'
    parts = []
    for i, stat in enumerate(stats):
        val = _stat_value(row, stat, report_base)
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
.rr-snippet summary { font-size: 0.85rem; font-weight: 600; }
.rr-snippet-body { width: 100%; box-sizing: border-box; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                    font-size: 0.8rem; margin: 0.4rem 0; padding: 0.4rem; }
.rr-snippet-hint { font-size: 0.78rem; color: #555; margin-bottom: 0.3rem; }
.rr-copy-btn { font-size: 0.8rem; padding: 0.15rem 0.6rem; cursor: pointer; }
.rr-copy-btn:disabled { cursor: not-allowed; opacity: 0.6; }
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
  function fmtPct(raw) {
    if (raw === null || raw === undefined || raw === "") { return ""; }
    var n = Number(raw);
    if (Number.isNaN(n)) { return String(raw); }
    return n.toFixed(2) + "%";
  }
  function statValue(row, stat, reportBase) {
    var field = statField(stat, reportBase);
    var val = row ? row[field] : undefined;
    if (val === undefined || val === null) { val = ""; }
    return field.slice(-4) === "_pct" ? fmtPct(val) : String(val);
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
  function cellNode(row, stats, reportBase) {
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
      cell.appendChild(document.createTextNode(statValue(row, stat, reportBase)));
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
    stats.forEach(function (stat) {
      var mark = statField(stat, reportBase) === featured ? " \\u2605" : "";
      theadRow.appendChild(
        sortableTh(statLabel(stat, reportBase) + mark, "num", { kind: "stat", stat: stat }, sort, onSort)
      );
    });
  }
  function buildFlatTotalRow(rows, hasAttr, showCode, stats, reportBase) {
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
    stats.forEach(function (stat) { tr.appendChild(cellNode(agg, [stat], reportBase)); });
    return tr;
  }
  function renderFlatTable(tableEl, rows, reportBase, presentation, sort, onSort) {
    var thead = tableEl.querySelector("thead tr");
    var tbody = tableEl.querySelector("tbody");
    var hasAttr = hasAttributeIn(rows);
    var stats = (presentation.stats && presentation.stats.length) ? presentation.stats : constants.default_flat_stats;
    var showCode = !!presentation.show_code;
    buildFlatHeader(thead, hasAttr, showCode, stats, reportBase, sort, onSort);
    tbody.innerHTML = "";
    // The Total row is synthesized from the full, unsorted row set and was never a
    // member of it, so sorting can neither reorder nor alter it.
    if (presentation.response_total === "before") {
      var totalBefore = buildFlatTotalRow(rows, hasAttr, showCode, stats, reportBase);
      if (totalBefore) { tbody.appendChild(totalBefore); }
    }
    sortItems(rows, sort, function (row) { return flatValueOf(row, sort.key, reportBase); }).forEach(function (row) {
      var tr = document.createElement("tr");
      if (hasAttr) { tr.appendChild(td(row.attribute || "")); }
      if (showCode) { tr.appendChild(td(row.response_code || "")); }
      tr.appendChild(td(row.response_label || ""));
      stats.forEach(function (stat) { tr.appendChild(cellNode(row, [stat], reportBase)); });
      tbody.appendChild(tr);
    });
    if (presentation.response_total === "after") {
      var totalAfter = buildFlatTotalRow(rows, hasAttr, showCode, stats, reportBase);
      if (totalAfter) { tbody.appendChild(totalAfter); }
    }
  }

  // ---- grouped (crosstab) table ----
  function pivotGrouped(rows, overallRows, presentation, reportBase) {
    var nField = constants.n_field[reportBase] || "eligible_n";
    var groupRows = {};
    var groupOrder = [];
    rows.forEach(function (r) {
      var gc = r.group_codes || "";
      if (!(gc in groupRows)) { groupRows[gc] = []; groupOrder.push(gc); }
      groupRows[gc].push(r);
    });
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
  function renderGroupedTable(tableEl, pivot, stats, showCode, orientation, reportBase, sort, onSort) {
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
          tr.appendChild(cellNode(data(g.code, opt.attr, opt.code, isAggregateOpt(opt)), stats, reportBase));
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
          tr.appendChild(cellNode(data(g.code, opt.attr, opt.code, isTotal), stats, reportBase));
        });
        tbody.appendChild(tr);
      });
    }
    return pivot;
  }
  function renderGrouped(tableEl, sdata, presentation, sort, onSort) {
    var pivot = pivotGrouped(sdata.rows, sdata.overall_rows, presentation, sdata.report_base);
    var stats = (presentation.stats && presentation.stats.length) ? presentation.stats : constants.default_cell_stats;
    renderGroupedTable(tableEl, pivot, stats, !!presentation.show_code,
      presentation.orientation || "columns", sdata.report_base, sort, onSort);
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
  }

  // ---- statistic definitions ----
  function buildDefsPanel(container, reportBase, kind, getStats) {
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
  function buildDiff(kind, effective, edited) {
    var keys = kind === "flat"
      ? ["show_code", "stats", "response_total"]
      : ["show_code", "orientation", "overall", "response_total", "stats"];
    var diff = {};
    keys.forEach(function (key) {
      var same = key === "stats" ? arraysEqual(effective[key], edited[key]) : effective[key] === edited[key];
      if (!same) { diff[key] = edited[key]; }
    });
    return diff;
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
  function snippetPart(parent, hintText) {
    var wrap = document.createElement("div");
    wrap.className = "rr-snippet-part";
    var hint = document.createElement("div");
    hint.className = "rr-snippet-hint";
    hint.textContent = hintText;
    wrap.appendChild(hint);
    var textarea = document.createElement("textarea");
    textarea.className = "rr-snippet-body";
    textarea.readOnly = true;
    textarea.rows = 4;
    wrap.appendChild(textarea);
    var copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "rr-copy-btn";
    copyBtn.textContent = "Copy";
    wrap.appendChild(copyBtn);
    var note = document.createElement("span");
    note.className = "rr-note";
    note.style.marginLeft = "0.5rem";
    wrap.appendChild(note);
    parent.appendChild(wrap);

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
          setTimeout(function () { note.textContent = ""; }, 1500);
        }, fallback);
      } else {
        fallback();
      }
    });
    return function (obj) {
      var empty = !obj || Object.keys(obj).length === 0;
      wrap.style.display = empty ? "none" : "";
      // Cleared rather than just hidden, so a stale snippet can't be copied.
      textarea.value = empty ? "" : JSON.stringify(obj, null, 2);
      copyBtn.disabled = empty;
      note.textContent = "";
      return !empty;
    };
  }
  function buildSnippetPanel(container, kind, questionId, groupKeys, effective, getEdited, getSort, getAxis) {
    var details = document.createElement("details");
    details.className = "rr-snippet";
    var summary = document.createElement("summary");
    summary.textContent = "Show config snippet";
    details.appendChild(summary);
    container.appendChild(details);

    var groupByText = groupKeys.map(function (g) { return '"' + g + '"'; }).join(", ");
    var presHint = kind === "flat"
      ? "Paste into \\"" + questionId + "\\"'s block in qualtrics_frequency_config.json."
      : "Paste into the matching \\"tables\\" entry (group_by: [" + groupByText +
        "]) for \\"" + questionId + "\\".";
    // Sort keys always belong to the question block, even for a grouped table.
    var sortHint = "Paste into \\"" + questionId + "\\"'s block (not the \\"tables\\" entry). " +
      "Applied when frequency tables are generated \\u2014 re-run the frequencies stage to see it.";

    var setPres = snippetPart(details, presHint);
    var setSort = snippetPart(details, sortHint);

    var empty = document.createElement("div");
    empty.className = "rr-snippet-hint";
    empty.textContent = "No changes \\u2014 matches the current config.";
    details.appendChild(empty);
    var sortNote = document.createElement("div");
    sortNote.className = "rr-snippet-hint";
    details.appendChild(sortNote);

    function refresh() {
      var shown = setPres(buildDiff(kind, effective, getEdited()));
      var sc = sortConfig(getSort(), getAxis());
      shown = setSort(sc.keys) || shown;
      sortNote.textContent = sc.note;
      empty.style.display = shown ? "none" : "";
    }
    return refresh;
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
    };
    var state = {
      show_code: effective.show_code,
      stats: effective.stats.slice(),
      response_total: effective.response_total,
      sort: null,
    };
    var hasAttr = hasAttributeIn(sdata.rows);

    function onSort(key) { state.sort = nextSort(state.sort, key); rerender(); }
    function rerender() {
      renderFlatTable(tableEl, sdata.rows, sdata.report_base, state, state.sort, onSort);
      refreshDefs();
      refreshSnippet();
    }

    var row1 = document.createElement("div");
    row1.className = "rr-row";
    addCheckbox(row1, "Show code column", state.show_code, function (v) { state.show_code = v; rerender(); });
    addSelect(row1, "Total row", state.response_total || "", POSITION_OPTS, function (v) { state.response_total = v || false; rerender(); });
    toolsEl.appendChild(row1);

    var row2 = document.createElement("div");
    row2.className = "rr-row";
    addStatChips(row2, constants.stat_order, state.stats, sdata.report_base, function (stats) { state.stats = stats; rerender(); });
    toolsEl.appendChild(row2);

    var refreshDefs = buildDefsPanel(toolsEl, sdata.report_base, "flat", function () { return state.stats; });
    var refreshSnippet = buildSnippetPanel(toolsEl, "flat", sdata.question_id, [], effective,
      function () {
        return { show_code: state.show_code, stats: state.stats, response_total: state.response_total };
      },
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
    };
    var state = {
      show_code: effective.show_code,
      orientation: effective.orientation,
      overall: effective.overall,
      response_total: effective.response_total,
      stats: effective.stats.slice(),
      sort: null,
    };
    var metaEl = toolsEl.previousElementSibling;
    if (!metaEl || !metaEl.classList || !metaEl.classList.contains("meta")) { metaEl = null; }
    var lastPivot = null;

    // Switching orientation swaps which axis the rows are, so a sort key from the
    // previous orientation no longer addresses a real column.
    function onSort(key) { state.sort = nextSort(state.sort, key); rerender(); }
    function rerender() {
      lastPivot = renderGrouped(tableEl, sdata, state, state.sort, onSort);
      if (metaEl) {
        var statNames = state.stats.map(function (s) { return statLabel(s, sdata.report_base); }).join(", ");
        metaEl.textContent = "Grouped by " + sdata.group_keys + " \\u00b7 orientation: " + state.orientation +
          " \\u00b7 cells show " + statNames + " (within group)";
      }
      refreshDefs();
      refreshSnippet();
    }

    var row1 = document.createElement("div");
    row1.className = "rr-row";
    addCheckbox(row1, "Show code column", state.show_code, function (v) { state.show_code = v; rerender(); });
    addSelect(row1, "Orientation", state.orientation, [["columns", "Columns"], ["rows", "Rows"]], function (v) {
      state.orientation = v;
      state.sort = null; // the old sort key addressed the other axis's columns
      rerender();
    });
    toolsEl.appendChild(row1);

    var row2 = document.createElement("div");
    row2.className = "rr-row";
    var overallSelect = addSelect(row2, "Overall column/row", state.overall || "", POSITION_OPTS, function (v) { state.overall = v || false; rerender(); });
    if (!hasOverall) {
      overallSelect.disabled = true;
      overallSelect.title = "No ungrouped table was generated for this question, so there is no Overall data to show.";
    }
    addSelect(row2, "Response total", state.response_total || "", POSITION_OPTS, function (v) { state.response_total = v || false; rerender(); });
    toolsEl.appendChild(row2);

    var row3 = document.createElement("div");
    row3.className = "rr-row";
    addStatChips(row3, constants.stat_order, state.stats, sdata.report_base, function (stats) { state.stats = stats; rerender(); });
    toolsEl.appendChild(row3);

    var refreshDefs = buildDefsPanel(toolsEl, sdata.report_base, "grouped", function () { return state.stats; });
    var groupByList = sdata.group_keys ? sdata.group_keys.split(" | ") : [];
    var refreshSnippet = buildSnippetPanel(toolsEl, "grouped", sdata.question_id, groupByList, effective,
      function () {
        return {
          show_code: state.show_code, orientation: state.orientation, overall: state.overall,
          response_total: state.response_total, stats: state.stats,
        };
      },
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
              return cellSortValue(lastPivot.data(gcode, opt.attr, opt.code, false), state.stats, sdata.report_base);
            }).filter(function (opt) { return !isAggregateOpt(opt); })
              .map(function (opt) { return opt.code || ""; });
          },
        };
      });
    rerender(); // re-render once so the headers become sort controls
  }

  document.querySelectorAll(".rr-tools[data-slug]").forEach(function (el) {
    var slug = el.getAttribute("data-slug");
    if (el.getAttribute("data-kind") === "grouped") { initGroupedSection(el, slug); }
    else { initFlatSection(el, slug); }
  });
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
) -> str:
    first = rows[0]
    question_id = first.get("question_id") or qkey
    question_text = first.get("question_text", "")
    qtype = first.get("question_type", "")
    scale = first.get("scale_type", "")
    eligible_n = first.get("eligible_n", "")
    total_n = first.get("total_n", "")
    report_base = first.get("report_base", "")

    show_code = presentation.get("show_code", True)
    stats = presentation.get("stats") or _DEFAULT_FLAT_STATS
    response_total = presentation.get("response_total", False)
    has_attribute = any((r.get("attribute") or "").strip() for r in rows)
    featured = _PCT_FIELD.get(report_base)

    header_cells = []
    if has_attribute:
        header_cells.append("<th>Attribute</th>")
    if show_code:
        header_cells.append("<th>Code</th>")
    header_cells.append("<th>Label</th>")
    for stat in stats:
        # Star the column that matches the featured reporting base.
        mark = " &#9733;" if _stat_field(stat, report_base) == featured else ""
        header_cells.append(f'<th class="num">{_esc(_stat_label(stat, report_base))}{mark}</th>')

    def _row_html(label_cells: str, datarow: dict) -> str:
        cells = label_cells + "".join(
            _cell_html(datarow, [stat], report_base) for stat in stats
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
    reported = f" &middot; Reported base: {_esc(report_base)} &#9733;" if report_base else ""
    meta = (
        f"Type: {_esc(qtype)} &middot; Scale: {_esc(scale)} &middot; "
        f"Eligible n: {_esc(eligible_n)} &middot; Total n: {_esc(total_n)}{reported}"
    )
    writein = _render_writein_table(writein_rows) if writein_rows else ""
    tools_div = f'<div class="rr-tools" data-kind="flat" data-slug="{_esc(slug)}"></div>'
    data_blob = _json_script(
        {
            "kind": "flat",
            "question_id": question_id,
            "report_base": report_base,
            "rows": rows,
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

    # Per-group row lists (in appearance order) and their base sizes.
    group_rows: dict[str, list[dict]] = {}
    group_order: list[str] = []
    for r in rows:
        gc = r.get("group_codes", "")
        if gc not in group_rows:
            group_rows[gc] = []
            group_order.append(gc)
        group_rows[gc].append(r)

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
                cells += _cell_html(_data(gcode, attr, code, is_total), stats, report_base)
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
                cells += _cell_html(_data(gcode, attr, code, is_total), stats, report_base)
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
            "report_base": report_base,
            "group_keys": group_keys,
            "rows": rows,
            "overall_rows": overall_rows or None,
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
        return {**_PRES_DEFAULT, **(presentation_map.get(slug) or {})}

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
            slug, rows, qkey in conditional, _presentation(slug), overall_by_qkey.get(qkey)
        )
        if is_grouped
        else _render_question_section(
            qkey, rows, qkey in conditional, writeins.get(qkey, []), _presentation(slug), slug
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
