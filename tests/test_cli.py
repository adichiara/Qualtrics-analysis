import json
from pathlib import Path

from qualtrics_pipeline import cli


class FakeInput:
    """Feeds canned answers to input_fn calls in order."""

    def __init__(self, answers: list[str]):
        self.answers = list(answers)

    def __call__(self, prompt: str = "") -> str:
        if not self.answers:
            raise AssertionError(f"FakeInput exhausted at prompt: {prompt!r}")
        return self.answers.pop(0)


def _capture():
    lines: list[str] = []

    def print_fn(*args, **kwargs):
        lines.append(" ".join(str(a) for a in args))

    return lines, print_fn


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_load_state_missing_file_returns_empty(tmp_path):
    assert cli.load_state(tmp_path / "nope.json") == {}


def test_load_state_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    assert cli.load_state(p) == {}


def test_save_and_load_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    cli.save_state({"last_survey_id": "SV_1"}, p)
    assert cli.load_state(p) == {"last_survey_id": "SV_1"}


def test_discover_runs_finds_only_dirs_with_column_map(tmp_path):
    base = tmp_path / "runs"
    (base / "a").mkdir(parents=True)
    (base / "a" / "column_map.json").write_text("[]", encoding="utf-8")
    (base / "b").mkdir(parents=True)  # no column_map.json
    (base / "c.txt").write_text("x", encoding="utf-8")  # not a dir

    found = cli.discover_runs(base)
    assert found == [base / "a"]


def test_discover_runs_missing_base_dir_returns_empty(tmp_path):
    assert cli.discover_runs(tmp_path / "does_not_exist") == []


