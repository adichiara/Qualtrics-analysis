"""Interactive menu for the Qualtrics export -> frequency -> report pipeline.

Wraps the existing module CLIs (export, frequencies, report, config_validate)
in a single guided menu so a full run doesn't require remembering which flags
go where. Run with ``python -m qualtrics_pipeline.cli`` or the ``qualtrics``
console script.

Pure/testable logic (state persistence, run discovery, env status) is kept in
plain functions; the menu loop itself is a thin I/O wrapper around them and
around the existing pipeline entry points (export.run_export,
frequencies.run_frequency_analysis, report.generate_html_report,
config_validate.validate_config).
"""

from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Callable

STATE_FILE = Path(".qualtrics_cli_state.json")
DEFAULT_CONFIG_NAME = "qualtrics_frequency_config.json"
REQUIRED_ENV_VARS = ["QUALTRICS_API_TOKEN", "QUALTRICS_DATA_CENTER", "QUALTRICS_DIRECTORY_ID"]
DEFAULT_EDITOR = "vi"

InputFn = Callable[[str], str]
PrintFn = Callable[..., None]


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without touching stdin/stdout)
# ---------------------------------------------------------------------------

def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def discover_runs(base_dir: str | Path = "runs") -> list[Path]:
    """Subdirectories of ``base_dir`` that look like export runs."""
    base = Path(base_dir)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "column_map.json").exists())


def pick_data_file(run_dir: str | Path) -> Path | None:
    """Prefer the deidentified file; fall back to raw."""
    run_dir = Path(run_dir)
    for name in ("responses_clean.csv", "responses_raw.csv"):
        candidate = run_dir / name
        if candidate.exists():
            return candidate
    return None


def env_status(env: dict[str, str] | None = None) -> dict[str, bool]:
    """Whether each required Qualtrics env var is set (never returns values)."""
    env = os.environ if env is None else env
    return {name: bool(env.get(name)) for name in REQUIRED_ENV_VARS}


