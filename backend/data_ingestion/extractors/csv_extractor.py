from __future__ import annotations

from pathlib import Path

import pandas as pd

from data_ingestion.extractors.base import BaseExtractor
from data_ingestion.extractors.flat_kml_table import (
    extract_flattened_kml_table,
    looks_like_flattened_kml_table,
)
from data_ingestion.schemas import RawExtractedRecord
from data_ingestion.utils.strings import clean_value


class CSVExtractor(BaseExtractor):
    """Extract rows from CSV files."""

    def extract(self, file_path: str | Path) -> list[RawExtractedRecord]:
        path = Path(file_path)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = df.rename(columns={column: str(column).strip() for column in df.columns})

        if looks_like_flattened_kml_table(df):
            return extract_flattened_kml_table(df, source_file=path.name)

        records: list[RawExtractedRecord] = []
        for idx, row in df.iterrows():
            raw_data = {str(col): clean_value(row[col]) for col in df.columns}
            if not any(value is not None for value in raw_data.values()):
                continue
            records.append(
                RawExtractedRecord(
                    source_file=path.name,
                    row_number=int(idx) + 2,
                    raw_data=raw_data,
                )
            )
        return records
