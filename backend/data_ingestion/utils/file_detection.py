from __future__ import annotations

from enum import Enum
from pathlib import Path


class FileType(str, Enum):
    CSV = "csv"
    EXCEL = "excel"
    KML = "kml"
    KMZ = "kmz"


SUPPORTED_SUFFIXES = {
    ".csv": FileType.CSV,
    ".xlsx": FileType.EXCEL,
    ".xls": FileType.EXCEL,
    ".kml": FileType.KML,
    ".kmz": FileType.KMZ,
}


def detect_file_type(path: str | Path) -> FileType:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    try:
        return SUPPORTED_SUFFIXES[suffix]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"Unsupported file type '{suffix}'. Supported types: {supported}") from exc
