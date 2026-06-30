from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from data_ingestion.database.geo import set_address_geom
from data_ingestion.database.models import Address, DispatchQueue, IngestionJob, IngestionLog
from data_ingestion.schemas import CanonicalAddressRecord, DispatchStatus, IngestionStatus


class IngestionRepository:
    """Persistence operations for ingestion jobs and canonical records."""

    def __init__(self, session: Session):
        self.session = session

    def create_job(self, *, customer_id: str | None, source_file: str, row_count: int) -> IngestionJob:
        job = IngestionJob(
            customer_id=customer_id,
            source_file=source_file,
            row_count=row_count,
            status=IngestionStatus.PROCESSING.value,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def update_job_status(
        self,
        job_id: UUID,
        status: IngestionStatus,
        error_message: str | None = None,
    ) -> None:
        self.session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(status=status.value, error_message=error_message)
        )

    def update_job_row_count(self, job_id: UUID, row_count: int) -> None:
        """Persist the number of Address rows stored for this job (post-merge/dedupe)."""
        self.session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(row_count=max(0, int(row_count)))
        )

    def count_stored_addresses(self, job_id: UUID) -> int:
        from sqlalchemy import func, text

        return int(
            self.session.scalar(
                select(func.count()).select_from(Address).where(
                    Address.job_id == job_id,
                    text(
                        "COALESCE(raw_metadata->>'map_layer_only','false') != 'true'"
                        " AND COALESCE(raw_metadata->>'geometry_type','') NOT IN ('Polygon','LineString')"
                    ),
                )
            )
            or 0
        )

    def save_addresses(self, records: list[CanonicalAddressRecord]) -> list[Address]:
        saved: list[Address] = []
        for record in records:
            address = Address(
                record_uuid=record.record_id,
                job_id=record.job_id,
                customer_id=record.customer_id,
                raw_address=record.raw_address,
                city=record.city,
                state=record.state,
                zip_code=record.zip_code,
                latitude=record.latitude,
                longitude=record.longitude,
                source_raw_address=record.raw_address,
                source_latitude=record.latitude,
                source_longitude=record.longitude,
                network_node=record.network_node,
                terminal_id=record.terminal_id,
                address_id=record.address_id,
                normalized_key=record.normalized_key,
                source_file=record.source_file,
                source_sheet=record.source_sheet,
                source_layer=record.source_layer,
                source_row_number=record.source_row_number,
                validation_errors={"items": record.validation_errors},
                validation_warnings={"items": record.validation_warnings},
                raw_metadata=record.raw_metadata,
            )
            if record.latitude is not None and record.longitude is not None:
                set_address_geom(address, record.longitude, record.latitude)

            self.session.add(address)
            saved.append(address)

        self.session.flush()
        return saved

    def enqueue_addresses(self, addresses: list[Address]) -> int:
        count = 0
        for address in addresses:
            statement = (
                insert(DispatchQueue)
                .values(job_id=address.job_id, address_id=address.id, status=DispatchStatus.PENDING.value)
                .on_conflict_do_nothing(index_elements=["address_id"])
            )
            result = self.session.execute(statement)
            count += result.rowcount or 0
        return count

    def create_log(
        self,
        *,
        job_id: UUID,
        source_file: str,
        records_processed: int,
        records_valid: int,
        records_invalid: int,
        records_duplicate: int,
        status: IngestionStatus,
        message: str | None = None,
    ) -> IngestionLog:
        log = IngestionLog(
            job_id=job_id,
            source_file=source_file,
            records_processed=records_processed,
            records_valid=records_valid,
            records_invalid=records_invalid,
            records_duplicate=records_duplicate,
            status=status.value,
            message=message,
        )
        self.session.add(log)
        self.session.flush()
        return log


class DispatchRepository:
    """Persistence operations for sequential dispatcher."""

    def __init__(self, session: Session):
        self.session = session

    def get_pending_items(self, *, limit: int) -> list[DispatchQueue]:
        stmt: Select = (
            select(DispatchQueue)
            .where(DispatchQueue.status == DispatchStatus.PENDING.value)
            .order_by(DispatchQueue.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self.session.scalars(stmt).all())

    def get_address(self, address_id: int) -> Address | None:
        return self.session.get(Address, address_id)

    def mark_processing(self, item: DispatchQueue) -> None:
        item.status = DispatchStatus.PROCESSING.value
        item.attempts += 1
        self.session.flush()

    def mark_completed(self, item: DispatchQueue) -> None:
        item.status = DispatchStatus.COMPLETED.value
        item.last_error = None
        self.session.flush()

    def mark_failed(self, item: DispatchQueue, error: str) -> None:
        item.status = DispatchStatus.FAILED.value
        item.last_error = error
        self.session.flush()
