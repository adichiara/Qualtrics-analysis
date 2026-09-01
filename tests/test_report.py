import csv
import json
from pathlib import Path

import pytest

from qualtrics_pipeline.report import _natural_question_key, generate_html_report


def _write_freq_csv(path: Path, rows: list[dict]) -> None:
    fields = ["question_key", "question_id", "question_text", "question_type", "attribute",
              "column", "scale_type", "response_code", "response_label", "n", "valid_n",
              "valid_pct", "eligible_n", "eligible_pct", "total_n", "total_pct", "report_base",
              "group_keys", "group_codes", "group_labels"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)


def _base_row(**kw) -> dict:
    row = {
        "question_key": "QID2", "question_id": "Q1.5", "question_text": "Installation",
        "question_type": "MC", "attribute": "", "column": "Q1.5", "scale_type": "nominal",
        "response_code": "1", "response_label": "Schofield", "n": "42", "valid_n": "101",
        "valid_pct": "41.58", "eligible_n": "101", "eligible_pct": "41.58", "total_n": "101",
        "total_pct": "41.58", "report_base": "eligible",
        "group_keys": "", "group_codes": "", "group_labels": "",
    }
    row.update(kw)
    return row


def _grouped_row(group_code, group_label, response_code, response_label, n, total_n, total_pct, **kw) -> dict:
    return _base_row(
        question_key="QID2", question_id="Q1.5", column="Q1.5",
        response_code=response_code, response_label=response_label, n=n,
        total_n=total_n, total_pct=total_pct, report_base="total",
        group_keys="Q1.9", group_codes=group_code, group_labels=group_label, **kw,
    )


def test_natural_question_order() -> None:
    keys = [
        _natural_question_key("Q1.10", "QIDa"),
        _natural_question_key("Q1.2", "QIDb"),
        _natural_question_key("Q2.1", "QIDc"),
        _natural_question_key("Q_DataPolicyViolations", "QIDd"),
    ]
    ordered = [k for k in sorted(keys)]
    # Q1.2 < Q1.10 < Q2.1 < non-numeric tag
    assert ordered[0][1] == [1, 2]
    assert ordered[1][1] == [1, 10]
    assert ordered[2][1] == [2, 1]
    assert ordered[3][0] == 1  # non-numeric sorts last


def test_generate_report_renders_values_and_conditional_badge(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)

    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_code="1", response_label="Schofield", n="42", valid_pct="41.58"),
        _base_row(response_code="2", response_label="Fort Bragg", n="34", valid_pct="33.66"),
    ])
    # A conditional question (eligible_n < total_n).
    _write_freq_csv(freq_dir / "QID3_frequencies.csv", [
        {"question_key": "QID3", "question_id": "Q1.6", "question_text": "Unit", "question_type": "MC",
         "attribute": "", "column": "Q1.6", "scale_type": "nominal", "response_code": "1",
         "response_label": "DIVARTY", "n": "9", "valid_n": "42", "valid_pct": "21.43",
         "eligible_n": "42", "eligible_pct": "21.43", "total_n": "101", "total_pct": "8.91",
         "report_base": "eligible"},
    ])
    (run_dir / "frequency_manifest.json").write_text(
        json.dumps({"data_path": "responses_clean.csv", "conditional_questions": {"QID3": 42}}),
        encoding="utf-8",
    )

    out = generate_html_report(run_dir)
    assert out == run_dir / "report.html"
    html = out.read_text(encoding="utf-8")

    assert html.startswith("<!DOCTYPE html>")
    # Values rendered
    assert "Schofield" in html
    assert "41.58%" in html
    assert ">42<" in html or "42" in html
    # Two question sections, indexed in survey order (Q1.5 before Q1.6)
    assert html.count("<section id=") == 2
    assert html.index("Q1.5") < html.index("Q1.6")
    # Conditional badge present for QID3. The caption names the base in force and
    # its size -- with one percentage column it is the only place the denominator
    # is stated, so it must be specific rather than listing every base.
    assert "conditional" in html
    assert "Base: Eligible n = 42" in html


