import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from agent import BuildingAgent


def main() -> None:
    agent = BuildingAgent()
    lat = 37.4220
    lon = -122.0841

    result = agent.process(lat, lon)

    print(json.dumps(result, indent=2))

    if result.get("matched") is True:
        print("PASS: matched")
    else:
        print("FAIL: no match")

    if result.get("area_m2") is not None:
        print("PASS: area")
    else:
        print("FAIL: missing area")

    if result.get("structure_hint"):
        print("PASS: classification")
    else:
        print("FAIL: classification missing")


if __name__ == "__main__":
    main()
