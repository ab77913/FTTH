from __future__ import annotations

from pathlib import Path

from data_ingestion.agents.agent5_0_offline_ocr import (
    Agent50Config,
    analyze_record,
    streetview_metadata,
    load_excel_records,
)


def _config() -> Agent50Config:
    return Agent50Config(
        max_workers=2,
        max_iterations=3,
        heading_offsets=(0.0,),
        fov_ladder=(60, 40, 25),
        lateral_steps_m=(0.0, 4.0, -4.0),
        use_ollama=True,
    )


def test_analyze_record_surfaces_streetview_api_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.streetview_metadata",
        lambda *_a, **_k: (
            False,
            {
                "status": "REQUEST_DENIED",
                "error_message": "You must enable Billing on the Google Cloud Project.",
            },
        ),
    )

    result = analyze_record(
        {"address": "15275 NW GAINESVILLE RD", "latitude": 29.37, "longitude": -82.19},
        config=_config(),
    )

    assert result["status"] == "no_imagery"
    assert "REQUEST_DENIED" in result["reason"]
    assert "enable Billing" in result["reason"]
    assert result["ocr_engine_used"] == "none"


def test_analyze_record_loops_until_paddle_match(monkeypatch) -> None:
    calls: list[int] = []

    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.streetview_metadata",
        lambda *_a, **_k: (True, {"status": "OK", "location": {"lat": 10.0, "lng": 20.0}}),
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.fetch_streetview_image",
        lambda *_a, **_k: (b"x" * 20_000, "mock-url"),
    )

    def fake_paddle(_image_bytes, _expected, *, threshold):
        calls.append(1)
        if len(calls) >= 3:
            return {"matched": True, "confidence": 0.96, "text": "15275", "variant": "full", "variants_run": 1}
        return {"matched": False, "confidence": 0.0, "text": "", "variant": "", "variants_run": 1}

    monkeypatch.setattr("data_ingestion.agents.agent5_0_offline_ocr._run_paddle_on_image", fake_paddle)
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr._ask_local_ollama",
        lambda *_a, **_k: {
            "house_number_visible": False,
            "recommended_fov": 40,
            "recommended_heading_delta_deg": 0,
            "recommended_lateral_move_meters": 4,
        },
    )

    result = analyze_record(
        {"address": "15275 NW GAINESVILLE RD", "latitude": 29.37, "longitude": -82.19},
        config=_config(),
    )

    assert result["status"] == "analyzed"
    assert result["ocr_match_found"] is True
    assert result["paddle_ocr_match_found"] is True
    assert result["cloud_llm_used"] is False
    assert result["azure_vision_used"] is False
    assert len(calls) == 3


def test_analyze_record_accepts_high_confidence_ollama_vision_ocr(monkeypatch) -> None:
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.streetview_metadata",
        lambda *_a, **_k: (True, {"status": "OK", "location": {"lat": 10.0, "lng": 20.0}}),
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.fetch_streetview_image",
        lambda *_a, **_k: (b"x" * 20_000, "mock-url"),
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr._run_paddle_on_image",
        lambda *_a, **_k: {"matched": False, "confidence": 0.2, "text": "1527", "variant": "", "variants_run": 1},
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr._ask_local_ollama",
        lambda *_a, **_k: {
            "house_number_visible": True,
            "house_number_text": "15275",
            "confidence": 0.99,
            "recommended_fov": 25,
            "recommended_heading_delta_deg": 0,
            "recommended_lateral_move_meters": 0,
            "structure_type": "SFH",
        },
    )

    result = analyze_record(
        {"address": "15275 NW GAINESVILLE RD", "latitude": 29.37, "longitude": -82.19},
        config=_config(),
    )

    assert result["status"] == "analyzed"
    assert result["ocr_match_found"] is True
    assert result["ollama_ocr_match_found"] is True
    assert result["ollama_ocr_text"] == "15275"
    assert result["ocr_engine_used"] == "ollama_vision"
    assert result["paddle_ocr_used"] is False
    assert result["structure_type"] == "SFH"


def test_analyze_record_paddle_primary_uses_ollama_as_guidance_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.streetview_metadata",
        lambda *_a, **_k: (True, {"status": "OK", "location": {"lat": 10.0, "lng": 20.0}}),
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr.fetch_streetview_image",
        lambda *_a, **_k: (b"x" * 20_000, "mock-url"),
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr._run_paddle_on_image",
        lambda *_a, **_k: {"matched": False, "confidence": 0.2, "text": "1527", "variant": "", "variants_run": 1},
    )
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr._ask_local_ollama",
        lambda *_a, **_k: {
            "house_number_visible": True,
            "house_number_text": "15275",
            "confidence": 0.99,
            "recommended_fov": 25,
            "recommended_heading_delta_deg": 0,
            "recommended_lateral_move_meters": 0,
            "structure_type": "SFH",
        },
    )

    result = analyze_record(
        {"address": "15275 NW GAINESVILLE RD", "latitude": 29.37, "longitude": -82.19},
        config=Agent50Config(
            max_workers=2,
            max_iterations=3,
            heading_offsets=(0.0,),
            fov_ladder=(60, 40, 25),
            lateral_steps_m=(0.0, 4.0, -4.0),
            use_ollama=True,
            ocr_engine="paddle_primary",
        ),
    )

    assert result["status"] == "review"
    assert result["ocr_match_found"] is False
    assert result["paddle_ocr_match_found"] is False
    assert result["structure_type"] == "SFH"


def test_load_excel_records_reads_address_lat_lon() -> None:
    import openpyxl

    tmp_dir = Path(".pytest_tmp")
    tmp_dir.mkdir(exist_ok=True)
    path = tmp_dir / "agent5_0_sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ADDRESS", "LATITUDE", "LONGITUDE"])
    ws.append(["15275 NW GAINESVILLE RD", 29.3707, -82.1970])
    wb.save(path)
    wb.close()

    records = load_excel_records(path)

    assert records == [
        {
            "row_number": 2,
            "address": "15275 NW GAINESVILLE RD",
            "latitude": 29.3707,
            "longitude": -82.1970,
        }
    ]
    path.unlink(missing_ok=True)


def test_defaults_fetch_fresh_and_scan_more_angles() -> None:
    cfg = Agent50Config()

    assert cfg.use_cache is False
    assert cfg.max_iterations == 6
    assert len(cfg.heading_offsets) >= 7


def test_streetview_metadata_bypasses_stale_denied_cache(monkeypatch, tmp_path) -> None:
    cache_file = tmp_path / "metadata.json"
    cache_file.write_text('{"status":"REQUEST_DENIED","error_message":"old bad key"}', encoding="utf-8")

    monkeypatch.setattr("data_ingestion.agents.agent5_0_offline_ocr._google_key", lambda: "fresh-key")
    monkeypatch.setattr(
        "data_ingestion.agents.agent5_0_offline_ocr._cache_path",
        lambda *_args: cache_file,
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "OK",
                "date": "2026-04",
                "location": {"lat": 29.37, "lng": -82.20},
                "pano_id": "fresh-pano",
            }

    calls: list[dict] = []

    def fake_get(_url, *, params, timeout, **kwargs):
        calls.append({"params": params, "timeout": timeout, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("data_ingestion.agents.agent5_0_offline_ocr.requests.get", fake_get)

    available, data = streetview_metadata(29.37, -82.20, timeout_s=3, use_cache=False)

    assert available is True
    assert data["status"] == "OK"
    assert data["pano_id"] == "fresh-pano"
    assert len(calls) == 1
    assert calls[0]["params"]["radius"] == "100"
