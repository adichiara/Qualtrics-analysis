from qualtrics_pipeline.export import build_column_map


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
