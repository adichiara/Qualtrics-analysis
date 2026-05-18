import json
from pathlib import Path

from qualtrics_pipeline.frequencies import build_default_config, run_frequency_analysis


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
    assert ("Q1.5_3_TEXT" in manifest["skipped_text_entry_columns"] or any("Q1.5_3_TEXT" in p for p in manifest["text_entry_outputs"]))
    assert "UnmappedCol" in manifest["skipped_unmapped_columns"]
    assert "QIDDP" in manifest["empty_output_tables"]


def test_real_fixture_text_column_frequency_mode(tmp_path) -> None:
    fixture = Path("tests/fixtures/real_run")
    data_path = fixture / "responses_clean.csv"
    column_map_path = fixture / "column_map.json"
    column_map = json.loads(column_map_path.read_text(encoding="utf-8"))
    config = build_default_config(column_map)
    config["questions"]["QID2"]["text_entry_columns"]["Q1.5_3_TEXT"] = {"text_reporting_mode": "frequency_text"}

    config_path = tmp_path / "config_ft.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    outdir = tmp_path / "out2"
    run_frequency_analysis(data_path, column_map_path, outdir, config_path, strict=False)

    qid2_csv = outdir / "frequency_tables" / "QID2_frequencies.csv"
    assert "Camp Zama" in qid2_csv.read_text(encoding="utf-8")

    manifest = json.loads((outdir / "frequency_manifest.json").read_text(encoding="utf-8"))
    assert "Q1.5_3_TEXT" not in manifest["skipped_text_entry_columns"]
