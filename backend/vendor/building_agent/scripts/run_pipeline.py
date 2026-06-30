"""CLI pipeline runner: read CSV/Excel -> full agent enrich -> write CSV + JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from loguru import logger

from src.agent import BuildingDataAgent
from src.utils import load_config


def _is_excel_file(path: str | Path) -> bool:
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls", ".xlsm"}:
        return True
    with path.open("rb") as fh:
        return fh.read(2) == b"PK"


def load_input_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)

    if _is_excel_file(path):
        try:
            return pd.read_excel(path)
        except ImportError as exc:
            raise RuntimeError(
                "Input file is Excel (.xlsx). Install openpyxl: pip install openpyxl"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Could not read Excel file: {path.name}") from exc

    read_kwargs = [
        {},
        {"encoding": "utf-8-sig"},
        {"encoding": "latin1"},
        {"sep": "|", "encoding": "latin1"},
        {"sep": "\t", "encoding": "utf-8-sig"},
    ]

    last_error: Exception | None = None
    for kwargs in read_kwargs:
        try:
            return pd.read_csv(path, **kwargs)
        except Exception as exc:
            last_error = exc

    hint = ""
    if path.suffix.lower() == ".csv":
        hint = " File may be Excel saved as .csv — rename to .xlsx or export as CSV."

    raise RuntimeError(f"Could not read input file: {path.name}.{hint}") from last_error


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    rename_map = {
        "latitude": "lat",
        "longitude": "lon",
        "lng": "lon",
        "long": "lon",
        "y": "lat",
        "x": "lon",
    }
    df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)

    if len(df.columns) == 1:
        logger.warning("Detected single column -> trying pipe split")
        col = df.columns[0]
        split_df = df[col].astype(str).str.split("|", expand=True)
        if split_df.shape[1] >= 2:
            split_df.columns = ["lat", "lon", "name"][: split_df.shape[1]]
            df = split_df

    for col in ("lat", "lon"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    before = len(df)
    df = df.dropna(subset=["lat", "lon"])
    dropped = before - len(df)
    if dropped:
        logger.warning(f"Dropped {dropped} rows with invalid lat/lon")

    return df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full building agent pipeline (match + features + SFU/MDU classification)."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input CSV or Excel with lat, lon.")
    parser.add_argument("--output", required=True, type=Path, help="Output CSV path (JSON written alongside).")
    parser.add_argument("--config", default="config/settings.example.yaml", help="Config YAML path.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel worker threads.")
    return parser.parse_args()


def write_outputs(results: list[dict], output_path: Path) -> tuple[Path, Path]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_path
    json_path = output_path.with_suffix(".json")

    if output_path.suffix.lower() == ".json":
        json_path = output_path
        csv_path = output_path.with_suffix(".csv")

    output_df = pd.DataFrame(results)

    for attempt in range(1, 10):
        try:
            output_df.to_csv(csv_path, index=False)
            break
        except PermissionError:
            stem = csv_path.stem
            csv_path = csv_path.with_name(f"{stem}_{attempt}{csv_path.suffix}")
            json_path = csv_path.with_suffix(".json")
            logger.warning(f"'{output_path}' locked — trying '{csv_path}'")
    else:
        raise RuntimeError("Could not write CSV — file is locked.")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    return csv_path, json_path


def main() -> None:
    args = parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    config = load_config(args.config)
    df = clean_dataframe(load_input_file(args.input))

    missing = {"lat", "lon"} - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")

    records = df.to_dict(orient="records")
    print(f"Processing {len(records)} records (full agent pipeline)")

    agent = BuildingDataAgent(config_path=args.config)
    results = agent.enrich_batch(records, workers=args.workers)

    csv_path, json_path = write_outputs(results, args.output)

    print(f"Wrote {len(results)} rows to {csv_path}")
    print(f"Wrote JSON to {json_path}")

    stats = agent.get_stats()
    print("\n--- Pipeline Stats ---")
    for key, val in stats.items():
        print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
