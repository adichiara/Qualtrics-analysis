import json

from qualtrics_pipeline.frequencies import build_default_config, build_frequency_manifest, generate_frequency_tables


def test_build_default_config_excludes_skipped_columns() -> None:
    cmap = [
        {"column": "Q1", "qid": "QID1", "is_open_text": False, "is_metadata": False, "is_sensitive": False},
        {"column": "RecipientEmail", "qid": "", "is_open_text": False, "is_metadata": True, "is_sensitive": True},
        {"column": "Q2_TEXT", "qid": "QID2", "is_open_text": True, "is_metadata": False, "is_sensitive": False},
    ]
    cfg = build_default_config(cmap)
    assert list(cfg["questions"].keys()) == ["QID1"]


def test_single_answer_mc_recode_and_missing_pct() -> None:
    rows = [{"Q1": "1"}, {"Q1": "2"}, {"Q1": ""}, {"Q1": "2"}]
    cmap = [{"column": "Q1", "qid": "QID1", "data_export_tag": "Q1", "question_type": "MC", "question_text": "Pick", "sub_question_text": "", "response_labels": {"1": "Yes", "2": "No"}, "is_open_text": False, "is_metadata": False, "is_sensitive": False}]
    config = {"defaults": {"frequency_mode": "auto"}, "questions": {"QID1": {}}}
    out = generate_frequency_tables(rows, cmap, config)
    q = out["QID1"]
    assert q[0]["valid_n"] == 3
    assert sorted([r["response_label"] for r in q]) == ["No", "Yes"]


def test_frequency_manifest_captures_skips_and_unmapped() -> None:
    rows = [{"Q2": "1", "RecipientEmail": "a@x.com", "Q3_TEXT": "hello", "Q99": "1"}]
    cmap = [
        {"column": "Q2", "qid": "QID2", "is_open_text": False, "is_metadata": False, "is_sensitive": False},
        {"column": "RecipientEmail", "qid": "", "is_open_text": False, "is_metadata": True, "is_sensitive": True},
        {"column": "Q3_TEXT", "qid": "QID3", "is_open_text": True, "is_metadata": False, "is_sensitive": False},
    ]
    m = build_frequency_manifest(rows, cmap, strict=False, output_files=["a.csv"], data_path="d.csv", column_map_path="cm.json")
    assert m["analyzed_columns"] == ["Q2"]
    assert "RecipientEmail" in m["skipped_metadata_columns"]
    assert "Q3_TEXT" in m["skipped_open_text_columns"]
    assert "Q99" in m["unmapped_columns"]


def test_strict_unmapped_question_like_column_fails() -> None:
    rows = [{"Q99": "1"}]
    try:
        generate_frequency_tables(rows, [], {"defaults": {}, "questions": {}}, strict=True)
    except SystemExit as e:
        assert "Unmapped question-like" in str(e)
    else:
        raise AssertionError("expected strict mode failure")
