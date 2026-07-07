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
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
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
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
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
    tables, _, _ = generate_frequency_tables(_SORT_ROWS, column_map, config)
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
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
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
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
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


def test_build_default_config_is_self_documenting() -> None:
    column_map = [
        {
            "survey_id": "SV_1", "qid": "QID2", "data_export_tag": "Q1.5",
            "column": "Q1.5", "question_type": "MC", "selector": "SAVR",
            "question_text": "Select your duty location:", "sub_question_text": "",
            "response_labels": {"1": "Schofield Barracks", "2": "Fort Bragg"},
            "is_open_text": False, "is_metadata": False, "is_sensitive": False,
            "is_text_entry_suffix": False, "parent_question_key": "QID2",
            "parent_choice_code": "", "parent_choice_label": "",
            "text_reporting_mode": "skip",
        }
    ]
    cfg = build_default_config(column_map)

    # Top-level cheat sheet and grouping reference are present.
    assert "sort_by" in cfg["_reference"]
    assert "percent_base" in cfg["_reference"]
    assert cfg["_groupable_questions"] == {"Q1.5": "Select your duty location:"}

    # Each question block identifies itself and its response codes for
    # hand-editing, without needing to cross-reference codebook.csv.
    q = cfg["questions"]["QID2"]
    assert q["_question"] == "Q1.5: Select your duty location:"
    assert q["_response_labels"] == {"1": "Schofield Barracks", "2": "Fort Bragg"}
    # Real engine fields are unaffected.
    assert q["include"] is True
    assert q["sort_by"] == "auto"


def test_config_reference_stats_match_stat_keys() -> None:
    from qualtrics_pipeline.frequencies import STAT_KEYS, _config_reference

    ref = _config_reference()
    for key in STAT_KEYS:
        assert key in ref["stats"]


def test_groupable_questions_doc_excludes_multiselect_and_text_entry() -> None:
    from qualtrics_pipeline.frequencies import _groupable_questions_doc

    column_map = [
        {"qid": "QID1", "data_export_tag": "Q1", "column": "Q1", "question_text": "Single",
         "selector": "SAVR", "is_metadata": False, "is_sensitive": False, "is_open_text": False,
         "is_text_entry_suffix": False},
        {"qid": "QID2", "data_export_tag": "Q2", "column": "Q2_1", "question_text": "Multi",
         "selector": "MAVR", "is_metadata": False, "is_sensitive": False, "is_open_text": False,
         "is_text_entry_suffix": False},
        {"qid": "QID1", "data_export_tag": "Q1", "column": "Q1_1_TEXT", "question_text": "Single",
         "selector": "SAVR", "is_metadata": False, "is_sensitive": False, "is_open_text": True,
         "is_text_entry_suffix": True},
    ]
    doc = _groupable_questions_doc(column_map)
    assert doc == {"Q1": "Single"}


def test_groupable_questions_doc_keys_by_column_not_tag_for_matrix() -> None:
    """A Matrix question's rows are separate columns sharing one
    data_export_tag; the reference must advertise the actual column (what
    group_by is validated against), not the shared tag, or a user following it
    would write an unresolvable group_by value (Codex review, PR #7)."""
    from qualtrics_pipeline.frequencies import _groupable_questions_doc

    column_map = [
        {"qid": "QID3", "data_export_tag": "Q3", "column": "Q3_1", "question_type": "Matrix",
         "question_text": "Rate your experience", "sub_question_text": "Service", "selector": "",
         "is_metadata": False, "is_sensitive": False, "is_open_text": False, "is_text_entry_suffix": False},
        {"qid": "QID3", "data_export_tag": "Q3", "column": "Q3_2", "question_type": "Matrix",
         "question_text": "Rate your experience", "sub_question_text": "Support", "selector": "",
         "is_metadata": False, "is_sensitive": False, "is_open_text": False, "is_text_entry_suffix": False},
    ]
    doc = _groupable_questions_doc(column_map)
    # Keyed by the resolvable column, not the shared tag "Q3" (which is not a
    # column and would fail group_by validation).
    assert "Q3" not in doc
    assert doc["Q3_1"] == "Rate your experience — Service"
    assert doc["Q3_2"] == "Rate your experience — Support"


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
    tables, _, _ = generate_frequency_tables(rows, column_map, config, display_logic=display_logic)

    qb = tables["QID_B"]
    assert len(qb) == 1  # one observed response value ("5")
    row = qb[0]
    assert row["n"] == 1
    assert row["valid_n"] == 1        # one respondent actually answered
    assert row["eligible_n"] == 2     # two were eligible (shown the question)
    assert row["total_n"] == 4        # four respondents overall
    assert row["valid_pct"] == 100.0  # 1/1
    assert row["eligible_pct"] == 50.0  # 1/2
    assert row["total_pct"] == 25.0   # 1/4

    # Unconditional Q_A (keyed under _mc_col's qid "QSORT"): eligible == total count.
    assert tables["QSORT"][0]["eligible_n"] == 4
    assert tables["QSORT"][0]["total_n"] == 4


