from __future__ import annotations

from typing import Any

import numpy as np

from .model import LABELS, MODEL_PATH, StructureClassifier


class ModelNotTrainedError(FileNotFoundError):
    """Raised when model.txt does not exist and prediction is attempted."""

    def __init__(self) -> None:
        super().__init__(
            f"No trained model found at {MODEL_PATH}. "
            "Run 'python scripts/train_model.py' to train and save the model before running predictions."
        )


def run_prediction(feature_vector: dict[str, Any]) -> dict[str, Any]:
    if not MODEL_PATH.exists():
        raise ModelNotTrainedError()
    classifier = StructureClassifier().load()
    probabilities = classifier.predict_proba(feature_vector)[0]
    class_index = int(np.argmax(probabilities))
    probs = {label: float(probabilities[index]) for index, label in enumerate(LABELS)}

    return {
        "class": LABELS[class_index],
        "confidence": float(probabilities[class_index]),
        "probs": probs,
    }
