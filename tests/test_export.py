import json

from qualtrics_pipeline.export import (
    _strip_html,
    annotate_run,
    build_column_map,
    build_run_manifest,
    non_substantive_info,
)


def test_build_column_map_multi_answer_mc_binary_labels() -> None:
    meta = {
        "QID1": {
            "DataExportTag": "Q1",
            "QuestionType": "MC",
            "Selector": "MAVR",
            "QuestionText": "Pick all",
            "Choices": {"1": {"Display": "A"}, "2": {"Display": "B"}},
            "RecodeValues": {"1": "11", "2": "22"},
        }
    }
    cmap = build_column_map("SV_1", ["Q1_11", "Q1_22"], meta)
    assert cmap[0]["response_labels"] == {"0": "Not selected", "1": "Selected"}
    assert cmap[0]["sub_question_text"] == "A"


def test_build_column_map_matrix_and_metadata() -> None:
    meta = {
        "QID2": {
            "DataExportTag": "Q2",
            "QuestionType": "Matrix",
            "QuestionText": "Rate",
            "Choices": {"1": {"Display": "Service"}},
            "Answers": {"1": {"Display": "Poor"}, "2": {"Display": "Good"}},
        }
    }
    cmap = build_column_map("SV_1", ["Q2_1", "RecipientEmail"], meta)
    q = cmap[0]
    assert q["response_labels"]["2"] == "Good"
    assert q["sub_question_text"] == "Service"
    assert cmap[1]["is_metadata"] is True
    assert cmap[1]["is_sensitive"] is True


def test_build_column_map_sbs_placeholder_supported() -> None:
    meta = {
        "QID3": {
            "DataExportTag": "Q3",
            "QuestionType": "SBS",
            "Selector": "SBSMatrix",
            "QuestionText": "SBS prompt",
        }
    }
    cmap = build_column_map("SV_1", ["Q3#1_1"], meta)
    assert cmap[0]["question_type"] == "SBS"
    assert cmap[0]["column"] == "Q3#1_1"


def test_build_run_manifest_raw_mode_fields() -> None:
    manifest = build_run_manifest(
        survey_id="SV_1",
        privacy_mode="raw",
        rows_raw=10,
        rows_output=10,
        data_file="responses_raw.csv",
        columns_contract=["Q1"],
        artifacts=["responses_raw.csv", "run_manifest.json"],
    )
    assert manifest["data_file"] == "responses_raw.csv"
    assert manifest["rows_output"] == 10
    assert manifest["privacy_mode"] == "raw"


def test_strip_html_removes_tags_and_unescapes() -> None:
    assert _strip_html("<div>Hello &amp; welcome</div>") == "Hello & welcome"
    assert _strip_html("<div></div>Please indicate your sex:") == "Please indicate your sex:"
    assert _strip_html("Rate it:&nbsp;<br><br>If unsure, ask.") == "Rate it: If unsure, ask."
    assert _strip_html("No HTML here") == "No HTML here"
    assert _strip_html("") == ""


def test_build_column_map_strips_html_from_question_text() -> None:
    meta = {
        "QID1": {
            "DataExportTag": "Q1",
            "QuestionType": "MC",
            "Selector": "SAVR",
            "QuestionText": "<div>Please indicate your sex:</div>",
            "Choices": {"1": {"Display": "Male"}, "2": {"Display": "Female"}},
        }
    }
    cmap = build_column_map("SV_1", ["Q1"], meta)
    assert cmap[0]["question_text"] == "Please indicate your sex:"


# --- non-substantive (N/A-type) response codes -------------------------------------

