from __future__ import annotations

from types import SimpleNamespace

from data_ingestion.agents.agent0_house_discovery import (
    _best_google_reverse_result,
    _config_value,
    generate_candidate_points,
    parse_polygon_coordinates,
    _suffix_address,
    _create_multi_unit_suffix_rows,
    _attom_geocode_for_point,
    _infer_unit_count_from_attom_property,
    _deduplicate_points,
    _parallel_reverse_geocode_candidates,
    _existing_kml_point_candidates,
)
from data_ingestion.utils.pipeline_options import apply_flow_config_to_pipeline_options


def test_parse_polygon_coordinates_closes_kml_ring() -> None:
    polygon = parse_polygon_coordinates(
        "-84.0000,32.0000,0 -83.9990,32.0000,0 -83.9990,32.0010,0 -84.0000,32.0010,0"
    )

    assert polygon is not None
    assert polygon.is_valid
    assert len(polygon.exterior.coords) == 5


def test_generate_candidate_points_stay_inside_polygon() -> None:
    polygon = parse_polygon_coordinates(
        "-84.0000,32.0000,0 -83.9980,32.0000,0 -83.9980,32.0020,0 -84.0000,32.0020,0"
    )

    points = generate_candidate_points(polygon, grid_step=0.0005, max_points=10)

    assert 1 <= len(points) <= 10
    for point in points:
        assert 32.0 < point["lat"] < 32.002
        assert -84.0 < point["lon"] < -83.998


def test_generate_candidate_points_zero_limit_means_unlimited() -> None:
    polygon = parse_polygon_coordinates(
        "-84.0000,32.0000,0 -83.9990,32.0000,0 -83.9990,32.0010,0 -84.0000,32.0010,0"
    )

    capped = generate_candidate_points(polygon, grid_step=0.0005, max_points=2)
    unlimited = generate_candidate_points(polygon, grid_step=0.0005, max_points=0)

    assert len(capped) == 2
    assert len(unlimited) > len(capped)


def test_config_value_prefers_project_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_GEOCODING_API_KEY=fresh-key\n", encoding="utf-8")
    monkeypatch.setattr("data_ingestion.agents.agent0_house_discovery._ENV_FILE", str(env_file))
    monkeypatch.setenv("GOOGLE_GEOCODING_API_KEY", "stale-process-key")

    assert _config_value("GOOGLE_GEOCODING_API_KEY") == "fresh-key"


def test_best_google_reverse_result_prefers_numbered_street_address() -> None:
    result = _best_google_reverse_result([
        {
            "formatted_address": "Brookway West Drive, Winston-Salem, NC, USA",
            "types": ["route"],
            "geometry": {"location_type": "GEOMETRIC_CENTER"},
        },
        {
            "formatted_address": "511 Cedarbrook Ct, Winston-Salem, NC 27104, USA",
            "types": ["street_address"],
            "geometry": {"location_type": "ROOFTOP"},
        },
    ])

    assert result is not None
    assert result["formatted_address"].startswith("511 Cedarbrook")


def test_suffix_address_adds_unit_to_leading_house_number() -> None:
    assert _suffix_address("4341 Main St, Americus, GA", 1) == "4341-1 Main St, Americus, GA"
    assert _suffix_address("4345 Main St, Americus, GA", 2) == "4345-2 Main St, Americus, GA"


