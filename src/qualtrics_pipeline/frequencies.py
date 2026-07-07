from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .survey_logic import evaluate as evaluate_logic

SKIP_KEYS = ("is_metadata", "is_sensitive", "is_open_text")
MULTI_SELECTORS = {"MAVR", "MAHR", "MACOL", "MSB"}


def load_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _is_missing(value: str | None) -> bool:
    if value is None:
        return True
    cleaned = str(value).strip()
    return cleaned == "" or cleaned.lower() in {"nan", "na", "null", "none"}


def _numeric_sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def _is_analyzable(mapping: dict[str, Any]) -> bool:
    return not any(mapping.get(k) for k in SKIP_KEYS)


def _question_key(mapping: dict[str, Any]) -> str:
    return mapping.get("qid") or mapping.get("data_export_tag") or mapping["column"]


def _resolve_only(column_map: list[dict[str, Any]], only_list: Any) -> set[str] | None:
    """Resolve a top-level "only" list (question tags or qkeys) to qkeys.

    Returns None when "only" is absent/empty, meaning no restriction (the
    default: every analyzable question is included unless individually
    excluded via include: false). When set, every question NOT resolved from
    this list is hidden from the report regardless of its own include value --
    this is the whitelist counterpart to the per-question include flag.
    """
    if not only_list:
        return None
    lookup: dict[str, str] = {}
    for m in column_map:
        qk = _question_key(m)
        lookup.setdefault(qk, qk)
        tag = m.get("data_export_tag")
        if tag:
            lookup.setdefault(tag, qk)
    return {lookup.get(str(entry), str(entry)) for entry in only_list}


def build_default_config(column_map: list[dict[str, Any]]) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    for m in column_map:
        if not _is_analyzable(m) and not m.get("is_text_entry_suffix"):
            continue
        qid = _question_key(m)
        if qid not in questions:
            questions[qid] = {
                **_question_doc_fields(column_map, qid),
                "include": True,
                "sort_by": "auto",
                "percent_base": "eligible",
                "response_order": [],
                "text_entry_columns": {},
            }
        if m.get("is_text_entry_suffix"):
            questions[qid]["text_entry_columns"][m["column"]] = {
                "text_reporting_mode": m.get("text_reporting_mode", "summarize_later")
            }
    return {
        "_reference": _config_reference(),
        "_groupable_questions": _groupable_questions_doc(column_map),
        "defaults": {"sort_by": "auto"},
        "questions": questions,
    }


def _question_looks_like(column: str) -> bool:
    return bool(re.match(r"^Q\d+", column))


def _text_mode_for(mapping: dict[str, Any], question_cfg: dict[str, Any]) -> str:
    per_col = (question_cfg.get("text_entry_columns") or {}).get(mapping["column"], {})
    return per_col.get("text_reporting_mode", mapping.get("text_reporting_mode", "skip"))


# Valid sort_by values and what they mean:
#   survey_order  – follow the key order of response_labels (survey designer's order)
#   count_desc    – most frequent first (default for nominal questions)
#   count_asc     – least frequent first
#   response_order – use the explicit response_order list from config
SORT_BY_VALUES = {"survey_order", "count_desc", "count_asc", "response_order"}


def _effective_sort_by(cfg: dict[str, Any], question_type: str) -> str:
    """Resolve the sort_by mode from config, with legacy frequency_mode fallback."""
    sort_by = cfg.get("sort_by")
    if sort_by and sort_by in SORT_BY_VALUES:
        return sort_by
    # Legacy frequency_mode support
    mode = cfg.get("frequency_mode", "auto")
    if mode == "interval":
        return "survey_order"
    if mode == "nominal":
        return "count_desc"
    # auto: Matrix (Likert-type) defaults to survey_order, everything else count_desc
    return "survey_order" if question_type == "Matrix" else "count_desc"


