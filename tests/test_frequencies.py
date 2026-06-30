import json
from pathlib import Path

from qualtrics_pipeline.frequencies import (
    build_default_config,
    generate_frequency_tables,
    run_frequency_analysis,
)


# ---------------------------------------------------------------------------
# Helpers shared by sort-order tests
# ---------------------------------------------------------------------------

def _mc_col(col: str, labels: dict, question_type: str = "MC") -> dict:
    """Minimal column-map entry for a single-answer MC column."""
    return {
        "survey_id": "SV_1", "qid": "QSORT", "data_export_tag": "QSORT",
        "column": col, "question_type": question_type, "selector": "SAVR",
        "question_text": "Q", "sub_question_text": "",
        "response_labels": labels,
        "is_open_text": False, "is_metadata": False, "is_sensitive": False,
        "is_text_entry_suffix": False, "parent_question_key": "QSORT",
        "parent_choice_code": "", "parent_choice_label": "",
        "text_reporting_mode": "skip",
    }


def _run_sort(rows: list[dict], labels: dict, sort_by: str, **extra_cfg) -> list[str]:
    """Return ordered response_label values for a single-column sort test."""
    column_map = [_mc_col("Q", labels)]
    config = {
        "defaults": {},
        "questions": {
            "QSORT": {"include": True, "sort_by": sort_by, "response_order": [], "text_entry_columns": {}, **extra_cfg}
        },
    }
    tables, _ = generate_frequency_tables(rows, column_map, config)
    return [r["response_label"] for r in tables["QSORT"]]


def test_real_fixture_frequency_default(tmp_path) -> None:
    fixture = Path("tests/fixtures/real_run")
    data_path = fixture / "responses_clean.csv"
    column_map_path = fixture / "column_map.json"

    column_map = json.loads(column_map_path.read_text(encoding="utf-8"))
    config = build_default_config(column_map)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    outdir = tmp_path / "out"
    run_frequency_analysis(data_path, column_map_path, outdir, config_path, strict=False)

    qid2_csv = outdir / "frequency_tables" / "QID2_frequencies.csv"
    assert qid2_csv.exists()
    content = qid2_csv.read_text(encoding="utf-8")
    assert "Schofield Barracks" in content
    assert "Fort Bragg" in content
    assert "Other" in content
    assert "Q1.5_3_TEXT" not in content

    assert not (outdir / "frequency_tables" / "date_frequencies.csv").exists()
    assert not (outdir / "frequency_tables" / "Q_DataPolicyViolations_frequencies.csv").exists()

    manifest = json.loads((outdir / "frequency_manifest.json").read_text(encoding="utf-8"))
    assert "Q1.5" in manifest["analyzed_columns"]
    assert "date" in manifest["skipped_metadata_columns"]
    assert "Q1.5_3_TEXT" in manifest["skipped_open_text_columns"]
    assert (
        "Q1.5_3_TEXT" in manifest["skipped_text_entry_columns"]
        or any("Q1.5_3_TEXT" in p for p in manifest["text_entry_outputs"])
    )
    assert "UnmappedCol" in manifest["skipped_unmapped_columns"]
    assert "QIDDP" in manifest["empty_output_tables"]


def test_real_fixture_text_column_frequency_mode(tmp_path) -> None:
    fixture = Path("tests/fixtures/real_run")
    data_path = fixture / "responses_clean.csv"
    column_map_path = fixture / "column_map.json"
    column_map = json.loads(column_map_path.read_text(encoding="utf-8"))
    config = build_default_config(column_map)
    config["questions"]["QID2"]["text_entry_columns"]["Q1.5_3_TEXT"] = {
        "text_reporting_mode": "frequency_text"
    }

    config_path = tmp_path / "config_ft.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    outdir = tmp_path / "out2"
    run_frequency_analysis(data_path, column_map_path, outdir, config_path, strict=False)

    qid2_csv = outdir / "frequency_tables" / "QID2_frequencies.csv"
    assert "Camp Zama" in qid2_csv.read_text(encoding="utf-8")

    manifest = json.loads((outdir / "frequency_manifest.json").read_text(encoding="utf-8"))
    assert "Q1.5_3_TEXT" not in manifest["skipped_text_entry_columns"]


