import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.feature_vector import build_features
from src.predict import run_prediction


def main() -> None:
    agent_output = {
        "area_m2": 500,
        "elongation_ratio": 1.8,
        "distance_m": 2.0,
        "source": "microsoft",
    }

    external_data = {
        "unit_count": 1,
        "dpv_type": "S",
        "owner_type": "individual",
        "year_built": 2005,
        "osm_building_tag": "residential",
    }

    features = build_features(agent_output, external_data)
    result = run_prediction(features)

    print("features:", json.dumps(features, indent=2))
    print("result:", json.dumps(result, indent=2))

    if "class" in result:
        print("PASS: class")
    else:
        print("FAIL: class")

    if "confidence" in result:
        print("PASS: confidence")
    else:
        print("FAIL: confidence")


if __name__ == "__main__":
    main()
