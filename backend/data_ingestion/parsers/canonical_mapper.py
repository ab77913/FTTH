from __future__ import annotations

import re
from typing import Any

from data_ingestion.schemas import CanonicalAddressRecord, RawExtractedRecord
from data_ingestion.utils.strings import (
    clean_value,
    normalize_address_key,
    normalize_header,
    parse_kml_coordinate,
    safe_float,
)
from data_ingestion.utils.res_com_addressing import (
    ENABLE_RES_COM_ADDRESSING,
    annotate_metadata,
    build_address_from_metadata,
    duplicate_key_for_address,
)


FIELD_ALIASES: dict[str, set[str]] = {
    "raw_address": {
        # Generic
        "address",
        "address 1",
        "address line 1",
        "address line",
        "addr1",
        "street address",
        "street address 1",
        "service address",
        "raw address",
        "full address",
        "location address",
        "location name",
        "site address",
        "premise address",
        "property address",
        "delivery address",
        "work location",
        "installation address",
        "drop address",
        "customer address",
        "node address",
        "service location",
        "service point address",
        "placemark name",
        "name2",
        "rawaddress",
        "secondary number",
        "secondary_number",
        "secondarynumber",
    },
    "city": {"city", "city name", "municipality", "town", "locality"},
    "state": {"state", "st", "state code", "province"},
    "zip_code": {"zip", "zip code", "zipcode", "postal code", "zip5", "postcode", "post code"},
    "latitude": {
        # Short forms
        "lat",
        "latitude",
        # Coordinate axis
        "y",
        "y coordinate",
        "y coord",
        "northing",
        "decimal latitude",
        "latitude dd",
        "lat dec",
        # FTTH-specific prefixes
        "network node latitude",
        "node latitude",
        "node lat",
        "service latitude",
        "service lat",
        "drop latitude",
        "drop lat",
        "geo latitude",
        "geographic latitude",
        "point latitude",
        "coord lat",
        "location latitude",
        "site latitude",
        "premise latitude",
    },
    "longitude": {
        # Short forms
        "lon",
        "lng",
        "long",
        "longitude",
        # Coordinate axis
        "x",
        "x coordinate",
        "x coord",
        "easting",
        "decimal longitude",
        "longitude dd",
        "lon dec",
        # FTTH-specific prefixes
        "network node longitude",
        "node longitude",
        "node lon",
        "node lng",
        "service longitude",
        "service lon",
        "drop longitude",
        "drop lon",
        "geo longitude",
        "geographic longitude",
        "point longitude",
        "coord lon",
        "coord lng",
        "location longitude",
        "site longitude",
        "premise longitude",
    },
    "network_node": {
        "node",
        "network node",
        "fiber node",
        "serving area",
        "service area",
        "hub",
        "pon",
        "network node name",
        "node name",
        "serving node",
    },
    "terminal_id": {
        "terminal",
        "terminal id",
        "terminal_id",
        "terminalid",
        "terminal number",
        "terminalnumber",
        "tap",
        "mst",
        "fat",
        "tap id",
        "fat id",
        "mst id",
    },
    "address_id": {
        "address id",
        "address_id",
        "addressid",
        "addr id",
        "customer id",
        "cust id",
        "location id",
        "id",
        "service id",
        "account id",
    },
    "coordinates": {"coordinates", "coords", "coordinate"},
}

EXCHANGE_CITY_HINTS: dict[str, tuple[str, str]] = {
    "AMRC": ("Americus", "GA"),
}

# First matching column wins (before generic FIELD_ALIASES scan).
RAW_ADDRESS_COLUMN_PRIORITY: tuple[str, ...] = (
    "address",
    "secondary number",
    "secondary_number",
    "secondarynumber",
    "street address",
    "street address 1",
    "service address",
    "full address",
    "property address",
    "delivery address",
    "site address",
    "premise address",
    "customer address",
    "placemark name",
    "name2",
    "location address",
    "location name",
)


