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
import webbrowser
from pathlib import Path
from typing import Any, Callable

STATE_FILE = Path(".qualtrics_cli_state.json")
DEFAULT_CONFIG_NAME = "qualtrics_frequency_config.json"
REQUIRED_ENV_VARS = ["QUALTRICS_API_TOKEN", "QUALTRICS_DATA_CENTER", "QUALTRICS_DIRECTORY_ID"]

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
    ("Initialize / update frequency config for a run", action_init_config),
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
