
#!/usr/bin/env python3
"""
Qualtrics Survey Data Exporter
--------------------------------
Pulls survey responses and metadata from the Qualtrics API
and exports the cleaned data to a CSV file.

Usage:
    python qualtrics_export.py <SURVEY_ID>

Environment Variables Required:
    QUALTRICS_API_TOKEN     - Your Qualtrics API token
    QUALTRICS_DATA_CENTER   - Your Qualtrics data center ID (e.g., gov1)
    QUALTRICS_DIRECTORY_ID  - Your Qualtrics directory/pool ID
"""

import sys
import os
import re
import argparse
import logging
from datetime import datetime
from html.parser import HTMLParser

import numpy as np
import pandas as pd
import requests

from QualtricsAPI.Setup import Credentials
from QualtricsAPI.Survey import Responses


# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("qualtrics_export.log"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Qualtrics Question Type Registry
# ---------------------------------------------------------------------------
QUESTION_TYPE_REGISTRY = {
    "MC": {
        "display_name": "Multiple Choice",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": False,
    },
    "Matrix": {
        "display_name": "Matrix Table",
        "has_choices":  True,
        "has_answers":  True,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "TE": {
        "display_name": "Text Entry",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": True,
        "multi_column": False,
    },
    "Slider": {
        "display_name": "Slider",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "RO": {
        "display_name": "Rank Order",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "NPS": {
        "display_name": "Net Promoter Score",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": False,
    },
    "SBS": {
        "display_name": "Side by Side",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  True,
        "is_open_text": False,
        "multi_column": True,
    },
    "CS": {
        "display_name": "Constant Sum",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "PGR": {
        "display_name": "Pick, Group and Rank",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "HeatMap": {
        "display_name": "Heat Map",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "HotSpot": {
        "display_name": "Hot Spot",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "HL": {
        "display_name": "Highlight",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": True,
        "multi_column": False,
    },
    "MaxDiff": {
        "display_name": "MaxDiff",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "ConjointFlash": {
        "display_name": "Conjoint (Choice-Based)",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "DD": {
        "display_name": "Drill Down",
        "has_choices":  True,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "FileUpload": {
        "display_name": "File Upload",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": True,
        "multi_column": False,
    },
    "Captcha": {
        "display_name": "Captcha Verification",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": False,
    },
    "Timing": {
        "display_name": "Timing",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": True,
    },
    "Meta": {
        "display_name": "Meta Info",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": True,
        "multi_column": True,
    },
    "DB": {
        "display_name": "Descriptive Text / Graphic",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": False,
        "multi_column": False,
    },
    "Calendar": {
        "display_name": "Calendar",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": True,
        "multi_column": False,
    },
    "Signature": {
        "display_name": "Signature",
        "has_choices":  False,
        "has_answers":  False,
        "has_columns":  False,
        "is_open_text": True,
        "multi_column": False,
    },
    "_UNKNOWN": {
        "display_name": "Unknown / Undocumented",
        "has_choices":  True,
        "has_answers":  True,
        "has_columns":  True,
        "is_open_text": False,
        "multi_column": False,
    },
}


def get_type_info(question_type: str) -> dict:
    """
    Return the registry entry for *question_type*.
    Falls back to the '_UNKNOWN' sentinel and logs a warning so that
    any new Qualtrics type is captured rather than silently dropped.
    """
    if question_type not in QUESTION_TYPE_REGISTRY:
        logger.warning(
            "Unrecognised question type '%s' — treating as unknown. "
            "Please report this type so the registry can be updated.",
            question_type,
        )
        return {**QUESTION_TYPE_REGISTRY["_UNKNOWN"], "display_name": f"Unknown ({question_type})"}
    return QUESTION_TYPE_REGISTRY[question_type]


# ---------------------------------------------------------------------------
# HTML Stripping Utility
# ---------------------------------------------------------------------------
class _HTMLStripper(HTMLParser):
    """Minimal HTMLParser subclass that collects only visible text."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def strip_html(raw: str) -> str:
    """
    Remove all HTML tags from *raw* and collapse whitespace.
    Also decodes common HTML entities (e.g. &  ).
    Returns an empty string for non-string input.
    """
    if not isinstance(raw, str):
        return ""
    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_text()
    text = re.sub(r"[\s\u00a0]+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Environment / Configuration
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """Load configuration from environment variables."""
    token = os.environ.get("QUALTRICS_API_TOKEN")
    data_center = 'gov1'
    directory_id = os.environ.get("QUALTRICS_DIRECTORY_ID")
    
    missing = [
        name
        for name, val in [
            ("QUALTRICS_API_TOKEN", token),
            ("QUALTRICS_DATA_CENTER", data_center),
            ("QUALTRICS_DIRECTORY_ID", directory_id),
        ]
        if not val
    ]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    return {
        "token": token,
        "data_center": data_center,
        "directory_id": directory_id,
        "base_url": f"https://{data_center}.qualtrics.com/API/v3",
    }


# ---------------------------------------------------------------------------
# Qualtrics API Helpers
# ---------------------------------------------------------------------------
def build_headers(token: str) -> dict:
    """Return standard Qualtrics API request headers."""
    return {
        "Content-Type": "application/json",
        "X-API-TOKEN": token,
    }


def get_survey_metadata(base_url: str, survey_id: str, headers: dict) -> dict:
    """Fetch and return full survey metadata from the Qualtrics API."""
    url = f"{base_url}/survey-definitions/{survey_id}"
    logger.info("Fetching survey metadata from: %s", url)
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        logger.error("HTTP error fetching survey metadata: %s", e)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logger.error("Network error fetching survey metadata: %s", e)
        sys.exit(1)
    return response.json()


def fetch_responses(survey_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Use QualtricsAPI library to fetch survey responses and question list.
    Returns (responses_df, questions_df).
    """
    logger.info("Fetching survey responses for survey: %s", survey_id)
    r = Responses()
    responses_df = r.get_survey_responses(survey=survey_id)
    questions_df = r.get_survey_questions(survey=survey_id)
    questions_df = questions_df.reset_index(names="Q_num")
    # Row 0 = header labels, Row 1 = import IDs — skip both
    responses_df = responses_df.loc[2:].reset_index(drop=True)
    return responses_df, questions_df


# ---------------------------------------------------------------------------
# Question Map Builder
# ---------------------------------------------------------------------------
def build_question_map(questions_meta: dict) -> dict:
    """
    Parse ALL Qualtrics question metadata into a flat lookup dictionary
    keyed by DataExportTag (e.g., Q1, Q2_1, Q3#1_1).
    """
    question_dict = {}

    for qid, qdata in questions_meta.items():
        question_type = qdata.get("QuestionType", "")
        data_export_tag = qdata.get("DataExportTag")

        if not data_export_tag:
            logger.warning("Question %s has no DataExportTag — skipping.", qid)
            continue

        type_info = get_type_info(question_type)

        base_entry = {
            "qid":            qdata.get("QuestionID"),
            "QuestionType":   question_type,
            "display_name":   type_info["display_name"],
            "QuestionText":   strip_html(qdata.get("QuestionText", "")),
            "is_open_text":   type_info["is_open_text"],
            "labels":         {},
            "sub_question_id":   "",
            "sub_question_text": "",
            "all_sub_questions": {},
            "column_labels":  {},
            "notes":          "",
        }

        if question_type == "DB":
            base_entry["notes"] = "Display-only block; no response data exported."
            question_dict[data_export_tag] = base_entry

        elif type_info["is_open_text"] and not type_info["has_choices"]:
            question_dict[data_export_tag] = base_entry

        elif question_type in ("NPS", "Captcha"):
            if question_type == "NPS":
                base_entry["labels"] = {str(i): str(i) for i in range(0, 11)}
                base_entry["notes"] = "0–10 numeric scale."
            elif question_type == "Captcha":
                base_entry["labels"] = {"0": "Fail", "1": "Pass"}
                base_entry["notes"] = "Captcha pass/fail."
            question_dict[data_export_tag] = base_entry

        elif question_type == "Timing":
            timing_cols = {
                "First Click":   f"{data_export_tag}_First Click",
                "Last Click":    f"{data_export_tag}_Last Click",
                "Page Submit":   f"{data_export_tag}_Page Submit",
                "Click Count":   f"{data_export_tag}_Click Count",
            }
            for label, tag in timing_cols.items():
                question_dict[tag] = {
                    **base_entry,
                    "sub_question_text": label,
                    "notes": "Timing metric (seconds or count).",
                }

        elif question_type == "MC":
            entry = {**base_entry, "labels": _build_labels(qdata)}
            selector = qdata.get("Selector", "")
            if selector in ("MAVR", "MAHR", "MACOL", "MSB"):
                choices = qdata.get("Choices", {})
                recodes = qdata.get("RecodeValues", {})
                sub_qs = _build_sub_questions(qdata)
                for choice_id, choice_data in choices.items():
                    recode = recodes.get(choice_id, choice_id)
                    tag = f"{data_export_tag}_{recode}"
                    question_dict[tag] = {
                        **base_entry,
                        "labels": {"0": "Not Selected", "1": "Selected"},
                        "sub_question_id":   choice_id,
                        "sub_question_text": strip_html(choice_data.get("Display", "")),
                        "all_sub_questions": sub_qs,
                        "notes": "Multi-answer MC; binary per choice.",
                    }
            else:
                question_dict[data_export_tag] = entry

        elif question_type == "Matrix":
            answer_labels = _build_answer_labels(qdata)
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            matrix_selector = qdata.get("Selector", "")
            for choice_id in choices:
                tag = f"{data_export_tag}_{choice_id}"
                entry_labels = answer_labels
                notes = ""
                if matrix_selector == "TE":
                    entry_labels = {}
                    notes = "Text-entry matrix cell; open text response."
                elif matrix_selector == "CS":
                    notes = "Constant sum matrix; numeric allocation."
                question_dict[tag] = {
                    **base_entry,
                    "labels":            entry_labels,
                    "is_open_text":      matrix_selector == "TE",
                    "sub_question_id":   choice_id,
                    "sub_question_text": sub_questions.get(choice_id, ""),
                    "all_sub_questions": sub_questions,
                    "notes":             notes,
                }

        elif question_type == "SBS":
            sub_questions = _build_sub_questions(qdata)
            column_labels = _build_column_labels(qdata)
            choices = qdata.get("Choices", {})
            columns = qdata.get("Columns", {})
            for choice_id in choices:
                for col_id, col_data in columns.items():
                    tag = f"{data_export_tag}#{col_id}_{choice_id}"
                    col_type = col_data.get("QuestionType", "")
                    col_labels = {}
                    if col_type == "MC":
                        col_labels = _build_answer_labels_from_column(col_data)
                    question_dict[tag] = {
                        **base_entry,
                        "labels":            col_labels,
                        "is_open_text":      col_type == "TE",
                        "sub_question_id":   choice_id,
                        "sub_question_text": sub_questions.get(choice_id, ""),
                        "all_sub_questions": sub_questions,
                        "column_labels":     column_labels,
                        "notes": f"SBS column: {strip_html(col_data.get('Description', ''))}",
                    }

        elif question_type == "RO":
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            for choice_id in choices:
                tag = f"{data_export_tag}_{choice_id}"
                question_dict[tag] = {
                    **base_entry,
                    "labels":            {},
                    "sub_question_id":   choice_id,
                    "sub_question_text": sub_questions.get(choice_id, ""),
                    "all_sub_questions": sub_questions,
                    "notes": "Rank order position (numeric).",
                }

        elif question_type == "Slider":
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            if len(choices) > 1:
                for choice_id in choices:
                    tag = f"{data_export_tag}_{choice_id}"
                    question_dict[tag] = {
                        **base_entry,
                        "sub_question_id":   choice_id,
                        "sub_question_text": sub_questions.get(choice_id, ""),
                        "all_sub_questions": sub_questions,
                        "notes": "Slider value (numeric).",
                    }
            else:
                base_entry["notes"] = "Slider value (numeric)."
                question_dict[data_export_tag] = base_entry

        elif question_type == "CS":
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            for choice_id in choices:
                tag = f"{data_export_tag}_{choice_id}"
                question_dict[tag] = {
                    **base_entry,
                    "sub_question_id":   choice_id,
                    "sub_question_text": sub_questions.get(choice_id, ""),
                    "all_sub_questions": sub_questions,
                    "notes": "Constant sum allocation (numeric).",
                }

        elif question_type == "DD":
            choices = qdata.get("Choices", {})
            for idx, choice_id in enumerate(choices, start=1):
                tag = f"{data_export_tag}_{idx}"
                question_dict[tag] = {
                    **base_entry,
                    "sub_question_id":   str(idx),
                    "sub_question_text": f"Level {idx}",
                    "notes": "Drill-down hierarchy level selection.",
                }

        elif question_type == "MaxDiff":
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            for choice_id in choices:
                tag = f"{data_export_tag}_{choice_id}"
                question_dict[tag] = {
                    **base_entry,
                    "labels": {"0": "Not Selected", "1": "Best", "-1": "Worst"},
                    "sub_question_id":   choice_id,
                    "sub_question_text": sub_questions.get(choice_id, ""),
                    "all_sub_questions": sub_questions,
                    "notes": "MaxDiff: Best=1, Worst=-1, Not shown=0.",
                }

        elif question_type in ("HeatMap", "HotSpot"):
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            for choice_id in choices:
                tag = f"{data_export_tag}_{choice_id}"
                question_dict[tag] = {
                    **base_entry,
                    "labels": {"0": "Not Selected", "1": "Selected"},
                    "sub_question_id":   choice_id,
                    "sub_question_text": sub_questions.get(choice_id, ""),
                    "all_sub_questions": sub_questions,
                    "notes": f"{type_info['display_name']} region click (binary).",
                }

        elif question_type == "PGR":
            sub_questions = _build_sub_questions(qdata)
            choices = qdata.get("Choices", {})
            for choice_id in choices:
                tag = f"{data_export_tag}_{choice_id}"
                question_dict[tag] = {
                    **base_entry,
                    "sub_question_id":   choice_id,
                    "sub_question_text": sub_questions.get(choice_id, ""),
                    "all_sub_questions": sub_questions,
                    "notes": "PGR group assignment and rank (numeric).",
                }

        elif question_type == "ConjointFlash":
            base_entry["notes"] = (
                "Conjoint (CBC) task; response structure varies by design. "
                "Refer to Qualtrics conjoint export for full attribute data."
            )
            question_dict[data_export_tag] = base_entry

        elif question_type == "HL":
            base_entry["notes"] = "Respondent-highlighted text stored as JSON string."
            question_dict[data_export_tag] = base_entry

        elif question_type == "Signature":
            base_entry["notes"] = "Base64-encoded image or URL string."
            question_dict[data_export_tag] = base_entry

        elif question_type == "Calendar":
            base_entry["notes"] = "Date/time string selected by respondent."
            question_dict[data_export_tag] = base_entry

        elif question_type == "FileUpload":
            base_entry["notes"] = "Uploaded file name or URL string."
            question_dict[data_export_tag] = base_entry

        elif question_type == "Meta":
            meta_cols = ["Browser", "Version", "Operating System", "Resolution"]
            for label in meta_cols:
                tag = f"{data_export_tag}_{label.replace(' ', '_')}"
                question_dict[tag] = {
                    **base_entry,
                    "sub_question_text": label,
                    "notes": "Browser/device metadata captured automatically.",
                }

        else:
            base_entry["notes"] = (
                f"Unrecognised type '{question_type}'. Choices and Answers "
                "parsed opportunistically; verify export columns manually."
            )
            if type_info["has_answers"] and qdata.get("Answers"):
                base_entry["labels"] = _build_answer_labels(qdata)
            elif type_info["has_choices"] and qdata.get("Choices"):
                base_entry["labels"] = _build_labels(qdata)

            if type_info["multi_column"] and qdata.get("Choices"):
                sub_questions = _build_sub_questions(qdata)
                for choice_id in qdata["Choices"]:
                    tag = f"{data_export_tag}_{choice_id}"
                    question_dict[tag] = {
                        **base_entry,
                        "sub_question_id":   choice_id,
                        "sub_question_text": sub_questions.get(choice_id, ""),
                        "all_sub_questions": sub_questions,
                    }
            else:
                question_dict[data_export_tag] = base_entry

    return question_dict


# ---------------------------------------------------------------------------
# Label / Sub-question Builders
# ---------------------------------------------------------------------------
def _build_labels(qdata: dict) -> dict:
    """Build {recode_value: display_text} from MC Choices (HTML stripped)."""
    choices = qdata.get("Choices", {})
    recodes = qdata.get("RecodeValues", {})
    labels = {}
    for choice_id, choice_data in choices.items():
        display_text = choice_data.get("Display")
        if display_text is not None:
            key = recodes.get(choice_id, choice_id)
            labels[str(key)] = strip_html(display_text)
    return labels


def _build_answer_labels(qdata: dict) -> dict:
    """
    Build {recode_value: display_text} from Matrix/SBS Answers
    (the scale options, e.g., Strongly Agree … Strongly Disagree).
    HTML stripped.
    """
    answers = qdata.get("Answers", {})
    recodes = qdata.get("RecodeValues", {})
    labels = {}
    for ans_id, ans_data in answers.items():
        display_text = ans_data.get("Display")
        if display_text is not None:
            key = recodes.get(ans_id, ans_id)
            labels[str(key)] = strip_html(display_text)
    return labels


def _build_answer_labels_from_column(col_data: dict) -> dict:
    """
    Build answer labels from an SBS column sub-definition.
    SBS columns carry their own Choices/Answers structures.
    """
    answers = col_data.get("Choices", col_data.get("Answers", {}))
    labels = {}
    for ans_id, ans_data in answers.items():
        display_text = ans_data.get("Display")
        if display_text is not None:
            labels[str(ans_id)] = strip_html(display_text)
    return labels


def _build_sub_questions(qdata: dict) -> dict:
    """
    Build {choice_id: display_text} for row statements in multi-row
    question types (Matrix, RO, Slider, CS, SBS, etc.). HTML stripped.
    """
    choices = qdata.get("Choices", {})
    return {
        cid: strip_html(cdata.get("Display", ""))
        for cid, cdata in choices.items()
    }


def _build_column_labels(qdata: dict) -> dict:
    """Build {col_id: display_text} for SBS column headers. HTML stripped."""
    columns = qdata.get("Columns", {})
    return {
        col_id: strip_html(col_data.get("Description", col_data.get("Display", "")))
        for col_id, col_data in columns.items()
    }


# ---------------------------------------------------------------------------
# Data Cleaning & Enrichment
# ---------------------------------------------------------------------------

COLUMNS_TO_DROP = [
    "Status",
    "IPAddress",
    "LocationLatitude",
    "LocationLongitude",
    "DistributionChannel",
    "UserLanguage",
]

META_COLUMNS = [
    "StartDate",
    "EndDate",
    "RecordedDate",
    "Progress",
    "Finished",
    "Duration (in seconds)",
    "ResponseId",
    "RecipientLastName",
    "RecipientFirstName",
    "RecipientEmail",
    "ExternalReference",
]


def drop_sensitive_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop columns that are not needed or contain sensitive data."""
    cols_present = [c for c in COLUMNS_TO_DROP if c in df.columns]
    dropped = [c for c in COLUMNS_TO_DROP if c not in df.columns]
    if dropped:
        logger.warning("Columns not found (skipping drop): %s", dropped)
    return df.drop(columns=cols_present)


def get_question_value_label(question_map: dict, q: str, val) -> str:
    """
    Resolve a numeric response value to its human-readable label.
    Returns an empty string if no mapping is found.
    Labels are already HTML-stripped at build time.
    """
    try:
        return question_map[q]["labels"][str(val)]
    except (KeyError, TypeError):
        return ""


def add_labeled_columns(df: pd.DataFrame, question_map: dict) -> pd.DataFrame:
    """
    For every MC / Matrix / other labelled column present in the DataFrame,
    append a '<column>_label' column containing the human-readable answer text.
    """
    label_cols = {}
    for col in df.columns:
        if col in question_map and question_map[col].get("labels"):
            label_cols[f"{col}_label"] = df[col].apply(
                lambda v, c=col: get_question_value_label(question_map, c, v)
            )
    if label_cols:
        df = pd.concat([df, pd.DataFrame(label_cols, index=df.index)], axis=1)
    return df


def compute_completion_stats(df: pd.DataFrame, question_cols: list) -> pd.DataFrame:
    """
    Append per-respondent completion metrics:
        - questions_answered  : count of non-empty question responses
        - completion_rate_pct : percentage of questions answered
    """
    present = [c for c in question_cols if c in df.columns]
    df["questions_answered"] = df[present].apply(
        lambda row: row.replace("", np.nan).notna().sum(), axis=1
    )
    df["completion_rate_pct"] = (df["questions_answered"] / len(present) * 100).round(2)
    return df


# ---------------------------------------------------------------------------
# Summary Report
# ---------------------------------------------------------------------------
def generate_summary_report(
    df: pd.DataFrame,
    question_map: dict,
    survey_id: str,
) -> pd.DataFrame:
    """
    Generate a frequency/percentage summary for all questions that carry
    discrete response labels (MC, Matrix, NPS, Captcha, HeatMap, etc.).

    For every labelled question column the output contains:
      - One row per defined response option (including options with zero responses),
        sorted ascending by coded value.
      - A TOTAL row showing the count of all valid (non-missing) responses.
      - A MISSING row showing the count of blank / null responses.

    Columns:
        survey_id | question_tag | question_type | display_name |
        question_text | sub_question_text | response_value | label |
        count | total_n | missing_n | pct | valid_pct

    Definitions
    -----------
    total_n     : total number of respondents in the survey (constant per survey)
    missing_n   : number of respondents who did not answer this specific question
    count       : number of respondents who selected this specific option
    pct         : count / total_n * 100  (includes missing in denominator)
    valid_pct   : count / (total_n - missing_n) * 100  (excludes missing)
    """
    records = []

    # Total respondents in the survey — constant denominator for pct
    survey_n = len(df)

    for col in df.columns:
        if col not in question_map:
            continue
        q_info = question_map[col]
        if not q_info.get("labels"):
            continue

        # ── Counts ──────────────────────────────────────────────────────
        col_series = df[col].replace("", np.nan)
        missing_n  = int(col_series.isna().sum())
        valid_n    = survey_n - missing_n          # respondents who answered
        value_counts = col_series.value_counts()   # only non-null values

        base = {
            "survey_id":         survey_id,
            "question_tag":      col,
            "question_type":     q_info.get("QuestionType", ""),
            "display_name":      q_info.get("display_name", ""),
            "question_text":     q_info.get("QuestionText", ""),
            "sub_question_text": q_info.get("sub_question_text", ""),
        }

        # ── One row per defined label, sorted by coded value (ascending) ─
        sorted_labels = sorted(
            q_info["labels"].items(), key=lambda kv: _sort_key(kv[0])
        )
        for val, label in sorted_labels:
            count = int(value_counts.get(val, 0))
            records.append({
                **base,
                "response_value": val,
                "label":          label,
                "count":          count,
                "total_n":        survey_n,
                "missing_n":      missing_n,
                "pct":       round(count / survey_n * 100, 2) if survey_n  else 0.0,
                "valid_pct": round(count / valid_n  * 100, 2) if valid_n   else 0.0,
            })

        # ── TOTAL row (sum of all valid responses) ───────────────────────
        records.append({
            **base,
            "response_value": "_TOTAL",
            "label":          "TOTAL",
            "count":          valid_n,
            "total_n":        survey_n,
            "missing_n":      missing_n,
            "pct":       round(valid_n / survey_n * 100, 2) if survey_n else 0.0,
            "valid_pct": 100.0 if valid_n else 0.0,
        })

        # ── MISSING row ──────────────────────────────────────────────────
        records.append({
            **base,
            "response_value": "_MISSING",
            "label":          "MISSING",
            "count":          missing_n,
            "total_n":        survey_n,
            "missing_n":      missing_n,
            "pct":       round(missing_n / survey_n * 100, 2) if survey_n else 0.0,
            "valid_pct": 0.0,
        })

    return pd.DataFrame(records, columns=[
        "survey_id", "question_tag", "question_type", "display_name",
        "question_text", "sub_question_text", "response_value", "label",
        "count", "total_n", "missing_n", "pct", "valid_pct",
    ])


# ---------------------------------------------------------------------------
# Codebook Generator
# ---------------------------------------------------------------------------
def generate_codebook(question_map: dict, survey_id: str) -> pd.DataFrame:
    """
    Build a human-readable codebook describing every question variable and
    its valid response options — without any actual response data.

    Output columns:
        survey_id        | question_tag     | question_type  | display_name  |
        question_text    | sub_question_id  | sub_question_text |
        response_value   | response_label   | notes

    One row per valid response option for labelled questions.
    Open-text types produce a single row with '[open text]'.
    Display-only (DB) types produce a single row with '[no response]'.
    Unknown types are included with whatever labels could be parsed.
    """
    records = []

    for tag, info in question_map.items():
        q_type    = info.get("QuestionType", "")
        d_name    = info.get("display_name", "")
        q_text    = info.get("QuestionText", "")
        sub_id    = info.get("sub_question_id", "")
        sub_text  = info.get("sub_question_text", "")
        labels    = info.get("labels", {})
        notes     = info.get("notes", "")
        open_text = info.get("is_open_text", False)

        base = {
            "survey_id":         survey_id,
            "question_tag":      tag,
            "question_type":     q_type,
            "display_name":      d_name,
            "question_text":     q_text,
            "sub_question_id":   sub_id,
            "sub_question_text": sub_text,
            "notes":             notes,
        }

        if q_type == "DB":
            records.append({**base, "response_value": "[no response]", "response_label": "[display only]"})
        elif open_text or not labels:
            records.append({**base, "response_value": "[open text]", "response_label": "[open text]"})
        else:
            for val, label in sorted(labels.items(), key=lambda kv: _sort_key(kv[0])):
                records.append({**base, "response_value": val, "response_label": label})

    codebook_df = pd.DataFrame(records, columns=[
        "survey_id", "question_tag", "question_type", "display_name",
        "question_text", "sub_question_id", "sub_question_text",
        "response_value", "response_label", "notes",
    ])

    codebook_df = codebook_df.sort_values(
        by=["question_tag", "response_value"], key=_series_sort_key
    ).reset_index(drop=True)

    return codebook_df


def _sort_key(val: str):
    """Return a (is_numeric, value) tuple for natural sort ordering."""
    try:
        return (0, float(val))
    except (ValueError, TypeError):
        return (1, str(val))


def _series_sort_key(series: pd.Series) -> pd.Series:
    """Pandas sort_values key= coercing numeric strings to float."""
    def _coerce(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return v
    return series.apply(_coerce)


# ---------------------------------------------------------------------------
# Lookup Utilities
# ---------------------------------------------------------------------------
def get_qid_from_q_num(questions_meta: dict, q_num: str) -> str | None:
    """Return the Qualtrics QID for a given DataExportTag (e.g., 'Q3')."""
    for k, v in questions_meta.items():
        if v.get("DataExportTag") == q_num:
            return k
    return None


def get_q_num_from_label(questions_df: pd.DataFrame, label: str) -> str | None:
    """Return the DataExportTag for a given question label string."""
    matches = questions_df.loc[questions_df["Questions"] == label, "Q_num"]
    return matches.values[0] if not matches.empty else None


def get_label_from_q_num(questions_df: pd.DataFrame, q_num: str) -> str | None:
    """Return the question label string for a given DataExportTag."""
    matches = questions_df.loc[questions_df["Q_num"] == q_num, "Questions"]
    return matches.values[0] if not matches.empty else None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def build_output_path(
    output_dir: str,
    label: str,
    survey_name: str,
    timestamp: str | None,
) -> str:
    """
    Construct an output file path.

    If *timestamp* is provided the filename is:
        <label>_<survey_name>_<timestamp>.csv
    Otherwise:
        <label>_<survey_name>.csv

    This keeps filenames deterministic (and therefore diff-friendly /
    pipeline-friendly) by default, while still allowing timestamped
    filenames when --timestamp is passed.
    """
    if timestamp:
        filename = f"{label}_{survey_name}_{timestamp}.csv"
    else:
        filename = f"{label}_{survey_name}.csv"
    return os.path.join(output_dir, filename)


def save_outputs(
    responses_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    codebook_df: pd.DataFrame,
    survey_name: str,
    output_dir: str = ".",
    timestamp: str | None = None,
) -> None:
    """
    Save the cleaned responses, frequency summary, and codebook to CSV files
    in *output_dir*.  Filenames include a timestamp only when *timestamp* is
    not None (controlled by the --timestamp CLI flag).
    """
    os.makedirs(output_dir, exist_ok=True)

    responses_path = build_output_path(output_dir, survey_name, "Data", timestamp)
    summary_path   = build_output_path(output_dir, survey_name, "Frequencies",  timestamp)
    codebook_path  = build_output_path(output_dir, survey_name, "Codebook", timestamp)

    responses_df.to_csv(responses_path, index=False, encoding="utf-8-sig")
    logger.info("Responses saved to  : %s", responses_path)

    if not summary_df.empty:
        summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
        logger.info("Summary saved to    : %s", summary_path)
    else:
        logger.info("Summary report skipped (--no-summary flag set or no labelled columns found).")

    if not codebook_df.empty:
        codebook_df.to_csv(codebook_path, index=False, encoding="utf-8-sig")
        logger.info("Codebook saved to   : %s", codebook_path)
    else:
        logger.info("Codebook skipped (--no-codebook flag set).")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Qualtrics survey data to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "survey_id",
        help="Qualtrics Survey ID (e.g., SV_xxxxxxxxxx)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to write output CSV files (default: current directory)",
    )
    parser.add_argument(
        "--timestamp",
        action="store_true",
        help=(
            "Append a YYYYMMDD_HHMMSS timestamp to output filenames. "
            "Without this flag filenames are deterministic: "
            "<label>_<survey_name>.csv"
        ),
    )
    parser.add_argument(
        "--no-labels",
        action="store_true",
        help="Skip appending human-readable label columns to the responses export",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip generating the frequency summary report",
    )
    parser.add_argument(
        "--no-codebook",
        action="store_true",
        help="Skip generating the survey codebook",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    survey_id = args.survey_id

    logger.info("=== Qualtrics Survey Exporter ===")
    logger.info("Survey ID : %s", survey_id)

    # 1. Load config from environment
    config = load_config()

    # 2. Initialise Qualtrics credentials
    creds = Credentials()
    creds.qualtrics_api_credentials(
        token=config["token"],
        data_center=config["data_center"],
        directory_id=config["directory_id"],
    )

    # 3. Fetch metadata and responses
    headers = build_headers(config["token"])
    metadata = get_survey_metadata(config["base_url"], survey_id, headers)
    
    survey_name = metadata['result']['SurveyName']
    logger.info("Survey Name : %s", survey_name)

    questions_meta = metadata["result"]["Questions"]

    responses_df, questions_df = fetch_responses(survey_id)
    logger.info("Raw response shape: %s", responses_df.shape)

    # 4. Build question map (all types, HTML stripped at build time)
    question_map = build_question_map(questions_meta)
    logger.info("Question map entries: %d", len(question_map))

    # 5. Clean responses
    responses_df = drop_sensitive_columns(responses_df)

    # 6. Identify question columns (non-meta)
    question_cols = [c for c in responses_df.columns if c not in META_COLUMNS]

    # 7. Compute per-respondent completion stats
    responses_df = compute_completion_stats(responses_df, question_cols)

    # 8. Optionally append human-readable label columns
    if not args.no_labels:
        responses_df = add_labeled_columns(responses_df, question_map)

    # 9. Optionally generate frequency summary
    summary_df = pd.DataFrame()
    if not args.no_summary:
        summary_df = generate_summary_report(responses_df, question_map, survey_name)
        logger.info("Summary report rows: %d", len(summary_df))

    # 10. Optionally generate codebook
    codebook_df = pd.DataFrame()
    if not args.no_codebook:
        codebook_df = generate_codebook(question_map, survey_id)
        logger.info("Codebook rows: %d", len(codebook_df))

    # 11. Resolve optional timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") if args.timestamp else None

    # 12. Save outputs
    save_outputs(
        responses_df,
        summary_df,
        codebook_df,
        survey_name,
        output_dir=args.output_dir,
        timestamp=timestamp,
    )

    logger.info("Export complete.")


if __name__ == "__main__":
    main()