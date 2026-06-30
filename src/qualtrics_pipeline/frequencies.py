from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

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


def build_default_config(column_map: list[dict[str, Any]]) -> dict[str, Any]:
    questions: dict[str, dict[str, Any]] = {}
    for m in column_map:
        if not _is_analyzable(m) and not m.get("is_text_entry_suffix"):
            continue
        qid = _question_key(m)
        if qid not in questions:
            questions[qid] = {
                "include": True,
                "frequency_mode": "auto",
                "response_order": [],
                "text_entry_columns": {},
            }
        if m.get("is_text_entry_suffix"):
            questions[qid]["text_entry_columns"][m["column"]] = {
                "text_reporting_mode": m.get("text_reporting_mode", "summarize_later")
            }
    return {"defaults": {"frequency_mode": "auto"}, "questions": questions}


def _question_looks_like(column: str) -> bool:
    return bool(re.match(r"^Q\d+", column))


def _text_mode_for(mapping: dict[str, Any], question_cfg: dict[str, Any]) -> str:
    per_col = (question_cfg.get("text_entry_columns") or {}).get(mapping["column"], {})
    return per_col.get("text_reporting_mode", mapping.get("text_reporting_mode", "skip"))


def generate_frequency_tables(rows, column_map, config, strict=False):
    if not rows:
        return {}, {}

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

    tables = {}
    for qkey, mappings in grouped.items():
        cfg = dict(defaults)
        cfg.update(qcfgs.get(qkey, {}))
        if cfg.get("include", True) is False:
            continue

        question_total_n = sum(1 for r in rows if any(not _is_missing(r.get(m["column"])) for m in mappings))
        out_rows = []
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
            # Multi-select columns store "1" when selected and blank otherwise,
            # so valid_n would equal n, giving 100% for every option. Use the
            # question-level total as the denominator instead.
            is_multi_select = m.get("selector") in MULTI_SELECTORS
            pct_denom = question_total_n if is_multi_select else len(valid)
            reported_valid_n = question_total_n if is_multi_select else len(valid)
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
                    "valid_pct": round((counts[code] / pct_denom) * 100.0, 2) if pct_denom else 0.0,
                    "valid_n": reported_valid_n,
                    "question_total_n": question_total_n,
                })
        tables[qkey] = out_rows
    return tables, text_outputs


def run_frequency_analysis(data_path, column_map_path, outdir, config_path, strict=False):
    outdir = Path(outdir)
    freq_dir = outdir / "frequency_tables"
    text_dir = outdir / "open_text_outputs"
    freq_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    rows = load_csv_rows(data_path)
    cmap = load_json(column_map_path)
    config = load_json(config_path)
    tables, text_outputs = generate_frequency_tables(rows, cmap, config, strict=strict)

    fields = ["question_key", "question_id", "question_text", "question_type", "attribute", "column", "scale_type", "response_code", "response_label", "n", "valid_pct", "valid_n", "question_total_n"]
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
        "output_files": [str(p) for p in outs],
    }
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
    print(f"Wrote {len(outs)} output file(s)")


if __name__ == "__main__":
    main()
