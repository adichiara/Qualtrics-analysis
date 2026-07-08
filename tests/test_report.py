import csv
import json
from pathlib import Path

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
    # Conditional badge present for QID3, eligible/total bases surfaced
    assert "conditional" in html
    assert "Eligible n: 42" in html
    assert "Total n: 101" in html


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