def test_pick_data_file_prefers_clean_over_raw(tmp_path):
    (tmp_path / "responses_clean.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "responses_raw.csv").write_text("a\n1\n", encoding="utf-8")
    assert cli.pick_data_file(tmp_path) == tmp_path / "responses_clean.csv"


def test_pick_data_file_falls_back_to_raw(tmp_path):
    (tmp_path / "responses_raw.csv").write_text("a\n1\n", encoding="utf-8")
    assert cli.pick_data_file(tmp_path) == tmp_path / "responses_raw.csv"


def test_pick_data_file_none_when_neither_exists(tmp_path):
    assert cli.pick_data_file(tmp_path) is None


def test_env_status_reports_missing_vars():
    status = cli.env_status(env={"QUALTRICS_API_TOKEN": "x"})
    assert status["QUALTRICS_API_TOKEN"] is True
    assert status["QUALTRICS_DATA_CENTER"] is False
    assert status["QUALTRICS_DIRECTORY_ID"] is False


def test_default_config_path_uses_run_dir_when_given():
    assert cli.default_config_path("runs/example") == Path("runs/example/qualtrics_frequency_config.json")


def test_default_config_path_falls_back_to_cwd_name_when_no_run():
    assert cli.default_config_path(None) == Path(cli.DEFAULT_CONFIG_NAME)


# ---------------------------------------------------------------------------
# Menu action wiring (no network / no real Qualtrics calls)
# ---------------------------------------------------------------------------

def _fixture_run(tmp_path) -> Path:
    """A minimal run directory good enough for config/report actions."""
    run_dir = tmp_path / "runs" / "SV_1"
    run_dir.mkdir(parents=True)
    column_map = [
        {"survey_id": "SV_1", "qid": "QID1", "data_export_tag": "Q1", "column": "Q1",
         "question_type": "MC", "selector": "SAVR", "question_text": "Pick one",
         "sub_question_text": "", "response_labels": {"1": "A", "2": "B"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QID1",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
    ]
    (run_dir / "column_map.json").write_text(json.dumps(column_map), encoding="utf-8")
    (run_dir / "responses_clean.csv").write_text("Q1\n1\n2\n1\n", encoding="utf-8")
    return run_dir


def test_action_init_config_writes_default_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run(tmp_path)
    lines, print_fn = _capture()
    input_fn = FakeInput([str(run_dir), str(run_dir / "cfg.json")])
    state: dict = {}

    result = cli.action_init_config(state, input_fn, print_fn)

    assert result == run_dir / "cfg.json"
    assert result.exists()
    cfg = json.loads(result.read_text(encoding="utf-8"))
    assert "QID1" in cfg["questions"]
    assert state["last_config_path"] == str(result)


def test_action_init_config_respects_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run(tmp_path)
    cfg_path = run_dir / "cfg.json"
    cfg_path.write_text(json.dumps({"defaults": {}, "questions": {"sentinel": True}}), encoding="utf-8")
    lines, print_fn = _capture()
    input_fn = FakeInput([str(run_dir), str(cfg_path), "n"])  # decline overwrite
    state: dict = {}

    cli.action_init_config(state, input_fn, print_fn)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["questions"] == {"sentinel": True}  # untouched


def test_action_validate_config_reports_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run(tmp_path)
    cfg_path = run_dir / "cfg.json"
    cfg_path.write_text(json.dumps({"defaults": {}, "questions": {}}), encoding="utf-8")
    lines, print_fn = _capture()
    input_fn = FakeInput([str(run_dir), str(cfg_path)])
    state: dict = {}

    cli.action_validate_config(state, input_fn, print_fn)

    assert any("OK" in line for line in lines)


def test_action_validate_config_reports_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run(tmp_path)
    cfg_path = run_dir / "cfg.json"
    cfg_path.write_text(json.dumps({"defaults": {}, "questions": {"QID1": {"sort_bye": "x"}}}), encoding="utf-8")
    lines, print_fn = _capture()
    input_fn = FakeInput([str(run_dir), str(cfg_path)])
    state: dict = {}

    cli.action_validate_config(state, input_fn, print_fn)

    assert any("ERROR" in line and "sort_bye" in line for line in lines)


def test_action_run_analysis_produces_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run(tmp_path)
    cfg_path = run_dir / "cfg.json"
    from qualtrics_pipeline.frequencies import build_default_config

    cmap = json.loads((run_dir / "column_map.json").read_text(encoding="utf-8"))
    cfg_path.write_text(json.dumps(build_default_config(cmap)), encoding="utf-8")

    lines, print_fn = _capture()
    input_fn = FakeInput([str(run_dir), str(cfg_path), "n"])  # decline opening browser
    state: dict = {}

    result = cli.action_run_analysis(state, input_fn, print_fn)

    assert result.resolve() == run_dir.resolve()
    assert (run_dir / "report.html").exists()
    assert (run_dir / "frequency_tables" / "QID1_frequencies.csv").exists()


def test_action_run_analysis_missing_data_file_reports_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "runs" / "empty"
    run_dir.mkdir(parents=True)
    (run_dir / "column_map.json").write_text("[]", encoding="utf-8")
    lines, print_fn = _capture()
    input_fn = FakeInput([str(run_dir)])
    state: dict = {}

    result = cli.action_run_analysis(state, input_fn, print_fn)

    assert result is None
    assert any("No responses_clean.csv" in line for line in lines)


def test_action_export_blocks_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUALTRICS_API_TOKEN", raising=False)
    monkeypatch.delenv("QUALTRICS_DATA_CENTER", raising=False)
    monkeypatch.delenv("QUALTRICS_DIRECTORY_ID", raising=False)
    lines, print_fn = _capture()
    input_fn = FakeInput([])  # should never be consulted
    state: dict = {}

    cli.action_export(state, input_fn, print_fn)

    assert any("Missing environment variable" in line for line in lines)


def test_main_exits_cleanly_on_default_enter(monkeypatch):
    lines, print_fn = _capture()
    input_fn = FakeInput([""])  # Enter on the menu defaults to Exit
    cli.main(input_fn=input_fn, print_fn=print_fn)
    assert any("Goodbye" in line for line in lines)


# ---------------------------------------------------------------------------
# action_configure_question
# ---------------------------------------------------------------------------

def _fixture_run_two_questions(tmp_path) -> Path:
    """A run with two single-answer questions, so grouping/breakouts work."""
    run_dir = tmp_path / "runs" / "SV_2"
    run_dir.mkdir(parents=True)
    column_map = [
        {"survey_id": "SV_2", "qid": "QID1", "data_export_tag": "Q1", "column": "Q1",
         "question_type": "MC", "selector": "SAVR", "question_text": "Duty location",
         "sub_question_text": "", "response_labels": {"1": "A", "2": "B", "3": "C"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QID1",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
        {"survey_id": "SV_2", "qid": "QID2", "data_export_tag": "Q2", "column": "Q2",
         "question_type": "MC", "selector": "SAVR", "question_text": "Uniform type",
         "sub_question_text": "", "response_labels": {"1": "X", "2": "Y"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QID2",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
    ]
    (run_dir / "column_map.json").write_text(json.dumps(column_map), encoding="utf-8")
    (run_dir / "responses_clean.csv").write_text("Q1,Q2\n1,1\n2,1\n3,2\n", encoding="utf-8")
    return run_dir


def test_configure_question_sets_sort_and_percent_base(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run_two_questions(tmp_path)
    cfg_path = run_dir / "cfg.json"
    inputs = FakeInput([
        str(run_dir),           # select run
        str(cfg_path),          # config path (doesn't exist yet -> defaults)
        "Q1",                   # find question by tag
        "2",                    # menu: Sort order
        "4",                    # sort_by choice: count_asc (auto,survey_order,count_desc,count_asc,...)
        "3",                    # menu: Percent base
        "3",                    # percent_base choice: total
        "8",                    # menu: Back
        "done",                 # finish
    ])
    lines, print_fn = _capture()
    state: dict = {}

    cli.action_configure_question(state, inputs, print_fn)

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["questions"]["QID1"]["sort_by"] == "count_asc"
    assert saved["questions"]["QID1"]["percent_base"] == "total"
    assert any("Saved" in line for line in lines)
    assert any("Config OK" in line for line in lines)


def test_configure_question_add_breakout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run_two_questions(tmp_path)
    cfg_path = run_dir / "cfg.json"
    inputs = FakeInput([
        str(run_dir), str(cfg_path),
        "Q1",         # configure the duty-location question
        "6",          # menu: Manage breakouts
        "1",          # breakouts: Add a breakout
        "1",          # group by option 1 -> Q2 (the only other single-answer question)
        "n",          # no Overall
        "n",          # no transpose
        "n",          # no response total
        "3",          # breakouts: Back
        "8",          # question menu: Back
        "done",
    ])
    lines, print_fn = _capture()
    state: dict = {}

    cli.action_configure_question(state, inputs, print_fn)

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    tables = saved["questions"]["QID1"]["tables"]
    assert tables[0] == {"group_by": []}
    assert tables[1]["group_by"] == ["Q2"]


def test_configure_question_stats_and_reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = _fixture_run_two_questions(tmp_path)
    cfg_path = run_dir / "cfg.json"
    inputs = FakeInput([
        str(run_dir), str(cfg_path),
        "Q1",
        "5",       # menu: Stats to display
        "1,2",     # pick n and pct
        "7",       # menu: Reset to defaults
        "y",       # confirm reset
        "8",       # Back
        "done",
    ])
    lines, print_fn = _capture()
    state: dict = {}

    cli.action_configure_question(state, inputs, print_fn)

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Reset removed the whole question block, including the stats we just set.
    assert "QID1" not in saved.get("questions", {})


def test_configure_question_ambiguous_query_disambiguates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "runs" / "SV_3"
    run_dir.mkdir(parents=True)
    column_map = [
        {"survey_id": "SV_3", "qid": "QID1", "data_export_tag": "Q1", "column": "Q1",
         "question_type": "MC", "selector": "SAVR", "question_text": "Select your location",
         "sub_question_text": "", "response_labels": {"1": "A"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QID1",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
        {"survey_id": "SV_3", "qid": "QID2", "data_export_tag": "Q2", "column": "Q2",
         "question_type": "MC", "selector": "SAVR", "question_text": "Select your unit",
         "sub_question_text": "", "response_labels": {"1": "B"},
         "is_open_text": False, "is_metadata": False, "is_sensitive": False,
         "is_text_entry_suffix": False, "parent_question_key": "QID2",
         "parent_choice_code": "", "parent_choice_label": "", "text_reporting_mode": "skip"},
    ]
    (run_dir / "column_map.json").write_text(json.dumps(column_map), encoding="utf-8")
    cfg_path = run_dir / "cfg.json"
    inputs = FakeInput([
        str(run_dir), str(cfg_path),
        "select",   # matches both questions
        "2",        # pick the second match (Q2)
        "1",        # menu: Include/exclude
        "n",        # exclude it
        "8",        # Back
        "done",
    ])
    lines, print_fn = _capture()
    state: dict = {}

    cli.action_configure_question(state, inputs, print_fn)

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Disambiguation picked the second match (QID2), not the first (QID1).
    assert saved["questions"]["QID2"]["include"] is False
    assert saved["questions"]["QID1"]["include"] is True  # untouched default


def test_configure_question_no_reportable_questions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "runs" / "empty"
    run_dir.mkdir(parents=True)
    (run_dir / "column_map.json").write_text("[]", encoding="utf-8")
    cfg_path = run_dir / "cfg.json"
    inputs = FakeInput([str(run_dir), str(cfg_path)])
    lines, print_fn = _capture()
    state: dict = {}

    cli.action_configure_question(state, inputs, print_fn)

    assert any("No reportable questions" in line for line in lines)
