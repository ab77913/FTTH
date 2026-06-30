import hashlib
from src.models.schemas import CanonicalAddress, ProviderResult


def _hash(address: str) -> int:
    return int(hashlib.md5(address.encode()).hexdigest(), 16)

class MockSmartyProvider:
    def validate(self, address: CanonicalAddress) -> ProviderResult:
        h = _hash(address.normalized_full_address + "smarty")
        dpv = "Y" if h % 10 not in {0, 1} else ("S" if h % 10 == 0 else "N")
        precision = "ROOFTOP" if dpv == "Y" else "INTERPOLATED"
        rec_type = "H" if address.unit_type or h % 17 == 0 else "S"
        return ProviderResult(
            provider="smarty",
            success=dpv != "N",
            standardized_address=address.normalized_full_address,
            dpv_match=dpv,
            zip_plus_4=(address.zip_code or "29072") + "-" + str(h % 10000).zfill(4),
            vacant=(h % 23 == 0),
            record_type=rec_type,
            latitude=address.latitude,
            longitude=address.longitude,
            geocode_precision=precision,
            unit_detected=bool(address.unit_type),
            raw_response={"mock": True, "hash": h},
        )

class MockMelissaProvider:
    def validate(self, address: CanonicalAddress) -> ProviderResult:
        h = _hash(address.normalized_full_address + "melissa")
        dpv = "Y" if h % 10 not in {2, 3} else ("D" if h % 10 == 2 else "S")
        precision = "ROOFTOP" if h % 5 != 0 else "INTERPOLATED"
        rec_type = "H" if address.unit_type or h % 13 == 0 else "S"
        return ProviderResult(
            provider="melissa",
            success=True,
            standardized_address=address.normalized_full_address,
            dpv_match=dpv,
            zip_plus_4=(address.zip_code or "29072") + "-" + str(h % 10000).zfill(4),
            vacant=(h % 29 == 0),
            record_type=rec_type,
            latitude=address.latitude,
            longitude=address.longitude,
            geocode_precision=precision,
            unit_detected=bool(address.unit_type) or dpv == "D",
            raw_response={"mock": True, "hash": h},
        )
