from qualtrics_pipeline.frequencies import generate_frequency_tables


def test_single_answer_mc_recode_and_missing_pct() -> None:
    rows = [{"Q1": "1"}, {"Q1": "2"}, {"Q1": ""}, {"Q1": "2"}]
    cmap = [{"column": "Q1", "qid": "QID1", "data_export_tag": "Q1", "question_type": "MC", "question_text": "Pick", "sub_question_text": "", "response_labels": {"1": "Yes", "2": "No"}, "is_open_text": False, "is_metadata": False, "is_sensitive": False}]
    config = {"defaults": {"frequency_mode": "auto"}, "questions": {"QID1": {}}}
    out = generate_frequency_tables(rows, cmap, config)
    q = out["QID1"]
    assert q[0]["valid_n"] == 3
    assert sorted([r["response_label"] for r in q]) == ["No", "Yes"]


def test_skip_metadata_open_text_sensitive() -> None:
    rows = [{"Q2": "1", "RecipientEmail": "a@x.com", "Q3_TEXT": "hello"}]
    cmap = [
        {"column": "Q2", "qid": "QID2", "data_export_tag": "Q2", "question_type": "MC", "question_text": "", "sub_question_text": "", "response_labels": {"1": "A"}, "is_open_text": False, "is_metadata": False, "is_sensitive": False},
        {"column": "RecipientEmail", "qid": "", "data_export_tag": "", "question_type": "", "question_text": "", "sub_question_text": "", "response_labels": {}, "is_open_text": False, "is_metadata": True, "is_sensitive": True},
        {"column": "Q3_TEXT", "qid": "QID3", "data_export_tag": "Q3", "question_type": "TE", "question_text": "", "sub_question_text": "", "response_labels": {}, "is_open_text": True, "is_metadata": False, "is_sensitive": False},
    ]
    out = generate_frequency_tables(rows, cmap, {"defaults": {}, "questions": {}})
    assert list(out.keys()) == ["QID2"]


def test_strict_unmapped_question_like_column_fails() -> None:
    rows = [{"Q99": "1"}]
    try:
        generate_frequency_tables(rows, [], {"defaults": {}, "questions": {}}, strict=True)
    except SystemExit as e:
        assert "Unmapped question-like" in str(e)
    else:
        raise AssertionError("expected strict mode failure")
