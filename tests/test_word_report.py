import json
from pathlib import Path

import pytest
from docx import Document

from qualtrics_pipeline.config_validate import validate_config
from qualtrics_pipeline.frequencies import run_frequency_analysis
from qualtrics_pipeline.report import _aggregate_rows
from qualtrics_pipeline.word_report import (
    DEFAULT_STYLE,
    generate_word_report,
    resolve_style,
    validate_style,
)


def _col(col, qid, tag, labels, selector="SAVR", qt="MC", sub="", text=None):
    return {
        "survey_id": "SV", "qid": qid, "data_export_tag": tag, "column": col,
        "question_type": qt, "selector": selector, "question_text": text or f"{tag} text",
        "sub_question_text": sub, "response_labels": labels, "is_open_text": False,
        "is_metadata": False, "is_sensitive": False, "is_text_entry_suffix": False,
        "parent_question_key": qid, "parent_choice_code": "", "parent_choice_label": "",
        "text_reporting_mode": "skip",
    }


_SELECTED = {"0": "Not selected", "1": "Selected"}


def _run(tmp_path: Path, questions: dict, rows: list[dict], cmap: list[dict], word=None) -> Path:
    data = tmp_path / "data.csv"
    cols = list(rows[0])
    data.write_text(
        ",".join(cols) + "\n" + "\n".join(",".join(r.get(c, "") for c in cols) for r in rows) + "\n",
        encoding="utf-8",
    )
    cmap_path = tmp_path / "column_map.json"
    cmap_path.write_text(json.dumps(cmap), encoding="utf-8")
    cfg = {"questions": questions}
    if word is not None:
        cfg["word"] = word
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    out = tmp_path / "out"
    run_frequency_analysis(data, cmap_path, out, cfg_path)
    return out


def _basic(tmp_path, q1=None, word=None):
    rows = [
        {"R": str(i), "G": "1" if i < 6 else "2", "Q1": ["1", "2", "1", "", "2", "1", "2", "", "1", "1"][i]}
        for i in range(10)
    ]
    cmap = [
        _col("G", "QG", "G", {"1": "Army", "2": "Navy"}),
        _col("Q1", "QA", "Q1", {"1": "Yes", "2": "No"}, text="<div>Do you <b>agree</b>?</div>"),
    ]
    return _run(tmp_path, {"QA": q1 or {}}, rows, cmap, word)


def _table_text(table) -> list[list[str]]:
    return [[c.text for c in row.cells] for row in table.rows]


def _doc(run_dir: Path) -> Document:
    return Document(str(run_dir / "report.docx"))


def test_frequency_run_writes_a_word_report(tmp_path) -> None:
    out = _basic(tmp_path)
    assert (out / "report.docx").exists()
    assert (out / "report.html").exists()
    doc = _doc(out)
    # No survey metadata in this run, so the default title.
    assert doc.paragraphs[0].text == "Survey frequency tables"


def test_question_heading_is_plain_text(tmp_path) -> None:
    doc = _doc(_basic(tmp_path))
    headings = [p.text for p in doc.paragraphs if p.text.startswith("Q1.")]
    assert headings == ["Q1. Do you agree?"]


def test_flat_table_rows_with_total_and_missing(tmp_path) -> None:
    out = _basic(tmp_path, {"response_total": "after", "include_missing": True,
                            "stats": ["n", "valid_pct", "eligible_pct"]})
    table = next(t for t in _doc(out).tables if _table_text(t)[0][-1] == "Eligible %")
    rows = _table_text(table)
    assert rows[0] == ["Code", "Response", "Count", "Valid %", "Eligible %"]
    assert rows[1] == ["1", "Yes", "5", "62.5%", "50.0%"]
    assert rows[3] == ["", "Total", "8", "100.0%", "80.0%"]
    # Missing comes last, after the Total, with Valid % left blank.
    assert rows[4] == ["", "Missing", "2", "", "20.0%"]
    assert table.rows[4].cells[1].paragraphs[0].runs[0].italic


def test_hidden_codes_are_left_out(tmp_path) -> None:
    out = _basic(tmp_path, {"hide_codes": ["2"]})
    rows = _table_text(_doc(out).tables[0])
    assert [r[1] for r in rows[1:]] == ["Yes"]


