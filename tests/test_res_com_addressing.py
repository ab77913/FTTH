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


def test_feature_switch_reads_environment(monkeypatch):
    from data_ingestion.config.settings import get_settings

    monkeypatch.setenv("RES_COM_ADDRESSING_ENABLED", "false")
    get_settings.cache_clear()
    assert res_com_addressing_enabled() is False

    monkeypatch.setenv("RES_COM_ADDRESSING_ENABLED", "true")
    get_settings.cache_clear()
    assert res_com_addressing_enabled() is True