def test_create_multi_unit_suffix_rows_creates_expected_addresses(monkeypatch) -> None:
    created_addresses: list[str] = []

    def fake_create_address(session, **kwargs):
        created_addresses.append(kwargs["address_override"])
        return SimpleNamespace(
            id=len(created_addresses),
            raw_address=kwargs["address_override"],
            latitude=kwargs["geocode"]["latitude"],
            longitude=kwargs["geocode"]["longitude"],
        )

    monkeypatch.setattr(
        "data_ingestion.agents.agent0_house_discovery._create_address_from_geocode",
        fake_create_address,
    )
    monkeypatch.setattr("data_ingestion.agents.agent0_house_discovery._record_discovery", lambda *a, **k: SimpleNamespace(id=99))
    monkeypatch.setattr("data_ingestion.agents.agent0_house_discovery._upsert_agent_result", lambda *a, **k: None)

    count = _create_multi_unit_suffix_rows(
        None,
        job_id="job",
        polygon_row=SimpleNamespace(id=10),
        candidate={"lat": 32.0, "lon": -84.0},
        geocode={"address": "4341 Main St, Americus, GA", "latitude": 32.0, "longitude": -84.0, "confidence": 95},
        unit_count=2,
        max_units=4,
        existing_keys=set(),
        existing_coords=[],
        inserted_ids=[],
        attom_info={"reason": "attom_units_count", "attom_id": 123},
    )

    assert count == 2
    assert created_addresses == ["4341-1 Main St, Americus, GA", "4341-2 Main St, Americus, GA"]


def test_attom_unit_count_infers_duplex_when_units_count_missing() -> None:
    prop = {
        "summary": {
            "propertyType": "DUPLEX (2 UNITS, ANY COMBINATION)",
            "propLandUse": "DUPLEX",
        },
        "building": {"summary": {"unitsCount": None}},
    }

    assert _infer_unit_count_from_attom_property(prop) == 2


def test_attom_geocode_for_point_returns_google_like_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "data_ingestion.agents.agent0_house_discovery._attom_units_for_point",
        lambda lat, lon: {
            "units_count": 2,
            "attom_id": 123,
            "attom_address": "4341 NW 154TH ST, REDDICK, FL 32686",
            "attom_property_type": "DUPLEX (2 UNITS, ANY COMBINATION)",
            "latitude": "29.373186",
            "longitude": "-82.197266",
            "distance_m": 14.5,
            "reason": "attom_units_count",
            "raw": {"identifier": {"attomId": 123}},
        },
    )

    geocode = _attom_geocode_for_point(29.373111, -82.197132)

    assert geocode is not None
    assert geocode["provider"] == "attom"
    assert geocode["address"] == "4341 NW 154TH ST, REDDICK, FL 32686"
    assert geocode["latitude"] == 29.373186
    assert geocode["longitude"] == -82.197266
    assert geocode["attom_info"]["units_count"] == 2


def test_flow_builder_preserves_agent0_numeric_options() -> None:
    opts = apply_flow_config_to_pipeline_options(None, {
        "agents": [
            {
                "agent_name": "agent0_house_discovery",
                "enabled": True,
                "pipeline_options": {
                    "reverse_geocode": True,
                    "grid_step": 0.0004,
                    "max_candidates_per_polygon": 17,
                    "max_candidates_per_job": 27,
                    "dedup_distance_m": 35,
                    "multi_unit_suffixing": True,
                    "max_units_per_base_address": 6,
                    "validate_discovered": True,
                    "validate_with_smarty": True,
                    "validate_with_regrid": True,
                    "validation_threshold": 91,
                    "exclude_discovered_from_agent1": True,
                },
            }
        ]
    })

    assert opts["agent0"]["enabled"] is True
    assert opts["agent0"]["reverse_geocode"] is True
    assert opts["agent0"]["grid_step"] == 0.0004
    assert opts["agent0"]["max_candidates_per_polygon"] == 17
    assert opts["agent0"]["max_candidates_per_job"] == 27
    assert opts["agent0"]["dedup_distance_m"] == 35
    assert opts["agent0"]["multi_unit_suffixing"] is True
    assert opts["agent0"]["max_units_per_base_address"] == 6
    assert opts["agent0"]["validate_discovered"] is True
    assert opts["agent0"]["validate_with_smarty"] is True
    assert opts["agent0"]["validate_with_regrid"] is True
    assert opts["agent0"]["validation_threshold"] == 91
    assert opts["agent0"]["exclude_discovered_from_agent1"] is True