def test_crosstab_has_a_column_per_group_and_overall(tmp_path) -> None:
    out = _basic(tmp_path, {"tables": [{"group_by": []}, {"group_by": ["G"], "overall": "after",
                                                          "response_total": "after"}]})
    doc = _doc(out)
    crosstab = doc.tables[1]
    head = _table_text(crosstab)[0]
    assert head == ["Code", "Response", "Army\nn = 6", "Navy\nn = 4", "Overall\nn = 10"]
    rows = _table_text(crosstab)
    yes = next(r for r in rows if r[1] == "Yes")
    assert yes[2:] == ["3\n50.0%", "2\n50.0%", "5\n50.0%"]
    assert any(p.text.startswith("By G: G text") for p in doc.paragraphs)


def test_wide_crosstab_splits_into_parts_that_fit(tmp_path) -> None:
    groups = {str(i): f"Group {i}" for i in range(1, 25)}
    rows = [{"R": str(i), "G": str(i % 24 + 1), "Q1": "1" if i % 3 else "2"} for i in range(96)]
    cmap = [_col("G", "QG", "G", groups), _col("Q1", "QA", "Q1", {"1": "Yes", "2": "No"})]
    out = _run(tmp_path, {"QA": {"tables": [{"group_by": ["G"]}]}}, rows, cmap)
    doc = _doc(out)
    parts = [t for t in doc.tables if "n = " in t.rows[0].cells[-1].text]
    assert len(parts) > 1
    group_cols = sum(len(t.columns) - 2 for t in parts)  # minus Code, Response
    assert group_cols == 24
    width_limit = 8.5 - 0.8 - 0.8
    for t in parts:
        assert sum(c.width.inches for c in t.rows[0].cells) <= width_limit + 0.01
    assert any("(continued)" in p.text for p in doc.paragraphs)


def test_select_all_lists_its_options_as_responses(tmp_path) -> None:
    rows = [{"R": "1", "S_1": "1", "S_2": ""}, {"R": "2", "S_1": "1", "S_2": "1"}, {"R": "3", "S_1": "", "S_2": ""}]
    cmap = [_col("S_1", "QS", "S", _SELECTED, "MAVR", sub="Boots"),
            _col("S_2", "QS", "S", _SELECTED, "MAVR", sub="Gloves")]
    out = _run(tmp_path, {"QS": {"include_missing": True}}, rows, cmap)
    rows_text = _table_text(_doc(out).tables[0])
    assert rows_text[0] == ["Response", "Count", "Eligible %"]
    assert [r[0] for r in rows_text[1:]] == ["Boots", "Gloves", "Missing"]


def test_code_column_dropped_when_codes_are_the_answers(tmp_path) -> None:
    rows = [{"R": "1", "T": "blue"}, {"R": "2", "T": "red"}, {"R": "3", "T": "blue"}]
    cmap = [_col("T", "QT", "T", {})]
    out = _run(tmp_path, {"QT": {}}, rows, cmap)
    assert _table_text(_doc(out).tables[0])[0] == ["Response", "Count", "Eligible %"]


def test_style_from_the_config_word_block(tmp_path) -> None:
    out = _basic(tmp_path, word={"page": {"orientation": "landscape"},
                                 "document": {"title": "My report", "notes": False}})
    doc = _doc(out)
    assert doc.paragraphs[0].text == "My report"
    sec = doc.sections[0]
    assert sec.page_width > sec.page_height


def test_validate_style_reports_bad_values() -> None:
    assert validate_style({}) == []
    errors = validate_style({"fonts": {"table_size": "big", "colour": 1},
                             "page": {"orientation": "sideways"},
                             "tables": {"header_color": "grey"}})
    assert "word.fonts.table_size: must be a number" in errors
    assert "word.fonts.colour: unknown setting" in errors
    assert any(e.startswith("word.page.orientation:") for e in errors)
    assert any(e.startswith("word.tables.header_color:") for e in errors)
    assert validate_style({"_comment": "ignored", "write_ins": {"max_rows": None}}) == []


