"""Validate a frequency/report config against the column map.

Catches misconfiguration that would otherwise fail silently or softly and
produce a subtly wrong report: unknown option keys (typos), invalid enum
values, and grouping variables that don't exist or are multi-select. Returns a
list of (level, location, message) tuples; "error" issues should block a run,
"warning" issues are advisory.
"""

from __future__ import annotations

from typing import Any

from .frequencies import (
    MULTI_SELECTORS,
    PERCENT_BASES,
    SORT_BY_VALUES,
    STAT_KEYS,
    _question_key,
)

SORT_BY_ALLOWED = SORT_BY_VALUES | {"auto"}
FREQUENCY_MODE_ALLOWED = {"auto", "interval", "nominal"}
ORIENTATION_ALLOWED = {"columns", "rows"}
POSITION_ALLOWED = {False, "before", "after"}
TEXT_MODES = {"skip", "frequency_text", "summarize_later"}

# Options honored at the question (and defaults) level.
KNOWN_QUESTION_KEYS = {
    "include", "sort_by", "frequency_mode", "percent_base", "response_order",
    "text_entry_columns", "tables", "show_code", "orientation", "overall",
    "response_total", "stats",
}
# Options honored on an individual table spec.
KNOWN_TABLE_KEYS = {"group_by", "show_code", "orientation", "overall", "response_total", "stats"}
# Keys that only take effect at the question level; ignored if put on a table spec.
TABLE_IGNORED_KEYS = {
    "percent_base", "sort_by", "response_order", "frequency_mode", "include",
    "tables", "text_entry_columns",
}

Issue = tuple[str, str, str]  # (level, location, message)


def _check_enums(block: dict[str, Any], where: str, errors: list[Issue]) -> None:
    """Validate the enum/typed option values shared by defaults/question/table."""
    def err(msg: str) -> None:
        errors.append(("error", where, msg))

    if "sort_by" in block and block["sort_by"] not in SORT_BY_ALLOWED:
        err(f"invalid sort_by {block['sort_by']!r} (allowed: {sorted(SORT_BY_ALLOWED)})")
    if "frequency_mode" in block and block["frequency_mode"] not in FREQUENCY_MODE_ALLOWED:
        err(f"invalid frequency_mode {block['frequency_mode']!r}")
    if "percent_base" in block and block["percent_base"] not in PERCENT_BASES:
        err(f"invalid percent_base {block['percent_base']!r} (allowed: {sorted(PERCENT_BASES)})")
    if "show_code" in block and not isinstance(block["show_code"], bool):
        err("show_code must be true or false")
    if "orientation" in block and block["orientation"] not in ORIENTATION_ALLOWED:
        err(f"invalid orientation {block['orientation']!r} (allowed: columns, rows)")
    if "overall" in block and block["overall"] not in POSITION_ALLOWED:
        err(f"invalid overall {block['overall']!r} (allowed: false, before, after)")
    if "response_total" in block and block["response_total"] not in POSITION_ALLOWED:
        err(f"invalid response_total {block['response_total']!r} (allowed: false, before, after)")
    if "stats" in block:
        stats = block["stats"]
        if not isinstance(stats, list):
            err("stats must be a list")
        else:
            bad = [s for s in stats if s not in STAT_KEYS]
            if bad:
                err(f"unknown stats {bad} (allowed: {sorted(STAT_KEYS)})")


def validate_config(config: Any, column_map: list[dict[str, Any]]) -> list[Issue]:
    """Return issues found in ``config`` relative to ``column_map``."""
    issues: list[Issue] = []

    def err(where: str, msg: str) -> None:
        issues.append(("error", where, msg))

    def warn(where: str, msg: str) -> None:
        issues.append(("warning", where, msg))

    if not isinstance(config, dict):
        return [("error", "(root)", "config must be a JSON object")]

    by_col = {m["column"]: m for m in column_map}
    valid_qkeys = {_question_key(m) for m in column_map}
    groupable = {c for c, m in by_col.items() if m.get("selector") not in MULTI_SELECTORS}

    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        err("defaults", "must be an object")
    else:
        for k in defaults:
            if k not in KNOWN_QUESTION_KEYS:
                err("defaults", f"unknown option {k!r}")
        _check_enums(defaults, "defaults", issues)

    questions = config.get("questions", {})
    if not isinstance(questions, dict):
        err("questions", "must be an object")
        return issues

    for qkey, qcfg in questions.items():
        where = f"questions.{qkey}"
        if qkey not in valid_qkeys:
            warn(where, f"question key {qkey!r} not found in column map")
        if not isinstance(qcfg, dict):
            err(where, "must be an object")
            continue

        for k in qcfg:
            if k not in KNOWN_QUESTION_KEYS:
                err(where, f"unknown option {k!r}")
        _check_enums(qcfg, where, issues)

        if "include" in qcfg and not isinstance(qcfg["include"], bool):
            err(where, "include must be true or false")
        if "response_order" in qcfg and not isinstance(qcfg["response_order"], list):
            err(where, "response_order must be a list")

        tec = qcfg.get("text_entry_columns")
        if tec is not None:
            if not isinstance(tec, dict):
                err(where, "text_entry_columns must be an object")
            else:
                for col, spec in tec.items():
                    mode = (spec or {}).get("text_reporting_mode")
                    if mode is not None and mode not in TEXT_MODES:
                        err(f"{where}.text_entry_columns.{col}",
                            f"invalid text_reporting_mode {mode!r}")

        tables = qcfg.get("tables")
        if tables is not None and not isinstance(tables, list):
            err(where, "tables must be a list")
        elif isinstance(tables, list):
            for i, spec in enumerate(tables):
                twhere = f"{where}.tables[{i}]"
                if not isinstance(spec, dict):
                    err(twhere, "table spec must be an object")
                    continue
                for k in spec:
                    if k in TABLE_IGNORED_KEYS:
                        warn(twhere, f"{k!r} is ignored on a table spec; set it on the question")
                    elif k not in KNOWN_TABLE_KEYS:
                        err(twhere, f"unknown table option {k!r}")
                gb = spec.get("group_by", [])
                if not isinstance(gb, list):
                    err(twhere, "group_by must be a list")
                else:
                    for gc in gb:
                        gc = str(gc)
                        if gc not in by_col:
                            err(twhere, f"grouping variable {gc!r} not found in column map")
                        elif gc not in groupable:
                            err(twhere, f"grouping variable {gc!r} is multi-select; not supported")
                _check_enums(spec, twhere, issues)

    return issues


def format_issues(issues: list[Issue]) -> str:
    """Render issues as one line each, errors first."""
    order = {"error": 0, "warning": 1}
    lines = [
        f"{level.upper()}: {where}: {msg}"
        for level, where, msg in sorted(issues, key=lambda i: order.get(i[0], 2))
    ]
    return "\n".join(lines)