def _ordered_codes(
    counts: Counter,
    sort_by: str,
    response_order_cfg: list[str],
    response_labels: dict[str, str],
) -> list[str]:
    """Return response codes in the requested display order."""
    all_codes = set(counts.keys())

    if sort_by == "response_order":
        explicit = [c for c in response_order_cfg if c in all_codes]
        remainder = sorted(
            [c for c in all_codes if c not in set(explicit)],
            key=lambda k: (-counts[k], k),
        )
        return explicit + remainder

    if sort_by == "survey_order":
        # Preserve the insertion order of response_labels (Qualtrics survey order).
        in_order = [c for c in response_labels if c in all_codes]
        remainder = sorted(
            [c for c in all_codes if c not in set(response_labels)],
            key=_numeric_sort_key,
        )
        return in_order + remainder

    if sort_by == "count_asc":
        return sorted(all_codes, key=lambda k: (counts[k], k))

    # count_desc
    return sorted(all_codes, key=lambda k: (-counts[k], k))


def _eligible_n(rows, qkey, display_logic):
    """Respondents eligible to see a question per its display logic.

    Uses the question's display-logic tree when it is fully evaluable;
    otherwise every respondent is treated as eligible.
    """
    entry = (display_logic or {}).get(qkey)
    if not entry or not entry.get("fully_evaluable") or not entry.get("tree"):
        return len(rows)
    tree = entry["tree"]
    return sum(1 for r in rows if evaluate_logic(tree, r))


# The percentage denominators every frequency row carries:
#   valid    – respondents who answered the question
#   eligible – respondents shown the question (display logic); == total when
#              the question has no display logic
#   total    – all survey respondents (prevalence base)
# percent_base names which of these the report should feature by default.
PERCENT_BASES = {"valid", "eligible", "total"}

# Presentation options consumed by the report layer (not the computation).
# Resolved per table and stamped into the manifest so the reporting code reads
# them from the data contract rather than re-parsing the config.
STAT_KEYS = {
    "n", "valid_n", "valid_pct", "eligible_n", "eligible_pct",
    "total_n", "total_pct", "pct", "base_n",
}
PRESENTATION_DEFAULTS = {
    "show_code": True,
    "orientation": "columns",   # group levels as columns; "rows" transposes
    "overall": False,           # False | "before" | "after"
    "response_total": False,    # False | "before" | "after"
    "stats": None,              # None -> renderer default
}


def _is_groupable(mapping: dict[str, Any]) -> bool:
    """Whether a column can be used as a breakout ("group_by") variable."""
    return (
        _is_analyzable(mapping)
        and mapping.get("selector") not in MULTI_SELECTORS
        and not mapping.get("is_text_entry_suffix")
    )


def _question_doc_fields(column_map: list[dict[str, Any]], qkey: str) -> dict[str, Any]:
    """Underscore-prefixed, engine-ignored fields describing a question.

    Generated configs are meant to be hand-edited; these make a question's
    block self-explanatory (which question it is, what its response codes
    mean) without cross-referencing codebook.csv. The engine and the config
    validator both ignore any key starting with "_".
    """
    tag = qkey
    text = ""
    labels: dict[str, str] = {}
    for m in column_map:
        if _question_key(m) != qkey:
            continue
        tag = m.get("data_export_tag") or tag
        text = text or m.get("question_text", "")
        for code, label in (m.get("response_labels") or {}).items():
            labels.setdefault(code, label)
    doc: dict[str, Any] = {"_question": f"{tag}: {text}" if text else tag}
    if labels:
        doc["_response_labels"] = labels
    return doc


def _groupable_questions_doc(column_map: list[dict[str, Any]]) -> dict[str, str]:
    """{column: description} reference for valid group_by values, in survey order.

    Keyed by the actual export column, not the data_export_tag: group_by is
    validated and resolved against column names (see by_col in
    config_validate/generate_frequency_tables). A Matrix question's rows are
    separate columns sharing one tag (e.g. column "Q3_1" under tag "Q3"), so
    the tag alone is not a resolvable group_by value.
    """
    out: dict[str, str] = {}
    for m in column_map:
        if not _is_groupable(m):
            continue
        col = m["column"]
        if col in out:
            continue
        text = m.get("question_text", "")
        sub = m.get("sub_question_text", "")
        out[col] = f"{text} — {sub}" if sub else text
    return out


