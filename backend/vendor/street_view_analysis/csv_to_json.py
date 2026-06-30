import csv
import json
import os

INPUT_CSV = r"output\addresses_extracted_v2.csv"
OUTPUT_JSON = r"output\addresses_extracted.json"


def csv_to_json(csv_path, json_path):
    records = []

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            records.append({
                # existing fields (unchanged)
                "longitude": row["Longitude"],
                "latitude": row["Latitude"],
                "address": row["Address"],
                "city": row["City"],
                "postal_code": row["Postal Code"],
                "zip_code": row["Zip Code"],

                # new fields for confidence improvement
                "state": row["State"],
                "address_type": row["Address Type"],
                "house_number": row["House Number"],
                "street_address": row["Street Address"],
                "street_direction": row["Street Direction"],
                "street_suffix": row["Street Suffix"],
                "unit_number": row["Unit Number"],
            })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    return len(records)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    total = csv_to_json(INPUT_CSV, OUTPUT_JSON)
    print(f"Converted {total} records -> {OUTPUT_JSON}")