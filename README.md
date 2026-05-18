# Qualtrics Analysis

Early-stage Python tooling for:

1. Exporting survey responses and metadata from Qualtrics.
2. Generating question-level frequency tables from exported data.
3. Building toward reproducible MS Word report generation.

## Current repository contents

- `Get Qualtrics Survey.py`: Qualtrics export script (responses + metadata).
- `qualtrics_analysis.py`: Frequency table generation script.

## Architecture review (current state)

### What is already working

- **Question-type-aware export logic** in the Qualtrics retrieval script, including a registry/fallback model for unknown question types.
- **Config-driven frequency generation** in `qualtrics_analysis.py` with per-question include/exclude + response ordering.
- **Reasonable CLI ergonomics** (`--init-config`, auto-create config, explicit input/output paths).

### Main gaps to address next

1. **Project structure and naming consistency**
   - `Get Qualtrics Survey.py` includes spaces in filename and currently blends API/export/cleaning concerns.
   - There is no package structure (`src/`, `tests/`, reusable modules).

2. **Reproducibility and environment setup**
   - No pinned dependencies (`requirements.txt`/`pyproject.toml`).
   - No standard way to run linting/tests in CI.

3. **Validation and test coverage**
   - Core analysis logic is testable but currently untested.
   - No fixture datasets for MC/matrix/missing-data edge cases.

4. **Reporting layer not yet implemented**
   - Frequency tables are generated, but there is no MS Word report assembly pipeline.

5. **Operational robustness**
   - Need clearer error handling for API failures, rate limits, and malformed metadata.
   - Need basic data contracts/schema checks between export and analysis steps.

## Recommended roadmap

### Phase 1 (foundation hardening)

- Rename scripts and introduce a Python package layout:
  - `src/qualtrics_pipeline/export.py`
  - `src/qualtrics_pipeline/frequencies.py`
  - `src/qualtrics_pipeline/config.py`
- Add dependency management (`pyproject.toml` preferred) and lockfile.
- Add baseline tooling:
  - formatting (`black`)
  - linting (`ruff`)
  - tests (`pytest`)
- Add smoke tests around `build_tag_map`, `get_column_context`, and frequency sorting behavior.

### Phase 2 (data model + QA)

- Introduce explicit intermediate artifacts:
  - `responses.csv`
  - `questions_meta.json`
  - `frequency_tables/*.csv`
  - `run_manifest.json` (timestamp, survey id, row counts, warnings)
- Add validation checks:
  - missing question tags
  - unknown question types encountered
  - column/question mismatches
- Add `--strict` mode to fail fast on schema inconsistencies.

### Phase 3 (Word reporting MVP)

- Implement a report builder using `python-docx`:
  - cover page (survey metadata)
  - one section per question
  - embedded frequency tables
  - optional charts (matplotlib export inserted as images)
- Add templating support:
  - style/template `.docx` file
  - heading/body/table style mapping
- Output: `analysis_output/report.docx`.

### Phase 4 (automation + scale)

- Add a single orchestrator command:
  - `python -m qualtrics_pipeline run --survey-id ... --outdir ...`
- Add CI workflow to run format/lint/tests on each commit.
- Add optional scheduled runs (cron/GitHub Actions) for recurring survey waves.

## Suggested near-term deliverables (next 1–2 weeks)

1. Restructure code into importable modules and rename files.
2. Add `pyproject.toml` + `pytest` + `ruff` + `black`.
3. Create test fixtures and at least 8–12 unit tests for frequency logic.
4. Add `reporting.py` MVP producing a simple `.docx` from one frequency table.
5. Document end-to-end usage in this README with one working command chain.

## Example target workflow

```bash
# 1) Export from Qualtrics
python -m qualtrics_pipeline.export --survey-id SV_123 --outdir runs/2026-05-18

# 2) Initialize/edit analysis config
python -m qualtrics_pipeline.frequencies --meta runs/2026-05-18/questions_meta.json --init-config

# 3) Generate frequencies
python -m qualtrics_pipeline.frequencies \
  --data runs/2026-05-18/responses.csv \
  --meta runs/2026-05-18/questions_meta.json \
  --config qualtrics_frequency_config.json \
  --outdir runs/2026-05-18

# 4) Build Word report (planned MVP)
python -m qualtrics_pipeline.reporting --input runs/2026-05-18/frequency_tables --output runs/2026-05-18/report.docx
```

## Notes

- Keep raw exports immutable; write transformed outputs into versioned run directories.
- Do not commit API tokens; use environment variables or a secrets manager.


## Phase 1 implementation status (completed)

- Added Python package layout under `src/qualtrics_pipeline`.
- Added `pyproject.toml` with runtime + dev dependencies.
- Added baseline tooling configuration for `black`, `ruff`, and `pytest`.
- Added smoke tests for key frequency functions in `tests/test_frequencies.py`.
- Kept backwards compatibility with `qualtrics_analysis.py` as a thin entrypoint.
