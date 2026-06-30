class MLClassifier:
    def predict(self, features: dict) -> dict:
        """Dummy ML layer (replace later with LightGBM)"""

        area = features.get("footprint_area_m2", 0)

        if area > 1500:
            return {"class": "MDU", "confidence": 0.85}
        elif area < 500:
            return {"class": "SFU", "confidence": 0.80}
        else:
            return {"class": "MDU", "confidence": 0.60}
