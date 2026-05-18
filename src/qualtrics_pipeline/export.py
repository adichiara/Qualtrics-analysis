"""Compatibility launcher for the legacy Qualtrics export script."""

from __future__ import annotations

from pathlib import Path
import runpy


def main() -> None:
    script_path = Path(__file__).resolve().parents[2] / "Get Qualtrics Survey.py"
    runpy.run_path(str(script_path), run_name="__main__")


if __name__ == "__main__":
    main()
