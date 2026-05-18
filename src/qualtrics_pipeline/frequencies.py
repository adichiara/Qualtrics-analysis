from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SKIP_KEYS = ("is_metadata", "is_sensitive", "is_open_text")


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
    return not (mapping.get("is_metadata") or mapping.get("is_sensitive") or mapping.get("is_open_text"))


def build_default_config(column_map: list[dict[str, Any]]) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    for m in column_map:
        if not _is_analyzable(m):
            continue
        qid = m.get("qid") or m.get("data_export_tag") or m["column"]
        if qid not in questions:
            questions[qid] = {"include": True, "frequency_mode": "auto", "response_order": []}
    return {"defaults": {"frequency_mode": "auto"}, "questions": questions}


def _question_looks_like(column: str) -> bool:
    return bool(re.match(r"^Q\d+", column))


def generate_frequency_tables(rows: list[dict[str, str]], column_map: list[dict[str, Any]], config: dict[str, Any], strict: bool = False) -> dict[str, list[dict[str, Any]]]:
    if not rows:
        return {}

    by_col = {m["column"]: m for m in column_map}
    all_cols = list(rows[0].keys())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for c in all_cols:
        m = by_col.get(c)
        if not m:
            if strict and _question_looks_like(c):
                raise SystemExit(f"Unmapped question-like column in strict mode: {c}")
            continue
        if not _is_analyzable(m):
            continue
        qkey = m.get("qid") or m.get("data_export_tag") or c
        grouped[qkey].append(m)

    tables: dict[str, list[dict[str, Any]]] = {}
    defaults = config.get("defaults", {})
    questions_cfg = config.get("questions", {})

    for qkey, mappings in grouped.items():
        cfg = dict(defaults)
        cfg.update(questions_cfg.get(qkey, {}))
        if cfg.get("include", True) is False:
            continue

        question_total_n = sum(1 for r in rows if any(not _is_missing(r.get(m["column"])) for m in mappings))
        out_rows: list[dict[str, Any]] = []

        for m in mappings:
            vals = [str(r.get(m["column"], "")).strip() for r in rows]
            valid = [v for v in vals if not _is_missing(v)]
            if not valid:
                continue
            counts = Counter(valid)
            mode = cfg.get("frequency_mode", "auto")
            scale_type = "interval" if (mode == "interval" or (mode == "auto" and m.get("question_type") == "Matrix")) else "nominal"

            response_order = [str(x) for x in (cfg.get("response_order", []) or []) if str(x) in counts]
            if scale_type == "interval":
                ordered = response_order + sorted([x for x in counts if x not in response_order], key=_numeric_sort_key)
            else:
                ordered = response_order + [k for k, _ in sorted(((k, v) for k, v in counts.items() if k not in response_order), key=lambda kv: (-kv[1], kv[0]))]

            labels = m.get("response_labels", {}) or {}
            for code in ordered:
                out_rows.append({
                    "question_key": qkey,
                    "question_id": m.get("data_export_tag", ""),
                    "question_text": m.get("question_text", ""),
                    "question_type": m.get("question_type", ""),
                    "attribute": m.get("sub_question_text", ""),
                    "column": m["column"],
                    "scale_type": scale_type,
                    "response_code": code,
                    "response_label": labels.get(code, code),
                    "n": counts[code],
                    "valid_pct": round((counts[code] / len(valid)) * 100.0, 2),
                    "valid_n": len(valid),
                    "question_total_n": question_total_n,
                })

        tables[qkey] = out_rows
    return tables


def build_frequency_manifest(rows: list[dict[str, str]], column_map: list[dict[str, Any]], strict: bool, output_files: list[str], data_path: str | Path, column_map_path: str | Path) -> dict[str, Any]:
    cols = list(rows[0].keys()) if rows else []
    by_col = {m["column"]: m for m in column_map}
    skipped_meta = [c for c in cols if c in by_col and by_col[c].get("is_metadata")]
    skipped_sensitive = [c for c in cols if c in by_col and by_col[c].get("is_sensitive")]
    skipped_open = [c for c in cols if c in by_col and by_col[c].get("is_open_text")]
    unmapped = [c for c in cols if c not in by_col]
    analyzed = [c for c in cols if c in by_col and _is_analyzable(by_col[c])]

    return {
        "data_path": str(data_path),
        "column_map_path": str(column_map_path),
        "total_columns": len(cols),
        "analyzed_columns": analyzed,
        "skipped_metadata_columns": skipped_meta,
        "skipped_sensitive_columns": skipped_sensitive,
        "skipped_open_text_columns": skipped_open,
        "unmapped_columns": unmapped,
        "strict_mode": strict,
        "output_files": output_files,
    }


def run_frequency_analysis(data_path: str | Path, column_map_path: str | Path, outdir: str | Path, config_path: str | Path, strict: bool = False) -> list[Path]:
    outdir = Path(outdir)
    freq_dir = outdir / "frequency_tables"
    freq_dir.mkdir(parents=True, exist_ok=True)

    rows = load_csv_rows(data_path)
    cmap = load_json(column_map_path)
    config = load_json(config_path)
    tables = generate_frequency_tables(rows, cmap, config, strict=strict)

    fields = ["question_key", "question_id", "question_text", "question_type", "attribute", "column", "scale_type", "response_code", "response_label", "n", "valid_pct", "valid_n", "question_total_n"]
    outs: list[Path] = []
    for qk in sorted(tables.keys()):
        p = freq_dir / f"{qk}_frequencies.csv"
        write_csv(p, tables[qk], fields)
        outs.append(p)

    manifest = build_frequency_manifest(rows, cmap, strict, [str(p) for p in outs], data_path, column_map_path)
    (outdir / "frequency_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return outs


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Qualtrics frequency table generator")
    p.add_argument("--data", required=False)
    p.add_argument("--column-map", required=True)
    p.add_argument("--outdir", default="analysis_output")
    p.add_argument("--config", default="qualtrics_frequency_config.json")
    p.add_argument("--init-config", action="store_true")
    p.add_argument("--strict", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.init_config:
        cmap = load_json(args.column_map)
        Path(args.config).write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")
        print(f"Created config file: {args.config}")
        return
    if not args.data:
        raise SystemExit("--data is required unless using --init-config")

    cp = Path(args.config)
    if not cp.exists():
        cmap = load_json(args.column_map)
        cp.write_text(json.dumps(build_default_config(cmap), indent=2), encoding="utf-8")

    outs = run_frequency_analysis(args.data, args.column_map, args.outdir, cp, strict=args.strict)
    print(f"Wrote {len(outs)} frequency table(s)")


if __name__ == "__main__":
    main()
