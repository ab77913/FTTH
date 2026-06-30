from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from data_ingestion.schemas import RawExtractedRecord


class BaseExtractor(ABC):
    """Common extractor interface."""

    @abstractmethod
    def extract(self, file_path: str | Path) -> list[RawExtractedRecord]:
        """Extract raw records from file_path."""
