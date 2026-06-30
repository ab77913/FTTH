from pathlib import Path
from typing import List
from src.models.schemas import RawAddressRecord
from src.extractors.csv_extractor import extract_csv
from src.extractors.kml_kmz_extractor import extract_kml_kmz


def load_records(input_path: str) -> List[RawAddressRecord]:
    path = Path(input_path)
    files = []
    if path.is_file():
        files = [path]
    else:
        files = list(path.glob("*.csv")) + list(path.glob("*.kml")) + list(path.glob("*.kmz"))
    all_records: List[RawAddressRecord] = []
    for file in files:
        if file.suffix.lower() == ".csv":
            all_records.extend(extract_csv(file))
        elif file.suffix.lower() in {".kml", ".kmz"}:
            all_records.extend(extract_kml_kmz(file))
    return all_records