def test_eligible_n_defaults_to_all_respondents_without_logic() -> None:
    rows = [{"Q": "1"}, {"Q": "2"}, {"Q": ""}]
    column_map = [_mc_col("Q", {"1": "A", "2": "B"})]
    config = {"defaults": {}, "questions": {}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)  # no display_logic
    for row in tables["QSORT"]:
        assert row["eligible_n"] == 3  # all respondents eligible
        assert row["total_n"] == 3


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
    tables, _, _ = generate_frequency_tables(rows, column_map, config, display_logic=display_logic)

    by_label = {r["response_label"]: r for r in tables["QID_B"]}
    rip = by_label["Rip"]
    assert rip["n"] == 2
    # All bases are computed regardless of the configured report_base.
    assert rip["eligible_n"] == 3      # three were shown the follow-up
    assert rip["eligible_pct"] == 66.67  # 2/3
    assert rip["total_n"] == 4         # all respondents
    assert rip["total_pct"] == 50.0    # 2/4 prevalence across the whole sample
    assert rip["valid_pct"] == 66.67   # 2/3 of those who answered
    # percent_base="total" is recorded as the featured reporting base.
    assert rip["report_base"] == "total"


def test_report_base_defaults_to_eligible() -> None:
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
    tables, _, _ = generate_frequency_tables(rows, column_map, config, display_logic=display_logic)
    rip = tables["QID_B"][0]
    assert rip["eligible_n"] == 2      # eligible (those shown), not total 3
    assert rip["report_base"] == "eligible"


# ---------------------------------------------------------------------------
# Grouping / crosstab tests
# ---------------------------------------------------------------------------

def _grouping_setup():
    """Q (response A/B) broken out by grouping var G (uniform X/Y)."""
    rows = [
        {"Q": "1", "G": "x"},
        {"Q": "1", "G": "x"},
        {"Q": "2", "G": "x"},
        {"Q": "1", "G": "y"},
        {"Q": "2", "G": "y"},
        {"Q": "2", "G": ""},   # missing grouping value -> dropped
    ]
    column_map = [
        {**_mc_col("Q", {"1": "A", "2": "B"}), "qid": "QID_Q", "data_export_tag": "QQ"},
        {**_mc_col("G", {"x": "Uniform X", "y": "Uniform Y"}), "qid": "QID_G", "data_export_tag": "GG"},
    ]
    return rows, column_map


def test_grouped_table_within_group_counts_and_slug() -> None:
    rows, column_map = _grouping_setup()
    config = {"defaults": {}, "questions": {
        "QID_Q": {"tables": [{"group_by": []}, {"group_by": ["G"]}]}
    }}
    tables, _, meta = generate_frequency_tables(rows, column_map, config)

    # Overall table keyed by qkey; grouped table keyed by slug.
    assert "QID_Q" in tables
    assert "QID_Q__by__G" in tables

    grouped = tables["QID_Q__by__G"]
    # Within Uniform X: A=2, B=1 (group size 3). Within Uniform Y: A=1, B=1.
    x_a = next(r for r in grouped if r["group_codes"] == "x" and r["response_code"] == "1")
    assert x_a["n"] == 2
    assert x_a["total_n"] == 3          # group X size (within-group base)
    assert x_a["total_pct"] == 66.67    # 2/3 within group X
    assert x_a["group_labels"] == "Uniform X"
    y_b = next(r for r in grouped if r["group_codes"] == "y" and r["response_code"] == "2")
    assert y_b["n"] == 1
    assert y_b["total_n"] == 2

    # The respondent with a missing grouping value is dropped and recorded.
    assert meta["table_specs"]["QID_Q__by__G"]["dropped_missing"] == 1
    assert meta["table_specs"]["QID_Q__by__G"]["n_groups"] == 2


