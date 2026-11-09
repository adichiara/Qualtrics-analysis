"""Qualtrics export pipeline with explicit data contract artifacts."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .survey_logic import build_display_logic_map

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
    "StartDate",
    "EndDate",
    "Status",
    "IPAddress",
    "Progress",
    "Duration (in seconds)",
    "Finished",
    "RecordedDate",
    "ResponseId",
    "RecipientLastName",
    "RecipientFirstName",
    "RecipientEmail",
    "ExternalReference",
    "LocationLatitude",
    "LocationLongitude",
    "DistributionChannel",
    "UserLanguage",
    "date",
}

MULTI_SELECTORS = {"MAVR", "MAHR", "MACOL", "MSB"}

# Response labels that mean "no substantive answer" (N/A, Don't know, ...). A code whose
# label matches is listed in a column's ``non_substantive_codes`` so analyses can leave it
# out of means and tests while the response itself stays in the data. Case-insensitive.
DEFAULT_NON_SUBSTANTIVE_PATTERNS: tuple[str, ...] = (
    r"\bn\s*/\s*a\b",  # N/A, n / a, "N/A (I do not have ...)"
    r"^\s*na\s*$",  # a bare "NA"
    r"\bnot\s+applicable\b",
    r"\b(?:don'?t|do\s+not)\s+know\b",
    r"\bprefer\s+not\s+to\s+(?:answer|say|respond)\b",
    r"\bno\s+opinion\b",
)

# Values Qualtrics uses in a question's ``AnalyzeChoices`` map for a choice marked
# "Exclude from analysis" in the survey editor (seen: {"6": "on"}).
_EXCLUDE_FLAG_VALUES = {"on", "yes", "true", "1"}

REASON_QUALTRICS_FLAG = "qualtrics_exclude_from_analysis"
REASON_LABEL_MATCH = "label_match"


def _strip_html(text: str) -> str:
    """Strip HTML tags and unescape entities, collapsing resulting whitespace."""
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return ' '.join(text.split())


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


def _detect_text_entry_suffix(column: str) -> bool:
    return column.endswith("_TEXT")


def _compile_patterns(patterns: list[str] | tuple[str, ...] | None) -> list[re.Pattern[str]]:
    if patterns is None:
        patterns = DEFAULT_NON_SUBSTANTIVE_PATTERNS
    return [re.compile(pat, re.IGNORECASE) for pat in patterns]


def _excluded_choice_ids(q: dict[str, Any]) -> set[str]:
    """Choice ids marked "Exclude from analysis" in Qualtrics (``AnalyzeChoices``)."""
    flags = q.get("AnalyzeChoices") or {}
    if not isinstance(flags, dict):
        return set()
    return {str(cid) for cid, val in flags.items() if str(val).strip().lower() in _EXCLUDE_FLAG_VALUES}


def _label_is_non_substantive(label: str, compiled: list[re.Pattern[str]]) -> bool:
    text = _strip_html(label or "")
    return any(p.search(text) for p in compiled)


def non_substantive_info(
    col: str, q: dict[str, Any], patterns: list[str] | tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Which responses in ``col`` are not substantive answers (N/A, Don't know, ...).

    Returns three column-map fields:

    - ``non_substantive_codes``: response codes (as in ``response_labels``) to leave out
      of analyses. Single-answer MC uses the choice's recode value; a Matrix column uses
      its scale-point code.
    - ``non_substantive_reasons``: ``{code: reason}``, the reason being
      ``qualtrics_exclude_from_analysis`` (the choice is marked "Exclude from analysis" in
      Qualtrics) or ``label_match`` (its label matches a non-substantive pattern).
    - ``is_non_substantive_option``: for a multi-select column, true when the option the
      column records is itself an N/A-type option (e.g. "N/A (I do not have ...)").

    Nothing is removed from the data; these fields only describe it.
    """
    compiled = _compile_patterns(patterns)
    qtype = q.get("QuestionType", "")
    selector = q.get("Selector", "")
    choices = q.get("Choices") or {}
    answers = q.get("Answers") or {}
    recodes = q.get("RecodeValues") or {}
    flagged = _excluded_choice_ids(q)

    reasons: dict[str, str] = {}
    is_option = False

    if _detect_text_entry_suffix(col):
        pass  # typed-in text: no codes to classify
    elif qtype == "MC" and selector in MULTI_SELECTORS:
        for cid, cnode in choices.items():
            recode = str(recodes.get(cid, cid))
            if col.endswith((f"_{recode}", f"_{cid}")):
                is_option = str(cid) in flagged or _label_is_non_substantive(
                    _extract_display(cnode), compiled
                )
                break
    elif qtype == "MC":
        for cid, cnode in choices.items():
            code = str(recodes.get(cid, cid))
            if str(cid) in flagged:
                reasons[code] = REASON_QUALTRICS_FLAG
            elif _label_is_non_substantive(_extract_display(cnode), compiled):
                reasons[code] = REASON_LABEL_MATCH
    elif qtype == "Matrix":
        # AnalyzeChoices on a matrix may refer to its rows rather than its scale, so only
        # scale-point labels are used here.
        for aid, anode in answers.items():
            if _label_is_non_substantive(_extract_display(anode), compiled):
                reasons[str(aid)] = REASON_LABEL_MATCH

    return {
        "non_substantive_codes": list(reasons),
        "non_substantive_reasons": reasons,
        "is_non_substantive_option": is_option,
    }