def _make_multi_select_column(col: str, sub_text: str) -> dict:
    return {
        "survey_id": "SV_1", "qid": "QID1", "data_export_tag": "Q1",
        "column": col, "question_type": "MC", "selector": "MAVR",
        "question_text": "Pick all that apply", "sub_question_text": sub_text,
        "response_labels": {"0": "Not selected", "1": "Selected"},
        "is_open_text": False, "is_metadata": False, "is_sensitive": False,
        "is_text_entry_suffix": False, "parent_question_key": "QID1",
        "parent_choice_code": "", "parent_choice_label": "",
        "text_reporting_mode": "skip",
    }


def test_multi_select_valid_pct_uses_total_respondents() -> None:
    """valid_pct for MAVR columns must use question_total_n as denominator."""
    rows = [
        {"Q1_1": "1", "Q1_2": ""},   # selected option A only
        {"Q1_1": "1", "Q1_2": "1"},  # selected both
        {"Q1_1": "",  "Q1_2": "1"},  # selected option B only
        {"Q1_1": "",  "Q1_2": ""},   # answered nothing (excluded from total)
    ]
    column_map = [
        _make_multi_select_column("Q1_1", "Option A"),
        _make_multi_select_column("Q1_2", "Option B"),
    ]
    config = {
        "defaults": {},
        "questions": {"QID1": {"include": True, "frequency_mode": "auto", "response_order": [], "text_entry_columns": {}}},
    }
    tables, _ = generate_frequency_tables(rows, column_map, config)
    assert "QID1" in tables

    by_col = {r["column"]: r for r in tables["QID1"]}
    # question_total_n = 3 (row 4 has all blanks, excluded)
    # Q1_1: 2 selected → 2/3 = 66.67%
    row_a = by_col["Q1_1"]
    assert row_a["n"] == 2
    assert row_a["valid_n"] == 3
    assert row_a["valid_pct"] == 66.67

    # Q1_2: 2 selected → 2/3 = 66.67%
    row_b = by_col["Q1_2"]
    assert row_b["n"] == 2
    assert row_b["valid_n"] == 3
    assert row_b["valid_pct"] == 66.67


# ---------------------------------------------------------------------------
# Sort-order tests
# ---------------------------------------------------------------------------

# Shared fixture: C appears 3×, A appears 2×, B appears 1×.
# survey_order (label key order): A, B, C
# count_desc: C, A, B
# count_asc:  B, A, C
_SORT_ROWS = [
    {"Q": "1"}, {"Q": "1"}, {"Q": "3"},
    {"Q": "3"}, {"Q": "3"}, {"Q": "2"},
]
# response_labels in survey insertion order: 1→A, 2→B, 3→C
_SORT_LABELS = {"1": "A", "2": "B", "3": "C"}


def test_sort_count_desc() -> None:
    assert _run_sort(_SORT_ROWS, _SORT_LABELS, "count_desc") == ["C", "A", "B"]


def test_sort_count_asc() -> None:
    assert _run_sort(_SORT_ROWS, _SORT_LABELS, "count_asc") == ["B", "A", "C"]


def test_sort_survey_order() -> None:
    # Follows response_labels key order regardless of counts.
    assert _run_sort(_SORT_ROWS, _SORT_LABELS, "survey_order") == ["A", "B", "C"]


def test_sort_response_order_explicit() -> None:
    # Explicit list [3, 1] → C first, then A; B (unlisted) appended by count_desc.
    column_map = [_mc_col("Q", _SORT_LABELS)]
    config = {
        "defaults": {},
        "questions": {
            "QSORT": {"include": True, "sort_by": "response_order", "response_order": ["3", "1"], "text_entry_columns": {}}
        },
    }
    tables, _ = generate_frequency_tables(_SORT_ROWS, column_map, config)
    labels = [r["response_label"] for r in tables["QSORT"]]
    assert labels == ["C", "A", "B"]


