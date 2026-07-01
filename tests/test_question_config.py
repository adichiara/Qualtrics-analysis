from qualtrics_pipeline import question_config as qc


def _mc(col, qid, tag, text, labels, selector="SAVR", is_text_entry_suffix=False, is_open_text=False):
    return {
        "survey_id": "SV_1", "qid": qid, "data_export_tag": tag, "column": col,
        "question_type": "MC", "selector": selector, "subselector": "",
        "question_text": text, "sub_question_text": "", "response_labels": labels,
        "is_open_text": is_open_text, "is_metadata": False, "is_sensitive": False,
        "is_text_entry_suffix": is_text_entry_suffix, "parent_question_key": qid,
        "parent_choice_code": "", "parent_choice_label": "",
        "text_reporting_mode": "summarize_later" if is_text_entry_suffix else "skip",
    }


def _column_map():
    return [
        _mc("Q1.5", "QID2", "Q1.5", "Select your duty location:", {"1": "Schofield", "2": "Fort Bragg", "3": "Other"}),
        _mc("Q1.5_3_TEXT", "QID2", "Q1.5", "Select your duty location:", {}, is_text_entry_suffix=True),
        _mc("Q1.9", "QID16", "Q1.9", "What uniform were you issued?", {"1": "A", "2": "B"}),
        _mc("Q1.14_1", "QID22", "Q1.14", "Select all that apply", {"0": "Not selected", "1": "Selected"}, selector="MAVR"),
        {**_mc("Q1.4", "QIDTE", "Q1.4", "Tell us more", {}), "question_type": "TE", "is_open_text": True},
        {**_mc("date", "", "", "", {}), "is_metadata": True, "qid": ""},
    ]


# ---------------------------------------------------------------------------
# question_summaries
# ---------------------------------------------------------------------------

def test_question_summaries_excludes_metadata_and_plain_text_entry():
    summaries = qc.question_summaries(_column_map())
    qkeys = {s["qkey"] for s in summaries}
    assert "QIDTE" not in qkeys  # plain TE question never appears in frequency tables
    assert "" not in qkeys      # metadata column has no qkey worth listing


def test_question_summaries_dedupes_by_qkey_and_orders_naturally():
    summaries = qc.question_summaries(_column_map())
    qkeys = [s["qkey"] for s in summaries]
    assert qkeys.count("QID2") == 1  # Q1.5 + Q1.5_3_TEXT share qkey QID2
    # Q1.5 (tag "Q1.5") should sort before Q1.9 and Q1.14
    ids = [s["question_id"] for s in summaries]
    assert ids.index("Q1.5") < ids.index("Q1.9") < ids.index("Q1.14")


def test_question_summaries_includes_multiselect():
    summaries = qc.question_summaries(_column_map())
    assert any(s["qkey"] == "QID22" for s in summaries)


# ---------------------------------------------------------------------------
# find_questions
# ---------------------------------------------------------------------------

def test_find_questions_by_index():
    summaries = qc.question_summaries(_column_map())
    assert qc.find_questions(summaries, "1") == [summaries[0]]


def test_find_questions_by_exact_tag():
    summaries = qc.question_summaries(_column_map())
    matches = qc.find_questions(summaries, "Q1.9")
    assert len(matches) == 1
    assert matches[0]["qkey"] == "QID16"


def test_find_questions_by_substring_in_text():
    summaries = qc.question_summaries(_column_map())
    matches = qc.find_questions(summaries, "uniform")
    assert len(matches) == 1
    assert matches[0]["qkey"] == "QID16"


def test_find_questions_no_match_returns_empty():
    summaries = qc.question_summaries(_column_map())
    assert qc.find_questions(summaries, "nonexistent") == []
    assert qc.find_questions(summaries, "") == []


def test_find_questions_ambiguous_substring_returns_multiple():
    summaries = qc.question_summaries(_column_map())
    # "Select" appears in both Q1.5's and Q1.14's question text.
    matches = qc.find_questions(summaries, "select")
    assert len(matches) >= 2


# ---------------------------------------------------------------------------
# labels / groupable columns
# ---------------------------------------------------------------------------

def test_question_response_labels_merges_across_columns():
    labels = qc.question_response_labels(_column_map(), "QID2")
    assert labels == {"1": "Schofield", "2": "Fort Bragg", "3": "Other"}


def test_groupable_columns_excludes_multiselect_and_text_entry():
    cols = {c["column"] for c in qc.groupable_columns(_column_map())}
    assert "Q1.5" in cols
    assert "Q1.9" in cols
    assert "Q1.14_1" not in cols       # multi-select
    assert "Q1.5_3_TEXT" not in cols   # text-entry suffix