def _map_question_column(
    col: str,
    survey_id: str,
    qid: str,
    q: dict[str, Any],
    non_substantive_patterns: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    qtype = q.get("QuestionType", "")
    selector = q.get("Selector", "")
    subselector = q.get("SubSelector", "")
    tag = q.get("DataExportTag", "")
    response_labels: dict[str, str] = {}
    sub_question_text = ""
    is_text_entry_suffix = _detect_text_entry_suffix(col)
    is_open_text = qtype in {"TE", "HL", "FileUpload", "Signature", "Calendar"} or is_text_entry_suffix

    parent_choice_code = ""
    parent_choice_label = ""
    parent_question_key = qid or tag

    choices = q.get("Choices", {})
    answers = q.get("Answers", {})
    recodes = q.get("RecodeValues", {})

    if qtype == "MC":
        if selector in MULTI_SELECTORS:
            response_labels = {"0": "Not selected", "1": "Selected"}
            for cid, cnode in choices.items():
                recode = str(recodes.get(cid, cid))
                if col.endswith((f"_{recode}", f"_{cid}", f"_{cid}_TEXT")):
                    sub_question_text = _extract_display(cnode)
                    parent_choice_code = recode
                    parent_choice_label = _extract_display(cnode)
        else:
            response_labels = {
                str(recodes.get(cid, cid)): _extract_display(cnode) for cid, cnode in choices.items()
            }
            for cid, cnode in choices.items():
                recode = str(recodes.get(cid, cid))
                if col.endswith((f"_{cid}_TEXT", f"_{recode}_TEXT")):
                    parent_choice_code = recode
                    parent_choice_label = _extract_display(cnode)
                    sub_question_text = _extract_display(cnode)

    elif qtype == "Matrix":
        response_labels = {str(k): _extract_display(v) for k, v in answers.items()}
        for cid, cnode in choices.items():
            if col.endswith(f"_{cid}"):
                sub_question_text = _extract_display(cnode)
                break
    elif qtype == "NPS":
        response_labels = {str(i): str(i) for i in range(11)}

    text_reporting_mode = "summarize_later" if is_text_entry_suffix else "skip"

    return {
        "survey_id": survey_id,
        "qid": qid,
        "data_export_tag": tag,
        "column": col,
        "question_type": qtype,
        "selector": selector,
        "subselector": subselector,
        "question_text": _strip_html(q.get("QuestionText", "")),
        "sub_question_text": sub_question_text,
        "response_labels": response_labels,
        "is_open_text": is_open_text,
        "is_metadata": False,
        "is_sensitive": col in SENSITIVE_COLUMNS,
        "is_text_entry_suffix": is_text_entry_suffix,
        "parent_question_key": parent_question_key,
        "parent_choice_code": parent_choice_code,
        "parent_choice_label": parent_choice_label,
        "text_reporting_mode": text_reporting_mode,
        **non_substantive_info(col, q, non_substantive_patterns),
    }


def build_column_map(
    survey_id: str,
    columns: list[str],
    questions_meta: dict[str, Any],
    non_substantive_patterns: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    tagged: dict[str, tuple[str, dict[str, Any]]] = {}
    for qid, q in questions_meta.items():
        tag = q.get("DataExportTag")
        if tag:
            tagged[tag] = (qid, q)

    out: list[dict[str, Any]] = []
    for col in columns:
        matched = None
        for tag, pair in tagged.items():
            if col == tag or col.startswith((f"{tag}_", f"{tag}#", f"{tag}.")):
                matched = pair
                break
        if matched is None:
            out.append(
                {
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
                    "is_open_text": _detect_text_entry_suffix(col),
                    "is_metadata": col in METADATA_COLUMNS,
                    "is_sensitive": col in SENSITIVE_COLUMNS,
                    "is_text_entry_suffix": _detect_text_entry_suffix(col),
                    "parent_question_key": "",
                    "parent_choice_code": "",
                    "parent_choice_label": "",
                    "text_reporting_mode": "summarize_later" if _detect_text_entry_suffix(col) else "skip",
                    "non_substantive_codes": [],
                    "non_substantive_reasons": {},
                    "is_non_substantive_option": False,
                }
            )
            continue
        qid, q = matched
        out.append(_map_question_column(col, survey_id, qid, q, non_substantive_patterns))
    return out


def annotate_run(
    run_dir: str | Path, non_substantive_patterns: list[str] | tuple[str, ...] | None = None
) -> int:
    """Add/refresh the non-substantive fields in an existing run's ``column_map.json``.

    Uses the run's ``questions_meta.json``, so older runs can be updated without a new
    export. Returns how many columns have non-substantive codes or options.
    """
    run_dir = Path(run_dir)
    cmap = json.loads((run_dir / "column_map.json").read_text(encoding="utf-8"))
    questions_meta = json.loads((run_dir / "questions_meta.json").read_text(encoding="utf-8"))
    flagged = 0
    for row in cmap:
        q = questions_meta.get(row.get("qid", ""))
        info = (
            non_substantive_info(row["column"], q, non_substantive_patterns)
            if q
            else {
                "non_substantive_codes": [],
                "non_substantive_reasons": {},
                "is_non_substantive_option": False,
            }
        )
        row.update(info)
        flagged += bool(info["non_substantive_codes"] or info["is_non_substantive_option"])
    (run_dir / "column_map.json").write_text(json.dumps(cmap, indent=2), encoding="utf-8")
    return flagged


def build_run_manifest(
    survey_id: str,
    privacy_mode: str,
    rows_raw: int,
    rows_output: int,
    data_file: str,
    columns_contract: list[str],
    artifacts: list[str],
) -> dict[str, Any]:
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
    p.add_argument("--survey-id")
    p.add_argument("--outdir")
    p.add_argument("--privacy-mode", choices=["deidentified", "internal", "raw"], default="deidentified")
    p.add_argument(
        "--non-substantive-pattern",
        action="append",
        default=[],
        metavar="REGEX",
        help="Extra label pattern (case-insensitive regex) marking a response as "
        "non-substantive, added to the defaults. Repeatable.",
    )
    p.add_argument(
        "--annotate-run",
        metavar="RUN_DIR",
        help="Instead of exporting, add the non-substantive fields to an existing run's "
        "column_map.json (uses its questions_meta.json).",
    )
    args = p.parse_args()
    if not args.annotate_run and not (args.survey_id and args.outdir):
        p.error("--survey-id and --outdir are required (or use --annotate-run RUN_DIR)")
    return args


def run_export(
    survey_id: str,
    outdir: str | Path,
    privacy_mode: str = "deidentified",
    non_substantive_patterns: list[str] | tuple[str, ...] | None = None,
) -> Path:
    """Export a survey's artifacts to ``outdir``. Returns ``outdir`` as a Path.

    Callable entry point used by both the argparse CLI and the interactive
    menu (cli.py), so both stay in sync with a single implementation.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    env = load_env()
    configure_qualtrics_client(env)
    survey_meta = get_survey_metadata(env["base_url"], survey_id, env["token"])
    questions_meta = survey_meta.get("Questions", {})

    raw_df = fetch_responses_df(survey_id)
    clean_df = raw_df.copy()
    if len(clean_df) >= 2:
        clean_df = clean_df.iloc[2:].reset_index(drop=True)

    if privacy_mode == "deidentified":
        clean_df = clean_df.drop(columns=[c for c in clean_df.columns if c in SENSITIVE_COLUMNS], errors="ignore")
        clean_df.to_csv(outdir / "responses_clean.csv", index=False)
    elif privacy_mode == "internal":
        raw_df.to_csv(outdir / "responses_raw.csv", index=False)
        clean_df = clean_df.drop(columns=[c for c in clean_df.columns if c in SENSITIVE_COLUMNS], errors="ignore")
        clean_df.to_csv(outdir / "responses_clean.csv", index=False)
    else:
        raw_df.to_csv(outdir / "responses_raw.csv", index=False)

    source_df = clean_df if privacy_mode != "raw" else raw_df

    (outdir / "survey_metadata.json").write_text(json.dumps(survey_meta, indent=2), encoding="utf-8")
    (outdir / "questions_meta.json").write_text(json.dumps(questions_meta, indent=2), encoding="utf-8")

    cmap = build_column_map(
        survey_id, list(source_df.columns), questions_meta, non_substantive_patterns
    )
    (outdir / "column_map.json").write_text(json.dumps(cmap, indent=2), encoding="utf-8")

    display_logic = build_display_logic_map(questions_meta)
    (outdir / "display_logic.json").write_text(json.dumps(display_logic, indent=2), encoding="utf-8")

    codebook_rows = [
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
            "is_text_entry_suffix": row["is_text_entry_suffix"],
            "parent_question_key": row["parent_question_key"],
            "parent_choice_code": row["parent_choice_code"],
            "parent_choice_label": row["parent_choice_label"],
            "text_reporting_mode": row["text_reporting_mode"],
            "response_labels": json.dumps(row["response_labels"], ensure_ascii=False),
            "non_substantive_codes": json.dumps(row["non_substantive_codes"]),
            "is_non_substantive_option": row["is_non_substantive_option"],
        }
        for row in cmap
    ]
    write_csv(
        outdir / "codebook.csv",
        codebook_rows,
        [
            "column",
            "qid",
            "data_export_tag",
            "question_type",
            "question_text",
            "sub_question_text",
            "is_open_text",
            "is_metadata",
            "is_sensitive",
            "is_text_entry_suffix",
            "parent_question_key",
            "parent_choice_code",
            "parent_choice_label",
            "text_reporting_mode",
            "response_labels",
            "non_substantive_codes",
            "is_non_substantive_option",
        ],
    )

    artifacts = ["survey_metadata.json", "questions_meta.json", "column_map.json", "display_logic.json", "codebook.csv", "run_manifest.json"]
    if privacy_mode == "deidentified":
        artifacts.insert(0, "responses_clean.csv")
    elif privacy_mode == "internal":
        artifacts = ["responses_raw.csv", "responses_clean.csv", *artifacts]
    else:
        artifacts = ["responses_raw.csv", *artifacts]

    data_file = "responses_raw.csv" if privacy_mode == "raw" else "responses_clean.csv"
    rows_output = len(raw_df) if privacy_mode == "raw" else len(clean_df)
    manifest = build_run_manifest(survey_id, privacy_mode, len(raw_df), rows_output, data_file, list(source_df.columns), artifacts)
    (outdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return outdir


def main() -> None:
    args = parse_args()
    patterns = [*DEFAULT_NON_SUBSTANTIVE_PATTERNS, *args.non_substantive_pattern]
    if args.annotate_run:
        n = annotate_run(args.annotate_run, patterns)
        print(f"Updated {Path(args.annotate_run) / 'column_map.json'}: {n} column(s) flagged")
        return
    run_export(args.survey_id, args.outdir, args.privacy_mode, patterns)


if __name__ == "__main__":
    main()