def _config_reference() -> dict[str, str]:
    """One-line cheat sheet for every configurable field, embedded in generated
    configs so hand-editing doesn't require looking up the README."""
    sort_opts = "|".join(["auto", *sorted(SORT_BY_VALUES)])
    pct_opts = "|".join(sorted(PERCENT_BASES))
    stat_opts = ", ".join(sorted(STAT_KEYS))
    return {
        "only": (
            "(top-level, not per-question) list of question tags or qids to show, e.g. [\"Q1.5\"]. "
            "When set, every other question is hidden from the report regardless of its own include "
            "value. Omit (the default) to include everything, minus any individual include: false."
        ),
        "include": "true|false - include this question in the report",
        "sort_by": f"{sort_opts} - response ordering; response_order also needs the response_order list below",
        "response_order": "list of response codes in the order to display them (used when sort_by is response_order)",
        "percent_base": f"{pct_opts} - featured denominator (valid=answered, eligible=shown per display logic, total=all respondents)",
        "show_code": "true|false - show the response-code column in the report",
        "stats": f"list from: {stat_opts} (omit/empty = report default)",
        "tables": (
            "list of breakout tables, e.g. [{\"group_by\": [\"Q1.9\"], \"orientation\": \"columns\", "
            "\"overall\": false, \"response_total\": false}]. Omit for a single overall table. "
            "See _groupable_questions below for valid group_by values (columns, not question tags)."
        ),
    }


def _resolve_presentation(cfg: dict, spec: dict) -> dict:
    """Resolve report presentation options: table spec over question over defaults."""
    out = dict(PRESENTATION_DEFAULTS)
    for key in PRESENTATION_DEFAULTS:
        if key in cfg:
            out[key] = cfg[key]
        if key in spec:
            out[key] = spec[key]
    if out["orientation"] not in ("columns", "rows"):
        out["orientation"] = "columns"
    if out["overall"] not in (False, "before", "after"):
        out["overall"] = False
    if out["response_total"] not in (False, "before", "after"):
        out["response_total"] = False
    if out["show_code"] not in (True, False):
        out["show_code"] = True
    if out["stats"] is not None:
        cleaned = [s for s in out["stats"] if s in STAT_KEYS]
        out["stats"] = cleaned or None
    return out


def _pct(numerator: int, denom: int) -> float:
    return round((numerator / denom) * 100.0, 2) if denom else 0.0


# Empty group columns for ungrouped (overall) tables.
_EMPTY_GROUP = {"group_keys": "", "group_codes": "", "group_labels": ""}


def _group_level_sort_key(codes, gcols, by_col):
    """Order group levels by each grouping column's survey (label) order."""
    key = []
    for gc, code in zip(gcols, codes):
        label_keys = list((by_col.get(gc, {}).get("response_labels") or {}).keys())
        idx = label_keys.index(code) if code in label_keys else len(label_keys)
        key.append((idx, _numeric_sort_key(code)))
    return key


def _build_question_rows(subset, qkey, mappings, cfg, display_logic, group_cols):
    """Frequency rows for a question computed over a subset of respondents.

    All three bases (valid/eligible/total) are computed within ``subset`` so
    grouped tables report within-group percentages. ``group_cols`` carries the
    group_keys/group_codes/group_labels stamped onto every emitted row.
    """
    total_n = len(subset)
    question_total_n = sum(
        1 for r in subset if any(not _is_missing(r.get(m["column"])) for m in mappings)
    )
    eligible_n = _eligible_n(subset, qkey, display_logic)
    report_base = cfg.get("percent_base", "eligible")
    if report_base not in PERCENT_BASES:
        report_base = "eligible"

    out_rows = []
    for m in mappings:
        vals = [str(r.get(m["column"], "")).strip() for r in subset]
        valid = [v for v in vals if not _is_missing(v)]
        if not valid:
            continue
        counts = Counter(valid)
        question_type = m.get("question_type", "")
        # scale_type is a semantic descriptor of the measurement scale.
        # interval: ordered/numeric (Matrix Likert, NPS); nominal: categorical.
        mode = cfg.get("frequency_mode", "auto")
        scale_type = (
            "interval"
            if (mode == "interval" or (mode == "auto" and question_type in {"Matrix", "NPS"}))
            else "nominal"
        )
        labels = m.get("response_labels", {}) or {}
        sort_by = _effective_sort_by(cfg, question_type)
        response_order_cfg = [str(x) for x in (cfg.get("response_order", []) or [])]
        ordered = _ordered_codes(counts, sort_by, response_order_cfg, labels)
        # Multi-select columns store "1" when selected and blank otherwise,
        # so a per-column non-missing count would equal n (always 100%). Use
        # the question-level "answered any option" total as the valid base.
        is_multi_select = m.get("selector") in MULTI_SELECTORS
        valid_n = question_total_n if is_multi_select else len(valid)
        for code in ordered:
            n = counts[code]
            row = {
                "question_key": qkey,
                "question_id": m.get("data_export_tag", ""),
                "question_text": m.get("question_text", ""),
                "question_type": m.get("question_type", ""),
                "attribute": m.get("sub_question_text", ""),
                "column": m["column"],
                "scale_type": scale_type,
                "response_code": code,
                "response_label": labels.get(code, code),
                "n": n,
                "valid_n": valid_n,
                "valid_pct": _pct(n, valid_n),
                "eligible_n": eligible_n,
                "eligible_pct": _pct(n, eligible_n),
                "total_n": total_n,
                "total_pct": _pct(n, total_n),
                "report_base": report_base,
            }
            row.update(group_cols)
            out_rows.append(row)
    return out_rows


