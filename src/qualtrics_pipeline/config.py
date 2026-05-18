"""Configuration helpers for frequency analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .frequencies import build_default_config


def load_json(path: str | Path) -> Any:
    """Load a JSON document from disk."""
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def init_frequency_config(config_path: str | Path, column_map_path: str | Path) -> Path:
    """Initialize frequency config from column_map.json."""
    config_path = Path(config_path)
    column_map = load_json(column_map_path)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(build_default_config(column_map), f, indent=2)
    return config_path
