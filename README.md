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

Run frequency analysis:

```bash
python -m qualtrics_pipeline.frequencies \
  --data runs/example/responses_clean.csv \
  --column-map runs/example/column_map.json \
  --config qualtrics_frequency_config.json \
  --outdir runs/example
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
