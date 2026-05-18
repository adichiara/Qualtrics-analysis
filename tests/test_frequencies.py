from qualtrics_pipeline.frequencies import (
    build_frequency_rows_for_column,
    build_tag_map,
    get_column_context,
)


def test_build_tag_map_groups_same_export_tag() -> None:
    meta = {
        "QID1": {"DataExportTag": "Q1", "QuestionType": "MC"},
        "QID2": {"DataExportTag": "Q1", "QuestionType": "Matrix"},
        "QID3": {"DataExportTag": "Q2", "QuestionType": "MC"},
    }
    tag_map = build_tag_map(meta)

    assert "Q1" in tag_map
    assert len(tag_map["Q1"]) == 2
    assert tag_map["Q2"][0][0] == "QID3"


def test_get_column_context_matrix_sub_question() -> None:
    meta = {
        "QID10": {
            "DataExportTag": "Q5",
            "QuestionType": "Matrix",
            "QuestionText": "Satisfaction",
            "Choices": {"1": {"Display": "Quality"}},
            "Answers": {"1": {"Display": "Poor"}, "2": {"Display": "Good"}},
        }
    }
    tag_map = build_tag_map(meta)
    context = get_column_context("Q5_1", tag_map)

    assert context.qid == "QID10"
    assert context.item_text == "Quality"
    assert context.response_labels["2"] == "Good"


def test_interval_sort_order_numeric_then_alpha() -> None:
    context = type("Ctx", (), {
        "question_type": "Matrix",
        "base_tag": "Q1",
        "question_text": "Rate",
        "item_text": "Item",
        "column": "Q1_1",
        "response_labels": {},
    })()
    config = {"defaults": {"frequency_mode": "auto"}, "questions": {"QID1": {}}}

    rows = build_frequency_rows_for_column(
        values=["10", "2", "A", "2"],
        context=context,
        question_key="QID1",
        question_total_n=4,
        config=config,
    )

    assert [r["response_code"] for r in rows] == ["2", "10", "A"]