def test_resolve_style_rejects_a_bad_patch() -> None:
    assert resolve_style(None, {})["fonts"] == DEFAULT_STYLE["fonts"]
    with pytest.raises(SystemExit):
        resolve_style({"page": {"paper_size": "tabloid"}})


def test_config_validator_checks_the_word_block() -> None:
    cmap = [_col("Q1", "QA", "Q1", {"1": "Yes"})]
    issues = validate_config({"word": {"fonts": {"table_size": 0}}}, cmap)
    assert ("error", "word.fonts.table_size", "must be positive") in issues
    assert validate_config({"word": {"fonts": {"table_size": 10}}}, cmap) == []


def test_total_percentages_do_not_drift() -> None:
    # Three thirds, each rounded to 33.33%, still total 100%.
    rows = [{"n": "1", "valid_n": "3", "valid_pct": "33.33", "eligible_n": "3",
             "eligible_pct": "33.33", "total_n": "3", "total_pct": "33.33"} for _ in range(3)]
    agg = _aggregate_rows(rows, "eligible")
    assert agg["n"] == 3
    assert agg["eligible_pct"] == pytest.approx(100.0)


def test_word_report_regenerates_from_a_run_dir(tmp_path) -> None:
    out = _basic(tmp_path)
    path = generate_word_report(out, tmp_path / "again.docx", {"questions": {"numbering": "off"}})
    doc = Document(str(path))
    assert any(p.text == "Do you agree?" for p in doc.paragraphs)


# ---------------------------------------------------------------------------
# Themes, spacing, and the rating-matrix grid
# ---------------------------------------------------------------------------

_SCALE = {"1": "Low", "2": "Medium", "3": "High"}


def _matrix(tmp_path, q=None, word=None):
    cmap = [_col(f"M_{i}", "QM", "M", _SCALE, "Likert", "Matrix", sub=sub)
            for i, sub in enumerate(["Speed", "Cost"], start=1)]
    rows = [{"R": "1", "M_1": "1", "M_2": "3"}, {"R": "2", "M_1": "3", "M_2": "3"},
            {"R": "3", "M_1": "1", "M_2": ""}, {"R": "4", "M_1": "2", "M_2": "1"}]
    return _run(tmp_path, {"QM": q or {}}, rows, cmap, word)


def test_rating_matrix_is_a_grid_of_items_by_scale_points(tmp_path) -> None:
    out = _matrix(tmp_path, {"include_missing": True, "response_total": "after"})
    rows = _table_text(_doc(out).tables[0])
    # Scale points in survey order, then Total and Missing as columns.
    assert rows[0] == ["Item", "Low\n(1)", "Medium\n(2)", "High\n(3)", "Total", "Missing"]
    assert rows[1] == ["Speed", "2\n50.0%", "1\n25.0%", "1\n25.0%", "4\n100.0%", "0\n0.0%"]
    assert rows[2] == ["Cost", "1\n25.0%", "—", "2\n50.0%", "3\n75.0%", "1\n25.0%"]


def test_matrix_grid_gets_a_valid_n_column_on_the_valid_base(tmp_path) -> None:
    out = _matrix(tmp_path, {"percent_base": "valid", "show_code": False})
    rows = _table_text(_doc(out).tables[0])
    assert rows[0] == ["Item", "Low", "Medium", "High", "Valid n"]
    assert [r[-1] for r in rows[1:]] == ["4", "3"]


def test_matrix_list_layout_is_still_available(tmp_path) -> None:
    out = _matrix(tmp_path, word={"matrices": {"layout": "list"}})
    head = _table_text(_doc(out).tables[0])[0]
    assert head[:3] == ["Attribute", "Code", "Response"]


def _borders(table) -> dict:
    from docx.oxml.ns import qn

    el = table._tbl.tblPr.find(qn("w:tblBorders"))
    return {child.tag.split("}")[1]: child.get(qn("w:val")) for child in el}


def test_corporate_theme_has_no_column_lines(tmp_path) -> None:
    b = _borders(_doc(_basic(tmp_path)).tables[0])
    assert b["top"] == "single" and b["insideH"] == "single"
    assert b["insideV"] == "nil"