def test_writein_table_rendered_under_parent_question(tmp_path) -> None:
    """Write-ins for a question render as a separate table inside that section,
    not folded into the main choice-frequency table."""
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    text_dir = run_dir / "open_text_outputs"
    freq_dir.mkdir(parents=True)
    text_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_code="3", response_label="Other:", n="25"),
    ])
    with (text_dir / "QID2_Q1.5_3_TEXT_open_text.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["question_key", "column", "text_response"])
        w.writeheader()
        # Duplicate "Camp Zama" should collapse to n=2.
        for val in ["Camp Zama", "Camp Zama", "Fort Hood"]:
            w.writerow({"question_key": "QID2", "column": "Q1.5_3_TEXT", "text_response": val})

    html = generate_html_report(run_dir).read_text(encoding="utf-8")
    # Other still a row in the main table
    assert "Other:" in html
    # Write-in table present, duplicates aggregated
    assert "Write-in response" in html
    assert "Camp Zama" in html
    assert "Fort Hood" in html
    # The write-in table lives inside the QID2 section (before the next section / end)
    section = html.split('id="QID2"')[1]
    assert "Camp Zama" in section


def test_writein_without_parent_table_renders_orphan(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    text_dir = run_dir / "open_text_outputs"
    freq_dir.mkdir(parents=True)
    text_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [_base_row()])
    with (text_dir / "QID9_Q9_TEXT_open_text.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["question_key", "column", "text_response"])
        w.writeheader()
        w.writerow({"question_key": "QID9", "column": "Q9_TEXT", "text_response": "Camp Zama"})

    html = generate_html_report(run_dir).read_text(encoding="utf-8")
    assert "QID9 (write-in)" in html
    assert "Camp Zama" in html


def test_grouped_table_renders_as_crosstab(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    # Q1.5 broken out by Q1.9 (uniform): two group levels, two response options.
    _write_freq_csv(freq_dir / "QID2__by__Q1.9_frequencies.csv", [
        _grouped_row("1", "Uniform A", "1", "Schofield", "10", "20", "50.0"),
        _grouped_row("1", "Uniform A", "2", "Fort Bragg", "10", "20", "50.0"),
        _grouped_row("2", "Uniform B", "1", "Schofield", "3", "15", "20.0"),
        _grouped_row("2", "Uniform B", "2", "Fort Bragg", "12", "15", "80.0"),
    ])
    html = generate_html_report(run_dir).read_text(encoding="utf-8")
    # Crosstab section present with group columns and within-group base sizes.
    assert "by Q1.9" in html
    assert "Uniform A" in html and "Uniform B" in html
    assert "crosstab" in html
    assert "n=20" in html and "n=15" in html        # per-group base in header
    assert "80.00%" in html                           # within-group cell pct (12/15)
    # Response options appear as rows.
    assert "Schofield" in html and "Fort Bragg" in html


def test_generate_report_escapes_html(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_label="A & B <script>"),
    ])
    html = generate_html_report(run_dir).read_text(encoding="utf-8")
    assert "A &amp; B &lt;script&gt;" in html
    # The raw, unescaped attacker string must never appear anywhere in the
    # document -- not in the visible table markup, and not inside the embedded
    # JSON data blob either (there '<'/'>'/'&' are \uXXXX-escaped rather than
    # HTML-entity-escaped, so the raw substring shouldn't appear there either).
    assert "A & B <script>" not in html


# ---------------------------------------------------------------------------
# Presentation options
# ---------------------------------------------------------------------------

def _write_manifest(run_dir: Path, presentation: dict) -> None:
    (run_dir / "frequency_manifest.json").write_text(
        json.dumps({"data_path": "x.csv", "table_presentation": presentation}),
        encoding="utf-8",
    )


def _flat_run(tmp_path, presentation=None):
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_code="1", response_label="Schofield", n="42", valid_pct="41.58"),
        _base_row(response_code="2", response_label="Fort Bragg", n="59", valid_pct="58.42"),
    ])
    if presentation is not None:
        _write_manifest(run_dir, {"QID2": presentation})
    return run_dir


def test_show_code_hidden(tmp_path) -> None:
    html = generate_html_report(_flat_run(tmp_path, {"show_code": False})).read_text()
    assert "<th>Code</th>" not in html
    # default keeps the Code column
    html2 = generate_html_report(_flat_run(tmp_path / "b")).read_text()
    assert "<th>Code</th>" in html2


