"""Frequency table generator for Qualtrics survey exports.

Produces one frequency table per question, using response CSV data and
Qualtrics question metadata JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COLUMN_PATTERN = re.compile(r"^(?P<base>Q\d+)(?:[_.#](?P<sub>\d+))?$")


@dataclass
class ColumnContext:
    """Metadata context for one response column."""

    column: str
    base_tag: str | None
    sub_id: str | None
    qid: str | None
    question_type: str | None
    question_text: str | None
    item_text: str | None
    response_labels: dict[str, str]


def load_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_questions_meta(path: str | Path) -> dict[str, dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _extract_display(node: dict[str, Any]) -> str:
    return node.get("Display") or node.get("Description") or node.get("ChoiceText") or str(node)


def _is_missing(value: str | None) -> bool:
    if value is None:
        return True
    cleaned = value.strip()
    return cleaned == "" or cleaned.lower() in {"nan", "na", "null", "none"}


def _numeric_sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)


def build_tag_map(questions_meta: dict[str, dict[str, Any]]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    tag_map: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for qid, question in questions_meta.items():
        tag = question.get("DataExportTag")
        if tag:
            tag_map[tag].append((qid, question))
    return tag_map


def resolve_question(candidates: list[tuple[str, dict[str, Any]]], sub_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not candidates:
        return None, None
    if sub_id is not None:
        for qid, q in candidates:
            if q.get("QuestionType") == "Matrix":
                return qid, q
    for qid, q in candidates:
        if q.get("QuestionType") != "Matrix":
            return qid, q
    return candidates[0]


def get_column_context(column: str, tag_map: dict[str, list[tuple[str, dict[str, Any]]]]) -> ColumnContext:
    match = COLUMN_PATTERN.match(column)
    if not match:
        return ColumnContext(column, None, None, None, None, None, None, {})

    base_tag = match.group("base")
    sub_id = match.group("sub")
    qid, question = resolve_question(tag_map.get(base_tag, []), sub_id)

    if question is None:
        return ColumnContext(column, base_tag, sub_id, None, None, None, None, {})

    question_type = question.get("QuestionType")
    question_text = question.get("QuestionText")
    item_text: str | None = None
    response_labels: dict[str, str] = {}

    if question_type == "Matrix":
        choices = question.get("Choices", {})
        answers = question.get("Answers", {})
        if sub_id and sub_id in choices:
            item_text = _extract_display(choices[sub_id])
        response_labels = {k: _extract_display(v) for k, v in answers.items()}
    elif question_type == "MC":
        choices = question.get("Choices", {})
        response_labels = {k: _extract_display(v) for k, v in choices.items()}

    return ColumnContext(column, base_tag, sub_id, qid, question_type, question_text, item_text, response_labels)


def infer_scale_type(question_type: str | None, configured_mode: str) -> str:
    if configured_mode in {"interval", "nominal"}:
        return configured_mode
    if question_type == "Matrix":
        return "interval"
    return "nominal"


def build_default_config(questions_meta: dict[str, dict[str, Any]]) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    for qid, question in questions_meta.items():
        tag = question.get("DataExportTag", "")
        qtype = question.get("QuestionType", "")
        default_mode = "interval" if qtype == "Matrix" else "nominal"
        questions[qid] = {
            "data_export_tag": tag,
            "question_type": qtype,
            "default_scale_type": default_mode,
            "frequency_mode": "auto",
            "response_order": [],
            "include": True,
            "question_text": question.get("QuestionText", ""),
        }

    return {
        "defaults": {
            "frequency_mode": "auto",
            "interval_sort": "value_asc",
            "nominal_sort": "count_desc",
            "matrix_as_single_question": True,
        },
        "questions": questions,
    }


def init_config_file(config_path: str | Path, meta_path: str | Path) -> Path:
    config_path = Path(config_path)
    questions_meta = load_questions_meta(meta_path)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(build_default_config(questions_meta), f, indent=2)
    return config_path


def _get_question_config(config: dict[str, Any], question_key: str) -> dict[str, Any]:
    defaults = config.get("defaults", {})
    specific = config.get("questions", {}).get(question_key, {})
    merged = dict(defaults)
    merged.update(specific)
    return merged


def build_frequency_rows_for_column(
    values: list[str],
    context: ColumnContext,
    question_key: str,
    question_total_n: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    valid_values = [str(v).strip() for v in values if not _is_missing(v)]
    valid_n = len(valid_values)
    if valid_n == 0:
        return []

    cfg = _get_question_config(config, question_key)
    scale_type = infer_scale_type(context.question_type, str(cfg.get("frequency_mode", "auto")))

    counts = Counter(valid_values)
    response_order = cfg.get("response_order", []) or []

    if scale_type == "interval":
        if response_order:
            ordered_codes = [str(v) for v in response_order if str(v) in counts]
            extras = [c for c in counts if c not in ordered_codes]
            ordered_codes.extend(sorted(extras, key=_numeric_sort_key))
        else:
            ordered_codes = sorted(counts.keys(), key=_numeric_sort_key)
    else:
        ordered_codes = [code for code, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    return [
        {
            "question_key": question_key,
            "question_id": context.base_tag or "",
            "question_text": context.question_text or "",
            "question_type": context.question_type or "",
            "attribute": context.item_text or "",
            "column": context.column,
            "scale_type": scale_type,
            "response_code": code,
            "response_label": context.response_labels.get(code, code),
            "n": counts[code],
            "valid_pct": round((counts[code] / valid_n) * 100.0, 2),
            "valid_n": valid_n,
            "question_total_n": question_total_n,
        }
        for code in ordered_codes
    ]


def generate_frequency_tables(
    rows: list[dict[str, str]],
    questions_meta: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    if not rows:
        return {}

    tag_map = build_tag_map(questions_meta)
    columns = list(rows[0].keys())

    grouped: dict[str, list[ColumnContext]] = defaultdict(list)
    for column in columns:
        context = get_column_context(column, tag_map)
        question_key = context.qid or context.base_tag or column
        grouped[question_key].append(context)

    tables: dict[str, list[dict[str, Any]]] = {}

    for question_key, contexts in grouped.items():
        cfg = _get_question_config(config, question_key)
        if cfg.get("include", True) is False:
            continue

        question_total_n = sum(
            1 for row in rows if any(not _is_missing(row.get(ctx.column, "")) for ctx in contexts)
        )

        rows_out: list[dict[str, Any]] = []
        for context in contexts:
            column_values = [row.get(context.column, "") for row in rows]
            rows_out.extend(
                build_frequency_rows_for_column(
                    values=column_values,
                    context=context,
                    question_key=question_key,
                    question_total_n=question_total_n,
                    config=config,
                )
            )

        tables[question_key] = rows_out

    return tables


def run_frequency_analysis(
    data_path: str | Path,
    meta_path: str | Path,
    outdir: str | Path,
    config_path: str | Path,
) -> list[Path]:
    outdir = Path(outdir)
    freq_dir = outdir / "frequency_tables"
    freq_dir.mkdir(parents=True, exist_ok=True)

    rows = load_csv_rows(data_path)
    meta = load_questions_meta(meta_path)
    config = load_config(config_path)
    tables = generate_frequency_tables(rows, meta, config)

    fieldnames = [
        "question_key",
        "question_id",
        "question_text",
        "question_type",
        "attribute",
        "column",
        "scale_type",
        "response_code",
        "response_label",
        "n",
        "valid_pct",
        "valid_n",
        "question_total_n",
    ]

    output_paths: list[Path] = []
    for question_key in sorted(tables.keys()):
        path = freq_dir / f"{question_key}_frequencies.csv"
        write_csv(path, tables[question_key], fieldnames)
        output_paths.append(path)
    return output_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qualtrics frequency table generator")
    parser.add_argument("--data", help="Path to Qualtrics CSV response export")
    parser.add_argument("--meta", required=True, help="Path to questions metadata JSON")
    parser.add_argument("--outdir", default="analysis_output", help="Output directory")
    parser.add_argument(
        "--config",
        default="qualtrics_frequency_config.json",
        help="Path to per-question frequency configuration JSON",
    )
    parser.add_argument("--init-config", action="store_true", help="Create default config and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.init_config:
        created = init_config_file(args.config, args.meta)
        print(f"Created config file: {created}")
        return

    if not args.data:
        raise SystemExit("--data is required unless using --init-config")

    config_path = Path(args.config)
    if not config_path.exists():
        created = init_config_file(config_path, args.meta)
        print(f"Config not found; created default config at: {created}")

    outputs = run_frequency_analysis(args.data, args.meta, args.outdir, config_path)
    print(f"Wrote {len(outputs)} frequency table(s):")
    for path in outputs:
        print(f"- {path}")


if __name__ == "__main__":
    main()
