"""Train the LightGBM structure classifier and save model.txt.

Usage:
    python scripts/train_model.py
    python scripts/train_model.py --data data/sample/training_data.csv
    python scripts/train_model.py --data my_labeled_data.csv --rounds 400 --output model.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.feature_vector import build_features
from src.model import LABELS, MODEL_PATH, StructureClassifier


FEATURE_COLUMNS = (
    "area_m2",
    "elongation_ratio",
    "distance_m",
    "unit_count",
    "dpv_type",
    "owner_type",
    "year_built",
    "osm_building_tag",
)
LABEL_COLUMN = "label"
DEFAULT_DATA = ROOT_DIR / "data" / "sample" / "training_data.csv"


def _row_to_agent_output(row: pd.Series) -> dict:
    return {
        "area_m2": row.get("area_m2"),
        "elongation_ratio": row.get("elongation_ratio"),
        "distance_m": row.get("distance_m"),
    }


def _row_to_external_data(row: pd.Series) -> dict:
    return {
        "unit_count": row.get("unit_count"),
        "dpv_type": row.get("dpv_type"),
        "owner_type": row.get("owner_type"),
        "year_built": row.get("year_built"),
        "osm_building_tag": row.get("osm_building_tag"),
    }


def build_dataset(frame: pd.DataFrame) -> tuple[list[dict], list[str]]:
    X, y = [], []
    for _, row in frame.iterrows():
        fv = build_features(_row_to_agent_output(row), _row_to_external_data(row))
        X.append(fv)
        y.append(str(row[LABEL_COLUMN]))
    return X, y


def validate_labels(y: list[str]) -> None:
    valid = set(LABELS)
    invalid = sorted({label for label in y if label not in valid})
    if invalid:
        raise ValueError(
            f"Training data contains unknown labels: {invalid}. "
            f"Allowed labels: {sorted(valid)}"
        )


def evaluate(classifier: StructureClassifier, X_val: list[dict], y_val: list[str]) -> float:
    import numpy as np

    predictions = classifier.predict(X_val)
    correct = sum(p == t for p, t in zip(predictions, y_val))
    accuracy = correct / len(y_val) if y_val else 0.0
    print(f"validation_accuracy={accuracy:.4f}  ({correct}/{len(y_val)} correct)")

    from collections import Counter
    confusion: dict[str, dict[str, int]] = {}
    for pred, true in zip(predictions, y_val):
        confusion.setdefault(true, Counter())[pred] += 1
    print("\nConfusion matrix (true -> predicted counts):")
    for true_label in sorted(LABELS):
        if true_label in confusion:
            row_str = "  ".join(
                f"{pred}:{count}"
                for pred, count in sorted(confusion[true_label].items())
            )
            print(f"  {true_label:<12} -> {row_str}")
    return accuracy


def train(data_path: Path, model_path: Path, num_boost_round: int, val_split: float) -> None:
    print(f"loading training data from {data_path}")
    frame = pd.read_csv(data_path)

    missing_cols = {LABEL_COLUMN} - set(frame.columns)
    if missing_cols:
        raise ValueError(f"Training CSV is missing required column: {', '.join(missing_cols)}")

    X, y = build_dataset(frame)
    validate_labels(y)

    if val_split > 0.0:
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=val_split, random_state=42, stratify=y
        )
        print(f"train_samples={len(X_train)}  val_samples={len(X_val)}")
    else:
        X_train, y_train = X, y
        X_val, y_val = [], []
        print(f"train_samples={len(X_train)}  (no validation split)")

    print(f"training LightGBM  num_boost_round={num_boost_round}")
    classifier = StructureClassifier(model_path=model_path)
    classifier.train(X_train, y_train, num_boost_round=num_boost_round)
    print(f"model saved to {model_path}")

    if X_val:
        evaluate(classifier, X_val, y_val)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the building structure classifier.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Labeled CSV training file.")
    parser.add_argument("--output", type=Path, default=MODEL_PATH, help="Path to save model.txt.")
    parser.add_argument("--rounds", type=int, default=200, help="LightGBM boosting rounds.")
    parser.add_argument("--val-split", type=float, default=0.2, help="Fraction of data held out for validation (0 to disable).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.data.exists():
        print(f"error: training data not found: {args.data}", file=sys.stderr)
        sys.exit(1)
    train(
        data_path=args.data,
        model_path=args.output,
        num_boost_round=args.rounds,
        val_split=args.val_split,
    )


if __name__ == "__main__":
    main()
