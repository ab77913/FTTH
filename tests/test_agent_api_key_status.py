from data_ingestion.utils.agent_api_key_status import (
    agent_api_key_ui_fields,
    persist_streetview_api_summary,
)


def test_persist_streetview_api_summary_stores_metadata():
    meta = persist_streetview_api_summary({}, {
        "status": "OK",
        "pano_id": "abc123",
        "date": "2024-05",
    })
    block = meta["streetview_api"]
    assert block["status"] == "OK"
    assert block["pano_id"] == "abc123"
    assert block["date"] == "2024-05"
    assert block["api_key_status"] == "Working"


def test_agent_api_key_status_from_streetview_metadata():
    fields = agent_api_key_ui_fields(
        {"streetview_api": {"status": "REQUEST_DENIED", "pano_id": "", "date": ""}},
        a50={"status": "no_imagery", "streetview_metadata": {"status": "REQUEST_DENIED"}},
    )
    assert fields["agent50_api_key_status"] == "Not Working"
    assert fields["agent50_streetview_api_status"] == "REQUEST_DENIED"


def test_agent6_api_key_is_na():
    fields = agent_api_key_ui_fields({}, a6={"status": "ok"})
    assert fields["agent6_api_key_status"] == "N/A"