def test_grid_and_minimal_themes(tmp_path) -> None:
    (tmp_path / "g").mkdir()
    (tmp_path / "m").mkdir()
    grid = _borders(_doc(_basic(tmp_path / "g", word={"tables": {"theme": "grid"}})).tables[0])
    assert grid["insideV"] == "single"
    minimal = _borders(_doc(_basic(tmp_path / "m", word={"tables": {"theme": "minimal"}})).tables[0])
    assert minimal["top"] == "nil" and minimal["insideH"] == "single"


def test_new_style_options_are_validated() -> None:
    assert validate_style({"tables": {"theme": "boxed", "spacing": "spacious",
                                      "value_align": "right", "cell_padding_inches": None},
                           "matrices": {"layout": "list", "max_columns": 7}}) == []
    errors = validate_style({"tables": {"theme": "fancy", "spacing": "roomy"},
                             "matrices": {"layout": "table", "max_columns": 2.5}})
    assert {e.split(":")[0] for e in errors} == {
        "word.tables.theme", "word.tables.spacing", "word.matrices.layout", "word.matrices.max_columns"}


# ---------------------------------------------------------------------------
# Matrix headers: horizontal (default) or sideways, scale columns equal width
# ---------------------------------------------------------------------------

_SEVEN = {"1": "Very Dissatisfied", "2": "Moderately Dissatisfied", "3": "Slightly Dissatisfied",
          "4": "Neither", "5": "Slightly Satisfied", "6": "Moderately Satisfied", "7": "Very Satisfied"}


def _seven_point(tmp_path, word=None):
    cmap = [_col(f"M_{i}", "QM", "M", _SEVEN, "Likert", "Matrix", sub=sub)
            for i, sub in enumerate(["Comfort", "Durability"], start=1)]
    rows = [{"R": str(r), "M_1": str(r % 7 + 1), "M_2": str((r * 3) % 7 + 1) if r % 5 else ""}
            for r in range(35)]
    q = {"QM": {"include_missing": True, "response_total": "after", "sort_by": "survey_order"}}
    return _run(tmp_path, q, rows, cmap, word)


def _is_rotated(cell) -> bool:
    from docx.oxml.ns import qn

    return cell._tc.get_or_add_tcPr().find(qn("w:textDirection")) is not None


def test_matrix_headers_are_horizontal_with_equal_scale_columns(tmp_path) -> None:
    doc = _doc(_seven_point(tmp_path))
    assert len(doc.tables) == 1  # fits on one table: no parts
    head = doc.tables[0].rows[0].cells
    assert not any(_is_rotated(c) for c in head)
    scale_widths = {round(c.width.inches, 3) for c in head[1:8]}
    assert len(scale_widths) == 1  # every scale point the same width
    # Whole words only: nothing is hyphenated.
    assert not any("\u00ad" in c.text or "-\n" in c.text for c in head)


def test_missing_moves_under_the_grid_when_it_would_squeeze_it(tmp_path) -> None:
    doc = _doc(_seven_point(tmp_path))
    head = [c.text for c in doc.tables[0].rows[0].cells]
    assert head[-1] == "Total" and "Missing" not in head
    note = next(p.text for p in doc.paragraphs if p.text.startswith("Missing (shown"))
    assert "Durability 7 (20.0%)" in note


def test_missing_policy_column_keeps_the_column(tmp_path) -> None:
    doc = _doc(_seven_point(tmp_path, word={"matrices": {"missing": "column"}}))
    head = [c.text for c in doc.tables[0].rows[0].cells]
    assert head[-1] == "Missing"
    assert not any(p.text.startswith("Missing (shown") for p in doc.paragraphs)


def test_missing_policy_note_always_uses_the_note(tmp_path) -> None:
    out = _matrix(tmp_path, {"include_missing": True}, word={"matrices": {"missing": "note"}})
    doc = _doc(out)
    assert "Missing" not in [c.text for c in doc.tables[0].rows[0].cells]
    assert any(p.text.startswith("Missing (shown") and "Cost 1 (25.0%)" in p.text for p in doc.paragraphs)


def test_matrix_headers_sideways_on_request(tmp_path) -> None:
    doc = _doc(_seven_point(tmp_path, word={"matrices": {"header_rotation": "always"}}))
    head = doc.tables[0].rows[0].cells
    assert all(_is_rotated(c) for c in head[1:])
    assert len({round(c.width.inches, 3) for c in head[1:8]}) == 1


