from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger
from tqdm import tqdm

from src.ml_classifier import MLClassifier

DEFAULT_CONFIG = "config/settings.example.yaml"


class BuildingAgent:
    """Footprint lookup adapter used by main.py (process → ML feature vector)."""

    def __init__(self, conn: Any = None, config_path: str = DEFAULT_CONFIG) -> None:
        from src.utils import load_config, setup_logger
        from src.loader import FootprintLoader
        from src.spatial import SpatialIndex
        from src.features import FeatureExtractor

        self._conn = conn
        self.config = load_config(config_path)
        log_cfg = self.config.get("log", {})
        setup_logger(level=log_cfg.get("level", "INFO"))

        self.loader = FootprintLoader(self.config)
        self.spatial = SpatialIndex(self.config)
        self.extractor = FeatureExtractor(self.config)

        if conn is not None:
            logger.warning(
                "PostGIS connection ignored — SpatialIndex uses auto-downloaded footprints."
            )

        self._index_ready = False

    def _ensure_index(self, lat: float, lon: float) -> None:
        if self._index_ready:
            return
        self.spatial.load_and_build([{"lat": lat, "lon": lon}])
        self._index_ready = True

    def process(self, lat: float, lon: float) -> dict[str, Any]:
        self._ensure_index(lat, lon)
        match = self.spatial.match(float(lat), float(lon))
        if not match:
            return {"matched": False}

        features = self.extractor.extract(match)
        return {
            "matched": True,
            "area_m2": features.get("footprint_area_m2"),
            "elongation_ratio": features.get("elongation_ratio"),
            "distance_m": features.get("footprint_match_distance_m"),
            "source": features.get("footprint_source") or match.get("source", "unknown"),
        }


class BuildingDataAgent:
    def __init__(self, config_path: str = DEFAULT_CONFIG):
        from src.utils import load_config
        from src.loader import FootprintLoader
        from src.spatial import SpatialIndex
        from src.features import FeatureExtractor
        from src.classifier import StructureHintClassifier

        self.config = load_config(config_path)

        self.loader = FootprintLoader(self.config)
        self.spatial = SpatialIndex(self.config)
        self.extractor = FeatureExtractor(self.config)
        self.classifier = StructureHintClassifier(self.config)
        self.ml_model = MLClassifier()

        # ✅ stats
        self.total = 0
        self.matched = 0
        self.distance_sum = 0
        self.class_counts = {}

        self._index_ready = False

    def _ensure_index(self, records: list[dict[str, Any]]) -> None:
        if self._index_ready:
            return
        self.spatial.load_and_build(records)
        self._index_ready = True

    # ---------------------------------------------------------
    # ✅ MAIN PIPELINE
    # ---------------------------------------------------------

    def enrich(self, address_record: dict) -> dict:

        lat = address_record["lat"]
        lon = address_record["lon"]

        self.total += 1
        result = dict(address_record)

        # ✅ MATCH
        match = self.spatial.match(lat, lon)

        if not match:
            result.update({
                "building_matched": False,
                "structure_hint": "UNRESOLVED",
                "hint_confidence": 0.0,
                "class_source": "NONE",
                "matched_radius_m": None
            })
            return result

        self.matched += 1

        # ✅ FEATURES
        features = self.extractor.extract(match)
        result.update(features)
        result["building_matched"] = True

        # ✅ MATCH INFO
        result["matched_radius_m"] = match.get("matched_radius_m")

        dist = features.get("footprint_match_distance_m", 0)
        self.distance_sum += dist

        # ✅ RULE ENGINE
        rule_result = self.classifier.classify(features)

        # Rule engine returns 0–100; the stub ML layer returns 0–1. Normalize
        # both to a 0–100 scale before applying the acceptance gate.
        rule_conf = float(rule_result.get("hint_confidence") or 0.0)
        if 0.0 < rule_conf <= 1.0:
            rule_conf *= 100.0

        if rule_conf >= 75.0:
            final = rule_result
            class_source = "RULE"
        else:
            ml_result = self.ml_model.predict(features)
            ml_conf = float(ml_result.get("confidence") or 0.0)
            if 0.0 < ml_conf <= 1.0:
                ml_conf *= 100.0

            if ml_conf >= 75.0:
                final = {
                    "structure_hint": ml_result["class"],
                    "hint_confidence": ml_conf,
                }
                class_source = "ML"
            else:
                final = {
                    "structure_hint": "UNRESOLVED",
                    "hint_confidence": 0.0
                }
                class_source = "HUMAN"

        # ✅ APPLY BASE CLASSIFICATION
        result.update(final)
        result["class_source"] = class_source

        # ✅ ADD FLOOR + UNIT COUNT
        result = self.extractor.add_post_classification(result)

        # ✅ 🔥 FIX: MDU → MDU_SMALL / MDU_LARGE
        
        structure = result.get("structure_hint")
        if structure in ["MDU", "MDU_SMALL", "MDU_LARGE"] and not result.get("unit_count"):
            area = result.get("footprint_area_m2", 0)
            hint = structure if structure in ["MDU_SMALL", "MDU_LARGE"] else "MDU_SMALL"
            if structure == "MDU_LARGE" or (
                structure == "MDU" and float(area or 0) > 550
            ):
                hint = "MDU_LARGE"
            result["unit_count"] = self.extractor.compute_unit_count(area, hint)

        # ✅ stats tracking (IMPORTANT: use FINAL class, not old one)
        final_class = result.get("structure_hint")
        self.class_counts[final_class] = self.class_counts.get(final_class, 0) + 1

        return result

    # ---------------------------------------------------------
    # ✅ BATCH
    # ---------------------------------------------------------

    def enrich_batch(self, records, workers=4):
        self._ensure_index(records)

        results = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self.enrich, r) for r in records]

            for f in tqdm(futures, desc="Enriching"):
                results.append(f.result())

        return results

    # ---------------------------------------------------------
    # ✅ STATS
    # ---------------------------------------------------------

    def get_stats(self):

        match_rate = self.matched / self.total if self.total else 0
        avg_distance = self.distance_sum / self.matched if self.matched else 0

        return {
            "total_processed": self.total,
            "matched": self.matched,
            "match_rate": round(match_rate, 3),
            "avg_distance_m": round(avg_distance, 2),
            "hint_distribution": self.class_counts
        }
