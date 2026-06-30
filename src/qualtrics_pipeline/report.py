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


# --- presentation: stat selection -----------------------------------------

_PCT_FIELD = {"valid": "valid_pct", "eligible": "eligible_pct", "total": "total_pct"}
_N_FIELD = {"valid": "valid_n", "eligible": "eligible_n", "total": "total_n"}
_STAT_LABEL = {
    "n": "n", "valid_n": "Valid n", "valid_pct": "Valid %", "eligible_n": "Eligible n",
    "eligible_pct": "Eligible %", "total_n": "Total n", "total_pct": "Total %",
    "pct": "%", "base_n": "Base n",
}
_DEFAULT_FLAT_STATS = ["n", "valid_pct", "eligible_pct", "total_pct"]
_DEFAULT_CELL_STATS = ["n", "pct"]
_PRES_DEFAULT = {
    "show_code": True, "orientation": "columns",
    "overall": False, "response_total": False, "stats": None,
}


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
    return (
        f'<section id="{_esc(qkey)}">'
        f'<h2>{_esc(question_id)}{badge}<a class="top" href="#top">top</a><br>'
        f'<span class="qtext">{_esc(question_text)}</span></h2>'
        f'<div class="meta">{meta}</div>'
        f"<table><thead><tr>{''.join(header_cells)}</tr></thead>"
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
        groups = [ov] + groups if overall_opt == "before" else groups + [ov]

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
        resp_axis = [TOTAL] + resp_axis
    elif response_total == "after":
        resp_axis = resp_axis + [TOTAL]

    def _resp_label(attr: str, code: str, label: str) -> str:
        text = "Total" if code == "__total__" and attr == "__total__" else label
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
                is_total = code == "__total__"
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
            is_total = code == "__total__"
            lead = f"<td>{_esc(attr)}</td>" if has_attr else ""
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
    return (
        f'<section id="{_esc(slug)}">'
        f'<h2>{_esc(question_id)} &mdash; by {_esc(group_keys)}{badge}'
        f'<a class="top" href="#top">top</a><br>'
        f'<span class="qtext">{_esc(question_text)}</span></h2>'
        f'<div class="meta">{meta}</div>'
        f'<table class="crosstab"><thead><tr>{head}</tr></thead>'
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
        slug = stem[: -len("_frequencies")] if stem.endswith("_frequencies") else stem
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
            qkey, rows, qkey in conditional, writeins.get(qkey, []), _presentation(slug)
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

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = (
        f'<div class="summary"><strong>{len(blocks)}</strong> question table(s) '
        f"from <code>{_esc(data_path)}</code>.<br>"
        "<span class=\"meta\">Each row carries three denominators: Valid % (of those who "
        "answered), Eligible % (of those shown the question per display logic), and Total % "
        "(of all respondents). The configured reporting base is marked &#9733;. Write-in / "
        "'Other' responses are shown in a separate table beneath each question. Grouped "
        "tables (crosstabs) show cells as n and the featured % within each group column. "
        'Questions gated by display logic are marked <span class="badge">conditional</span>.'
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