def default_config_path(run_dir: str | Path | None) -> Path:
    """Where a run's frequency config lives: alongside the run if known."""
    if run_dir is None:
        return Path(DEFAULT_CONFIG_NAME)
    return Path(run_dir) / DEFAULT_CONFIG_NAME


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _ask(input_fn: InputFn, prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input_fn(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def _ask_choice(input_fn: InputFn, print_fn: PrintFn, prompt: str, options: list[str], default_index: int = 0) -> str:
    print_fn(prompt)
    for i, opt in enumerate(options, 1):
        marker = " (default)" if i - 1 == default_index else ""
        print_fn(f"  {i}. {opt}{marker}")
    raw = input_fn("> ").strip()
    if not raw:
        return options[default_index]
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    return options[default_index]


def _confirm(input_fn: InputFn, prompt: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input_fn(f"{prompt} [{suffix}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _pause(input_fn: InputFn, print_fn: PrintFn) -> None:
    input_fn("Press Enter to continue...")


def _select_run(state: dict, input_fn: InputFn, print_fn: PrintFn) -> Path | None:
    """Let the user pick an existing run directory or type a custom path."""
    runs = discover_runs()
    if not runs:
        print_fn("No runs found under ./runs. Enter a run directory path.")
        raw = _ask(input_fn, "Run directory", state.get("last_run_dir"))
        return Path(raw) if raw else None

    options = [str(r) for r in runs] + ["Enter a custom path..."]
    default_index = 0
    last = state.get("last_run_dir")
    if last and last in options:
        default_index = options.index(last)
    choice = _ask_choice(input_fn, print_fn, "Select a run:", options, default_index)
    if choice == "Enter a custom path...":
        raw = _ask(input_fn, "Run directory")
        return Path(raw) if raw else None
    return Path(choice)


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def action_export(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    from .export import run_export

    status = env_status()
    missing = [k for k, ok in status.items() if not ok]
    if missing:
        print_fn(f"Missing environment variable(s): {', '.join(missing)}")
        print_fn("Set them (e.g. in your shell profile) before exporting.")
        return

    survey_id = _ask(input_fn, "Survey ID", state.get("last_survey_id"))
    if not survey_id:
        print_fn("Survey ID is required.")
        return
    default_outdir = state.get("last_run_dir") or f"runs/{survey_id}"
    outdir = _ask(input_fn, "Output directory", default_outdir)
    privacy_mode = _ask_choice(
        input_fn, print_fn, "Privacy mode:",
        ["deidentified", "internal", "raw"],
        default_index=["deidentified", "internal", "raw"].index(state.get("last_privacy_mode", "deidentified")),
    )

    print_fn(f"Exporting {survey_id} -> {outdir} ({privacy_mode})...")
    try:
        run_export(survey_id, outdir, privacy_mode)
    except SystemExit as e:
        print_fn(f"Export failed: {e}")
        return
    except Exception as e:  # network/API errors from QualtricsAPI/requests
        print_fn(f"Export failed: {e}")
        return

    state.update(last_survey_id=survey_id, last_run_dir=outdir, last_privacy_mode=privacy_mode)
    save_state(state)
    print_fn(f"Export complete: {outdir}")


def action_init_config(state: dict, input_fn: InputFn, print_fn: PrintFn) -> Path | None:
    from .frequencies import build_default_config

    run_dir = _select_run(state, input_fn, print_fn)
    if run_dir is None:
        return None
    column_map_path = run_dir / "column_map.json"
    if not column_map_path.exists():
        print_fn(f"No column_map.json found in {run_dir}")
        return None

    config_path = Path(_ask(input_fn, "Config path", str(default_config_path(run_dir))))
    if config_path.exists() and not _confirm(input_fn, f"{config_path} already exists. Overwrite?", default=False):
        print_fn("Kept existing config.")
        state.update(last_run_dir=str(run_dir), last_config_path=str(config_path))
        save_state(state)
        return config_path

    cmap = json.loads(column_map_path.read_text(encoding="utf-8"))
    config_path.write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")
    print_fn(f"Wrote {config_path}")
    state.update(last_run_dir=str(run_dir), last_config_path=str(config_path))
    save_state(state)
    return config_path


def action_edit_config(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    """Hand-edit the config file in $EDITOR, then validate it on return.

    Since --init-config now writes a self-documenting config (a _reference
    cheat sheet, _groupable_questions, and per-question _question /
    _response_labels annotations), editing the JSON directly is the fastest
    way to sweep through many questions -- faster than the guided
    "Configure a single question" menu, which is better suited to one-off
    tweaks.
    """
    from .config_validate import format_issues, validate_config

    run_dir = _select_run(state, input_fn, print_fn)
    if run_dir is None:
        return
    column_map_path = run_dir / "column_map.json"
    if not column_map_path.exists():
        print_fn(f"No column_map.json found in {run_dir}")
        return
    cmap = json.loads(column_map_path.read_text(encoding="utf-8"))

    config_path = Path(_ask(input_fn, "Config path", state.get("last_config_path") or str(default_config_path(run_dir))))
    if not config_path.exists():
        if not _confirm(input_fn, f"{config_path} does not exist. Initialize it now?"):
            print_fn("Nothing to edit.")
            return
        from .frequencies import build_default_config

        config_path.write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")
        print_fn(f"Wrote {config_path}")

    state.update(last_run_dir=str(run_dir), last_config_path=str(config_path))
    save_state(state)

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or DEFAULT_EDITOR
    print_fn(f"Opening {config_path} in '{editor}'...")
    try:
        subprocess.run([editor, str(config_path)], check=False)
    except OSError:
        print_fn(f"Could not launch editor '{editor}'. Set $EDITOR and edit {config_path} yourself, "
                  "then use 'Validate config'.")
        return

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print_fn(f"{config_path} is not valid JSON: {e}")
        return
    issues = validate_config(config, cmap)
    print_fn(format_issues(issues) if issues else "Config OK: no issues found.")


def _truncate(text: str, width: int) -> str:
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "…"


SORT_BY_CHOICES = ["auto", "survey_order", "count_desc", "count_asc", "response_order"]
PERCENT_BASE_CHOICES = ["eligible", "valid", "total"]
STAT_ORDER = ["n", "pct", "valid_n", "valid_pct", "eligible_n", "eligible_pct", "total_n", "total_pct", "base_n"]
STAT_LABELS = {
    "n": "n (count)", "pct": "% (featured base)", "valid_n": "Valid n", "valid_pct": "Valid %",
    "eligible_n": "Eligible n", "eligible_pct": "Eligible %", "total_n": "Total n",
    "total_pct": "Total %", "base_n": "n (featured base)",
}


def _load_or_init_config(run_dir: Path, config_path: Path, cmap: list[dict], print_fn: PrintFn) -> dict:
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    from .frequencies import build_default_config

    print_fn(f"{config_path} does not exist; starting from defaults (not written until you save).")
    return build_default_config(cmap)


def _edit_stats(config: dict, qkey: str, eff: dict, cmap: list[dict], input_fn: InputFn, print_fn: PrintFn) -> None:
    from .question_config import set_question_field, unset_question_field

    current = eff.get("stats")
    print_fn("Available stats (current marked *):")
    for i, key in enumerate(STAT_ORDER, 1):
        mark = " *" if current and key in current else ""
        print_fn(f"  {i}. {STAT_LABELS[key]}{mark}")
    default_raw = ",".join(str(STAT_ORDER.index(s) + 1) for s in current if s in STAT_ORDER) if current else ""
    raw = _ask(input_fn, "Numbers comma-separated (blank = use report default)", default_raw)
    if not raw.strip():
        unset_question_field(config, qkey, "stats")
        print_fn("Using report default stats.")
        return
    picks = [STAT_ORDER[int(tok) - 1] for tok in raw.split(",")
             if tok.strip().isdigit() and 1 <= int(tok.strip()) <= len(STAT_ORDER)]
    if picks:
        set_question_field(config, qkey, "stats", picks, column_map=cmap)
        print_fn(f"Stats set: {', '.join(picks)}")
    else:
        print_fn("No valid selection; unchanged.")


def _manage_breakouts(config: dict, qkey: str, cmap: list[dict], input_fn: InputFn, print_fn: PrintFn) -> None:
    from .question_config import add_table_spec, groupable_columns, list_table_specs, remove_table_spec

    while True:
        tables = list_table_specs(config, qkey)
        print_fn("\nTables for this question:")
        for i, t in enumerate(tables):
            gb = t.get("group_by") or []
            label = "Overall (ungrouped)" if not gb else f"By {', '.join(gb)}"
            extra = []
            if t.get("orientation") == "rows":
                extra.append("groups as rows")
            if t.get("overall"):
                extra.append(f"+Overall ({t['overall']})")
            if t.get("response_total"):
                extra.append(f"+Total ({t['response_total']})")
            if extra:
                label += f" [{', '.join(extra)}]"
            print_fn(f"  [{i}] {label}")

        action = _ask_choice(input_fn, print_fn, "Breakouts:",
                              ["Add a breakout", "Remove a breakout", "Back"], default_index=2)
        if action == "Back":
            return

        if action == "Add a breakout":
            options = groupable_columns(cmap, exclude_qkey=qkey)
            if not options:
                print_fn("No single-answer questions available to group by.")
                continue
            print_fn("Group by which question(s)? (comma-separated numbers for more than one)")
            for i, o in enumerate(options, 1):
                print_fn(f"  {i}. {o['question_id']}: {_truncate(o['question_text'], 60)}")
            raw = input_fn("> ").strip()
            picks = [options[int(tok) - 1]["column"] for tok in raw.split(",")
                     if tok.strip().isdigit() and 1 <= int(tok.strip()) <= len(options)]
            if not picks:
                print_fn("No valid selection; nothing added.")
                continue
            spec: dict = {"group_by": picks}
            if _confirm(input_fn, "Add an Overall column/row alongside the groups?", default=False):
                spec["overall"] = _ask_choice(input_fn, print_fn, "Overall position:", ["before", "after"], 1)
            if _confirm(input_fn, "Transpose: show groups as rows instead of columns?", default=False):
                spec["orientation"] = "rows"
            if _confirm(input_fn, "Add a Total across response options?", default=False):
                spec["response_total"] = _ask_choice(input_fn, print_fn, "Total position:", ["before", "after"], 1)
            add_table_spec(config, qkey, spec, column_map=cmap)
            print_fn("Breakout added.")

        elif action == "Remove a breakout":
            raw = _ask(input_fn, "Index to remove (see [n] above)")
            if raw.isdigit() and remove_table_spec(config, qkey, int(raw)):
                print_fn("Removed.")
            else:
                print_fn("Nothing removed.")


def _edit_question(selected: dict, config: dict, cmap: list[dict], input_fn: InputFn, print_fn: PrintFn) -> None:
    from .question_config import (
        effective_question_config,
        list_table_specs,
        question_response_labels,
        reset_question,
        set_question_field,
    )

    qkey = selected["qkey"]
    while True:
        eff = effective_question_config(config, qkey)
        tables = list_table_specs(config, qkey)
        print_fn(f"\n--- {selected['question_id']}: {_truncate(selected['question_text'], 70)} ---")
        print_fn(f"  include: {eff.get('include', True)}")
        sort_by = eff.get("sort_by", "auto")
        order_note = f" (order: {eff.get('response_order')})" if sort_by == "response_order" else ""
        print_fn(f"  sort_by: {sort_by}{order_note}")
        print_fn(f"  percent_base: {eff.get('percent_base', 'eligible')}")
        print_fn(f"  show_code: {eff.get('show_code', True)}")
        print_fn(f"  stats: {eff.get('stats') or '(report default)'}")
        print_fn(f"  tables: {len(tables)}")
        for i, t in enumerate(tables):
            gb = t.get("group_by") or []
            print_fn(f"    [{i}] {'Overall' if not gb else 'By ' + ', '.join(gb)}")

        options = [
            "Include/exclude this question", "Sort order", "Percent base",
            "Show/hide response code", "Stats to display", "Manage breakouts (grouped tables)",
            "Reset to defaults", "Back",
        ]
        choice = _ask_choice(input_fn, print_fn, "Edit:", options, default_index=len(options) - 1)

        if choice == "Back":
            return
        if choice == "Include/exclude this question":
            new = _confirm(input_fn, "Include this question in the report?", default=eff.get("include", True))
            set_question_field(config, qkey, "include", new, column_map=cmap)
        elif choice == "Sort order":
            cur = sort_by if sort_by in SORT_BY_CHOICES else "auto"
            new_sort = _ask_choice(input_fn, print_fn, "Sort by:", SORT_BY_CHOICES, SORT_BY_CHOICES.index(cur))
            set_question_field(config, qkey, "sort_by", new_sort, column_map=cmap)
            if new_sort == "response_order":
                labels = question_response_labels(cmap, qkey)
                if labels:
                    print_fn("Available codes:")
                    for code, label in labels.items():
                        print_fn(f"  {code}: {label}")
                raw = _ask(input_fn, "Codes in desired order, comma-separated",
                           ",".join(eff.get("response_order") or []))
                order = [c.strip() for c in raw.split(",") if c.strip()]
                set_question_field(config, qkey, "response_order", order, column_map=cmap)
        elif choice == "Percent base":
            cur = eff.get("percent_base", "eligible")
            cur = cur if cur in PERCENT_BASE_CHOICES else "eligible"
            new_base = _ask_choice(input_fn, print_fn, "Percent base:", PERCENT_BASE_CHOICES,
                                    PERCENT_BASE_CHOICES.index(cur))
            set_question_field(config, qkey, "percent_base", new_base, column_map=cmap)
        elif choice == "Show/hide response code":
            new = _confirm(input_fn, "Show the response-code column?", default=eff.get("show_code", True))
            set_question_field(config, qkey, "show_code", new, column_map=cmap)
        elif choice == "Stats to display":
            _edit_stats(config, qkey, eff, cmap, input_fn, print_fn)
        elif choice == "Manage breakouts (grouped tables)":
            _manage_breakouts(config, qkey, cmap, input_fn, print_fn)
        elif choice == "Reset to defaults":
            if _confirm(input_fn, "Remove all custom settings for this question?", default=False):
                reset_question(config, qkey)
                print_fn("Reset.")


def action_configure_question(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    from .config_validate import format_issues, validate_config
    from .question_config import find_questions, question_summaries

    run_dir = _select_run(state, input_fn, print_fn)
    if run_dir is None:
        return
    column_map_path = run_dir / "column_map.json"
    if not column_map_path.exists():
        print_fn(f"No column_map.json found in {run_dir}")
        return
    cmap = json.loads(column_map_path.read_text(encoding="utf-8"))

    config_path = Path(_ask(input_fn, "Config path", state.get("last_config_path") or str(default_config_path(run_dir))))
    config = _load_or_init_config(run_dir, config_path, cmap, print_fn)

    summaries = question_summaries(cmap)
    if not summaries:
        print_fn("No reportable questions found in this column map.")
        return

    print_fn(f"\n{len(summaries)} question(s). Type a number, a tag (e.g. Q1.5), part of the "
              "question text, or 'done' to save and exit.")
    for i, s in enumerate(summaries, 1):
        print_fn(f"  {i}. {s['question_id']}: {_truncate(s['question_text'], 65)}")

    while True:
        query = input_fn("\nQuestion (or 'done'): ").strip()
        if not query or query.lower() in ("done", "exit", "q"):
            break
        matches = find_questions(summaries, query)
        if not matches:
            print_fn("No matching question. Try again, or type 'done' to finish.")
            continue
        if len(matches) > 1:
            print_fn(f"{len(matches)} matches:")
            for i, s in enumerate(matches, 1):
                print_fn(f"  {i}. {s['question_id']}: {_truncate(s['question_text'], 65)}")
            sub = input_fn("Pick a number, or Enter to cancel: ").strip()
            if not sub.isdigit() or not (1 <= int(sub) <= len(matches)):
                continue
            selected = matches[int(sub) - 1]
        else:
            selected = matches[0]
        _edit_question(selected, config, cmap, input_fn, print_fn)

    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print_fn(f"Saved {config_path}")
    state.update(last_run_dir=str(run_dir), last_config_path=str(config_path))
    save_state(state)

    issues = validate_config(config, cmap)
    print_fn(format_issues(issues) if issues else "Config OK: no issues found.")


def action_validate_config(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    from .config_validate import format_issues, validate_config

    run_dir = _select_run(state, input_fn, print_fn)
    if run_dir is None:
        return
    column_map_path = run_dir / "column_map.json"
    if not column_map_path.exists():
        print_fn(f"No column_map.json found in {run_dir}")
        return
    config_path = Path(_ask(input_fn, "Config path", state.get("last_config_path") or str(default_config_path(run_dir))))
    if not config_path.exists():
        print_fn(f"{config_path} does not exist. Initialize it first.")
        return

    cmap = json.loads(column_map_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    issues = validate_config(config, cmap)
    if not issues:
        print_fn("Config OK: no issues found.")
    else:
        print_fn(format_issues(issues))
    state.update(last_run_dir=str(run_dir), last_config_path=str(config_path))
    save_state(state)


def action_run_analysis(state: dict, input_fn: InputFn, print_fn: PrintFn) -> Path | None:
    from .frequencies import run_frequency_analysis

    run_dir = _select_run(state, input_fn, print_fn)
    if run_dir is None:
        return None
    column_map_path = run_dir / "column_map.json"
    if not column_map_path.exists():
        print_fn(f"No column_map.json found in {run_dir}")
        return None
    data_path = pick_data_file(run_dir)
    if data_path is None:
        print_fn(f"No responses_clean.csv or responses_raw.csv found in {run_dir}")
        return None
    config_path = Path(_ask(input_fn, "Config path", state.get("last_config_path") or str(default_config_path(run_dir))))
    if not config_path.exists():
        print_fn(f"{config_path} does not exist.")
        if _confirm(input_fn, "Initialize a default config now?"):
            from .frequencies import build_default_config

            cmap = json.loads(column_map_path.read_text(encoding="utf-8"))
            config_path.write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")
            print_fn(f"Wrote {config_path}")
        else:
            return None

    print_fn(f"Running frequency analysis on {data_path}...")
    try:
        outs = run_frequency_analysis(data_path, column_map_path, run_dir, config_path)
    except SystemExit as e:
        print_fn(f"Analysis failed: {e}")
        return None

    print_fn(f"Wrote {len(outs)} output file(s), including report.html")
    state.update(last_run_dir=str(run_dir), last_config_path=str(config_path))
    save_state(state)
    report_path = run_dir / "report.html"
    if report_path.exists() and _confirm(input_fn, "Open report.html in a browser?"):
        webbrowser.open(report_path.resolve().as_uri())
    return run_dir


def action_regenerate_report(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    from .report import generate_html_report

    run_dir = _select_run(state, input_fn, print_fn)
    if run_dir is None:
        return
    try:
        report_path = generate_html_report(run_dir)
    except SystemExit as e:
        print_fn(f"Report generation failed: {e}")
        return
    print_fn(f"Wrote {report_path}")
    state.update(last_run_dir=str(run_dir))
    save_state(state)
    if _confirm(input_fn, "Open report.html in a browser?"):
        webbrowser.open(report_path.resolve().as_uri())


def action_full_pipeline(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    print_fn("--- Step 1: Export ---")
    action_export(state, input_fn, print_fn)
    run_dir = state.get("last_run_dir")
    if not run_dir or not Path(run_dir).exists():
        print_fn("Export did not complete; stopping.")
        return

    config_path = Path(state.get("last_config_path") or default_config_path(run_dir))
    if not config_path.exists():
        print_fn("--- Step 2: Initialize config ---")
        cmap_path = Path(run_dir) / "column_map.json"
        from .frequencies import build_default_config

        cmap = json.loads(cmap_path.read_text(encoding="utf-8"))
        config_path.write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")
        print_fn(f"Wrote {config_path}")
        state["last_config_path"] = str(config_path)
        save_state(state)

    print_fn("--- Step 3: Run analysis + report ---")
    from .frequencies import run_frequency_analysis

    data_path = pick_data_file(run_dir)
    try:
        outs = run_frequency_analysis(data_path, Path(run_dir) / "column_map.json", run_dir, config_path)
    except SystemExit as e:
        print_fn(f"Analysis failed: {e}")
        return
    print_fn(f"Wrote {len(outs)} output file(s), including report.html")
    report_path = Path(run_dir) / "report.html"
    if report_path.exists() and _confirm(input_fn, "Open report.html in a browser?"):
        webbrowser.open(report_path.resolve().as_uri())


def action_show_status(state: dict, input_fn: InputFn, print_fn: PrintFn) -> None:
    print_fn("Environment:")
    for name, ok in env_status().items():
        print_fn(f"  {name}: {'set' if ok else 'MISSING'}")
    print_fn("Remembered state:")
    for k, v in state.items():
        print_fn(f"  {k}: {v}")
    runs = discover_runs()
    print_fn(f"Discovered runs ({len(runs)}):")
    for r in runs:
        print_fn(f"  {r}")


MENU = [
    ("Export survey from Qualtrics", action_export),
    ("Initialize frequency config for a run", action_init_config),
    ("Edit config file (recommended: hand-edit the self-documenting JSON)", action_edit_config),
    ("Configure a single question (guided menu)", action_configure_question),
    ("Validate config", action_validate_config),
    ("Run frequency analysis + report", action_run_analysis),
    ("Regenerate HTML report only", action_regenerate_report),
    ("Full pipeline (export -> config -> analysis)", action_full_pipeline),
    ("Show status (env vars, state, discovered runs)", action_show_status),
]


def main(input_fn: InputFn = input, print_fn: PrintFn = print) -> None:
    state = load_state()
    print_fn("Qualtrics Pipeline")
    print_fn("==================")
    while True:
        options = [label for label, _ in MENU] + ["Exit"]
        print_fn("")
        choice = _ask_choice(input_fn, print_fn, "What would you like to do?", options, default_index=len(options) - 1)
        if choice == "Exit":
            print_fn("Goodbye.")
            return
        handler = dict(MENU)[choice]
        try:
            handler(state, input_fn, print_fn)
        except (EOFError, KeyboardInterrupt):
            print_fn("\nGoodbye.")
            return
        except Exception as e:  # keep the menu alive on unexpected errors
            print_fn(f"Error: {e}")


if __name__ == "__main__":
    main()
