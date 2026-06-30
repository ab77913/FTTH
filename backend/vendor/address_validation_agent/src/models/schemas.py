from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List

@dataclass
class RawAddressRecord:
    source_file: str
    source_type: str
    row_id: str
    address_id: Optional[str]
    raw_address: str
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    network_node: Optional[str] = None
    terminal_id: Optional[str] = None
    qualified_desc: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CanonicalAddress:
    source_file: str
    row_id: str
    address_id: Optional[str]
    raw_address: str
    house_number: Optional[str]
    street_name: Optional[str]
    street_suffix: Optional[str]
    unit_type: Optional[str]
    unit_number: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str]
    country: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    network_node: Optional[str]
    terminal_id: Optional[str]
    normalized_full_address: str

@dataclass
class ProviderResult:
    provider: str
    success: bool
    standardized_address: Optional[str] = None
    dpv_match: Optional[str] = None
    zip_plus_4: Optional[str] = None
    vacant: Optional[bool] = None
    record_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geocode_precision: Optional[str] = None
    unit_detected: Optional[bool] = None
    lacs_status: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

@dataclass
class ComparisonResult:
    dpv_agree: bool
    zip4_agree: bool
    vacancy_agree: bool
    record_type_agree: bool
    geocode_distance_m: Optional[float]
    better_provider: str
    reason: str
    conflict_level: str

@dataclass
class FinalValidationRecord:
    source_file: str
    row_id: str
    address_id: Optional[str]
    raw_address: str
    canonical_address: str
    smarty_standardized_address: Optional[str]
    melissa_standardized_address: Optional[str]
    chosen_standardized_address: Optional[str]
    smarty_dpv: Optional[str]
    melissa_dpv: Optional[str]
    smarty_zip_plus_4: Optional[str]
    melissa_zip_plus_4: Optional[str]
    smarty_vacant: Optional[bool]
    melissa_vacant: Optional[bool]
    smarty_record_type: Optional[str]
    melissa_record_type: Optional[str]
    chosen_provider: str
    structure_hint: str
    confidence_score: int
    validation_status: str
    exception_reason: str
    comparison_reason: str
