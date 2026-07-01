from qualtrics_pipeline.config_validate import validate_config


def _column_map():
    return [
        {"column": "Q1.5", "qid": "QID2", "data_export_tag": "Q1.5", "selector": "SAVR",
         "response_labels": {"1": "A", "2": "B"}},
        {"column": "Q1.9", "qid": "QID16", "data_export_tag": "Q1.9", "selector": "SAVR",
         "response_labels": {"1": "X", "2": "Y"}},
        {"column": "Q1.14_1", "qid": "QID22", "data_export_tag": "Q1.14", "selector": "MAVR",
         "response_labels": {"0": "Not selected", "1": "Selected"}},
    ]


def _errors(issues):
    return [(w, m) for level, w, m in issues if level == "error"]


def _warnings(issues):
    return [(w, m) for level, w, m in issues if level == "warning"]


def test_valid_config_has_no_errors():
    config = {
        "defaults": {"sort_by": "auto"},
        "questions": {
            "QID2": {
                "include": True, "sort_by": "count_desc", "percent_base": "total",
                "tables": [{"group_by": []}, {"group_by": ["Q1.9"], "orientation": "rows",
                                              "stats": ["n", "pct"], "overall": "after"}],
            }
        },
    }
    assert _errors(validate_config(config, _column_map())) == []


def test_unknown_option_key_is_error():
    config = {"questions": {"QID2": {"sort_bye": "count_desc"}}}  # typo
    errs = _errors(validate_config(config, _column_map()))
    assert any("unknown option" in m and "sort_bye" in m for _w, m in errs)


def test_invalid_enum_values_are_errors():
    config = {"questions": {"QID2": {
        "percent_base": "everyone", "sort_by": "random", "orientation": "diagonal",
        "overall": "middle", "stats": ["n", "bogus"],
    }}}
    errs = _errors(validate_config(config, _column_map()))
    msgs = " ".join(m for _w, m in errs)
    assert "percent_base" in msgs
    assert "sort_by" in msgs
    assert "orientation" in msgs
    assert "overall" in msgs
    assert "bogus" in msgs


def test_missing_grouping_variable_is_error():
    config = {"questions": {"QID2": {"tables": [{"group_by": ["NoSuchCol"]}]}}}
    errs = _errors(validate_config(config, _column_map()))
    assert any("not found" in m for _w, m in errs)


def test_multiselect_grouping_variable_is_error():
    config = {"questions": {"QID2": {"tables": [{"group_by": ["Q1.14_1"]}]}}}
    errs = _errors(validate_config(config, _column_map()))
    assert any("multi-select" in m for _w, m in errs)


def test_table_level_percent_base_warns():
    config = {"questions": {"QID2": {"tables": [{"group_by": ["Q1.9"], "percent_base": "total"}]}}}
    warns = _warnings(validate_config(config, _column_map()))
    assert any("percent_base" in m and "ignored" in m for _w, m in warns)


def test_unknown_question_key_warns():
    config = {"questions": {"QID_NOPE": {"include": True}}}
    warns = _warnings(validate_config(config, _column_map()))
    assert any("not found in column map" in m for _w, m in warns)


def test_bad_types_are_errors():
    config = {"questions": {"QID2": {"include": "yes", "response_order": "1,2", "tables": {}}}}
    errs = _errors(validate_config(config, _column_map()))
    msgs = " ".join(m for _w, m in errs)
    assert "include" in msgs
    assert "response_order" in msgs
    assert "tables" in msgs


def test_invalid_text_reporting_mode_is_error():
    config = {"questions": {"QID2": {
        "text_entry_columns": {"Q1.5_3_TEXT": {"text_reporting_mode": "do_magic"}}
    }}}
    errs = _errors(validate_config(config, _column_map()))
    assert any("text_reporting_mode" in m for _w, m in errs)


def test_unknown_top_level_key_is_error():
    config = {"question": {"QID2": {}}}  # typo for "questions"
    errs = _errors(validate_config(config, _column_map()))
    assert any("unknown top-level key" in m and "question" in m for _w, m in errs)


def test_defaults_get_full_validation():
    # Defaults are merged into every question, so they need the same checks.
    config = {"defaults": {"tables": {}, "include": "yes", "percent_base": "nope"}}
    errs = _errors(validate_config(config, _column_map()))
    msgs = " ".join(m for w, m in errs if w == "defaults")
    assert "tables must be a list" in msgs
    assert "include" in msgs
    assert "percent_base" in msgs


def test_enum_as_list_is_error_not_crash():
    # Unhashable value must not raise TypeError in the membership check.
    config = {"questions": {"QID2": {"sort_by": ["auto"]}}}
    errs = _errors(validate_config(config, _column_map()))
    assert any("invalid sort_by" in m for _w, m in errs)


def test_non_dict_text_spec_is_error_not_crash():
    config = {"questions": {"QID2": {"text_entry_columns": {"Q1_TEXT": "frequency_text"}}}}
    errs = _errors(validate_config(config, _column_map()))
    assert any("must be an object" in m for _w, m in errs)


def test_unknown_text_entry_column_is_error():
    config = {"questions": {"QID2": {
        "text_entry_columns": {"Q1.5_3_TEX": {"text_reporting_mode": "summarize_later"}}
    }}}
    errs = _errors(validate_config(config, _column_map()))
    assert any("not found in column map" in m for _w, m in errs)


def test_stale_question_block_is_advisory_not_fatal():
    # A question absent from the column map is never applied, so even an
    # otherwise-fatal error inside it stays advisory.
    config = {"questions": {"QID_OLD": {"tables": [{"group_by": ["NoCol"]}], "sort_by": "bogus"}}}
    issues = validate_config(config, _column_map())
    assert _errors(issues) == []
    assert any("not found in column map" in m for _w, m in _warnings(issues))


def test_underscore_keys_are_ignored_everywhere():
    """Underscore-prefixed keys are a documentation convention (see
    build_default_config) and must never trip the unknown-option checks, at
    any level: top-level, defaults/question block, or table spec."""
    config = {
        "_reference": {"sort_by": "..."},
        "_groupable_questions": {"Q1.9": "Uniform"},
        "defaults": {"_note": "applies to all questions", "sort_by": "auto"},
        "questions": {
            "QID2": {
                "_question": "Q1.5: Select your duty location:",
                "_response_labels": {"1": "A"},
                "include": True,
                "tables": [{"_comment": "overall", "group_by": []}],
            }
        },
    }
    assert _errors(validate_config(config, _column_map())) == []


def test_run_aborts_on_invalid_config(tmp_path):
    import json

    from qualtrics_pipeline.frequencies import run_frequency_analysis

    # Minimal data + column map; config with a fatal grouping error.
    (tmp_path / "data.csv").write_text("Q1.5\n1\n2\n", encoding="utf-8")
    cm = tmp_path / "cm.json"
    cm.write_text(json.dumps(_column_map()), encoding="utf-8")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"questions": {"QID2": {"tables": [{"group_by": ["NoSuchCol"]}]}}}),
                   encoding="utf-8")

    import pytest
    with pytest.raises(SystemExit) as exc:
        run_frequency_analysis(tmp_path / "data.csv", cm, tmp_path / "out", cfg)
    assert "Invalid config" in str(exc.value)
