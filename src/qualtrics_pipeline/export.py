"""Qualtrics export pipeline with explicit data contract artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_COLUMNS = {
    "RecipientFirstName",
    "RecipientLastName",
    "RecipientEmail",
    "IPAddress",
    "LocationLatitude",
    "LocationLongitude",
    "ExternalReference",
}

METADATA_COLUMNS = {
    "StartDate", "EndDate", "Status", "IPAddress", "Progress", "Duration (in seconds)",
    "Finished", "RecordedDate", "ResponseId", "RecipientLastName", "RecipientFirstName",
    "RecipientEmail", "ExternalReference", "LocationLatitude", "LocationLongitude", "DistributionChannel",
    "UserLanguage",
}

MULTI_SELECTORS = {"MAVR", "MAHR", "MACOL", "MSB"}


def _extract_display(node: dict[str, Any]) -> str:
    return node.get("Display") or node.get("Description") or node.get("ChoiceText") or str(node)


def load_env() -> dict[str, str]:
    token = os.environ.get("QUALTRICS_API_TOKEN", "")
    data_center = os.environ.get("QUALTRICS_DATA_CENTER", "gov1")
    directory_id = os.environ.get("QUALTRICS_DIRECTORY_ID", "")
    if not token:
        raise SystemExit("Missing QUALTRICS_API_TOKEN")
    if not directory_id:
        raise SystemExit("Missing QUALTRICS_DIRECTORY_ID")
    return {
        "token": token,
        "data_center": data_center,
        "directory_id": directory_id,
        "base_url": f"https://{data_center}.qualtrics.com/API/v3",
    }


def configure_qualtrics_client(env: dict[str, str]) -> None:
    from QualtricsAPI.Setup import Credentials

    Credentials().qualtrics_api_credentials(
        token=env["token"], data_center=env["data_center"], directory_id=env["directory_id"]
    )


def get_survey_metadata(base_url: str, survey_id: str, token: str) -> dict[str, Any]:
    import requests

    r = requests.get(
        f"{base_url}/survey-definitions/{survey_id}",
        headers={"X-API-TOKEN": token, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("result", {})


def fetch_responses_df(survey_id: str):
    from QualtricsAPI.Survey import Responses

    return Responses().get_survey_responses(survey=survey_id)


def _map_question_column(col: str, survey_id: str, qid: str, q: dict[str, Any]) -> dict[str, Any]:
    qtype = q.get("QuestionType", "")
    selector = q.get("Selector", "")
    subselector = q.get("SubSelector", "")
    tag = q.get("DataExportTag", "")
    response_labels: dict[str, str] = {}
    sub_question_text = ""
    is_open_text = qtype in {"TE", "HL", "FileUpload", "Signature", "Calendar"}

    choices = q.get("Choices", {})
    answers = q.get("Answers", {})
    recodes = q.get("RecodeValues", {})

    if qtype == "MC":
        if selector in MULTI_SELECTORS:
            response_labels = {"0": "Not selected", "1": "Selected"}
            for cid, cnode in choices.items():
                recode = str(recodes.get(cid, cid))
                if col.endswith(f"_{recode}") or col.endswith(f"_{cid}"):
                    sub_question_text = _extract_display(cnode)
        else:
            response_labels = {str(recodes.get(cid, cid)): _extract_display(cnode) for cid, cnode in choices.items()}
    elif qtype == "Matrix":
        response_labels = {str(k): _extract_display(v) for k, v in answers.items()}
        for cid, cnode in choices.items():
            if col.endswith(f"_{cid}"):
                sub_question_text = _extract_display(cnode)
                break
    elif qtype == "NPS":
        response_labels = {str(i): str(i) for i in range(11)}
    elif qtype == "RO":
        for cid, cnode in choices.items():
            if col.endswith(f"_{cid}"):
                sub_question_text = _extract_display(cnode)
                break
    elif qtype in {"CS", "Slider", "PGR", "DD", "Timing", "SBS"}:
        # numeric/special structures; keep labels empty by default
        pass

    return {
        "survey_id": survey_id,
        "qid": qid,
        "data_export_tag": tag,
        "column": col,
        "question_type": qtype,
        "selector": selector,
        "subselector": subselector,
        "question_text": q.get("QuestionText", ""),
        "sub_question_text": sub_question_text,
        "response_labels": response_labels,
        "is_open_text": is_open_text,
        "is_metadata": False,
        "is_sensitive": col in SENSITIVE_COLUMNS,
    }


def build_column_map(survey_id: str, columns: list[str], questions_meta: dict[str, Any]) -> list[dict[str, Any]]:
    tagged: dict[str, tuple[str, dict[str, Any]]] = {}
    for qid, q in questions_meta.items():
        tag = q.get("DataExportTag")
        if tag:
            tagged[tag] = (qid, q)

    out: list[dict[str, Any]] = []
    for col in columns:
        matched = None
        for tag, pair in tagged.items():
            if col == tag or col.startswith(f"{tag}_") or col.startswith(f"{tag}#"):
                matched = pair
                break
        if matched is None:
            out.append({
                "survey_id": survey_id,
                "qid": "",
                "data_export_tag": "",
                "column": col,
                "question_type": "",
                "selector": "",
                "subselector": "",
                "question_text": "",
                "sub_question_text": "",
                "response_labels": {},
                "is_open_text": False,
                "is_metadata": col in METADATA_COLUMNS,
                "is_sensitive": col in SENSITIVE_COLUMNS,
            })
            continue
        qid, q = matched
        out.append(_map_question_column(col, survey_id, qid, q))
    return out




def build_run_manifest(survey_id: str, privacy_mode: str, rows_raw: int, rows_output: int, data_file: str, columns_contract: list[str], artifacts: list[str]) -> dict[str, Any]:
    return {
        "survey_id": survey_id,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "privacy_mode": privacy_mode,
        "rows_raw": int(rows_raw),
        "rows_output": int(rows_output),
        "data_file": data_file,
        "columns_contract": columns_contract,
        "artifacts": artifacts,
    }

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
    configure_qualtrics_client(env)
    survey_meta = get_survey_metadata(env["base_url"], args.survey_id, env["token"])
    questions_meta = survey_meta.get("Questions", {})

    raw_df = fetch_responses_df(args.survey_id)
    clean_df = raw_df.copy()
    if len(clean_df) >= 2:
        clean_df = clean_df.iloc[2:].reset_index(drop=True)

    if args.privacy_mode == "deidentified":
        clean_df = clean_df.drop(columns=[c for c in clean_df.columns if c in SENSITIVE_COLUMNS], errors="ignore")
        clean_df.to_csv(outdir / "responses_clean.csv", index=False)
    elif args.privacy_mode == "internal":
        raw_df.to_csv(outdir / "responses_raw.csv", index=False)
        clean_df = clean_df.drop(columns=[c for c in clean_df.columns if c in SENSITIVE_COLUMNS], errors="ignore")
        clean_df.to_csv(outdir / "responses_clean.csv", index=False)
    else:  # raw
        raw_df.to_csv(outdir / "responses_raw.csv", index=False)

    source_df = clean_df if args.privacy_mode != "raw" else raw_df

    (outdir / "survey_metadata.json").write_text(json.dumps(survey_meta, indent=2), encoding="utf-8")
    (outdir / "questions_meta.json").write_text(json.dumps(questions_meta, indent=2), encoding="utf-8")

    cmap = build_column_map(args.survey_id, list(source_df.columns), questions_meta)
    (outdir / "column_map.json").write_text(json.dumps(cmap, indent=2), encoding="utf-8")

    codebook_rows = [{
        "column": row["column"], "qid": row["qid"], "data_export_tag": row["data_export_tag"],
        "question_type": row["question_type"], "question_text": row["question_text"],
        "sub_question_text": row["sub_question_text"], "is_open_text": row["is_open_text"],
        "is_metadata": row["is_metadata"], "is_sensitive": row["is_sensitive"],
        "response_labels": json.dumps(row["response_labels"], ensure_ascii=False),
    } for row in cmap]
    write_csv(outdir / "codebook.csv", codebook_rows, [
        "column", "qid", "data_export_tag", "question_type", "question_text", "sub_question_text",
        "is_open_text", "is_metadata", "is_sensitive", "response_labels",
    ])

    artifacts = ["survey_metadata.json", "questions_meta.json", "column_map.json", "codebook.csv", "run_manifest.json"]
    if args.privacy_mode == "deidentified":
        artifacts.insert(0, "responses_clean.csv")
    elif args.privacy_mode == "internal":
        artifacts = ["responses_raw.csv", "responses_clean.csv", *artifacts]
    else:
        artifacts = ["responses_raw.csv", *artifacts]

    data_file = "responses_raw.csv" if args.privacy_mode == "raw" else "responses_clean.csv"
    rows_output = int(len(raw_df)) if args.privacy_mode == "raw" else int(len(clean_df))
    manifest = build_run_manifest(args.survey_id, args.privacy_mode, int(len(raw_df)), rows_output, data_file, list(source_df.columns), artifacts)
    (outdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