def test_sort_auto_matrix_defaults_to_survey_order() -> None:
    # Matrix questions in auto mode should use survey_order, not count_desc.
    # Labels in order: 1→Disagree, 2→Neutral, 3→Agree — all equally frequent.
    rows = [{"Q": "3"}, {"Q": "1"}, {"Q": "2"}]
    labels = {"1": "Disagree", "2": "Neutral", "3": "Agree"}
    column_map = [_mc_col("Q", labels, question_type="Matrix")]
    config = {
        "defaults": {},
        "questions": {"QSORT": {"include": True, "sort_by": "auto", "response_order": [], "text_entry_columns": {}}},
    }
    tables, _ = generate_frequency_tables(rows, column_map, config)
    result = [r["response_label"] for r in tables["QSORT"]]
    assert result == ["Disagree", "Neutral", "Agree"]


def test_sort_legacy_frequency_mode_interval_maps_to_survey_order() -> None:
    # Old configs with frequency_mode: interval should behave like survey_order.
    rows = [{"Q": "3"}, {"Q": "1"}, {"Q": "2"}]
    labels = {"1": "Low", "2": "Mid", "3": "High"}
    column_map = [_mc_col("Q", labels)]
    config = {
        "defaults": {},
        "questions": {"QSORT": {"include": True, "frequency_mode": "interval", "response_order": [], "text_entry_columns": {}}},
    }
    tables, _ = generate_frequency_tables(rows, column_map, config)
    result = [r["response_label"] for r in tables["QSORT"]]
    assert result == ["Low", "Mid", "High"]


def test_build_default_config_emits_sort_by() -> None:
    column_map = [
        {
            "survey_id": "SV_1", "qid": "QID1", "data_export_tag": "Q1",
            "column": "Q1", "question_type": "MC", "selector": "SAVR",
            "question_text": "Q", "sub_question_text": "",
            "response_labels": {"1": "Yes", "2": "No"},
            "is_open_text": False, "is_metadata": False, "is_sensitive": False,
            "is_text_entry_suffix": False, "parent_question_key": "QID1",
            "parent_choice_code": "", "parent_choice_label": "",
            "text_reporting_mode": "skip",
        }
    ]
    cfg = build_default_config(column_map)
    assert cfg["defaults"]["sort_by"] == "auto"
    assert cfg["questions"]["QID1"]["sort_by"] == "auto"
    assert cfg["questions"]["QID1"]["percent_base"] == "eligible"
    assert "frequency_mode" not in cfg["defaults"]


# ---------------------------------------------------------------------------
# Display-logic base_n tests
# ---------------------------------------------------------------------------

def test_base_n_reflects_display_logic() -> None:
    """A conditional question's base_n = respondents the logic shows it to.

    Q_A (gate): Yes/No. Q_B shown only when Q_A == "1" (Yes).
    Two said Yes; of those, one answered Q_B and one (eligible) left it blank.
    base_n must be 2 (both eligible), while valid_n is 1 (one answered).
    """
    rows = [
        {"Q_A": "1", "Q_B": "5"},  # Yes, answered Q_B
        {"Q_A": "1", "Q_B": ""},   # Yes, eligible but skipped Q_B
        {"Q_A": "0", "Q_B": ""},   # No, not shown Q_B
        {"Q_A": "0", "Q_B": ""},   # No, not shown Q_B
    ]
    column_map = [
        _mc_col("Q_A", {"0": "No", "1": "Yes"}),
        {
            "survey_id": "SV_1", "qid": "QID_B", "data_export_tag": "QID_B",
            "column": "Q_B", "question_type": "MC", "selector": "SAVR",
            "question_text": "How many?", "sub_question_text": "",
            "response_labels": {"5": "Five"},
            "is_open_text": False, "is_metadata": False, "is_sensitive": False,
            "is_text_entry_suffix": False, "parent_question_key": "QID_B",
            "parent_choice_code": "", "parent_choice_label": "",
            "text_reporting_mode": "skip",
        },
    ]
    config = {"defaults": {}, "questions": {}}
    display_logic = {
        "QID_B": {
            "fully_evaluable": True,
            "tree": {"type": "pred", "column": "Q_A", "op": "equals", "value": "1"},
        }
    }
    tables, _ = generate_frequency_tables(rows, column_map, config, display_logic=display_logic)

    qb = tables["QID_B"]
    assert len(qb) == 1  # one observed response value ("5")
    row = qb[0]
    assert row["n"] == 1
    assert row["valid_n"] == 1       # one respondent actually answered
    assert row["base_n"] == 2        # two were eligible (shown the question)
    assert row["valid_pct"] == 100.0  # 1/1
    assert row["base_pct"] == 50.0    # 1/2

    # Unconditional Q_A (keyed under _mc_col's qid "QSORT"): base_n is the full count.
    assert tables["QSORT"][0]["base_n"] == 4


