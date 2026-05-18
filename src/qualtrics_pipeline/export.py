"""Qualtrics export pipeline with explicit data contract artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from QualtricsAPI.Survey import Responses

SENSITIVE_COLUMNS = {
    "RecipientFirstName",
    "RecipientLastName",
    "RecipientEmail",
    "IPAddress",
    "LocationLatitude",
    "LocationLongitude",
    "ExternalReference",
    "UserLanguage",
}

QUESTION_LIKE = re.compile(r"^Q\d+")


def _extract_display(node: dict[str, Any]) -> str:
    return node.get("Display") or node.get("Description") or node.get("ChoiceText") or str(node)


def load_env() -> dict[str, str]:
    token = os.environ.get("QUALTRICS_API_TOKEN", "")
    data_center = os.environ.get("QUALTRICS_DATA_CENTER", "")
    if not data_center:
        data_center = "gov1"
    if not token:
        raise SystemExit("Missing QUALTRICS_API_TOKEN")
    return {"token": token, "data_center": data_center, "base_url": f"https://{data_center}.qualtrics.com/API/v3"}


def get_survey_metadata(base_url: str, survey_id: str, token: str) -> dict[str, Any]:
    r = requests.get(
        f"{base_url}/survey-definitions/{survey_id}",
        headers={"X-API-TOKEN": token, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("result", {})


def fetch_responses_df(survey_id: str) -> pd.DataFrame:
    r = Responses()
    df = r.get_survey_responses(survey=survey_id)
    return df


def build_column_map(survey_id: str, columns: list[str], questions_meta: dict[str, Any]) -> list[dict[str, Any]]:
    tag_map: dict[str, tuple[str, dict[str, Any]]] = {}
    for qid, q in questions_meta.items():
        tag = q.get("DataExportTag")
        if tag:
            tag_map[tag] = (qid, q)

    out: list[dict[str, Any]] = []
    for col in columns:
        base = col.split("_")[0].split("#")[0]
        qid = ""
        q = None
        if base in tag_map:
            qid, q = tag_map[base]

        is_metadata = not bool(q)
        question_type = q.get("QuestionType", "") if q else ""
        selector = q.get("Selector", "") if q else ""
        subselector = q.get("SubSelector", "") if q else ""
        question_text = q.get("QuestionText", "") if q else ""
        sub_question_text = ""
        response_labels: dict[str, str] = {}
        is_open_text = question_type in {"TE", "HL", "FileUpload", "Signature", "Calendar"}

        if q:
            choices = q.get("Choices", {})
            answers = q.get("Answers", {})
            recodes = q.get("RecodeValues", {})
            if question_type == "MC":
                for cid, cnode in choices.items():
                    code = str(recodes.get(cid, cid))
                    response_labels[code] = _extract_display(cnode)
                    if col.endswith(f"_{code}") or col.endswith(f"_{cid}"):
                        sub_question_text = _extract_display(cnode)
            elif question_type == "Matrix":
                response_labels = {str(k): _extract_display(v) for k, v in answers.items()}
                for cid, cnode in choices.items():
                    if col.endswith(f"_{cid}"):
                        sub_question_text = _extract_display(cnode)
                        break

        out.append(
            {
                "survey_id": survey_id,
                "qid": qid,
                "data_export_tag": q.get("DataExportTag", "") if q else "",
                "column": col,
                "question_type": question_type,
                "selector": selector,
                "subselector": subselector,
                "question_text": question_text,
                "sub_question_text": sub_question_text,
                "response_labels": response_labels,
                "is_open_text": is_open_text,
                "is_metadata": is_metadata,
                "is_sensitive": col in SENSITIVE_COLUMNS,
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export Qualtrics survey artifacts")
    p.add_argument("--survey-id", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--privacy-mode", choices=["deidentified", "internal", "raw"], default="deidentified")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    env = load_env()
    survey_meta = get_survey_metadata(env["base_url"], args.survey_id, env["token"])
    questions_meta = survey_meta.get("Questions", {})

    raw_df = fetch_responses_df(args.survey_id)
    raw_path = outdir / "responses_raw.csv"
    raw_df.to_csv(raw_path, index=False)

    clean_df = raw_df.copy()
    if len(clean_df) >= 2:
        clean_df = clean_df.iloc[2:].reset_index(drop=True)
    if args.privacy_mode == "deidentified":
        drop_cols = [c for c in clean_df.columns if c in SENSITIVE_COLUMNS]
        clean_df = clean_df.drop(columns=drop_cols, errors="ignore")

    clean_path = outdir / "responses_clean.csv"
    clean_df.to_csv(clean_path, index=False)

    (outdir / "survey_metadata.json").write_text(json.dumps(survey_meta, indent=2), encoding="utf-8")
    (outdir / "questions_meta.json").write_text(json.dumps(questions_meta, indent=2), encoding="utf-8")

    cmap = build_column_map(args.survey_id, list(clean_df.columns), questions_meta)
    (outdir / "column_map.json").write_text(json.dumps(cmap, indent=2), encoding="utf-8")

    codebook_rows = []
    for row in cmap:
        codebook_rows.append(
            {
                "column": row["column"],
                "qid": row["qid"],
                "data_export_tag": row["data_export_tag"],
                "question_type": row["question_type"],
                "question_text": row["question_text"],
                "sub_question_text": row["sub_question_text"],
                "is_open_text": row["is_open_text"],
                "is_metadata": row["is_metadata"],
                "is_sensitive": row["is_sensitive"],
                "response_labels": json.dumps(row["response_labels"], ensure_ascii=False),
            }
        )
    write_csv(
        outdir / "codebook.csv",
        codebook_rows,
        ["column", "qid", "data_export_tag", "question_type", "question_text", "sub_question_text", "is_open_text", "is_metadata", "is_sensitive", "response_labels"],
    )

    manifest = {
        "survey_id": args.survey_id,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "privacy_mode": args.privacy_mode,
        "rows_raw": int(len(raw_df)),
        "rows_clean": int(len(clean_df)),
        "columns_clean": list(clean_df.columns),
        "artifacts": [
            "responses_raw.csv",
            "responses_clean.csv",
            "survey_metadata.json",
            "questions_meta.json",
            "column_map.json",
            "codebook.csv",
            "run_manifest.json",
        ],
    }
    (outdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
