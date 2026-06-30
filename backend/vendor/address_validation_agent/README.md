# FTTH Address Validation Agent — Smarty + Melissa POC

This project implements a provider-agnostic FTTH address validation POC.
It extracts address records from CSV/KML/KMZ files, normalizes them, sends the same canonical address to both Smarty and Melissa providers, compares both results, scores confidence, and generates CSV/JSON/Excel reports.

## What this POC proves

- One common address pipeline can support multiple validation providers.
- Smarty and Melissa can be benchmarked on the same FTTH dataset.
- Differences in DPV, ZIP+4, vacancy, geocode precision, and unit handling can be reviewed.
- The final output can become the future input contract for parcel matching, structure classification, Street View validation, and network design.

## Folder structure

```text
src/
  extractors/       CSV/KML/KMZ extraction
  core/             parser, canonicalizer, scoring, comparison
  providers/        Smarty and Melissa adapters
  models/           shared dataclasses/schema
  utils/            logging, file helpers
run_pipeline.py     main batch runner
outputs/            generated reports
```

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_pipeline.py --input data/input --limit 50
```

By default the project runs in **mock provider mode**, so it works without API keys.
To use real APIs, set keys in `.env` and set:

```text
USE_MOCK_PROVIDERS=false
```

## Input files supported

- `.csv`
- `.kml`
- `.kmz`

## Main outputs

- `outputs/final_validation_results.csv`
- `outputs/final_validation_results.json`
- `outputs/smarty_melissa_comparison.xlsx`
- `outputs/exceptions.csv`

## Current POC recommendation

Start with `6BA8.csv`, run 20–50 records, then compare Smarty vs Melissa output quality.
