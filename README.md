# Qualtrics Analysis

Pipeline for exporting Qualtrics survey data and generating frequency tables with an explicit data contract.

## Install

```bash
pip install -e .
pip install -e '.[dev]'
```

## Export stage

```bash
python -m qualtrics_pipeline.export --survey-id SV_123 --outdir runs/example
```

Produces contract artifacts:
- `survey_metadata.json`
- `questions_meta.json`
- `column_map.json`
- `display_logic.json`
- `codebook.csv`
- `run_manifest.json`

Data files by privacy mode:
- `deidentified` (default): `responses_clean.csv` only
- `internal`: `responses_raw.csv` + `responses_clean.csv`
- `raw`: `responses_raw.csv` only

## Frequency stage

Initialize config from column map:

```bash
python -m qualtrics_pipeline.frequencies --column-map runs/example/column_map.json --config qualtrics_frequency_config.json --init-config
```

Run frequency analysis (deidentified/internal):

```bash
python -m qualtrics_pipeline.frequencies \
  --data runs/example/responses_clean.csv \
  --column-map runs/example/column_map.json \
  --config qualtrics_frequency_config.json \
  --outdir runs/example
```

For raw mode, use `responses_raw.csv` with `--data`.

Strict validation mode:

```bash
python -m qualtrics_pipeline.frequencies \
  --data runs/example/responses_clean.csv \
  --column-map runs/example/column_map.json \
  --config qualtrics_frequency_config.json \
  --outdir runs/example \
  --strict
```

Default frequency behavior skips unmapped, metadata, sensitive, and open-text columns unless config includes them explicitly.


Frequency outputs also include `frequency_manifest.json` summarizing analyzed, skipped, and unmapped columns.

## Response ordering (`sort_by`)

Each question in the frequency config takes a `sort_by` value controlling row order:

- `count_desc` — most frequent first (default for multiple-choice questions)
- `count_asc` — least frequent first
- `survey_order` — follow the survey designer's choice order (from `response_labels`); use for ordinal scales (height, rank, sizes) whose codes are not in logical sequence
- `response_order` — explicit ordered list supplied in the question's `response_order` field; unlisted codes are appended by `count_desc`
- `auto` (default) — Matrix questions use `survey_order`, all other types use `count_desc`

The legacy `frequency_mode` field is still honored (`interval` → `survey_order`, `nominal` → `count_desc`).

## Display logic and conditional bases

Questions gated by Qualtrics display logic are only shown to a subset of respondents,
so their correct denominator is the *eligible base*, not all respondents. The export
stage parses each question's `DisplayLogic` into data-level predicates and writes
`display_logic.json`. The frequency stage loads it automatically (a sibling of
`--column-map`, or pass `--display-logic`) and adds two columns to every frequency table:

- `base_n` — respondents eligible to see the question (display logic evaluates true);
  for unconditional questions this is the full respondent count
- `base_pct` — `n / base_n * 100` (percentage of those shown the question)

The existing `valid_n` / `valid_pct` (percentage of those who actually answered) are
unchanged. Respondents who were eligible but left the question blank still count toward
`base_n`. `frequency_manifest.json` lists `conditional_questions` (question → base_n) and
`logic_not_evaluable` (questions whose logic contained a condition type the parser could
not resolve; these fall back to treating all respondents as eligible).