def _table_specs(cfg):
    """Per-question list of table specs; defaults to a single overall table."""
    specs = cfg.get("tables")
    if not specs:
        return [{"group_by": []}]
    return specs


def _validate_group_by(qkey, gcols, by_col, warnings):
    """Return True if every grouping column is a usable single-answer column."""
    for gc in gcols:
        gm = by_col.get(gc)
        if gm is None:
            warnings.append(f"{qkey}: grouping variable '{gc}' not found; table skipped")
            return False
        if gm.get("selector") in MULTI_SELECTORS:
            warnings.append(
                f"{qkey}: multi-select grouping variable '{gc}' not supported; table skipped"
            )
            return False
    return True


def generate_frequency_tables(rows, column_map, config, strict=False, display_logic=None):
    if not rows:
        return {}, {}, {"table_specs": {}, "grouping_warnings": []}

    by_col = {m["column"]: m for m in column_map}
    all_cols = list(rows[0].keys())
    grouped = defaultdict(list)
    text_outputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    defaults = config.get("defaults", {})
    qcfgs = config.get("questions", {})

    for c in all_cols:
        m = by_col.get(c)
        if not m:
            if strict and _question_looks_like(c):
                raise SystemExit(f"Unmapped question-like column in strict mode: {c}")
            continue
        qkey = _question_key(m)
        cfg = dict(defaults)
        cfg.update(qcfgs.get(qkey, {}))

        if m.get("is_text_entry_suffix"):
            mode = _text_mode_for(m, cfg)
            if mode == "frequency_text":
                grouped[qkey].append(m)
            elif mode == "summarize_later":
                text_outputs[qkey].append(m)
            continue

        if not _is_analyzable(m):
            continue
        grouped[qkey].append(m)

    display_logic = display_logic or {}
    only_set = _resolve_only(column_map, config.get("only"))

    def _question_hidden(qkey: str) -> bool:
        cfg = dict(defaults)
        cfg.update(qcfgs.get(qkey, {}))
        if cfg.get("include", True) is False:
            return True
        return only_set is not None and qkey not in only_set

    # Write-in outputs must respect the same include/only visibility as
    # frequency tables -- otherwise a question hidden by "only" can still
    # surface its verbatim text responses as an orphan report section.
    for qkey in list(text_outputs.keys()):
        if _question_hidden(qkey):
            del text_outputs[qkey]

    tables: dict[str, list[dict[str, Any]]] = {}
    table_meta: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for qkey, mappings in grouped.items():
        if _question_hidden(qkey):
            continue
        cfg = dict(defaults)
        cfg.update(qcfgs.get(qkey, {}))

        for spec in _table_specs(cfg):
            gcols = [str(g) for g in (spec.get("group_by") or [])]
            if not _validate_group_by(qkey, gcols, by_col, warnings):
                continue
            presentation = _resolve_presentation(cfg, spec)

            if not gcols:
                tables[qkey] = _build_question_rows(
                    rows, qkey, mappings, cfg, display_logic, dict(_EMPTY_GROUP)
                )
                table_meta[qkey] = {
                    "qkey": qkey, "group_by": [], "n_groups": 1,
                    "dropped_missing": 0, "presentation": presentation,
                }
                continue

            # Group rows by the (non-missing) tuple of grouping-variable values.
            level_rows: dict[tuple, list] = defaultdict(list)
            dropped = 0
            for r in rows:
                codes = tuple(str(r.get(gc, "")).strip() for gc in gcols)
                if any(_is_missing(c) for c in codes):
                    dropped += 1
                    continue
                level_rows[codes].append(r)

            slug = f"{qkey}__by__{'_'.join(gcols)}"
            out_rows: list[dict[str, Any]] = []
            for codes in sorted(level_rows, key=lambda c: _group_level_sort_key(c, gcols, by_col)):
                glabels = [
                    (by_col[gc].get("response_labels") or {}).get(code, code)
                    for gc, code in zip(gcols, codes)
                ]
                group_cols = {
                    "group_keys": " | ".join(gcols),
                    "group_codes": " | ".join(codes),
                    "group_labels": " | ".join(glabels),
                }
                out_rows.extend(
                    _build_question_rows(level_rows[codes], qkey, mappings, cfg, display_logic, group_cols)
                )
            tables[slug] = out_rows
            table_meta[slug] = {
                "qkey": qkey,
                "group_by": gcols,
                "n_groups": len(level_rows),
                "dropped_missing": dropped,
                "presentation": presentation,
            }

    return tables, text_outputs, {"table_specs": table_meta, "grouping_warnings": warnings}


