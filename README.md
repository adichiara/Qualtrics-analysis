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

Running the frequency stage also writes `report.html` (see below).

## HTML validation report

The frequency stage emits `report.html` in the output directory automatically. It
renders every question's frequency table into one self-contained HTML page —
questions in natural survey order, with `n`, `valid_n`, `valid_pct`, and `base_pct`,
and a badge on questions gated by display logic — so the computed values can be
checked for accuracy. (The presentation-quality MS Word output is produced
separately; this report is a validation aid.)

To (re)generate it from an existing run directory:

```bash
python -m qualtrics_pipeline.report --run-dir runs/example
```

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

## Percentage bases (computed up front)

Every frequency-table row carries all three denominators so the report layer can
present whichever is appropriate without recomputation:

- `valid_n` / `valid_pct` — among respondents who answered the question
- `eligible_n` / `eligible_pct` — among respondents shown the question per display
  logic (equals the full sample when the question has no display logic)
- `total_n` / `total_pct` — among all survey respondents (prevalence base)

`eligible_n` is derived from Qualtrics display logic: the export stage parses each
question's `DisplayLogic` into data-level predicates and writes `display_logic.json`,
which the frequency stage loads automatically (a sibling of `--column-map`, or pass
`--display-logic`). Respondents who were eligible but left the question blank still
count toward `eligible_n`. `frequency_manifest.json` lists `conditional_questions`
(question → eligible count) and `logic_not_evaluable` (questions whose logic contained
a condition type the parser could not resolve; these treat all respondents as eligible).

The per-question `percent_base` config value (`valid` / `eligible` / `total`, default
`eligible`) names which base the report should feature. It is recorded in each row as
`report_base` and marked with a star in the HTML report; it does not change the computed
numbers. Use `total` for prevalence reporting — e.g. the share of *all* respondents who
reported a durability issue, not just those routed to a follow-up question.

## Grouped tables (crosstabs)

A question can be broken out by one or more grouping variables. Each question in the
config takes a `tables` list; every entry produces one output table:

```jsonc
"QID16": {
  "tables": [
    { "group_by": [] },          // overall (default when "tables" is omitted)
    { "group_by": ["Q1.9"] },    // broken out by uniform type
    { "group_by": ["Q2.4"] }     // also broken out by rank category
  ]
}
```

Each grouped table is the question's distribution computed independently *within* each
level of the grouping variable, so all three bases (valid/eligible/total) are
within-group. Grouped output is written to `{qkey}__by__{group}_frequencies.csv` in long
form (with `group_keys` / `group_codes` / `group_labels` columns); the HTML report pivots
it into a wide crosstab (response options as rows, group levels as columns, cells showing
n and the featured percentage).

Notes:
- Respondents missing the grouping variable's value are excluded from the grouped table;
  the dropped count is recorded in `frequency_manifest.json` under `grouped_tables`.
- Grouping variables must be single-answer columns. Multi-select grouping variables (and
  unknown columns) are skipped with a note in `grouping_warnings`.
- Multiple grouping variables in one spec (`"group_by": ["Q1.9", "Q2.4"]`) are supported;
  levels are the observed combinations of values.

## Table presentation options

Presentation is controlled per table spec (falling back to the question level, then
`defaults`). These options affect only how the report renders — every stat is always
computed and stored. The resolved options are written to `frequency_manifest.json` under
`table_presentation[slug]` so downstream reporting code can consume the same contract.

```jsonc
"QID16": {
  "tables": [
    { "group_by": ["Q1.9"],
      "show_code": false,          // hide the response-code column (default true)
      "orientation": "columns",    // group levels as columns (default) | "rows" (transpose)
      "overall": "after",          // add an Overall (ungrouped) column/row: false | "before" | "after"
      "response_total": "after",   // add a Total over response options: false | "before" | "after"
      "stats": ["n", "pct"]        // which stats to show per cell/column
    }
  ]
}
```

`stats` values: `n`, `valid_n`, `valid_pct`, `eligible_n`, `eligible_pct`, `total_n`,
`total_pct`, plus `pct` and `base_n` (aliases for the featured `report_base`'s percent and
count). When unset, flat tables show `n` + all three percents and crosstab cells show
`n` + the featured percent. `orientation`, `overall`, and `response_total` apply to grouped
tables; `overall` requires the question's ungrouped table to also be produced.