def test_numeric_scale_labels_still_make_a_grid(tmp_path) -> None:
    labels = {str(i): str(i) for i in range(1, 6)}
    cmap = [_col("N_1", "QN", "N", labels, "Likert", "Matrix", sub="Speed"),
            _col("N_2", "QN", "N", labels, "Likert", "Matrix", sub="Cost")]
    rows = [{"R": "1", "N_1": "1", "N_2": "5"}, {"R": "2", "N_1": "3", "N_2": "4"}]
    out = _run(tmp_path, {"QN": {"sort_by": "survey_order"}}, rows, cmap)
    assert _table_text(_doc(out).tables[0])[0] == ["Item", "1", "2", "3", "4", "5"]


# ---------------------------------------------------------------------------
# A matrix that cannot fit one grid becomes one table per item (never split)
# ---------------------------------------------------------------------------

def _eleven_point(tmp_path, q=None, word=None):
    labels = {str(i): str(i) for i in range(11)}
    labels["0"], labels["10"] = "Not at all likely", "Extremely likely"
    cmap = [_col(f"L_{i}", "QL", "L", labels, "Likert", "Matrix", sub=sub)
            for i, sub in enumerate(["Comfort", "Durability"], start=1)]
    rows = [{"R": str(r), "L_1": str(r % 11), "L_2": str((r * 7) % 11) if r % 6 else ""}
            for r in range(44)]
    cfg = {"include_missing": True, "response_total": "after", "sort_by": "survey_order", **(q or {})}
    return _run(tmp_path, {"QL": cfg}, rows, cmap, word)


def test_too_wide_matrix_becomes_one_vertical_table_per_item(tmp_path) -> None:
    doc = _doc(_eleven_point(tmp_path))
    assert len(doc.tables) == 2  # one per item, no "(continued)" parts
    assert not any("(continued)" in p.text for p in doc.paragraphs)
    texts = [p.text for p in doc.paragraphs]
    assert "Comfort" in texts and "Durability" in texts  # an item sub-heading each
    rows = _table_text(doc.tables[1])
    assert rows[0] == ["Code", "Response", "Count", "Eligible %"]
    assert [r[1] for r in rows[-2:]] == ["Total", "Missing"]


def test_items_layout_can_be_chosen(tmp_path) -> None:
    out = _matrix(tmp_path, word={"matrices": {"layout": "items"}})
    doc = _doc(out)
    assert len(doc.tables) == 2
    assert _table_text(doc.tables[0])[0][:2] == ["Code", "Response"]


def test_a_question_can_override_the_document_style(tmp_path) -> None:
    # The document says grid; this one question asks for the long list.
    out = _matrix(tmp_path, {"word": {"matrices": {"layout": "list"}}})
    manifest = json.loads((out / "frequency_manifest.json").read_text())
    assert manifest["table_word_style"]["QM"] == {"matrices": {"layout": "list"}}
    head = _table_text(_doc(out).tables[0])[0]
    assert head[:3] == ["Attribute", "Code", "Response"]


def test_question_style_override_is_validated() -> None:
    cmap = [_col("Q1", "QA", "Q1", {"1": "Yes"})]
    issues = validate_config({"questions": {"QA": {"word": {
        "page": {"orientation": "landscape"},
        "matrices": {"layout": "sideways"},
    }}}}, cmap)
    where = {w for _lvl, w, _m in issues}
    assert "questions.QA.word.page" in where           # document-wide only
    assert "questions.QA.word.matrices.layout" in where
    ok = validate_config({"questions": {"QA": {"word": {"matrices": {"layout": "items"}}}}}, cmap)
    assert ok == []


def test_html_report_carries_word_blocks_into_its_config_export(tmp_path) -> None:
    out = _matrix(tmp_path, {"word": {"matrices": {"layout": "list"}}},
                  word={"tables": {"theme": "grid"}})
    html = (out / "report.html").read_text()
    blob = json.loads(html.split('id="QM-data">')[1].split("</script>")[0])
    assert blob["word"] == {"matrices": {"layout": "list"}}
    constants = json.loads(html.split('id="rr-constants">')[1].split("</script>")[0])
    assert constants["word_style"] == {"tables": {"theme": "grid"}}