def run_frequency_analysis(data_path, column_map_path, outdir, config_path, strict=False, display_logic_path=None):
    outdir = Path(outdir)
    freq_dir = outdir / "frequency_tables"
    text_dir = outdir / "open_text_outputs"
    freq_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    rows = load_csv_rows(data_path)
    cmap = load_json(column_map_path)
    config = load_json(config_path)

    # Fail loud on config errors so a typo can't yield a subtly wrong report.
    from .config_validate import format_issues, validate_config

    issues = validate_config(config, cmap)
    errors = [i for i in issues if i[0] == "error"]
    warnings = [i for i in issues if i[0] == "warning"]
    if warnings:
        print(format_issues(warnings))
    if errors:
        raise SystemExit("Invalid config:\n" + format_issues(errors))

    # Load display logic: explicit path, else a sibling of the column map.
    if display_logic_path is None:
        sibling = Path(column_map_path).with_name("display_logic.json")
        display_logic_path = sibling if sibling.exists() else None
    display_logic = load_json(display_logic_path) if display_logic_path else {}

    tables, text_outputs, report_meta = generate_frequency_tables(
        rows, cmap, config, strict=strict, display_logic=display_logic
    )

    # frequency_tables/ and open_text_outputs/ are fully owned/generated by
    # this tool, and generate_html_report globs every file in them. Clear
    # stale output first, or a question excluded by the current config (e.g.
    # via "only") but present from a prior, broader run would still appear in
    # the report.
    for stale in freq_dir.glob("*_frequencies.csv"):
        stale.unlink()
    for stale in text_dir.glob("*_open_text.csv"):
        stale.unlink()

    fields = ["question_key", "question_id", "question_text", "question_type", "attribute", "column", "scale_type", "response_code", "response_label", "n", "valid_n", "valid_pct", "eligible_n", "eligible_pct", "total_n", "total_pct", "report_base", "group_keys", "group_codes", "group_labels"]
    outs: list[Path] = []
    empty_output_tables: list[str] = []
    for qk in sorted(tables.keys()):
        if not tables[qk]:
            empty_output_tables.append(qk)
            continue
        p = freq_dir / f"{qk}_frequencies.csv"
        write_csv(p, tables[qk], fields)
        outs.append(p)

    for qk, maps in text_outputs.items():
        for m in maps:
            vals = [str(r.get(m["column"], "")).strip() for r in rows if not _is_missing(r.get(m["column"], ""))]
            if not vals:
                continue
            rows_out = [{"question_key": qk, "column": m["column"], "text_response": v} for v in vals]
            p = text_dir / f"{qk}_{m['column']}_open_text.csv"
            write_csv(p, rows_out, ["question_key", "column", "text_response"])
            outs.append(p)

    cols = list(rows[0].keys()) if rows else []
    by_col = {m["column"]: m for m in cmap}
    mode_by_text_col = {}
    defaults = config.get("defaults", {})
    qcfgs = config.get("questions", {})
    for m in cmap:
        if m.get("is_text_entry_suffix"):
            qkey = _question_key(m)
            cfg = dict(defaults)
            cfg.update(qcfgs.get(qkey, {}))
            mode_by_text_col[m["column"]] = _text_mode_for(m, cfg)
    written_text_cols = {
        Path(path).name.split("_open_text.csv")[0].split("_", 1)[-1]
        for path in [str(p) for p in outs if "open_text_outputs" in str(p)]
    }
    manifest = {
        "data_path": str(data_path),
        "column_map_path": str(column_map_path),
        "total_columns": len(cols),
        "analyzed_columns": [c for c in cols if c in by_col and _is_analyzable(by_col[c])],
        "skipped_metadata_columns": [c for c in cols if c in by_col and by_col[c].get("is_metadata")],
        "skipped_open_text_columns": [c for c in cols if c in by_col and by_col[c].get("is_open_text")],
        "skipped_text_entry_columns": sorted([c for c in cols if c in by_col and by_col[c].get("is_text_entry_suffix") and c not in written_text_cols and mode_by_text_col.get(c, by_col[c].get("text_reporting_mode", "skip")) != "frequency_text"]),
        "skipped_unmapped_columns": [c for c in cols if c not in by_col],
        "empty_output_tables": empty_output_tables,
        "text_entry_outputs": [str(p) for p in outs if "open_text_outputs" in str(p)],
        "strict_mode": strict,
        "conditional_questions": {
            qk: _eligible_n(rows, qk, display_logic)
            for qk, entry in display_logic.items()
            if entry.get("fully_evaluable")
        },
        "logic_not_evaluable": sorted(
            qk for qk, entry in display_logic.items() if not entry.get("fully_evaluable")
        ),
        "grouped_tables": [
            {"table": slug, "qkey": meta["qkey"], "group_by": meta["group_by"],
             "n_groups": meta["n_groups"], "dropped_missing": meta["dropped_missing"]}
            for slug, meta in sorted(report_meta["table_specs"].items())
            if meta["group_by"] and slug in tables and tables[slug]
        ],
        "grouping_warnings": report_meta["grouping_warnings"],
        "config_warnings": [f"{where}: {msg}" for _level, where, msg in warnings],
        "table_presentation": {
            slug: meta["presentation"]
            for slug, meta in report_meta["table_specs"].items()
            if slug in tables and tables[slug]
        },
        "output_files": [str(p) for p in outs],
    }
    (outdir / "frequency_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Emit an HTML validation report alongside the CSV artifacts.
    from .report import generate_html_report

    report_path = generate_html_report(outdir)
    outs.append(report_path)
    return outs


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Qualtrics frequency table generator")
    p.add_argument("--data", required=False)
    p.add_argument("--column-map", required=True)
    p.add_argument("--outdir", default="analysis_output")
    p.add_argument("--config", default="qualtrics_frequency_config.json")
    p.add_argument("--display-logic", required=False, help="Path to display_logic.json (defaults to sibling of --column-map)")
    p.add_argument("--init-config", action="store_true")
    p.add_argument("--validate-config", action="store_true", help="Validate the config against the column map and exit")
    p.add_argument("--strict", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.init_config:
        cmap = load_json(args.column_map)
        Path(args.config).write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")
        print(f"Created config file: {args.config}")
        return
    if args.validate_config:
        from .config_validate import format_issues, validate_config

        cmap = load_json(args.column_map)
        config = load_json(args.config)
        issues = validate_config(config, cmap)
        if issues:
            print(format_issues(issues))
        errors = [i for i in issues if i[0] == "error"]
        if errors:
            raise SystemExit(f"{len(errors)} config error(s)")
        print("Config OK" if not issues else f"Config OK with {len(issues)} warning(s)")
        return
    if not args.data:
        raise SystemExit("--data is required unless using --init-config")

    cp = Path(args.config)
    if not cp.exists():
        cmap = load_json(args.column_map)
        cp.write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")

    outs = run_frequency_analysis(
        args.data, args.column_map, args.outdir, cp, strict=args.strict, display_logic_path=args.display_logic
    )
    print(f"Wrote {len(outs)} output file(s)")


if __name__ == "__main__":
    main()