def test_stats_percent_only(tmp_path) -> None:
    html = generate_html_report(
        _flat_run(tmp_path, {"stats": ["pct"], "show_code": False})
    ).read_text()
    # Only one stat column (the featured %); n / Valid n columns absent from the
    # rendered table. (The shared JS constants blob at the end of the document
    # legitimately lists every stat's label, for the browser-side stat-toggle
    # chips to offer -- exclude it here, it's not part of the rendered table.)
    body = html.split('id="rr-constants"')[0]
    assert "<th>Label</th>" in body
    assert "<th class=\"num\">n</th>" not in body
    assert "Valid n" not in body


def test_response_total_row(tmp_path) -> None:
    html = generate_html_report(
        _flat_run(tmp_path, {"response_total": "after", "stats": ["n"]})
    ).read_text()
    assert "Total" in html
    # 42 + 59 = 101 summed in the total row
    assert "101" in html


def _grouped_run(tmp_path, presentation):
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    # overall table (for the Overall column) + grouped table
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_code="1", response_label="Schofield", n="13", report_base="total",
                  total_n="35", total_pct="37.14"),
        _base_row(response_code="2", response_label="Fort Bragg", n="22", report_base="total",
                  total_n="35", total_pct="62.86"),
    ])
    _write_freq_csv(freq_dir / "QID2__by__Q1.9_frequencies.csv", [
        _grouped_row("1", "Uniform A", "1", "Schofield", "10", "20", "50.0"),
        _grouped_row("1", "Uniform A", "2", "Fort Bragg", "10", "20", "50.0"),
        _grouped_row("2", "Uniform B", "1", "Schofield", "3", "15", "20.0"),
        _grouped_row("2", "Uniform B", "2", "Fort Bragg", "12", "15", "80.0"),
    ])
    _write_manifest(run_dir, {"QID2__by__Q1.9": presentation})
    return run_dir


def test_grouped_overall_column(tmp_path) -> None:
    html = generate_html_report(
        _grouped_run(tmp_path, {"overall": "after", "stats": ["n"]})
    ).read_text()
    assert "Overall" in html
    # overall base size n=35 surfaced as a column header
    assert "n=35" in html


def test_grouped_orientation_rows(tmp_path) -> None:
    html = generate_html_report(
        _grouped_run(tmp_path, {"orientation": "rows", "stats": ["n"]})
    ).read_text()
    # Group axis becomes rows: a "Group" header cell, response options as columns.
    assert "<th>Group</th>" in html
    assert "Uniform A" in html and "Uniform B" in html
    assert "orientation: rows" in html


def test_grouped_response_total_shows_aggregated_values(tmp_path) -> None:
    """Regression test: the Total row in a crosstab must show the summed n within
    each group column (20 for Uniform A, 15 for Uniform B), not a placeholder dash."""
    html = generate_html_report(
        _grouped_run(tmp_path, {"response_total": "after", "stats": ["n"]})
    ).read_text()
    tbody = html.split('id="QID2__by__Q1.9"')[1].split("<tbody>")[1].split("</tbody>")[0]
    assert "&mdash;" not in tbody
    assert "<strong>Total</strong>" in tbody
    total_row = tbody.split("<strong>Total</strong>")[1]
    assert ">20<" in total_row
    assert ">15<" in total_row


def test_grouped_response_total_rows_orientation(tmp_path) -> None:
    html = generate_html_report(
        _grouped_run(tmp_path, {"orientation": "rows", "response_total": "after", "stats": ["n"]})
    ).read_text()
    tbody = html.split('id="QID2__by__Q1.9"')[1].split("<tbody>")[1].split("</tbody>")[0]
    assert "&mdash;" not in tbody


# ---------------------------------------------------------------------------
# Interactive scaffolding (embedded data blobs, control-bar placeholders)
# ---------------------------------------------------------------------------

def test_interactive_scaffolding_embedded(tmp_path) -> None:
    html = generate_html_report(_flat_run(tmp_path)).read_text()
    assert 'id="rr-constants"' in html
    assert '<div class="rr-tools" data-kind="flat" data-slug="QID2">' in html
    assert 'id="QID2-data"' in html
    assert 'id="QID2-table"' in html
    assert "function rrInit" in html
    # Constants blob carries the shared enums the JS port needs.
    assert '"default_flat_stats"' in html
    assert '"stat_labels"' in html


