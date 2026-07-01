"""Pure logic for browsing and editing per-question reporting settings.

Lets a user find a question by tag/text/number and set its reporting options
(sort order, percent base, response-code visibility, stats, grouped-table
breakouts) without hand-writing the JSON keys documented in the README. Kept
free of any I/O so it can be unit tested directly; the interactive prompts
live in cli.py.
"""

from __future__ import annotations

from typing import Any

from .frequencies import _is_analyzable, _is_groupable, _question_doc_fields, _question_key
from .report import _natural_question_key

# Skeleton used when a question has no config block yet, matching
# frequencies.build_default_config so editing doesn't change engine defaults.
QUESTION_DEFAULT_SKELETON = {
    "include": True,
    "sort_by": "auto",
    "percent_base": "eligible",
    "response_order": [],
    "text_entry_columns": {},
}


def question_summaries(column_map: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One entry per reportable question, in natural survey order.

    Mirrors build_default_config's inclusion rule (analyzable columns, or the
    text-entry suffix columns attached to them) so every question that can
    appear in a frequency table is configurable, and nothing else is.
    """
    seen: dict[str, dict[str, str]] = {}
    for m in column_map:
        if not (_is_analyzable(m) or m.get("is_text_entry_suffix")):
            continue
        qkey = _question_key(m)
        if not qkey or qkey in seen:
            continue
        seen[qkey] = {
            "qkey": qkey,
            "question_id": m.get("data_export_tag") or qkey,
            "question_text": m.get("question_text", ""),
            "question_type": m.get("question_type", ""),
        }
    summaries = list(seen.values())
    summaries.sort(key=lambda s: _natural_question_key(s["question_id"], s["qkey"]))
    return summaries


def find_questions(summaries: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Resolve a user-typed query to matching questions.

    A pure digit string is a 1-based index into ``summaries``. Otherwise, an
    exact (case-insensitive) match on tag or question key wins outright;
    failing that, a substring match against tag, key, or question text.
    """
    query = (query or "").strip()
    if not query:
        return []
    if query.isdigit():
        idx = int(query) - 1
        return [summaries[idx]] if 0 <= idx < len(summaries) else []

    q = query.lower()
    exact = [s for s in summaries if s["question_id"].lower() == q or s["qkey"].lower() == q]
    if exact:
        return exact
    return [
        s for s in summaries
        if q in s["question_id"].lower() or q in s["qkey"].lower() or q in (s["question_text"] or "").lower()
    ]


def question_response_labels(column_map: list[dict[str, Any]], qkey: str) -> dict[str, str]:
    """Merged {code: label} across all columns belonging to ``qkey``."""
    labels: dict[str, str] = {}
    for m in column_map:
        if _question_key(m) == qkey:
            for code, label in (m.get("response_labels") or {}).items():
                labels.setdefault(code, label)
    return labels


def groupable_columns(column_map: list[dict[str, Any]], exclude_qkey: str | None = None) -> list[dict[str, str]]:
    """Single-answer columns usable as a grouping variable for a breakout.

    ``exclude_qkey`` omits the question currently being configured, since
    grouping a question by itself is not a meaningful breakout.
    """
    seen_cols: set[str] = set()
    out: list[dict[str, str]] = []
    for m in column_map:
        col = m["column"]
        if col in seen_cols:
            continue
        if exclude_qkey is not None and _question_key(m) == exclude_qkey:
            continue
        if not _is_groupable(m):
            continue
        seen_cols.add(col)
        out.append({
            "column": col,
            "question_id": m.get("data_export_tag") or col,
            "question_text": m.get("question_text", ""),
        })
    out.sort(key=lambda s: _natural_question_key(s["question_id"], s["column"]))
    return out


def ensure_question_block(
    config: dict[str, Any], qkey: str, column_map: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Return config["questions"][qkey], creating it (with the standard
    skeleton) if this is the first customization for that question.

    When ``column_map`` is given, the new block is stamped with the same
    underscore-prefixed documentation fields build_default_config emits (which
    question this is, its response codes), so a config touched through the
    interactive menu stays just as self-explanatory to hand-edit afterward.
    """
    config.setdefault("questions", {})
    if qkey not in config["questions"]:
        block = dict(QUESTION_DEFAULT_SKELETON)
        if column_map is not None:
            block = {**_question_doc_fields(column_map, qkey), **block}
        config["questions"][qkey] = block
    return config["questions"][qkey]


def effective_question_config(config: dict[str, Any], qkey: str) -> dict[str, Any]:
    """Read-only merged view: defaults overridden by this question's block."""
    merged = dict(config.get("defaults", {}))
    merged.update(config.get("questions", {}).get(qkey, {}))
    return merged


def set_question_field(
    config: dict[str, Any], qkey: str, field: str, value: Any, column_map: list[dict[str, Any]] | None = None
) -> None:
    ensure_question_block(config, qkey, column_map)[field] = value


def unset_question_field(config: dict[str, Any], qkey: str, field: str) -> None:
    """Remove an override so the question falls back to defaults/engine default."""
    block = config.get("questions", {}).get(qkey)
    if block and field in block:
        del block[field]


def reset_question(config: dict[str, Any], qkey: str) -> None:
    """Discard all customization for a question."""
    config.get("questions", {}).pop(qkey, None)


def list_table_specs(config: dict[str, Any], qkey: str) -> list[dict[str, Any]]:
    """The effective list of table specs (matching frequencies._table_specs)."""
    tables = config.get("questions", {}).get(qkey, {}).get("tables")
    return tables if tables else [{"group_by": []}]


def add_table_spec(
    config: dict[str, Any], qkey: str, spec: dict[str, Any], column_map: list[dict[str, Any]] | None = None
) -> None:
    """Append a breakout, preserving the implicit overall table if present."""
    block = ensure_question_block(config, qkey, column_map)
    tables = block.get("tables")
    if not tables:
        tables = [{"group_by": []}]
    tables.append(spec)
    block["tables"] = tables


def remove_table_spec(config: dict[str, Any], qkey: str, index: int) -> bool:
    block = config.get("questions", {}).get(qkey)
    if not block:
        return False
    tables = block.get("tables")
    if not tables or not (0 <= index < len(tables)):
        return False
    tables.pop(index)
    block["tables"] = tables
    return True