def test_groupable_columns_excludes_current_question():
    cols = {c["column"] for c in qc.groupable_columns(_column_map(), exclude_qkey="QID2")}
    assert "Q1.5" not in cols   # belongs to QID2, the question being configured
    assert "Q1.9" in cols       # a different question is still offered


# ---------------------------------------------------------------------------
# question block get/set/reset
# ---------------------------------------------------------------------------

def test_ensure_question_block_creates_skeleton():
    config = {}
    block = qc.ensure_question_block(config, "QID2")
    assert block == qc.QUESTION_DEFAULT_SKELETON
    assert config["questions"]["QID2"] is block


def test_ensure_question_block_reuses_existing():
    config = {"questions": {"QID2": {"include": False}}}
    block = qc.ensure_question_block(config, "QID2")
    assert block == {"include": False}  # not overwritten


def test_ensure_question_block_stamps_doc_fields_when_column_map_given():
    config = {}
    block = qc.ensure_question_block(config, "QID2", column_map=_column_map())
    assert block["_question"] == "Q1.5: Select your duty location:"
    assert block["_response_labels"] == {"1": "Schofield", "2": "Fort Bragg", "3": "Other"}
    # Engine fields are unaffected.
    assert block["include"] is True
    assert block["sort_by"] == "auto"


def test_ensure_question_block_without_column_map_stays_plain():
    config = {}
    block = qc.ensure_question_block(config, "QID2")
    assert "_question" not in block
    assert block == qc.QUESTION_DEFAULT_SKELETON


def test_effective_question_config_merges_defaults_and_overrides():
    config = {"defaults": {"sort_by": "count_desc"}, "questions": {"QID2": {"sort_by": "survey_order"}}}
    eff = qc.effective_question_config(config, "QID2")
    assert eff["sort_by"] == "survey_order"
    eff_other = qc.effective_question_config(config, "QID99")
    assert eff_other["sort_by"] == "count_desc"


def test_set_question_field_creates_block_if_needed():
    config = {}
    qc.set_question_field(config, "QID2", "percent_base", "total")
    assert config["questions"]["QID2"]["percent_base"] == "total"


def test_unset_question_field_removes_override():
    config = {"questions": {"QID2": {"stats": ["n", "pct"]}}}
    qc.unset_question_field(config, "QID2", "stats")
    assert "stats" not in config["questions"]["QID2"]


def test_unset_question_field_noop_when_absent():
    config = {"questions": {"QID2": {}}}
    qc.unset_question_field(config, "QID2", "stats")  # should not raise
    qc.unset_question_field(config, "QID_NOPE", "stats")  # no block at all


def test_reset_question_removes_block():
    config = {"questions": {"QID2": {"include": False}, "QID3": {"include": True}}}
    qc.reset_question(config, "QID2")
    assert "QID2" not in config["questions"]
    assert "QID3" in config["questions"]


# ---------------------------------------------------------------------------
# table spec management
# ---------------------------------------------------------------------------

def test_list_table_specs_defaults_to_overall_only():
    config = {"questions": {"QID2": {}}}
    assert qc.list_table_specs(config, "QID2") == [{"group_by": []}]


def test_add_table_spec_preserves_implicit_overall():
    config = {}
    qc.add_table_spec(config, "QID2", {"group_by": ["Q1.9"]})
    tables = qc.list_table_specs(config, "QID2")
    assert tables == [{"group_by": []}, {"group_by": ["Q1.9"]}]


def test_add_table_spec_appends_to_existing_tables():
    config = {"questions": {"QID2": {"tables": [{"group_by": []}, {"group_by": ["Q1.9"]}]}}}
    qc.add_table_spec(config, "QID2", {"group_by": ["Q2.4"]})
    tables = qc.list_table_specs(config, "QID2")
    assert len(tables) == 3
    assert tables[-1] == {"group_by": ["Q2.4"]}


def test_remove_table_spec_by_index():
    config = {"questions": {"QID2": {"tables": [{"group_by": []}, {"group_by": ["Q1.9"]}]}}}
    assert qc.remove_table_spec(config, "QID2", 1) is True
    assert qc.list_table_specs(config, "QID2") == [{"group_by": []}]


def test_remove_table_spec_out_of_range_returns_false():
    config = {"questions": {"QID2": {"tables": [{"group_by": []}]}}}
    assert qc.remove_table_spec(config, "QID2", 5) is False


def test_remove_table_spec_missing_question_returns_false():
    config = {}
    assert qc.remove_table_spec(config, "QID2", 0) is False
