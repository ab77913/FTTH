from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import shap


def get_shap_values(model, feature_vector: dict[str, Any]) -> list[dict[str, float | str]]:
    frame = pd.DataFrame([feature_vector])
    booster = getattr(model, "model", model)
    explainer = shap.TreeExplainer(booster)
    values = explainer.shap_values(frame)

    if isinstance(values, list):
        probabilities = np.asarray(booster.predict(frame))[0]
        class_index = int(np.argmax(probabilities))
        impacts = np.asarray(values[class_index][0], dtype=float)
    else:
        array = np.asarray(values, dtype=float)
        impacts = array[0] if array.ndim == 2 else array[0, :, int(np.argmax(np.abs(array[0]).sum(axis=0)))]

    top_indices = np.argsort(np.abs(impacts))[-3:][::-1]
    return [
        {
            "feature": str(frame.columns[index]),
            "impact": float(impacts[index]),
        }
        for index in top_indices
    ]