def test_base_n_defaults_to_all_respondents_without_logic() -> None:
    rows = [{"Q": "1"}, {"Q": "2"}, {"Q": ""}]
    column_map = [_mc_col("Q", {"1": "A", "2": "B"})]
    config = {"defaults": {}, "questions": {}}
    tables, _ = generate_frequency_tables(rows, column_map, config)  # no display_logic
    for row in tables["QSORT"]:
        assert row["base_n"] == 3  # all respondents eligible


def test_percent_base_total_uses_full_sample_for_prevalence() -> None:
    """percent_base='total' reports prevalence over all respondents, even for a
    conditional question shown only to a subset (durability-issue example)."""
    # 4 respondents; Q_B (issue type) shown only when Q_A == "1" (had issues).
    rows = [
        {"Q_A": "1", "Q_B": "rip"},
        {"Q_A": "1", "Q_B": "rip"},
        {"Q_A": "1", "Q_B": "zipper"},
        {"Q_A": "0", "Q_B": ""},
    ]
    column_map = [
        _mc_col("Q_A", {"0": "No", "1": "Yes"}),
        {
            "survey_id": "SV_1", "qid": "QID_B", "data_export_tag": "QID_B",
            "column": "Q_B", "question_type": "MC", "selector": "SAVR",
            "question_text": "Which issue?", "sub_question_text": "",
            "response_labels": {"rip": "Rip", "zipper": "Broken zipper"},
            "is_open_text": False, "is_metadata": False, "is_sensitive": False,
            "is_text_entry_suffix": False, "parent_question_key": "QID_B",
            "parent_choice_code": "", "parent_choice_label": "",
            "text_reporting_mode": "skip",
        },
    ]
    display_logic = {
        "QID_B": {"fully_evaluable": True,
                  "tree": {"type": "pred", "column": "Q_A", "op": "equals", "value": "1"}}
    }
    config = {"defaults": {}, "questions": {"QID_B": {"percent_base": "total"}}}
    tables, _ = generate_frequency_tables(rows, column_map, config, display_logic=display_logic)

    by_label = {r["response_label"]: r for r in tables["QID_B"]}
    rip = by_label["Rip"]
    assert rip["n"] == 2
    assert rip["base_n"] == 4          # all respondents, not just the 3 with issues
    assert rip["base_type"] == "total"
    assert rip["base_pct"] == 50.0     # 2/4 prevalence across the whole sample
    assert rip["valid_pct"] == 66.67   # 2/3 of those who answered, unchanged


def test_percent_base_eligible_is_default() -> None:
    rows = [{"Q_A": "1", "Q_B": "rip"}, {"Q_A": "1", "Q_B": "rip"}, {"Q_A": "0", "Q_B": ""}]
    column_map = [
        _mc_col("Q_A", {"0": "No", "1": "Yes"}),
        {**_mc_col("Q_B_dummy", {}), "column": "Q_B", "qid": "QID_B",
         "response_labels": {"rip": "Rip"}},
    ]
    display_logic = {
        "QID_B": {"fully_evaluable": True,
                  "tree": {"type": "pred", "column": "Q_A", "op": "equals", "value": "1"}}
    }
    config = {"defaults": {}, "questions": {}}  # no percent_base -> eligible
    tables, _ = generate_frequency_tables(rows, column_map, config, display_logic=display_logic)
    rip = tables["QID_B"][0]
    assert rip["base_n"] == 2          # eligible (those shown), not total 3
    assert rip["base_type"] == "eligible"