def test_stat_chip_labels_are_unique_for_every_base() -> None:
    """Regression: `pct`/`base_n` are aliases that resolve to whichever base is
    featured, so labelling the stat toggles with _STAT_LABEL rendered a duplicate
    pair (e.g. "Eligible n" and "Eligible %" twice). Toggles use _STAT_CHIP_LABEL,
    which must stay collision-free for every reporting base."""
    from qualtrics_pipeline.report import _STAT_CHIP_LABEL, _STAT_ORDER, _stat_label

    for base in ("valid", "eligible", "total"):
        labels = [_STAT_CHIP_LABEL.get(s) or _stat_label(s, base) for s in _STAT_ORDER]
        assert len(labels) == len(set(labels)), f"duplicate chip label for base {base}: {labels}"


def test_constants_blob_carries_chip_labels_and_definitions(tmp_path) -> None:
    """The browser reads labels/definitions from the shared constants blob rather
    than duplicating them as JS literals, so they must actually be published."""
    html = generate_html_report(_flat_run(tmp_path)).read_text()
    blob = html.split('id="rr-constants"')[1].split("</script>")[0]
    for key in ('"stat_chip_labels"', '"stat_definitions"', '"alias_stats"'):
        assert key in blob
    assert "Featured %" in blob and "Featured base n" in blob
    # every selectable stat is documented
    for stat in ["n", "valid_n", "valid_pct", "eligible_n", "eligible_pct",
                 "total_n", "total_pct", "pct", "base_n"]:
        assert f'"{stat}":' in blob


def test_grouped_scaffolding_overall_rows_null_when_absent(tmp_path) -> None:
    """When a question has no ungrouped/overall table, the embedded overall_rows
    must be explicit JSON null (not just omitted) so the browser-side Overall
    control can be reliably disabled rather than silently failing."""
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2__by__Q1.9_frequencies.csv", [
        _grouped_row("1", "Uniform A", "1", "Schofield", "10", "20", "50.0"),
        _grouped_row("2", "Uniform B", "1", "Schofield", "3", "15", "20.0"),
    ])
    html = generate_html_report(run_dir).read_text()
    blob = html.split('id="QID2__by__Q1.9-data"')[1].split("</script>")[0]
    assert '"overall_rows":null' in blob or '"overall_rows": null' in blob


# ---------------------------------------------------------------------------
# Decimal places, hidden rows, and the Valid n rebase
# ---------------------------------------------------------------------------

def _rebase_rows() -> list[dict]:
    """60 A / 30 B / 10 N/A out of valid_n 100 (single-response: counts sum to 100)."""
    return [
        _base_row(response_code="1", response_label="A", n="60", valid_n="100",
                  valid_pct="60.0", eligible_pct="60.0", total_pct="30.0", report_base="valid"),
        _base_row(response_code="2", response_label="B", n="30", valid_n="100",
                  valid_pct="30.0", eligible_pct="30.0", total_pct="15.0", report_base="valid"),
        _base_row(response_code="-1", response_label="N/A", n="10", valid_n="100",
                  valid_pct="10.0", eligible_pct="10.0", total_pct="5.0", report_base="valid"),
    ]


def _render(tmp_path, rows, presentation) -> str:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", rows)
    _write_manifest(run_dir, {"QID2": presentation})
    html = generate_html_report(run_dir).read_text()
    return html.split('id="QID2-table"')[1].split("</table>")[0]


def test_pct_decimals_controls_displayed_precision(tmp_path) -> None:
    rows = _rebase_rows()
    assert "60.00%" in _render(tmp_path, rows, {"stats": ["valid_pct"]})
    assert "60%" in _render(tmp_path / "b", rows, {"stats": ["valid_pct"], "pct_decimals": 0})
    assert "60.0000%" in _render(tmp_path / "c", rows, {"stats": ["valid_pct"], "pct_decimals": 4})


