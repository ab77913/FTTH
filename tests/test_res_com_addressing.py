from data_ingestion.utils.res_com_addressing import (
    annotate_metadata,
    build_address_from_metadata,
    duplicate_key_for_address,
    is_res_com_token,
    res_com_addressing_enabled,
    resolve_best_address,
)


def test_res_com_tokens_are_categories_not_addresses():
    assert is_res_com_token("Res")
    assert is_res_com_token("Com")
    assert not is_res_com_token("3145 Bellvue Rd")


def test_builds_real_address_from_street_number_and_name():
    meta = {
        "Address Type": "Res",
        "Street No": "3145",
        "Street Name": "BELLVUE RD",
        "City": "TILLAMOOK",
        "State": "OR",
        "Zip": "97141",
    }

    assert build_address_from_metadata(meta, raw_address="Res") == "3145 BELLVUE RD, TILLAMOOK, OR, 97141"


def test_annotates_residential_category_without_removing_raw_fields():
    meta = annotate_metadata({"Address Type": "Res", "Street No": "3145"}, raw_address="Res")

    assert meta["address_type"] == "Residential"
    assert meta["address_type_code"] == "Res"
    assert meta["category"] == "household"
    assert meta["Street No"] == "3145"


def test_resolve_best_address_prefers_validated_agent_output():
    meta = {
        "Address Type": "Com",
        "Street No": "1845",
        "Street Name": "MAIN AVE N",
    }

    assert resolve_best_address(
        raw_address="Com",
        meta=meta,
        chosen_address="1845 Main Ave N Tillamook OR 97141-9252",
    ) == "1845 Main Ave N Tillamook OR 97141-9252"


def test_duplicate_key_uses_real_address_not_res_com_token():
    meta_a = {"Address Type": "Res", "Street No": "3145", "Street Name": "BELLVUE RD"}
    meta_b = {"Address Type": "Res", "Street No": "3345", "Street Name": "HILLCREST RD N"}

    key_a = duplicate_key_for_address(raw_address="Res", meta=meta_a, city="TILLAMOOK", state="OR", zip_code="97141")
    key_b = duplicate_key_for_address(raw_address="Res", meta=meta_b, city="TILLAMOOK", state="OR", zip_code="97141")

    assert key_a != key_b
    assert key_a != "RES"
    assert key_b != "RES"


def test_builds_address_with_street_number_suffix_canada_post_style():
    meta_a = {
        "Street Number": "37",
        "Street Number Suffix": "A",
        "Street Name": "JOHN",
        "Street Type": "ST",
        "City": "St. Catharines",
        "State": "ON",
        "Postal Code": "L2N4P2",
    }
    meta_b = {
        "Street Number": "37",
        "Street Number Suffix": "B",
        "Street Name": "JOHN",
        "Street Type": "ST",
        "City": "St. Catharines",
        "State": "ON",
        "Postal Code": "L2N4P2",
    }

    address_a = build_address_from_metadata(meta_a)
    address_b = build_address_from_metadata(meta_b)

    assert "37A" in address_a.replace(" ", "")
    assert "37B" in address_b.replace(" ", "")
    assert "JOHN" in address_a.upper()
    assert address_a != address_b

    key_a = duplicate_key_for_address(meta=meta_a, city="St. Catharines", state="ON", zip_code="L2N4P2")
    key_b = duplicate_key_for_address(meta=meta_b, city="St. Catharines", state="ON", zip_code="L2N4P2")
    assert key_a != key_b


def test_resolve_best_address_includes_suffix_when_no_validated_output():
    meta = {
        "Street Number": "65",
        "Street Number Suffix": "A",
        "Street Name": "EXAMPLE",
        "Street Type": "RD",
        "City": "St. Catharines",
        "State": "ON",
    }
    resolved = resolve_best_address(raw_address="", meta=meta, city="St. Catharines", state="ON")
    assert "65A" in resolved.replace(" ", "")


def test_address_column_does_not_override_street_number_suffix():
    """Canada Post rows often have ADDRESS=9 DOROTHY ST plus suffix column A/B."""
    meta_a = {
        "ADDRESS": "9 DOROTHY ST, ST CATHARINES, ON",
        "Street Number": "9",
        "Street Number Suffix": "A",
        "Street Name": "DOROTHY",
        "Street Type": "ST",
        "City": "ST CATHARINES",
        "State": "ON",
        "Postal Code": "L2N4A4",
    }
    meta_b = {
        "ADDRESS": "9 DOROTHY ST, ST CATHARINES, ON",
        "Street Number": "9",
        "Street Number Suffix": "B",
        "Street Name": "DOROTHY",
        "Street Type": "ST",
        "City": "ST CATHARINES",
        "State": "ON",
        "Postal Code": "L2N4A4",
    }

    address_a = build_address_from_metadata(meta_a, raw_address=meta_a["ADDRESS"])
    address_b = build_address_from_metadata(meta_b, raw_address=meta_b["ADDRESS"])

    assert "9A" in address_a.replace(" ", "").upper()
    assert "9B" in address_b.replace(" ", "").upper()
    assert address_a != address_b

    key_a = duplicate_key_for_address(meta=meta_a, raw_address=meta_a["ADDRESS"])
    key_b = duplicate_key_for_address(meta=meta_b, raw_address=meta_b["ADDRESS"])
    assert key_a != key_b


def test_normalized_header_lookup_finds_suffix_column_variants():
    meta = {
        "street_number": "49",
        "street_number_suffix": "A",
        "street_name": "DOROTHY",
        "street_type": "ST",
    }
    address = build_address_from_metadata(meta)
    assert "49A" in address.replace(" ", "").upper()


def test_unit_number_is_not_prepended_to_street_address():
    meta = {
        "ADDRESS": "42 DOROTHY ST, ST CATHARINES, ON",
        "Street Number": "42",
        "Unit Number": "3",
        "Street Name": "DOROTHY",
        "Street Type": "ST",
        "City": "ST CATHARINES",
        "State": "ON",
        "Postal Code": "L2N4A5",
    }
    address = build_address_from_metadata(meta, raw_address=meta["ADDRESS"])
    assert not address.upper().startswith("UNIT")
    assert "42" in address
    assert "DOROTHY" in address.upper()


def test_street_number_suffix_still_applied_without_unit_prefix():
    meta = {
        "ADDRESS": "58 CECIL ST, ST CATHARINES, ON",
        "Street Number": "58",
        "Street Number Suffix": "A",
        "Street Name": "CECIL",
        "Street Type": "ST",
        "City": "ST CATHARINES",
        "State": "ON",
        "Postal Code": "L2N4B2",
    }
    address = build_address_from_metadata(meta, raw_address=meta["ADDRESS"])
    assert "58A" in address.replace(" ", "").upper()
    assert "CECIL" in address.upper()


def test_feature_switch_reads_environment(monkeypatch):
    from data_ingestion.config.settings import get_settings

    monkeypatch.setenv("RES_COM_ADDRESSING_ENABLED", "false")
    get_settings.cache_clear()
    assert res_com_addressing_enabled() is False

    monkeypatch.setenv("RES_COM_ADDRESSING_ENABLED", "true")
    get_settings.cache_clear()
    assert res_com_addressing_enabled() is True
