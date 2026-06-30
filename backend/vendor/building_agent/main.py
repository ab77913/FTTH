from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from src.agent import BuildingAgent
from src.feature_vector import NUMERIC_MEDIANS, build_features
from src.model import MODEL_PATH, StructureClassifier
from src.predict import run_prediction
from src.shap_explainer import get_shap_values
from src.validator import _top2_diff, validate_with_streetview


OUTPUT_PATH = Path("output_results.csv")
TRAINING_DATA = Path(__file__).resolve().parent / "data" / "sample" / "training_data.csv"


def _connect():
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise EnvironmentError(
            "DATABASE_URL environment variable is not set. "
            "Set it with: $env:DATABASE_URL = \"postgresql://postgres:PASSWORD@localhost:5432/buildings\" "
            "Or run with --offline to skip the database."
        )
    return psycopg2.connect(db_url)


def _offline_agent_output(row: pd.Series) -> dict[str, Any]:
    unit_count = row.get("unit_count")
    try:
        units = float(unit_count) if unit_count not in (None, "") else 1.0
    except (TypeError, ValueError):
        units = 1.0

    area_m2 = row.get("area_m2")
    if area_m2 in (None, ""):
        if units <= 1:
            area_m2 = 180.0
        elif units <= 15:
            area_m2 = 400.0
        else:
            area_m2 = 1000.0
    else:
        area_m2 = float(area_m2)

    elongation = row.get("elongation_ratio")
    elongation_ratio = float(elongation) if elongation not in (None, "") else 1.4

    distance = row.get("distance_m")
    distance_m = float(distance) if distance not in (None, "") else float(NUMERIC_MEDIANS["distance_m"])

    source = row.get("source") or "microsoft"

    return {
        "matched": True,
        "area_m2": area_m2,
        "elongation_ratio": elongation_ratio,
        "distance_m": distance_m,
        "source": source,
    }


def _ensure_model() -> StructureClassifier:
    if not MODEL_PATH.exists():
        if not TRAINING_DATA.exists():
            print(
                f"error: model not found at {MODEL_PATH}\n"
                f"Training data not found at {TRAINING_DATA}\n"
                "Run: python scripts/train_model.py",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"model not found — training from {TRAINING_DATA} ...")
        import subprocess

        train_script = Path(__file__).resolve().parent / "scripts" / "train_model.py"
        result = subprocess.run(
            [sys.executable, str(train_script), "--data", str(TRAINING_DATA)],
            check=False,
        )
        if not MODEL_PATH.exists():
            print("error: model training failed. Run: python scripts/train_model.py", file=sys.stderr)
            sys.exit(1)
    return StructureClassifier().load()


def _needs_streetview(ml_result: dict[str, Any]) -> bool:
    confidence = float(ml_result.get("confidence", 0.0))
    probs = ml_result.get("probs")
    return confidence < 0.75 or _top2_diff(probs) < 0.15


def _process_row(
    row: pd.Series,
    classifier: StructureClassifier,
    agent: BuildingAgent | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    lat = float(row["lat"])
    lon = float(row["lon"])
    address = str(row.get("address", ""))

    if offline:
        agent_output = _offline_agent_output(row)
    else:
        if agent is None:
            raise ValueError("agent is required when not in offline mode")
        agent_output = agent.process(lat, lon)

    external_data: dict[str, Any] = {
        key: row.get(key)
        for key in (
            "unit_count",
            "dpv_type",
            "owner_type",
            "year_built",
            "osm_building_tag",
        )
    }

    fv = build_features(agent_output, external_data)
    ml_result = run_prediction(fv)

    try:
        class_signals = get_shap_values(classifier, fv)
    except Exception:
        class_signals = []

    sv_result: dict[str, Any] = {}
    if _needs_streetview(ml_result):
        sv_result = validate_with_streetview(lat, lon, ml_result)

    sv_used = bool(sv_result.get("sv_used", False))
    final_class = sv_result.get("final_class") or ml_result.get("class")
    final_confidence = float(sv_result.get("confidence") or ml_result.get("confidence", 0.0))
    class_source = "ML+SV" if sv_used else "ML"

    return {
        "address": address,
        "lat": lat,
        "lon": lon,
        "area_m2": agent_output.get("area_m2"),
        "elongation_ratio": agent_output.get("elongation_ratio"),
        "structure_class": final_class,
        "confidence": final_confidence,
        "class_source": class_source,
        "sv_used": sv_used,
        "class_signals": json.dumps(class_signals),
    }


def run_pipeline(input_csv: Path, output_csv: Path, offline: bool = False) -> None:
    frame = pd.read_csv(input_csv)
    required = {"lat", "lon"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {', '.join(sorted(missing))}")

    agent = None
    if offline:
        print("offline mode: using CSV-only fallback features (no local footprint lookup)")
    else:
        print("local mode: using local footprint data (no PostGIS)")
        agent = BuildingAgent()

    classifier = _ensure_model()

    results: list[dict[str, Any]] = []

    for _, row in frame.iterrows():
        try:
            result = _process_row(row, classifier, agent=agent, offline=offline)
        except Exception as exc:
            result = {
                "address": str(row.get("address", "")),
                "lat": row.get("lat"),
                "lon": row.get("lon"),
                "area_m2": None,
                "elongation_ratio": None,
                "structure_class": "error",
                "confidence": 0.0,
                "class_source": "error",
                "sv_used": False,
                "class_signals": json.dumps([{"error": str(exc)}]),
            }
        results.append(result)

    output_frame = pd.DataFrame(results)
    output_frame.to_csv(output_csv, index=False)
    print(f"wrote {len(output_frame)} rows to {output_csv}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the end-to-end building intelligence pipeline.")
    parser.add_argument("--input", required=True, type=Path, help="Input CSV with address, lat, lon columns.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output CSV path.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run without PostGIS (uses CSV assessor fields to estimate building features).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    offline = args.offline
    run_pipeline(args.input, args.output, offline=offline)


if __name__ == "__main__":
    main()