def test_hidden_rows_rebase_valid_n_only(tmp_path) -> None:
    """Hiding a code makes Valid n mean "chose one of the responses shown", so it
    and Valid % rebase; Eligible % and Total % are prevalence over people and
    must not move."""
    table = _render(tmp_path, _rebase_rows(), {
        "stats": ["n", "valid_n", "valid_pct", "eligible_pct", "total_pct"],
        "hide_codes": ["-1"], "show_code": False,
    })
    assert "N/A" not in table                 # row is gone
    assert ">90<" in table                    # valid_n 100 -> 90
    assert "66.67%" in table and "33.33%" in table   # 60/90 and 30/90
    assert "60.00%" in table and "30.00%" in table   # eligible % unchanged
    assert "15.00%" in table                  # total % unchanged


def test_hidden_rows_skip_rebase_for_multi_select(tmp_path) -> None:
    """Multi-select counts sum past valid_n, so the shown-response count is not
    derivable from the table; the row is hidden but Valid n is left alone."""
    rows = _rebase_rows()
    rows[1]["n"] = "50"  # 60 + 50 + 10 = 120 > valid_n 100
    table = _render(tmp_path, rows, {
        "stats": ["n", "valid_n"], "hide_codes": ["-1"], "show_code": False,
    })
    assert "N/A" not in table
    assert ">100<" in table and ">90<" not in table


def test_hide_picker_choices_embedded_with_labels(tmp_path) -> None:
    """The browser offers the codes actually present, with labels: "-1" is a real
    "Other" option in some exports, so N/A can't be hardcoded."""
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", _rebase_rows())
    blob = generate_html_report(run_dir).read_text().split('id="QID2-data"')[1].split("</script>")[0]
    assert '"codes"' in blob
    assert '"code": "-1"' in blob or '"code":"-1"' in blob
    assert "N/A" in blob


def test_base_n_not_offered_as_a_toggle() -> None:
    """base_n resolves to whichever base is featured, which a crosstab already
    prints in its group header and a flat table can name outright."""
    from qualtrics_pipeline.report import _STAT_ORDER

    assert "base_n" not in _STAT_ORDER
    assert "pct" in _STAT_ORDER  # crosstabs default to it


# ---------------------------------------------------------------------------
# Single reporting base: caption, star, and default stats
# ---------------------------------------------------------------------------

def _caption(html: str, slug: str = "QID2") -> str:
    import re
    return re.search(r'<div class="meta">(.*?)</div>', html.split(f'id="{slug}"')[1]).group(1)


def _based_rows() -> list[dict]:
    """Distinct bases so the caption and percentages can't be confused: 100 answered,
    120 eligible, 200 total."""
    return [
        _base_row(response_code="1", response_label="A", n="60", valid_n="100", valid_pct="60.0",
                  eligible_n="120", eligible_pct="50.0", total_n="200", total_pct="30.0"),
        _base_row(response_code="2", response_label="B", n="30", valid_n="100", valid_pct="30.0",
                  eligible_n="120", eligible_pct="25.0", total_n="200", total_pct="15.0"),
        _base_row(response_code="-1", response_label="N/A", n="10", valid_n="100", valid_pct="10.0",
                  eligible_n="120", eligible_pct="8.33", total_n="200", total_pct="5.0"),
    ]


def test_flat_default_is_a_single_percentage() -> None:
    """Flat tables now match crosstabs: one percentage that follows the reporting
    base, rather than three side by side."""
    from qualtrics_pipeline.report import _DEFAULT_CELL_STATS, _DEFAULT_FLAT_STATS

    assert _DEFAULT_FLAT_STATS == ["n", "pct"] == _DEFAULT_CELL_STATS


def test_caption_names_the_base_and_its_size(tmp_path) -> None:
    """With one percentage column the caption is the only place the denominator is
    stated, so it must name the base in force and its n."""
    table = _render(tmp_path, _based_rows(), {})
    assert "Eligible %" in table          # column follows report_base
    html = generate_html_report(tmp_path / "run").read_text()
    assert "Base: Eligible n = 120" in _caption(html)


def test_caption_reports_hidden_rows(tmp_path) -> None:
    _render(tmp_path, _based_rows(), {"hide_codes": ["-1"]})
    assert "(1 hidden)" in _caption(generate_html_report(tmp_path / "run").read_text())