class CanonicalMapper:
    """Map extractor-specific raw rows/features into the canonical FTTH schema."""

    def map_record(
        self,
        raw_record: RawExtractedRecord,
        *,
        customer_id: str | None = None,
    ) -> CanonicalAddressRecord:
        raw = raw_record.raw_data
        normalized_lookup = self._build_normalized_lookup(raw)

        raw_address = self._resolve_raw_address(normalized_lookup, raw)

        coordinates = self._first_value(normalized_lookup, "coordinates")
        latitude = safe_float(self._first_value(normalized_lookup, "latitude"))
        longitude = safe_float(self._first_value(normalized_lookup, "longitude"))

        if (latitude is None or longitude is None) and coordinates:
            latitude, longitude = parse_kml_coordinate(coordinates)
        if not self._valid_coordinate_pair(latitude, longitude):
            latitude, longitude = None, None

        address_id = self._first_value(normalized_lookup, "address_id")
        if not address_id or str(address_id).strip() in {"0", "0.0"}:
            win_lon_value = clean_value(raw.get("win_lon") or raw.get("WIN_LON"))
            if isinstance(win_lon_value, str) and win_lon_value.strip().upper().startswith("A"):
                address_id = win_lon_value.strip()

        city = self._first_value(normalized_lookup, "city")
        state = self._first_value(normalized_lookup, "state")
        zip_code = self._first_value(normalized_lookup, "zip_code")
        inferred_city, inferred_state = self._infer_city_state(raw)
        city = city or inferred_city
        state = state or inferred_state

        raw_meta = dict(raw)
        if not isinstance(raw_meta.get("_uploaded_data"), dict):
            internal_upload_keys = {
                "upload_batch_id", "upload_file_index", "original_source_file",
                "source_format", "file_role", "upload_group_source_files",
            }
            uploaded_data = {
                str(key): value
                for key, value in raw.items()
                if str(key) not in internal_upload_keys and not str(key).startswith("_")
            }
            raw_meta["_uploaded_data"] = uploaded_data
            raw_meta["_uploaded_columns"] = list(uploaded_data)
        if ENABLE_RES_COM_ADDRESSING:
            raw_meta = annotate_metadata(raw_meta, raw_address=raw_address)
            resolved_address = build_address_from_metadata(
                raw_meta,
                raw_address=raw_address,
                city=city,
                state=state,
                zip_code=zip_code,
            )
            if resolved_address:
                raw_address = resolved_address

        # Snapshot ingestion-time canonical fields into raw_metadata["old"].
        # Only write if "old" is not already present (e.g. KMZ files may include their own).
        if "old" not in raw_meta:
            _city_state = f"{city}, {state}" if city and state else (city or state or "")
            raw_meta["old"] = {
                "street_number_name": raw_address or "",
                "zip_postal_code": zip_code or "",
                "latitude": latitude,
                "longitude": longitude,
                "city_state": _city_state,
                "city": city or "",
                "state": state or "",
                "country_code": "",
            }

        record = CanonicalAddressRecord(
            raw_address=raw_address,
            city=city,
            state=state,
            zip_code=zip_code,
            latitude=latitude,
            longitude=longitude,
            network_node=self._first_value(normalized_lookup, "network_node"),
            terminal_id=self._first_value(normalized_lookup, "terminal_id"),
            address_id=address_id,
            customer_id=customer_id,
            source_file=raw_record.source_file,
            source_sheet=raw_record.source_sheet,
            source_layer=raw_record.source_layer,
            source_row_number=raw_record.row_number,
            raw_metadata=raw_meta,
        )
        record.normalized_key = self._build_duplicate_key(record)
        return record

    def map_records(
        self,
        raw_records: list[RawExtractedRecord],
        *,
        customer_id: str | None = None,
    ) -> list[CanonicalAddressRecord]:
        return [self.map_record(record, customer_id=customer_id) for record in raw_records]

    def _build_normalized_lookup(self, raw: dict[str, Any]) -> dict[str, Any]:
        lookup: dict[str, Any] = {}
        for key, value in raw.items():
            norm_key = normalize_header(key)
            if not norm_key:
                continue
            lookup[norm_key] = clean_value(value)
        return lookup

    def _first_value(self, normalized_lookup: dict[str, Any], canonical_field: str) -> Any:
        accepted = FIELD_ALIASES[canonical_field]
        for key, value in normalized_lookup.items():
            if key in accepted and value is not None:
                return value
        return None

    def _resolve_raw_address(self, normalized_lookup: dict[str, Any], raw: dict[str, Any]) -> Any:
        for priority_key in RAW_ADDRESS_COLUMN_PRIORITY:
            if priority_key in normalized_lookup and normalized_lookup[priority_key] is not None:
                return normalized_lookup[priority_key]
        value = self._first_value(normalized_lookup, "raw_address")
        if value is not None:
            return value
        return clean_value(raw.get("placemark_name") or raw.get("name"))

    def _valid_coordinate_pair(self, latitude: float | None, longitude: float | None) -> bool:
        if latitude is None or longitude is None:
            return False
        if abs(latitude) < 0.000001 and abs(longitude) < 0.000001:
            return False
        return -90 <= latitude <= 90 and -180 <= longitude <= 180

    def _infer_city_state(self, raw: dict[str, Any]) -> tuple[str | None, str | None]:
        haystack = " ".join(
            str(clean_value(raw.get(key)) or "")
            for key in ("win_exchange_id", "data_source", "noc_plan_desc", "network_node", "gcomms_ft_to_bb")
        ).upper()
        for exchange, city_state in EXCHANGE_CITY_HINTS.items():
            if exchange in haystack:
                return city_state
        state_match = re.search(r"\b([A-Z]{2})\d{3,}[A-Z]{3,}\b", haystack)
        if state_match:
            return None, state_match.group(1)
        state_match = re.search(r"\b[A-Z]{4}([A-Z]{2})\b", haystack)
        if state_match:
            return None, state_match.group(1)
        return None, None

    def _build_duplicate_key(self, record: CanonicalAddressRecord) -> str | None:
        if ENABLE_RES_COM_ADDRESSING:
            return duplicate_key_for_address(
                raw_address=record.raw_address,
                meta=record.raw_metadata,
                city=record.city,
                state=record.state,
                zip_code=record.zip_code,
            )
        address_key = normalize_address_key(record.raw_address)
        if not address_key:
            return None
        parts = [address_key]
        if record.zip_code:
            parts.append(str(record.zip_code))
        elif record.city and record.state:
            parts.append(str(record.city).upper())
            parts.append(str(record.state).upper())
        return "|".join(parts)
