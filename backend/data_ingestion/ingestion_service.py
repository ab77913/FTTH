from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from data_ingestion.database.repositories import IngestionRepository
from data_ingestion.database.models import UploadedSourceRecord, UploadedSourceTable
from data_ingestion.extractors import CSVExtractor, ExcelExtractor, KMLExtractor, KMZExtractor
from data_ingestion.extractors.base import BaseExtractor
from data_ingestion.parsers import CanonicalMapper
from data_ingestion.schemas import CanonicalAddressRecord, IngestionResult, IngestionStatus, RawExtractedRecord
from data_ingestion.utils.csv_kmz_merge import apply_csv_kmz_merge
from data_ingestion.utils.file_detection import FileType, detect_file_type
from data_ingestion.utils.kml_categories import records_for_storage
from data_ingestion.utils.strings import normalize_duplicate_address_key
from data_ingestion.validators import validate_and_deduplicate

logger = logging.getLogger(__name__)


class IngestionService:
    """End-to-end orchestration for the ingestion pipeline."""

    def __init__(self, repository: IngestionRepository):
        self.repository = repository
        self.mapper = CanonicalMapper()

    def ingest_file(self, file_path: str | Path, *, customer_id: str | None = None) -> IngestionResult:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Input file does not exist: {path}")

        logger.info("Starting ingestion for %s", path)
        extractor = self._get_extractor(path)
        raw_records = extractor.extract(path)
        logger.info("Extracted %s raw records from %s", len(raw_records), path.name)
        job = self.repository.create_job(
            customer_id=customer_id,
            source_file=path.name,
            row_count=len(raw_records),
        )

        try:
            canonical_records = self.mapper.map_records(raw_records, customer_id=customer_id)
            for record in canonical_records:
                record.job_id = job.id

            kmz_reference_count = len(canonical_records) - len(records_for_storage(canonical_records))
            if kmz_reference_count:
                logger.info(
                    "Discarding %d KMZ/KML reference row(s) after extraction for %s",
                    kmz_reference_count,
                    path.name,
                )
            canonical_records = records_for_storage(canonical_records)

            validation_summary = validate_and_deduplicate(canonical_records)

            # Store ALL records (valid + invalid + duplicate) so they appear in the UI
            # Clear normalized_key on duplicates/invalid to avoid unique constraint violations
            for rec in validation_summary.duplicate_records:
                rec.normalized_key = None
            for rec in validation_summary.invalid_records:
                rec.normalized_key = None
            all_records = (
                validation_summary.valid_records
                + validation_summary.invalid_records
                + validation_summary.duplicate_records
            )
            saved_addresses = self.repository.save_addresses(all_records)
            self.repository.update_job_row_count(job.id, len(all_records))
            # Only enqueue valid records for downstream dispatch
            valid_saved = saved_addresses[:validation_summary.valid_count]
            queued_count = self.repository.enqueue_addresses(valid_saved)

            status = IngestionStatus.COMPLETED
            if validation_summary.invalid_count > 0 or validation_summary.duplicate_count > 0:
                status = IngestionStatus.PARTIAL

            self.repository.update_job_status(job.id, status)
            self.repository.create_log(
                job_id=job.id,
                source_file=path.name,
                records_processed=len(raw_records),
                records_valid=validation_summary.valid_count,
                records_invalid=validation_summary.invalid_count,
                records_duplicate=validation_summary.duplicate_count,
                status=status,
            )

            return IngestionResult(
                job_id=job.id,
                source_file=path.name,
                total_raw_records=len(raw_records),
                valid_records=validation_summary.valid_count,
                invalid_records=validation_summary.invalid_count,
                duplicate_records=validation_summary.duplicate_count,
                stored_records=len(all_records),
                queued_records=queued_count,
                status=status,
            )
        except Exception as exc:  # noqa: BLE001 - capture failure in job log
            logger.exception("Ingestion failed for %s", path)
            self.repository.update_job_status(job.id, IngestionStatus.FAILED, error_message=str(exc))
            self.repository.create_log(
                job_id=job.id,
                source_file=path.name,
                records_processed=len(raw_records),
                records_valid=0,
                records_invalid=0,
                records_duplicate=0,
                status=IngestionStatus.FAILED,
                message=str(exc),
            )
            raise

    def ingest_files_grouped(
        self,
        file_paths: list[str | Path],
        *,
        customer_id: str | None = None,
        source_file: str | None = None,
        upload_batch_id: str | None = None,
    ) -> IngestionResult:
        """Ingest multiple related files as one job and annotate merge status.

        This keeps CSV/Excel/KML/KMZ rows together for one pipeline run while
        preserving each row's original source file/type in raw_metadata.
        """
        paths = [Path(p) for p in file_paths]
        if not paths:
            raise ValueError("No files supplied for grouped ingestion")
        for path in paths:
            if not path.exists():
                raise FileNotFoundError(f"Input file does not exist: {path}")

        batch_id = upload_batch_id or uuid4().hex
        raw_records: list[RawExtractedRecord] = []
        file_summaries: list[dict[str, Any]] = []

        for file_index, path in enumerate(paths, start=1):
            extractor = self._get_extractor(path)
            extracted = extractor.extract(path)
            source_format = path.suffix.lower().lstrip(".") or "unknown"
            file_role = "geospatial" if source_format in {"kml", "kmz"} else "tabular"
            file_summaries.append({
                "file_name": path.name,
                "source_format": source_format,
                "file_role": file_role,
                "record_count": len(extracted),
            })
            for rec in extracted:
                raw = dict(rec.raw_data or {})
                raw.update({
                    "upload_batch_id": batch_id,
                    "upload_file_index": file_index,
                    "original_source_file": path.name,
                    "source_format": source_format,
                    "file_role": file_role,
                    "upload_group_source_files": [p.name for p in paths],
                })
                rec.raw_data = raw
                rec.source_file = path.name
                raw_records.append(rec)

        grouped_source = source_file or " + ".join(path.name for path in paths)
        job = self.repository.create_job(
            customer_id=customer_id,
            source_file=grouped_source,
            row_count=len(raw_records),
        )

        try:
            canonical_records = self.mapper.map_records(raw_records, customer_id=customer_id)
            for record in canonical_records:
                record.job_id = job.id

            self._annotate_grouped_merge(canonical_records, batch_id=batch_id, file_summaries=file_summaries)

            kmz_reference_count = len(canonical_records) - len(records_for_storage(canonical_records))
            if kmz_reference_count:
                logger.info(
                    "Skipping %d KMZ row(s) absorbed into matching CSV records for batch %s",
                    kmz_reference_count,
                    batch_id,
                )
            canonical_records = records_for_storage(canonical_records)

            validation_summary = validate_and_deduplicate(canonical_records)
            for rec in validation_summary.duplicate_records:
                rec.normalized_key = None
            for rec in validation_summary.invalid_records:
                rec.normalized_key = None
            all_records = (
                validation_summary.valid_records
                + validation_summary.invalid_records
                + validation_summary.duplicate_records
            )
            saved_addresses = self.repository.save_addresses(all_records)
            self.repository.update_job_row_count(job.id, len(all_records))
            self._save_grouped_source_tables(
                all_records,
                saved_addresses,
                batch_id=batch_id,
                file_summaries=file_summaries,
                job_id=job.id,
            )
            valid_saved = saved_addresses[:validation_summary.valid_count]
            queued_count = self.repository.enqueue_addresses(valid_saved)

            status = IngestionStatus.COMPLETED
            if validation_summary.invalid_count > 0 or validation_summary.duplicate_count > 0:
                status = IngestionStatus.PARTIAL

            self.repository.update_job_status(job.id, status)
            self.repository.create_log(
                job_id=job.id,
                source_file=grouped_source,
                records_processed=len(raw_records),
                records_valid=validation_summary.valid_count,
                records_invalid=validation_summary.invalid_count,
                records_duplicate=validation_summary.duplicate_count,
                status=status,
                message=f"grouped_upload batch={batch_id} files={len(paths)}",
            )

            return IngestionResult(
                job_id=job.id,
                source_file=grouped_source,
                total_raw_records=len(raw_records),
                valid_records=validation_summary.valid_count,
                invalid_records=validation_summary.invalid_count,
                duplicate_records=validation_summary.duplicate_count,
                stored_records=len(all_records),
                queued_records=queued_count,
                status=status,
            )
        except Exception as exc:
            logger.exception("Grouped ingestion failed for %s", grouped_source)
            self.repository.update_job_status(job.id, IngestionStatus.FAILED, error_message=str(exc))
            self.repository.create_log(
                job_id=job.id,
                source_file=grouped_source,
                records_processed=len(raw_records),
                records_valid=0,
                records_invalid=0,
                records_duplicate=0,
                status=IngestionStatus.FAILED,
                message=str(exc),
            )
            raise

    def _save_grouped_source_tables(
        self,
        records: list[CanonicalAddressRecord],
        saved_addresses: list[Any],
        *,
        batch_id: str,
        file_summaries: list[dict[str, Any]],
        job_id: Any,
    ) -> None:
        summaries = {item["file_name"]: item for item in file_summaries}
        by_file: dict[str, list[tuple[CanonicalAddressRecord, Any]]] = {}
        for rec, addr in zip(records, saved_addresses, strict=False):
            source = rec.source_file
            by_file.setdefault(source, []).append((rec, addr))

        used_table_names: dict[str, int] = {}
        for source_file, items in by_file.items():
            summary = summaries.get(source_file, {})
            base_table_name = _safe_table_name(source_file)
            used_table_names[base_table_name] = used_table_names.get(base_table_name, 0) + 1
            table_name = base_table_name if used_table_names[base_table_name] == 1 else f"{base_table_name}_{used_table_names[base_table_name]}"
            table = UploadedSourceTable(
                job_id=job_id,
                upload_batch_id=batch_id,
                source_file=source_file,
                table_name=table_name,
                source_format=summary.get("source_format") or Path(source_file).suffix.lower().lstrip("."),
                file_role=summary.get("file_role"),
                stored_path=(items[0][0].raw_metadata or {}).get("stored_path", ""),
                record_count=len(items),
            )
            self.repository.session.add(table)
            self.repository.session.flush()

            for rec, addr in items:
                meta = rec.raw_metadata or {}
                self.repository.session.add(UploadedSourceRecord(
                    source_table_id=table.id,
                    job_id=job_id,
                    address_id=getattr(addr, "id", None),
                    row_number=rec.source_row_number,
                    raw_data=meta,
                    canonical_data={
                        "raw_address": rec.raw_address,
                        "city": rec.city,
                        "state": rec.state,
                        "zip_code": rec.zip_code,
                        "latitude": rec.latitude,
                        "longitude": rec.longitude,
                        "network_node": rec.network_node,
                        "terminal_id": rec.terminal_id,
                        "address_id": rec.address_id,
                    },
                    merge_status=meta.get("merge_status"),
                    merge_color=meta.get("merge_color"),
                ))

    def _annotate_grouped_merge(
        self,
        records: list[CanonicalAddressRecord],
        *,
        batch_id: str,
        file_summaries: list[dict[str, Any]],
    ) -> None:
        """Classify grouped CSV/KMZ rows using normalized address-only matching."""
        apply_csv_kmz_merge(records)

        storage_records = records_for_storage(records)
        storage_record_ids = {id(rec) for rec in storage_records}
        canonical_by_key: dict[str, tuple[int, CanonicalAddressRecord]] = {}
        duplicate_to_canonical: dict[int, tuple[int, CanonicalAddressRecord, str]] = {}

        for idx, rec in enumerate(storage_records):
            key = normalize_duplicate_address_key(rec.raw_address)
            if not key:
                continue
            canonical = canonical_by_key.get(key)
            if canonical is None:
                canonical_by_key[key] = (idx, rec)
                continue
            duplicate_to_canonical[id(rec)] = (canonical[0], canonical[1], key)

        for rec in storage_records:
            duplicate = duplicate_to_canonical.get(id(rec))
            if duplicate is None:
                continue
            canonical_idx, canonical_rec, key = duplicate
            distance_m = _distance_between_records_m(rec, canonical_rec)
            meta = dict(rec.raw_metadata or {})
            meta.update({
                "upload_batch_id": batch_id,
                "upload_file_summaries": file_summaries,
                "record_status": "DUPLICATE",
                "canonical_record_id": str(canonical_rec.record_id),
                "canonical_record_index": canonical_idx,
                "canonical_source_file": canonical_rec.source_file,
                "canonical_source_row_number": canonical_rec.source_row_number,
                "canonical_raw_address": canonical_rec.raw_address,
                "duplicate_reason": "Normalized address match",
                "duplicate_normalized_address": key,
                "distance_to_canonical_metres": distance_m,
                "merge_status": "duplicate",
                "merge_color": "white",
                "merge_reason": "Duplicate address in uploaded raw data",
                "merge_match_type": "normalized_address",
                "merge_classification": {
                    "status": "duplicate",
                    "color": "white",
                    "reason": "Duplicate address in uploaded raw data",
                    "match_type": "normalized_address",
                    "canonical_record_id": str(canonical_rec.record_id),
                    "canonical_source_file": canonical_rec.source_file,
                    "canonical_source_row_number": canonical_rec.source_row_number,
                    "normalized_address": key,
                    "distance_to_canonical_metres": distance_m,
                },
            })
            rec.raw_metadata = meta

        duplicate_record_ids = set(duplicate_to_canonical)
        for rec in records:
            if id(rec) in duplicate_record_ids:
                continue
            meta = dict(rec.raw_metadata or {})
            meta.setdefault("upload_batch_id", batch_id)
            meta.setdefault("upload_file_summaries", file_summaries)
            role = str(meta.get("file_role") or "").lower()
            source_format = str(meta.get("source_format") or Path(rec.source_file or "").suffix.lstrip(".")).lower()
            if not meta.get("address_source"):
                if role == "tabular" or source_format in {"csv", "xlsx", "xls", "excel"}:
                    meta["address_source"] = "csv"
                elif role == "geospatial" or source_format in {"kmz", "kml"}:
                    meta["address_source"] = "kmz"
            if id(rec) in storage_record_ids:
                meta.setdefault("record_status", "UNIQUE")
            rec.raw_metadata = meta
    def _get_extractor(self, path: Path) -> BaseExtractor:
        file_type = detect_file_type(path)
        if file_type == FileType.CSV:
            return CSVExtractor()
        if file_type == FileType.EXCEL:
            return ExcelExtractor()
        if file_type == FileType.KML:
            return KMLExtractor()
        if file_type == FileType.KMZ:
            return KMZExtractor()
        raise ValueError(f"No extractor available for file type: {file_type}")


def _safe_table_name(source_file: str) -> str:
    stem = Path(source_file).stem.lower()
    safe = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return f"src_{safe or 'file'}"


def _distance_between_records_m(left: CanonicalAddressRecord, right: CanonicalAddressRecord) -> float | None:
    if left.latitude is None or left.longitude is None or right.latitude is None or right.longitude is None:
        return None
    lat1 = float(left.latitude)
    lon1 = float(left.longitude)
    lat2 = float(right.latitude)
    lon2 = float(right.longitude)
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return round(radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 3)