def test_star_only_when_several_percentages_are_shown(tmp_path) -> None:
    """The star distinguishes one percentage column from others; with a single one
    it always lands on it and conveys nothing."""
    single = _render(tmp_path, _based_rows(), {"stats": ["n", "pct"]})
    assert "&#9733;" not in single

    several = _render(tmp_path / "b", _based_rows(),
                      {"stats": ["n", "valid_pct", "eligible_pct", "total_pct"]})
    assert "Eligible % &#9733;" in several   # the featured base is marked again


# ---------------------------------------------------------------------------
# Config export scaffolding
# ---------------------------------------------------------------------------

def test_sections_carry_the_keys_the_config_export_needs(tmp_path) -> None:
    """The whole-report config groups tables under their question, so each
    section has to name its question key, and a breakout its group_by."""
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [_base_row()])
    _write_freq_csv(freq_dir / "QID2__by__Q1.9_frequencies.csv", [
        _grouped_row("1", "Uniform A", "1", "Schofield", "10", "20", "50.0"),
    ])
    html = generate_html_report(run_dir).read_text()

    flat = html.split('id="QID2-data"')[1].split("</script>")[0]
    assert '"question_key": "QID2"' in flat

    grouped = html.split('id="QID2__by__Q1.9-data"')[1].split("</script>")[0]
    assert '"question_key": "QID2"' in grouped
    assert '"group_by": ["Q1.9"]' in grouped


def test_report_exposes_a_host_for_the_whole_report_config(tmp_path) -> None:
    html = generate_html_report(_flat_run(tmp_path)).read_text()
    assert 'id="rr-global"' in html
    # the browser builds both the per-table and the report-wide config from these
    assert "function buildReportConfig" in html
    assert "function tableConfig" in html
    assert "function questionConfig" in html


def test_config_vocabulary_published_for_the_browser(tmp_path) -> None:
    """Applying a pasted config means validating it in the browser, so the
    accepted values ship with the page rather than being duplicated as JS
    literals that could drift from the config validator."""
    from qualtrics_pipeline.frequencies import PCT_DECIMALS_MAX, STAT_KEYS

    html = generate_html_report(_flat_run(tmp_path)).read_text()
    blob = html.split('id="rr-constants"')[1].split("</script>")[0]
    for key in ('"stat_keys"', '"orientations"', '"positions"', '"report_bases"'):
        assert key in blob
    for stat in STAT_KEYS:
        assert f'"{stat}"' in blob
    assert f'"pct_decimals_max": {PCT_DECIMALS_MAX}' in blob


def test_report_can_read_a_config_back_in(tmp_path) -> None:
    html = generate_html_report(_flat_run(tmp_path)).read_text()
    # validation, application, and the whole-file distribution all ship
    assert "function readConfig" in html
    assert "function applyConfig" in html
    assert "function applyReportConfig" in html


# ---------------------------------------------------------------------------
# include_empty_codes reaches the browser as a question-level key
# ---------------------------------------------------------------------------

def _empty_codes_run(tmp_path, include_empty: bool):
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_code="1", response_label="Schofield", n="42"),
        _base_row(response_code="2", response_label="Fort Bragg", n="59"),
        _base_row(response_code="3", response_label="Nobody picked me", n="0", valid_pct="0.0",
                  eligible_pct="0.0", total_pct="0.0"),
    ])
    (run_dir / "frequency_manifest.json").write_text(
        json.dumps({
            "data_path": "x.csv",
            "table_presentation": {"QID2": {}},
            "table_frequency_opts": {"QID2": {"include_empty_codes": include_empty}},
        }),
        encoding="utf-8",
    )
    return run_dir


def _blob(html: str, slug: str) -> dict:
    raw = html.split(f'id="{slug}-data">')[1].split("</script>")[0]
    return json.loads(raw)


def test_include_empty_codes_reaches_the_data_blob(tmp_path) -> None:
    html = generate_html_report(_empty_codes_run(tmp_path, True)).read_text()
    assert _blob(html, "QID2")["presentation"]["include_empty_codes"] is True