def test_single_answer_mc_flags_exclude_from_analysis_and_na_labels() -> None:
    meta = {
        "QID5": {
            "DataExportTag": "Q5",
            "QuestionType": "MC",
            "Selector": "SAVR",
            "QuestionText": "How well did it fit?",
            "Choices": {
                "1": {"Display": "Poor"},
                "2": {"Display": "Good"},
                "3": {"Display": "Not applicable"},
                "4": {"Display": "Did not wear it"},
            },
            "RecodeValues": {"3": "-1", "4": "99"},
            "AnalyzeChoices": {"4": "on"},
        }
    }
    row = build_column_map("SV_1", ["Q5"], meta)[0]
    assert row["non_substantive_codes"] == ["-1", "99"]
    assert row["non_substantive_reasons"] == {
        "-1": "label_match",
        "99": "qualtrics_exclude_from_analysis",
    }
    assert row["is_non_substantive_option"] is False
    # The labels themselves are untouched.
    assert row["response_labels"]["-1"] == "Not applicable"


def test_multi_select_column_for_na_option_is_marked() -> None:
    # Shape of a real survey question: last option is N/A and excluded from analysis.
    meta = {
        "QID22": {
            "DataExportTag": "Q1.14",
            "QuestionType": "MC",
            "Selector": "MAVR",
            "QuestionText": "Which have you worn?",
            "Choices": {
                "1": {"Display": "Uniform A"},
                "6": {"Display": "N/A (I do not have experience with FR uniforms)"},
            },
            "AnalyzeChoices": {"6": "on"},
        }
    }
    cmap = build_column_map("SV_1", ["Q1.14_1", "Q1.14_6"], meta)
    assert cmap[0]["is_non_substantive_option"] is False
    assert cmap[1]["is_non_substantive_option"] is True
    assert cmap[1]["non_substantive_codes"] == []  # 0/1 selected codes stay substantive


def test_matrix_scale_point_labels_are_matched() -> None:
    meta = {
        "QID9": {
            "DataExportTag": "Q9",
            "QuestionType": "Matrix",
            "QuestionText": "Rate the impact",
            "Choices": {"1": {"Display": "Standing"}},
            "Answers": {
                "1": {"Display": "No impact"},
                "5": {"Display": "Severe impact"},
                "6": {"Display": "N/A"},
                "7": {"Display": "Don't know"},
            },
        }
    }
    row = build_column_map("SV_1", ["Q9_1"], meta)[0]
    assert row["non_substantive_codes"] == ["6", "7"]


def test_label_patterns_avoid_false_positives_and_are_extendable() -> None:
    q = {
        "QuestionType": "MC",
        "Selector": "SAVR",
        "Choices": {
            "1": {"Display": "NATO operations"},
            "2": {"Display": "Banana"},
            "3": {"Display": "Unsure"},
        },
    }
    assert non_substantive_info("Q1", q)["non_substantive_codes"] == []
    extended = non_substantive_info("Q1", q, [r"^\s*unsure\s*$"])
    assert extended["non_substantive_codes"] == ["3"]


def test_text_entry_and_unmatched_columns_have_empty_fields() -> None:
    meta = {
        "QID5": {
            "DataExportTag": "Q5",
            "QuestionType": "MC",
            "Selector": "SAVR",
            "Choices": {"1": {"Display": "Other"}, "2": {"Display": "N/A"}},
        }
    }
    cmap = build_column_map("SV_1", ["Q5_1_TEXT", "StartDate"], meta)
    for row in cmap:
        assert row["non_substantive_codes"] == []
        assert row["non_substantive_reasons"] == {}
        assert row["is_non_substantive_option"] is False


def test_annotate_run_updates_existing_column_map(tmp_path) -> None:
    meta = {
        "QID5": {
            "DataExportTag": "Q5",
            "QuestionType": "MC",
            "Selector": "SAVR",
            "Choices": {"1": {"Display": "Yes"}, "2": {"Display": "N/A"}},
        }
    }
    old_row = {"column": "Q5", "qid": "QID5", "response_labels": {"1": "Yes", "2": "N/A"}}
    (tmp_path / "column_map.json").write_text(json.dumps([old_row]), encoding="utf-8")
    (tmp_path / "questions_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert annotate_run(tmp_path) == 1
    row = json.loads((tmp_path / "column_map.json").read_text(encoding="utf-8"))[0]
    assert row["non_substantive_codes"] == ["2"]
    assert row["response_labels"] == {"1": "Yes", "2": "N/A"}  # other fields kept
