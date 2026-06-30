from __future__ import annotations

from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd


LABELS = ("SFU", "MDU", "MXU", "ANCHOR")
LABEL_TO_ID = {label: index for index, label in enumerate(LABELS)}
ID_TO_LABEL = {index: label for label, index in LABEL_TO_ID.items()}
MODEL_PATH = Path(__file__).resolve().parents[1] / "model.txt"


class StructureClassifier:
    def __init__(self, model_path: str | Path = MODEL_PATH, params: dict[str, Any] | None = None) -> None:
        self.model_path = Path(model_path)
        self.params = {
            "objective": "multiclass",
            "num_class": len(LABELS),
            "metric": "multi_logloss",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.9,
            "bagging_freq": 1,
            "verbose": -1,
        }
        if params:
            self.params.update(params)
        self.model: lgb.Booster | None = None

    def train(self, X: pd.DataFrame | list[dict[str, Any]], y: pd.Series | list[str], num_boost_round: int = 200) -> lgb.Booster:
        frame = self._frame(X)
        labels = np.array([LABEL_TO_ID[str(label)] for label in y], dtype=np.int32)
        dataset = lgb.Dataset(frame, label=labels, feature_name=list(frame.columns))
        self.model = lgb.train(self.params, dataset, num_boost_round=num_boost_round)
        self.save()
        return self.model

    def predict(self, X: pd.DataFrame | dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        probabilities = self.predict_proba(X)
        return [ID_TO_LABEL[int(index)] for index in np.argmax(probabilities, axis=1)]

    def predict_proba(self, X: pd.DataFrame | dict[str, Any] | list[dict[str, Any]]) -> np.ndarray:
        model = self._model()
        frame = self._frame(X)
        probabilities = model.predict(frame)
        return np.asarray(probabilities, dtype=float)

    def save(self, path: str | Path | None = None) -> None:
        if self.model is None:
            raise ValueError("No trained model to save")
        output_path = Path(path) if path else self.model_path
        self.model.save_model(str(output_path))

    def load(self, path: str | Path | None = None) -> "StructureClassifier":
        input_path = Path(path) if path else self.model_path
        self.model = lgb.Booster(model_file=str(input_path))
        return self

    def _model(self) -> lgb.Booster:
        if self.model is None:
            self.load()
        if self.model is None:
            raise ValueError("Model is not loaded")
        return self.model

    @staticmethod
    def _frame(X: pd.DataFrame | dict[str, Any] | list[dict[str, Any]]) -> pd.DataFrame:
        if isinstance(X, pd.DataFrame):
            return X
        if isinstance(X, dict):
            return pd.DataFrame([X])
        return pd.DataFrame(X)
