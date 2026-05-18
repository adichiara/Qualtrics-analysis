from qualtrics_pipeline.export import build_column_map, build_run_manifest


def test_build_column_map_text_suffix_other() -> None:
    meta = {
        "QID1": {
            "DataExportTag": "Q1.5",
            "QuestionType": "MC",
            "Selector": "SAVR",
            "Choices": {"3": {"Display": "Other"}},
            "RecodeValues": {"3": "3"},
        }
    }
    cmap = build_column_map("SV_1", ["Q1.5", "Q1.5_3_TEXT"], meta)
    txt = [x for x in cmap if x["column"] == "Q1.5_3_TEXT"][0]
    assert txt["is_text_entry_suffix"] is True
    assert txt["is_open_text"] is True
    assert txt["text_reporting_mode"] == "summarize_later"


def test_build_column_map_sbs_placeholder_supported() -> None:
    meta = {"QID3": {"DataExportTag": "Q3", "QuestionType": "SBS", "Selector": "SBSMatrix"}}
    cmap = build_column_map("SV_1", ["Q3#1_1"], meta)
    assert cmap[0]["question_type"] == "SBS"


def test_build_run_manifest_raw_mode_fields() -> None:
    manifest = build_run_manifest("SV_1", "raw", 10, 10, "responses_raw.csv", ["Q1"], ["responses_raw.csv"])
    assert manifest["data_file"] == "responses_raw.csv"