def test_flow_builder_preserves_agent0_unlimited_candidate_options() -> None:
    opts = apply_flow_config_to_pipeline_options(None, {
        "agents": [
            {
                "agent_name": "agent0_house_discovery",
                "enabled": True,
                "pipeline_options": {
                    "max_candidates_per_polygon": 0,
                    "max_candidates_per_job": 0,
                },
            }
        ]
    })

    assert opts["agent0"]["max_candidates_per_polygon"] == 0
    assert opts["agent0"]["max_candidates_per_job"] == 0


def test_flow_builder_agent0_string_false_becomes_false() -> None:
    opts = apply_flow_config_to_pipeline_options(None, {
        "agents": [
            {
                "agent_name": "agent0_house_discovery",
                "enabled": True,
                "pipeline_options": {
                    "reverse_geocode": "false",
                    "multi_unit_suffixing": "0",
                },
            }
        ]
    })

    assert opts["agent0"]["reverse_geocode"] is False
    assert opts["agent0"]["multi_unit_suffixing"] is False


def test_flow_builder_preserves_agent5_offline_llm_options() -> None:
    opts = apply_flow_config_to_pipeline_options(None, {
        "agents": [
            {
                "agent_name": "agent5_streetview",
                "enabled": True,
                "pipeline_options": {
                    "analysis_mode": "offline",
                    "gpt_vision": True,
                    "llm_provider": "offline",
                    "ollama_vision_model": "qwen2.5vl:latest",
                },
            }
        ]
    })

    assert opts["agent5"]["analysis_mode"] == "offline"
    assert opts["agent5"]["gpt_vision"] is True
    assert opts["agent5"]["llm_provider"] == "offline"
    assert opts["agent5"]["ollama_vision_model"] == "qwen2.5vl:latest"


def test_agent0_deduplicate_points_preserves_existing_kml_first() -> None:
    points = [
        {"lat": 29.370000, "lon": -82.190000, "source": "existing_kml_point"},
        {"lat": 29.370010, "lon": -82.190010, "source": "generated_candidate"},
        {"lat": 29.371000, "lon": -82.191000, "source": "generated_candidate"},
    ]

    deduped = _deduplicate_points(points, threshold_m=20)

    assert len(deduped) == 2
    assert deduped[0]["source"] == "existing_kml_point"
    assert deduped[1]["source"] == "generated_candidate"


def test_agent0_parallel_reverse_geocode_uses_cache(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    def fake_reverse(lat: float, lon: float, _api_key: str):
        calls.append((lat, lon))
        return {
            "address": f"{lat:.6f},{lon:.6f}",
            "latitude": lat,
            "longitude": lon,
            "confidence": 95,
        }

    monkeypatch.setattr("data_ingestion.agents.agent0_house_discovery._reverse_geocode", fake_reverse)
    candidates = [
        {"lat": 29.3700001, "lon": -82.1900001},
        {"lat": 29.3700002, "lon": -82.1900002},
    ]

    results = _parallel_reverse_geocode_candidates(
        candidates,
        api_key="key",
        workers=1,
        use_cache=True,
        progress_callback=None,
        processed_start=0,
        progress_total=2,
    )

    assert len(results) == 2
    assert len(calls) == 1
    assert results[0][1]["address"] == results[1][1]["address"]


def test_existing_kml_point_candidates_filters_to_uploaded_points() -> None:
    rows = [
        SimpleNamespace(
            id=1,
            source_file="input.kmz",
            raw_address="100 Main St",
            latitude=32.1,
            longitude=-84.1,
            raw_metadata={"geometry_type": "Point", "source_format": "kml", "map_layer_only": False},
        ),
        SimpleNamespace(
            id=2,
            source_file="input.csv",
            raw_address="200 Main St",
            latitude=32.2,
            longitude=-84.2,
            raw_metadata={"geometry_type": "Point", "source_format": "csv", "map_layer_only": False},
        ),
    ]

    class FakeScalars:
        def where(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

    class FakeSession:
        def scalars(self, _stmt):
            return SimpleNamespace(all=lambda: rows)

    candidates = _existing_kml_point_candidates(FakeSession(), "job")

    assert len(candidates) == 1
    assert candidates[0]["address_id"] == 1
    assert candidates[0]["address_key"]
