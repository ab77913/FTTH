from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from data_ingestion.extractors.base import BaseExtractor
from data_ingestion.extractors.kml_extractor import KMLExtractor
from data_ingestion.schemas import RawExtractedRecord


class KMZExtractor(BaseExtractor):
    """Extract KML Placemarks from compressed KMZ files."""

    def extract(self, file_path: str | Path) -> list[RawExtractedRecord]:
        path = Path(file_path)
        extractor = KMLExtractor()
        records: list[RawExtractedRecord] = []

        with ZipFile(path) as kmz:
            kml_names = [name for name in kmz.namelist() if name.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError(f"KMZ file '{path}' does not contain a .kml file")

            for kml_name in kml_names:
                content = kmz.read(kml_name)
                records.extend(
                    extractor.extract_from_bytes(content, source_file=f"{path.name}:{kml_name}")
                )

        return records
