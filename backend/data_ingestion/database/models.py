from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from data_ingestion.database.geo import postgis_enabled

if postgis_enabled():
    from geoalchemy2 import Geometry


class Base(DeclarativeBase):
    pass


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[object] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="INGESTED", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    addresses: Mapped[list["Address"]] = relationship(back_populates="job")


def _address_table_args() -> tuple:
    args: list = [
        UniqueConstraint("job_id", "normalized_key", name="uq_addresses_job_normalized_key"),
        Index("ix_addresses_job_id", "job_id"),
        Index("ix_addresses_address_id", "address_id"),
        Index("ix_addresses_terminal_id", "terminal_id"),
    ]
    if postgis_enabled():
        args.append(Index("ix_addresses_geom", "geom", postgresql_using="gist"))
    return tuple(args)


class Address(Base):
    __tablename__ = "addresses"
    __table_args__ = _address_table_args()

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_uuid: Mapped[object] = mapped_column(UUID(as_uuid=True), default=uuid4, nullable=False)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    raw_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Original values from upload (frozen on first coord/address validation)
    source_raw_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Verified / corrected values after comparing address vs coordinates
    validated_raw_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_street_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_postcode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validated_city_state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validated_country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    validated_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    validated_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    coord_address_match_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    coord_address_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    coord_address_validation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reverse_geocode_confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    network_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    terminal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_sheet: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_layer: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    validation_errors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_warnings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    job: Mapped[IngestionJob] = relationship(back_populates="addresses")


def _network_asset_table_args() -> tuple:
    if postgis_enabled():
        return (Index("ix_network_assets_geometry", "geometry", postgresql_using="gist"),)
    return ()


