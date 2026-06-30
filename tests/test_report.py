import csv
import json
from pathlib import Path

from qualtrics_pipeline.report import _natural_question_key, generate_html_report


def _write_freq_csv(path: Path, rows: list[dict]) -> None:
    fields = ["question_key", "question_id", "question_text", "question_type", "attribute",
              "column", "scale_type", "response_code", "response_label", "n", "valid_n",
              "valid_pct", "eligible_n", "eligible_pct", "total_n", "total_pct", "report_base"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _base_row(**kw) -> dict:
    row = {
        "question_key": "QID2", "question_id": "Q1.5", "question_text": "Installation",
        "question_type": "MC", "attribute": "", "column": "Q1.5", "scale_type": "nominal",
        "response_code": "1", "response_label": "Schofield", "n": "42", "valid_n": "101",
        "valid_pct": "41.58", "eligible_n": "101", "eligible_pct": "41.58", "total_n": "101",
        "total_pct": "41.58", "report_base": "eligible",
    }
    row.update(kw)
    return row


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


def test_generate_report_escapes_html(tmp_path) -> None:
    run_dir = tmp_path / "run"
    freq_dir = run_dir / "frequency_tables"
    freq_dir.mkdir(parents=True)
    _write_freq_csv(freq_dir / "QID2_frequencies.csv", [
        _base_row(response_label="A & B <script>"),
    ])
    html = generate_html_report(run_dir).read_text(encoding="utf-8")
    assert "A &amp; B &lt;script&gt;" in html
    assert "<script>" not in html.split("<style>")[1]  # not injected into body
