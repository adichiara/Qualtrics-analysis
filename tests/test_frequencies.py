import json
from pathlib import Path

from qualtrics_pipeline.frequencies import build_default_config, generate_frequency_tables, run_frequency_analysis


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
