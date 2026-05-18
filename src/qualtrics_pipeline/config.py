"""Configuration helpers for frequency analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .frequencies import build_default_config, load_questions_meta


def init_frequency_config(config_path: str | Path, meta_path: str | Path) -> Path:
    """Initialize a default frequency-analysis config file from metadata."""
    config_path = Path(config_path)
    questions_meta = load_questions_meta(meta_path)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(build_default_config(questions_meta), f, indent=2)
    return config_path


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON document from disk."""
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)
