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

KNOWN_TOP_LEVEL = {"defaults", "questions"}

Issue = tuple[str, str, str]  # (level, location, message)


def _is_comment_key(key: str) -> bool:
    """Underscore-prefixed keys are documentation, ignored everywhere.

    build_default_config emits keys like "_reference" and "_question" so a
    generated config is self-explanatory when hand-edited; this convention
    also lets a user add their own "_note" fields freely without tripping the
    unknown-option check.
    """
    return str(key).startswith("_")


def _safe_in(value: Any, allowed: set) -> bool:
    """Membership test that tolerates unhashable values (lists/dicts)."""
    try:
        return value in allowed
    except TypeError:
        return False


def _check_enums(block: dict[str, Any], where: str, errors: list[Issue]) -> None:
    """Validate enum/typed option values shared by defaults/question/table.

    Tolerant of malformed values (lists/dicts) so the validator reports a
    config error rather than crashing on an unhashable membership test.
    """
    def err(msg: str) -> None:
        errors.append(("error", where, msg))

    for field, allowed in (
        ("sort_by", SORT_BY_ALLOWED),
        ("frequency_mode", FREQUENCY_MODE_ALLOWED),
        ("percent_base", PERCENT_BASES),
        ("orientation", ORIENTATION_ALLOWED),
        ("overall", POSITION_ALLOWED),
        ("response_total", POSITION_ALLOWED),
    ):
        if field in block and not _safe_in(block[field], allowed):
            err(f"invalid {field} {block[field]!r}")
    if "show_code" in block and not isinstance(block["show_code"], bool):
        err("show_code must be true or false")
    if "stats" in block:
        stats = block["stats"]
        if not isinstance(stats, list):
            err("stats must be a list")
        else:
            bad = [s for s in stats if not _safe_in(s, STAT_KEYS)]
            if bad:
                err(f"unknown stats {bad} (allowed: {sorted(STAT_KEYS)})")


def _check_block(
    block: dict[str, Any],
    where: str,
    by_col: dict[str, Any],
    groupable: set[str],
    issues: list[Issue],
    qkey: str | None,
) -> None:
    """Validate an option block (defaults or a single question)."""
    def err(w: str, msg: str) -> None:
        issues.append(("error", w, msg))

    def warn(w: str, msg: str) -> None:
        issues.append(("warning", w, msg))

    for k in block:
        if not _is_comment_key(k) and k not in KNOWN_QUESTION_KEYS:
            err(where, f"unknown option {k!r}")
    _check_enums(block, where, issues)

    if "include" in block and not isinstance(block["include"], bool):
        err(where, "include must be true or false")
    if "response_order" in block and not isinstance(block["response_order"], list):
        err(where, "response_order must be a list")

    tec = block.get("text_entry_columns")
    if tec is not None and not isinstance(tec, dict):
        err(where, "text_entry_columns must be an object")
    elif isinstance(tec, dict):
        for col, spec in tec.items():
            cwhere = f"{where}.text_entry_columns.{col}"
            m = by_col.get(col)
            if m is None:
                err(cwhere, f"column {col!r} not found in column map")
            elif not m.get("is_text_entry_suffix"):
                warn(cwhere, f"column {col!r} is not a text-entry (_TEXT) column; mode is ignored")
            elif qkey is not None and _question_key(m) != qkey:
                warn(cwhere,
                     f"column {col!r} belongs to {_question_key(m)!r}, not {qkey!r}; mode is ignored")
            if not isinstance(spec, dict):
                err(cwhere, "text-entry spec must be an object")
            else:
                mode = spec.get("text_reporting_mode")
                if mode is not None and not _safe_in(mode, TEXT_MODES):
                    err(cwhere, f"invalid text_reporting_mode {mode!r}")

    tables = block.get("tables")
    if tables is not None and not isinstance(tables, list):
        err(where, "tables must be a list")
    elif isinstance(tables, list):
        for i, spec in enumerate(tables):
            twhere = f"{where}.tables[{i}]"
            if not isinstance(spec, dict):
                err(twhere, "table spec must be an object")
                continue
            for k in spec:
                if _is_comment_key(k):
                    continue
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


def validate_config(config: Any, column_map: list[dict[str, Any]]) -> list[Issue]:
    """Return issues found in ``config`` relative to ``column_map``."""
    issues: list[Issue] = []

    def err(where: str, msg: str) -> None:
        issues.append(("error", where, msg))

    def warn(where: str, msg: str) -> None:
        issues.append(("warning", where, msg))

    if not isinstance(config, dict):
        return [("error", "(root)", "config must be a JSON object")]

    for k in config:
        if not _is_comment_key(k) and k not in KNOWN_TOP_LEVEL:
            err("(root)", f"unknown top-level key {k!r} (expected: defaults, questions)")

    by_col = {m["column"]: m for m in column_map}
    valid_qkeys = {_question_key(m) for m in column_map}
    groupable = {c for c, m in by_col.items() if m.get("selector") not in MULTI_SELECTORS}

    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        err("defaults", "must be an object")
    else:
        _check_block(defaults, "defaults", by_col, groupable, issues, qkey=None)

    questions = config.get("questions", {})
    if not isinstance(questions, dict):
        err("questions", "must be an object")
        return issues

    for qkey, qcfg in questions.items():
        where = f"questions.{qkey}"
        # A config block for a question absent from the column map is never
        # applied to any analyzed question, so keep it advisory rather than
        # letting its (unused) contents abort the whole run.
        if qkey not in valid_qkeys:
            warn(where, f"question key {qkey!r} not found in column map; block ignored")
            continue
        if not isinstance(qcfg, dict):
            err(where, "must be an object")
            continue
        _check_block(qcfg, where, by_col, groupable, issues, qkey=qkey)

    return issues


def format_issues(issues: list[Issue]) -> str:
    """Render issues as one line each, errors first."""
    order = {"error": 0, "warning": 1}
    lines = [
        f"{level.upper()}: {where}: {msg}"
        for level, where, msg in sorted(issues, key=lambda i: order.get(i[0], 2))
    ]
    return "\n".join(lines)