# ---------------------------------------------------------------------------
# Grid by default; anything else with items is one titled table per item
# ---------------------------------------------------------------------------

_FIT = {"1": "Much too short", "2": "Too short", "3": "Just right", "4": "Too long", "5": "Much too long"}


def _fit_matrix(tmp_path, extra_cols=(), extra_rows=None, q=None):
    cmap = [_col(f"F_{i}", "QF", "Q4.21", _FIT, "Likert", "Matrix", sub=sub)
            for i, sub in enumerate(["Sleeve length", "Torso length", "Trouser length"], start=1)]
    cmap += list(extra_cols)
    rows = [{"R": str(r), "F_1": str(r % 5 + 1), "F_2": str((r + 2) % 5 + 1), "F_3": str((r + 4) % 5 + 1),
             **(extra_rows(r) if extra_rows else {})} for r in range(40)]
    cfg = {"sort_by": "survey_order", "response_total": "after", "include_missing": True, **(q or {})}
    return _run(tmp_path, {"QF": cfg}, rows, cmap)


def test_five_point_matrix_with_total_and_missing_is_one_grid(tmp_path) -> None:
    doc = _doc(_fit_matrix(tmp_path))
    assert len(doc.tables) == 1
    head = [c.text.replace("\n", " ") for c in doc.tables[0].rows[0].cells]
    assert head == ["Item", "Much too short (1)", "Too short (2)", "Just right (3)", "Too long (4)",
                    "Much too long (5)", "Total", "Missing"]
    # The item column is only as wide as its labels need.
    assert doc.tables[0].rows[0].cells[0].width.inches < 1.5


def test_items_that_cannot_be_a_grid_get_a_titled_table_each(tmp_path) -> None:
    # A typed-in answer among the rows (no item, verbatim "code") rules out a grid.
    other = {**_col("F_3_TEXT", "QF", "Q4.21", {}, "Likert", "Matrix", sub=""),
             "is_text_entry_suffix": True, "text_reporting_mode": "frequency_text"}
    out = _fit_matrix(tmp_path, extra_cols=[other],
                      extra_rows=lambda r: {"F_3_TEXT": "too baggy" if r < 2 else ""},
                      q={"text_entry_columns": {"F_3_TEXT": {"text_reporting_mode": "frequency_text"}}})
    doc = _doc(out)
    heads = [_table_text(t)[0] for t in doc.tables]
    assert all("Attribute" not in h for h in heads)     # never an item column
    texts = [p.text for p in doc.paragraphs]
    for item in ("Sleeve length", "Torso length", "Trouser length"):
        assert item in texts                               # each table titled


# ---------------------------------------------------------------------------
# Question text / number overrides
# ---------------------------------------------------------------------------

def test_question_text_and_number_can_be_changed(tmp_path) -> None:
    out = _basic(tmp_path, {"question_text": "Agreement", "question_number": "A1"})
    doc = _doc(out)
    assert any(p.text == "A1. Agreement" for p in doc.paragraphs)
    html = (out / "report.html").read_text()
    assert '<span class="rr-qnum">A1</span>' in html and '<span class="qtext">Agreement</span>' in html


def test_question_number_can_be_hidden(tmp_path) -> None:
    out = _basic(tmp_path, {"show_question_number": False})
    assert any(p.text == "Do you agree?" for p in _doc(out).paragraphs)
    assert '<span class="rr-qnum"></span>' in (out / "report.html").read_text()


def test_question_display_options_are_validated() -> None:
    cmap = [_col("Q1", "QA", "Q1", {"1": "Yes"})]
    issues = validate_config({"defaults": {"question_text": "Same for all"},
                              "questions": {"QA": {"question_number": 5, "show_question_number": "no"}}}, cmap)
    msgs = {(lvl, m) for lvl, _w, m in issues}
    assert ("error", "question_number must be text") in msgs
    assert ("error", "show_question_number must be true or false") in msgs
    assert any(lvl == "warning" and "question_text" in m for lvl, m in msgs)
