from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecordType(str, Enum):
    ADDRESS = "ADDRESS"
    NETWORK_ASSET = "NETWORK_ASSET"


class DispatchStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IngestionStatus(str, Enum):
    INGESTED = "INGESTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class RawExtractedRecord(BaseModel):
    """Record emitted by a file extractor before canonical mapping."""

    model_config = ConfigDict(extra="allow")

    source_file: str
    source_sheet: str | None = None
    source_layer: str | None = None
    row_number: int | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class CanonicalAddressRecord(BaseModel):
    """Provider-agnostic canonical FTTH address/service record."""

    record_id: UUID = Field(default_factory=uuid4)
    job_id: UUID | None = None
    record_type: RecordType = RecordType.ADDRESS

    raw_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None

    latitude: float | None = None
    longitude: float | None = None

    network_node: str | None = None
    terminal_id: str | None = None
    address_id: str | None = None
    customer_id: str | None = None

    source_file: str
    source_sheet: str | None = None
    source_layer: str | None = None
    source_row_number: int | None = None

    normalized_key: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("raw_address", "city", "state", "zip_code", "network_node", "terminal_id", "address_id")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("state")
    @classmethod
    def normalize_state(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("zip_code")
    @classmethod
    def normalize_zip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        if value.endswith(".0") and value.replace(".0", "").isdigit():
            value = value[:-2]
        return value or None

    @model_validator(mode="after")
    def normalize_coordinates(self) -> "CanonicalAddressRecord":
        if self.latitude is not None:
            self.latitude = float(self.latitude)
        if self.longitude is not None:
            self.longitude = float(self.longitude)
        return self


class IngestionResult(BaseModel):
    job_id: UUID
    source_file: str
    total_raw_records: int
    valid_records: int
    invalid_records: int
    duplicate_records: int
    stored_records: int
    queued_records: int
    status: IngestionStatus
    errors: list[str] = Field(default_factory=list)


class FlowAgentConfig(BaseModel):
    """Configuration for a single agent in a flow."""
    agent_name: str
    enabled: bool = True
    data_source: str = "database"  # "database" or "agent{N}_output" or "all_agents" (legacy: raw_input)
    confidence_thresholds: dict[str, int] = Field(default_factory=dict)  # "threshold_1", "threshold_2"
    order: int


class PipelineFlowTemplateSchema(BaseModel):
    """Pydantic schema for pipeline flow templates."""
    id: UUID | None = None
    name: str
    description: str | None = None
    is_active: bool = True
    created_by: str | None = None
    config: dict[str, Any]  # Full flow definition with agents list
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JobPipelineFlowSchema(BaseModel):
    """Pydantic schema for job pipeline flow association."""
    id: UUID | None = None
    job_id: UUID
    template_id: UUID
    flow_config: dict[str, Any]  # Snapshot at time of execution
    created_at: datetime | None = None