def test_grouped_table_levels_ordered_by_survey_order() -> None:
    rows, column_map = _grouping_setup()
    config = {"defaults": {}, "questions": {"QID_Q": {"tables": [{"group_by": ["G"]}]}}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
    codes_in_order = [r["group_codes"] for r in tables["QID_Q__by__G"]]
    # Uniform X (label key order) appears before Uniform Y.
    assert codes_in_order.index("x") < codes_in_order.index("y")


def test_grouping_by_missing_variable_warns_and_skips() -> None:
    rows, column_map = _grouping_setup()
    config = {"defaults": {}, "questions": {"QID_Q": {"tables": [{"group_by": ["NoSuchCol"]}]}}}
    tables, _, meta = generate_frequency_tables(rows, column_map, config)
    assert not any("__by__" in k for k in tables)  # no grouped table produced
    assert any("not found" in w for w in meta["grouping_warnings"])


def test_grouping_by_multiselect_warns_and_skips() -> None:
    rows = [{"Q": "1", "M_1": "1"}, {"Q": "2", "M_1": ""}]
    column_map = [
        {**_mc_col("Q", {"1": "A", "2": "B"}), "qid": "QID_Q"},
        _make_multi_select_column("M_1", "Opt"),  # selector MAVR
    ]
    config = {"defaults": {}, "questions": {"QID_Q": {"tables": [{"group_by": ["M_1"]}]}}}
    tables, _, meta = generate_frequency_tables(rows, column_map, config)
    assert not any("__by__" in k for k in tables)
    assert any("multi-select" in w for w in meta["grouping_warnings"])


def test_no_tables_key_defaults_to_overall_only() -> None:
    rows, column_map = _grouping_setup()
    config = {"defaults": {}, "questions": {}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
    assert "QID_Q" in tables
    assert not any("__by__" in k for k in tables)


# ---------------------------------------------------------------------------
# Top-level "only" whitelist
# ---------------------------------------------------------------------------

def _two_question_setup():
    rows = [{"Q1": "1", "Q2": "1"}, {"Q1": "2", "Q2": "2"}]
    column_map = [
        {"survey_id": "SV_1", "qid": "QIDA", "data_export_tag": "Q1", "column": "Q1",
         "question_type": "MC", "selector": "SAVR", "question_text": "First",
         "sub_question_text": "", "response_labels": {"1": "A", "2": "B"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QIDA",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
        {"survey_id": "SV_1", "qid": "QIDB", "data_export_tag": "Q2", "column": "Q2",
         "question_type": "MC", "selector": "SAVR", "question_text": "Second",
         "sub_question_text": "", "response_labels": {"1": "X", "2": "Y"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QIDB",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
    ]
    return rows, column_map


def test_only_by_tag_shows_just_that_question() -> None:
    """The scenario from the bug report: editing the config down to one
    question's worth of settings must actually hide every other question,
    not just leave them defaulted to shown."""
    rows, column_map = _two_question_setup()
    config = {"only": ["Q1"], "defaults": {}, "questions": {}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
    assert "QIDA" in tables
    assert "QIDB" not in tables


def test_only_by_qkey_also_works() -> None:
    rows, column_map = _two_question_setup()
    config = {"only": ["QIDB"], "defaults": {}, "questions": {}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
    assert "QIDB" in tables
    assert "QIDA" not in tables


def test_only_absent_shows_everything() -> None:
    rows, column_map = _two_question_setup()
    config = {"defaults": {}, "questions": {}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
    assert "QIDA" in tables
    assert "QIDB" in tables


def test_only_and_include_false_combine() -> None:
    """include: false still excludes a question even if it's in "only"."""
    rows, column_map = _two_question_setup()
    config = {"only": ["Q1", "Q2"], "defaults": {}, "questions": {"QIDA": {"include": False}}}
    tables, _, _ = generate_frequency_tables(rows, column_map, config)
    assert "QIDA" not in tables
    assert "QIDB" in tables


def test_config_reference_documents_only() -> None:
    from qualtrics_pipeline.frequencies import _config_reference

    assert "only" in _config_reference()


def test_only_also_hides_write_in_outputs() -> None:
    """A question excluded by "only" must not leak its write-in (open-text)
    responses either -- otherwise it still surfaces as an orphan section in
    the report (Codex review, PR #9)."""
    rows = [
        {"Q1": "1", "Q1_TEXT": ""},
        {"Q1": "3", "Q1_TEXT": "Some other duty station"},
    ]
    column_map = [
        {"survey_id": "SV_1", "qid": "QIDA", "data_export_tag": "Q1", "column": "Q1",
         "question_type": "MC", "selector": "SAVR", "question_text": "First",
         "sub_question_text": "", "response_labels": {"1": "A", "3": "Other"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QIDA",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
        {"survey_id": "SV_1", "qid": "QIDA", "data_export_tag": "Q1", "column": "Q1_TEXT",
         "question_type": "MC", "selector": "SAVR", "question_text": "First",
         "sub_question_text": "Other", "response_labels": {},
         "is_open_text": True, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": True, "parent_question_key": "QIDA",
         "parent_choice_code": "3", "parent_choice_label": "Other",
         "text_reporting_mode": "summarize_later"},
    ]
    config = {"only": ["NoSuchTag"], "defaults": {}, "questions": {}}  # excludes QIDA entirely
    tables, text_outputs, _ = generate_frequency_tables(rows, column_map, config)
    assert "QIDA" not in tables
    assert "QIDA" not in text_outputs


def test_only_include_false_also_hides_write_in_outputs() -> None:
    """Same guarantee via a plain include: false (not just "only")."""
    rows = [{"Q1": "3", "Q1_TEXT": "Camp Zama"}]
    column_map = [
        {"survey_id": "SV_1", "qid": "QIDA", "data_export_tag": "Q1", "column": "Q1",
         "question_type": "MC", "selector": "SAVR", "question_text": "First",
         "sub_question_text": "", "response_labels": {"3": "Other"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QIDA",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
        {"survey_id": "SV_1", "qid": "QIDA", "data_export_tag": "Q1", "column": "Q1_TEXT",
         "question_type": "MC", "selector": "SAVR", "question_text": "First",
         "sub_question_text": "Other", "response_labels": {},
         "is_open_text": True, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": True, "parent_question_key": "QIDA",
         "parent_choice_code": "3", "parent_choice_label": "Other",
         "text_reporting_mode": "summarize_later"},
    ]
    config = {"defaults": {}, "questions": {"QIDA": {"include": False}}}
    _, text_outputs, _ = generate_frequency_tables(rows, column_map, config)
    assert "QIDA" not in text_outputs


def test_rerun_with_narrower_only_clears_stale_output_files(tmp_path) -> None:
    """Re-running analysis in the same outdir after tightening "only" must not
    leave a previously-written question's frequency_tables/*.csv (or
    open_text_outputs/*.csv) behind -- the report globs the whole directory,
    so stale files would make an excluded question reappear (Codex review,
    PR #9)."""
    rows, column_map = _two_question_setup()
    data_path = tmp_path / "data.csv"
    data_path.write_text("Q1,Q2\n1,1\n2,2\n", encoding="utf-8")
    cmap_path = tmp_path / "cm.json"
    cmap_path.write_text(json.dumps(column_map), encoding="utf-8")
    outdir = tmp_path / "out"

    # First run: unrestricted, both questions get a file.
    config_path = tmp_path / "cfg.json"
    config_path.write_text(json.dumps({"defaults": {}, "questions": {}}), encoding="utf-8")
    run_frequency_analysis(data_path, cmap_path, outdir, config_path)
    freq_dir = outdir / "frequency_tables"
    assert (freq_dir / "QIDA_frequencies.csv").exists()
    assert (freq_dir / "QIDB_frequencies.csv").exists()

    # Second run in the SAME outdir: narrowed to only QIDA.
    config_path.write_text(json.dumps({"only": ["Q1"], "defaults": {}, "questions": {}}), encoding="utf-8")
    run_frequency_analysis(data_path, cmap_path, outdir, config_path)
    assert (freq_dir / "QIDA_frequencies.csv").exists()
    assert not (freq_dir / "QIDB_frequencies.csv").exists()  # stale file removed

    from qualtrics_pipeline.report import generate_html_report

    html = generate_html_report(outdir).read_text(encoding="utf-8")
    assert html.count("<section") == 1  # only QIDA's section, no stale QIDB
