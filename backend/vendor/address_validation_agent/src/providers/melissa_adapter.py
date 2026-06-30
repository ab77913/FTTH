import logging
import os
import requests
import urllib3
from src.models.schemas import CanonicalAddress, ProviderResult

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class MelissaProvider:
    def __init__(self):
        self.license_key = os.getenv("MELISSA_LICENSE_KEY")

        self.url = "https://address.melissadata.net/v3/WEB/GlobalAddress/doGlobalAddress"

    def _build_params(self, address: CanonicalAddress) -> dict:
        """Melissa GlobalAddress: a1 (address), loc (city), admarea (state), postal, ctry."""
        street_parts = [
            p for p in [
                address.house_number,
                address.street_name,
                address.street_suffix,
                address.unit_type,
                address.unit_number,
            ] if p
        ]
        street = " ".join(street_parts).strip() or (address.raw_address or "").strip()
        return {
            "a1": street,
            "loc": address.city or "",
            "admarea": address.state or "",
            "postal": address.zip_code or "",
            "ctry": (address.country or "US").upper(),
            "format": "JSON",
        }

    def validate(self, address: CanonicalAddress, *, request: dict | None = None) -> ProviderResult:

        if not self.license_key:
            return ProviderResult(
                provider="melissa",
                success=False,
                error="Missing Melissa credentials"
            )

        if request:
            params = dict(request)
            params["id"] = self.license_key
            params.setdefault("format", "JSON")
        else:
            params = self._build_params(address)
            params["id"] = self.license_key

        try:
            logger.debug("Melissa request: %s", {**params, "id": "***"})

            response = requests.get(
                self.url,
                params=params,
                timeout=30,
                verify=False
            )

            response.raise_for_status()

            data = response.json()

            logger.debug("Melissa response: %s", data)

            records = data.get("Records", [])

            if not records:
                return ProviderResult(
                    provider="melissa",
                    success=False,
                    raw_response=data,
                    error="No records returned"
                )

            record = records[0]

            results = record.get("Results", "")

            return ProviderResult(
                provider="melissa",
                success=True,
                standardized_address=record.get("FormattedAddress"),
                dpv_match="Y" if "AV" in results else "N",
                zip_plus_4=record.get("PostalCode"),
                vacant=False,
                record_type=record.get("AddressType"),
                latitude=record.get("Latitude"),
                longitude=record.get("Longitude"),
                geocode_precision=record.get("AddressPrecisionCode"),
                unit_detected=bool(record.get("SubPremises")),
                raw_response=record,
            )

        except Exception as e:
            return ProviderResult(
                provider="melissa",
                success=False,
                error=str(e)
            )