def test_include_empty_codes_defaults_off_without_a_manifest_entry(tmp_path) -> None:
    html = generate_html_report(_flat_run(tmp_path)).read_text()
    assert _blob(html, "QID2")["presentation"]["include_empty_codes"] is False


def test_zero_count_rows_are_server_rendered_as_written(tmp_path) -> None:
    # The initial HTML mirrors the CSV; only the browser control filters them.
    html = generate_html_report(_empty_codes_run(tmp_path, True)).read_text()
    table = html.split('id="QID2-table"')[1].split("</table>")[0]
    assert "Nobody picked me" in table


def test_include_empty_codes_is_a_question_level_config_key() -> None:
    from qualtrics_pipeline import report as report_mod

    script = report_mod._SCRIPT
    # Emitted in the question block, never in a table spec.
    assert "include_empty_codes: !!state.include_empty_codes" in script
    assert '"include_empty_codes"];' in script
    table_keys = script.split('var TABLE_KEYS = ')[1].split("];")[0]
    assert "include_empty_codes" not in table_keys


# ---------------------------------------------------------------------------
# Browser-side controls: what they cover and that the script actually parses
# ---------------------------------------------------------------------------

def test_hide_picker_choices_are_ordered_by_code() -> None:
    from qualtrics_pipeline.report import _code_choices

    rows = [
        {"response_code": "10", "response_label": "Ten"},
        {"response_code": "2", "response_label": "Two"},
        {"response_code": "-1", "response_label": "Other"},
        {"response_code": "typed answer", "response_label": "typed answer"},
        {"response_code": "9", "response_label": "Nine"},
    ]
    # Numeric where the codes are numeric, so 10 follows 9 rather than 1;
    # verbatim write-in "codes" sort after the coded ones.
    assert [c["code"] for c in _code_choices(rows)] == ["-1", "2", "9", "10", "typed answer"]


def test_universe_reaches_the_data_blob(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [_base_row()])
    universe = [{"column": "Q1.5", "attribute": "", "multi_select": False,
                 "codes": [["1", "Schofield"], ["2", "Fort Bragg"]]}]
    (run_dir / "frequency_manifest.json").write_text(
        json.dumps({"data_path": "x.csv", "table_code_universe": {"QID2": universe}}),
        encoding="utf-8",
    )
    html = generate_html_report(run_dir).read_text()
    assert _blob(html, "QID2")["universe"] == universe


def test_resolved_sort_by_reaches_the_data_blob(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [_base_row()])
    (run_dir / "frequency_manifest.json").write_text(
        json.dumps({"data_path": "x.csv",
                    "table_frequency_opts": {"QID2": {"sort_by": "survey_order"}}}),
        encoding="utf-8",
    )
    html = generate_html_report(run_dir).read_text()
    assert _blob(html, "QID2")["presentation"]["sort_by"] == "survey_order"


def test_every_browser_drivable_config_key_has_a_control() -> None:
    """The control bar must not quietly fall behind the config vocabulary."""
    from qualtrics_pipeline.config_validate import KNOWN_QUESTION_KEYS
    from qualtrics_pipeline.report import _SCRIPT

    specs = _SCRIPT.split("function optionSpecs(")[1].split("\n  }")[0]
    keyed = {line.split('key: "')[1].split('"')[0] for line in specs.splitlines() if 'key: "' in line}
    # stats has its own chip widget and hide_codes its own picker, so neither is
    # a row spec; row_order is the control name for sort_by/response_order.
    covered = keyed | {"stats", "hide_codes", "sort_by", "response_order"}
    # Keys the browser cannot act on: text_reporting_mode is per column and
    # "tables"/"group_by" build a table that does not exist yet, both of which
    # need the frequencies stage to run before there is anything to show.
    not_offered = {"tables", "text_entry_columns", "frequency_mode"}
    assert KNOWN_QUESTION_KEYS - covered == not_offered


def test_report_script_is_syntactically_valid_javascript() -> None:
    """A mangled escape in the embedded JS breaks every control at once.

    Nothing else in the suite would notice: the HTML still renders, the tables
    still show, and only the browser console says the script never ran.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available to parse the embedded script")
    from qualtrics_pipeline.report import _SCRIPT

    proc = subprocess.run([node, "--check", "-"], input=_SCRIPT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
