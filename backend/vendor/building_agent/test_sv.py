import json

from validator import validate_with_streetview


def main() -> None:
    lat = 37.4220
    lon = -122.0841

    ml_result = {
        "class": "MDU",
        "confidence": 0.65,
        "probs": {"SFU": 0.2, "MDU": 0.65, "MXU": 0.1, "ANCHOR": 0.05},
    }

    result = validate_with_streetview(lat, lon, ml_result)
    print(json.dumps(result, indent=2))

    if "sv_used" in result:
        print("PASS: sv_used")
    else:
        print("FAIL: sv_used")

    if "final_class" in result:
        print("PASS: final_class")
    else:
        print("FAIL: final_class")


if __name__ == "__main__":
    main()
