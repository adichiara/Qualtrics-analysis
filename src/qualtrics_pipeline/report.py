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
    qkey: str, rows: list[dict[str, str]], conditional: bool, writein_rows: list[dict[str, str]]
) -> str:
    first = rows[0]
    question_id = first.get("question_id") or qkey
    question_text = first.get("question_text", "")
    qtype = first.get("question_type", "")
    scale = first.get("scale_type", "")
    eligible_n = first.get("eligible_n", "")
    total_n = first.get("total_n", "")
    report_base = first.get("report_base", "")

    has_attribute = any((r.get("attribute") or "").strip() for r in rows)

    # Mark the configured reporting base in the header so it is clear which
    # percentage the final (Word) table would feature.
    def _pct_header(label: str, base: str) -> str:
        mark = " &#9733;" if base == report_base else ""
        return f'<th class="num">{label}{mark}</th>'

    header_cells = []
    if has_attribute:
        header_cells.append("<th>Attribute</th>")
    header_cells += [
        "<th>Code</th>", "<th>Label</th>",
        '<th class="num">n</th>', '<th class="num">Valid n</th>',
        _pct_header("Valid %", "valid"),
        _pct_header("Eligible %", "eligible"),
        _pct_header("Total %", "total"),
    ]

    body = []
    for r in rows:
        cells = []
        if has_attribute:
            cells.append(f"<td>{_esc(r.get('attribute'))}</td>")
        cells += [
            f"<td>{_esc(r.get('response_code'))}</td>",
            f"<td>{_esc(r.get('response_label'))}</td>",
            f'<td class="num">{_esc(r.get("n"))}</td>',
            f'<td class="num">{_esc(r.get("valid_n"))}</td>',
            f'<td class="num">{_fmt_pct(r.get("valid_pct", ""))}</td>',
            f'<td class="num">{_fmt_pct(r.get("eligible_pct", ""))}</td>',
            f'<td class="num">{_fmt_pct(r.get("total_pct", ""))}</td>',
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")

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


_PCT_FIELD = {"valid": "valid_pct", "eligible": "eligible_pct", "total": "total_pct"}
_N_FIELD = {"valid": "valid_n", "eligible": "eligible_n", "total": "total_n"}


def _render_grouped_section(slug: str, rows: list[dict[str, str]], conditional: bool) -> str:
    """Pivot a long grouped frequency table into a wide crosstab.

    Rows are response options; columns are the levels of the grouping
    variable(s). Each cell shows n and the configured report_base percentage
    (within-group). Group column headers show that group's base size.
    """
    first = rows[0]
    question_id = first.get("question_id") or slug
    question_text = first.get("question_text", "")
    report_base = first.get("report_base", "eligible")
    group_keys = first.get("group_keys", "")
    pct_field = _PCT_FIELD.get(report_base, "eligible_pct")
    n_field = _N_FIELD.get(report_base, "eligible_n")

    # Group levels (columns) in the order they appear; carry each group's base n.
    levels: list[tuple[str, str, str]] = []
    seen_levels: set[str] = set()
    for r in rows:
        gc = r.get("group_codes", "")
        if gc not in seen_levels:
            seen_levels.add(gc)
            levels.append((gc, r.get("group_labels", ""), r.get(n_field, "")))

    # Response options (rows) in first-seen order; identity is (attribute, code).
    opts: list[tuple[tuple[str, str], dict[str, str]]] = []
    seen_opts: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.get("attribute", ""), r.get("response_code", ""))
        if key not in seen_opts:
            seen_opts.add(key)
            opts.append((key, r))

    cell = {
        (r.get("group_codes", ""), r.get("attribute", ""), r.get("response_code", "")): r
        for r in rows
    }
    has_attr = any(attr for (attr, _), _ in opts)

    head = ("<th>Attribute</th><th>Response</th>" if has_attr else "<th>Response</th>")
    for _gc, glabel, gn in levels:
        head += f'<th class="num">{_esc(glabel)}<br><span class="meta">n={_esc(gn)}</span></th>'

    body = []
    for (attr, code), rr in opts:
        cells = f"<td>{_esc(attr)}</td>" if has_attr else ""
        cells += f"<td>{_esc(rr.get('response_label'))}</td>"
        for gc, _glabel, _gn in levels:
            c = cell.get((gc, attr, code))
            if c is None:
                cells += '<td class="num">&mdash;</td>'
            else:
                cells += (
                    f'<td class="num">{_esc(c.get("n"))}'
                    f'<br><span class="meta">{_fmt_pct(c.get(pct_field, ""))}</span></td>'
                )
        body.append(f"<tr>{cells}</tr>")

    badge = '<span class="badge">conditional</span>' if conditional else ""
    meta = (
        f"Grouped by {_esc(group_keys)} &middot; cells show n and {_esc(report_base)} % "
        "&#9733; (within group)"
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
    data_path = manifest.get("data_path", "(unknown)")

    blocks: list[tuple[tuple, str, str, bool, list[dict[str, str]]]] = []
    for csv_path in freq_dir.glob("*_frequencies.csv"):
        rows = load_csv_rows(csv_path)
        if not rows:
            continue
        stem = csv_path.stem
        slug = stem[: -len("_frequencies")] if stem.endswith("_frequencies") else stem
        qkey = rows[0].get("question_key") or slug
        is_grouped = bool((rows[0].get("group_keys") or "").strip())
        # Tiebreak by slug so the overall table sorts before its grouped variants.
        sort_key = _natural_question_key(rows[0].get("question_id", ""), slug)
        blocks.append((sort_key, slug, qkey, is_grouped, rows))
    blocks.sort(key=lambda b: b[0])

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
        _render_grouped_section(slug, rows, qkey in conditional)
        if is_grouped
        else _render_question_section(qkey, rows, qkey in conditional, writeins.get(qkey, []))
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