class NetworkAsset(Base):
    __tablename__ = "network_assets"
    __table_args__ = _network_asset_table_args()

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object | None] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"))
    asset_type: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class UploadedSourceTable(Base):
    """Logical per-upload-file table registry.

    We avoid creating arbitrary physical SQL table names from user filenames,
    but every uploaded file still gets a separate logical table keyed by a
    stable sanitized name and backed by JSON records.
    """

    __tablename__ = "uploaded_source_tables"
    __table_args__ = (
        UniqueConstraint("job_id", "table_name", name="uq_uploaded_source_tables_job_table"),
        Index("ix_uploaded_source_tables_job_id", "job_id"),
        Index("ix_uploaded_source_tables_batch_id", "upload_batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    upload_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stored_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class UploadedSourceRecord(Base):
    """Rows extracted from one uploaded source file before/alongside merging."""

    __tablename__ = "uploaded_source_records"
    __table_args__ = (
        Index("ix_uploaded_source_records_table_id", "source_table_id"),
        Index("ix_uploaded_source_records_job_id", "job_id"),
        Index("ix_uploaded_source_records_address_id", "address_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_table_id: Mapped[int] = mapped_column(Integer, ForeignKey("uploaded_source_tables.id"), nullable=False)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    address_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    canonical_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    merge_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    merge_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object | None] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"))
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    records_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_invalid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_duplicate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class DispatchQueue(Base):
    __tablename__ = "dispatch_queue"
    __table_args__ = (
        Index("ix_dispatch_status_created_at", "status", "created_at"),
        UniqueConstraint("address_id", name="uq_dispatch_address_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    address_id: Mapped[int] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class AgentTable(Base):
    """Registry of agent teams and their per-field color rules for map visualisation."""

    __tablename__ = "agent_tables"
    __table_args__ = (UniqueConstraint("agent_name", name="uq_agent_tables_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # color_rules: [{"field":"status","value":"qualified","color":"#22c55e","label":"Qualified"}, ...]
    color_rules: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    results: Mapped[list["AgentResult"]] = relationship(
        back_populates="agent_table", cascade="all, delete-orphan"
    )


class Agent1Result(Base):
    """Stores Address Validation Agent 1 (Smarty + Melissa) results per address."""

    __tablename__ = "agent1_results"
    __table_args__ = (
        UniqueConstraint("address_id", name="uq_agent1_results_address_id"),
        Index("ix_agent1_results_job_id", "job_id"),
        Index("ix_agent1_results_address_id", "address_id"),
        Index("ix_agent1_results_validation_status", "validation_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    address_id: Mapped[int] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=False)

    # Raw + canonical
    raw_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Smarty output
    smarty_standardized_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    smarty_dpv: Mapped[str | None] = mapped_column(String(10), nullable=True)
    smarty_zip_plus_4: Mapped[str | None] = mapped_column(String(20), nullable=True)
    smarty_vacant: Mapped[bool | None] = mapped_column(nullable=True)
    smarty_record_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    smarty_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    smarty_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Melissa output
    melissa_standardized_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    melissa_dpv: Mapped[str | None] = mapped_column(String(10), nullable=True)
    melissa_zip_plus_4: Mapped[str | None] = mapped_column(String(20), nullable=True)
    melissa_vacant: Mapped[bool | None] = mapped_column(nullable=True)
    melissa_record_type: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Final chosen result
    chosen_standardized_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    chosen_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    structure_hint: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exception_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparison_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full output JSON — mirrors all typed columns in one blob for consistency with agents 2-6
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Agent0HouseDiscoveryResult(Base):
    """Typed discovery rows emitted by Agent 0 before accepted households enter addresses."""

    __tablename__ = "agent0_house_discovery_results"
    __table_args__ = (
        Index("ix_agent0_house_discovery_job_id", "job_id"),
        Index("ix_agent0_house_discovery_address_id", "address_id"),
        Index("ix_agent0_house_discovery_polygon_address_id", "polygon_address_id"),
        Index("ix_agent0_house_discovery_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    address_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=True)
    polygon_address_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=True)
    polygon_source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    reverse_geocoded_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    reverse_geocoded_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    reverse_geocoded_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dedupe_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class AgentResult(Base):
    """Stores one agent team's processed output per address record."""

    __tablename__ = "agent_results"
    __table_args__ = (
        UniqueConstraint("agent_name", "address_id", name="uq_agent_results_agent_address"),
        Index("ix_agent_results_agent_name", "agent_name"),
        Index("ix_agent_results_job_id", "job_id"),
        Index("ix_agent_results_address_id", "address_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(
        String(100), ForeignKey("agent_tables.agent_name", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    address_id: Mapped[int] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    agent_table: Mapped["AgentTable"] = relationship(back_populates="results")


class AddressResult(Base):
    """
    Street View / satellite analysis per address (Agent 5).
    Mirrors reference google_street_view_analysis pipeline storage.
    """

    __tablename__ = "address_results"
    __table_args__ = (
        UniqueConstraint("address_id", name="uq_address_results_address_id"),
        Index("ix_address_results_job_id", "job_id"),
        Index("ix_address_results_lat_lon", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    address_id: Mapped[int] = mapped_column(Integer, ForeignKey("addresses.id"), nullable=False)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    imagery_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    structure_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visible_units_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visible_units_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    multiple_entrances: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multiple_mailboxes: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    commercial_signage: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    under_construction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    image_quality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_vision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    images: Mapped[list["AddressImage"]] = relationship(
        back_populates="result",
        cascade="all, delete-orphan",
    )


class AddressImage(Base):
    """Imagery blobs linked to an address_results row."""

    __tablename__ = "address_images"
    __table_args__ = (Index("ix_address_images_result_id", "result_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("address_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    image_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    result: Mapped["AddressResult"] = relationship(back_populates="images")


class PipelineFlowTemplate(Base):
    """Reusable pipeline flow configuration template."""
    __tablename__ = "pipeline_flow_templates"
    __table_args__ = (
        Index("ix_pipeline_flow_templates_is_active", "is_active"),
    )
    
    id: Mapped[object] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)  # Full flow definition
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class JobPipelineFlow(Base):
    """Associates a job with the flow configuration used at execution time (audit trail)."""
    __tablename__ = "job_pipeline_flows"
    __table_args__ = (
        Index("ix_job_pipeline_flows_job_id", "job_id"),
        Index("ix_job_pipeline_flows_template_id", "template_id"),
    )
    
    id: Mapped[object] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    template_id: Mapped[object] = mapped_column(UUID(as_uuid=True), ForeignKey("pipeline_flow_templates.id"), nullable=False)
    flow_config: Mapped[dict] = mapped_column(JSON, nullable=False)  # Snapshot of template at execution time
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


if postgis_enabled():
    Address.geom = mapped_column(  # type: ignore[attr-defined]
        Geometry(geometry_type="POINT", srid=4326),
        nullable=True,
    )
    NetworkAsset.geometry = mapped_column(  # type: ignore[attr-defined]
        Geometry(srid=4326),
        nullable=True,
    )
