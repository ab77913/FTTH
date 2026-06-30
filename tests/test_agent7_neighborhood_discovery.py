from shapely.geometry import Polygon

from data_ingestion.agents.agent7_neighborhood_discovery import (
    _agent7_validation_provider,
    _create_address_from_discovery,
    _passes_agent7_validation,
    filter_new_geocodes,
    generate_neighborhood_candidates,
)
from data_ingestion.database.models import Address
from data_ingestion.utils.strings import normalize_address_key
from data_ingestion.utils.pipeline_options import normalize_pipeline_options


def test_agent7_is_bounded_and_disabled_until_selected():
    defaults = normalize_pipeline_options(None)["agent7"]
    assert defaults["enabled"] is False
    assert defaults["max_candidates_per_job"] == 500
    assert defaults["validation_provider"] == "reverse_rooftop"
    assert defaults["validation_threshold"] == 90
    assert defaults["microsoft_building_enrichment"] is True

    selected = normalize_pipeline_options({
        "agent7": {
            "enabled": True,
            "samples_per_address": 2,
            "concurrency": 4,
            "validation_provider": "regrid",
            "validation_threshold": 91,
        }
    })["agent7"]
    assert selected["enabled"] is True
    assert selected["samples_per_address"] == 2
    assert selected["concurrency"] == 4
    assert selected["validation_provider"] == "regrid"
    assert selected["validation_threshold"] == 91


def test_agent7_validation_accepts_selected_provider_above_threshold():
    assert _passes_agent7_validation({"smarty": 91, "regrid": 0}, 90, "smarty") is True
    assert _passes_agent7_validation({"smarty": 90, "regrid": 95}, 90, "smarty") is False
    assert _passes_agent7_validation({"smarty": 35, "regrid": 95}, 90, "regrid") is True
    assert _passes_agent7_validation({"smarty": 99, "regrid": 90}, 90, "regrid") is False


def test_agent7_validation_provider_normalizes_dropdown_and_legacy_options():
    assert _agent7_validation_provider({"validation_provider": "Regrid"}) == "regrid"
    assert _agent7_validation_provider({"validate_with_smarty": False, "validate_with_regrid": True}) == "regrid"
    assert _agent7_validation_provider({"validation_provider": "bad"}) == "smarty"


def test_candidates_are_bounded_inside_polygon_and_round_robin():
    polygon = Polygon([(-80.01, 34.99), (-79.99, 34.99), (-79.99, 35.01), (-80.01, 35.01)])
    seeds = [
        {"address_id": 11, "polygon_id": 1, "latitude": 35.0, "longitude": -80.0},
        {"address_id": 12, "polygon_id": 1, "latitude": 35.001, "longitude": -80.0},
    ]

    candidates = generate_neighborhood_candidates(
        seeds,
        {1: polygon},
        distance_m=60,
        samples_per_address=4,
        max_candidates=3,
    )

    assert len(candidates) == 3
    assert [item["seed_address_id"] for item in candidates[:2]] == [11, 12]
    assert all(polygon.contains(__import__("shapely").geometry.Point(item["lon"], item["lat"])) for item in candidates)


def test_filter_removes_final_and_discovered_duplicates():
    polygon = Polygon([(-80.01, 34.99), (-79.99, 34.99), (-79.99, 35.01), (-80.01, 35.01)])

    def item(address, lat, lon, seed=11):
        return {
            "seed_address_id": seed,
            "polygon_id": 1,
            "geocode": {"address": address, "latitude": lat, "longitude": lon, "confidence": 91},
        }

    accepted, counts = filter_new_geocodes(
        [
            item("100 Main Street", 35.0005, -80.0005),  # normalized Final match
            item("Different Label", 35.00001, -80.0),  # coordinate-near Final match
            item("102 Main St", 35.002, -80.002),
            item("102 MAIN STREET", 35.0021, -80.0021),  # normalized discovery match
            item("Outside Address", 35.02, -80.0),
        ],
        final_address_keys={normalize_address_key("100 Main St")},
        final_coords=[(35.0, -80.0)],
        polygons={1: polygon},
        dedup_distance_m=25,
    )

    assert [row["address"] for row in accepted] == ["102 Main St"]
    assert counts == {
        "duplicate_final": 2,
        "duplicate_discovery": 1,
        "outside_polygon": 1,
        "failed": 0,
    }


def test_discovery_becomes_final_ready_address_record():
    class FakeSession:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        def flush(self):
            self.added[-1].id = 999

    seed = Address(
        id=11,
        job_id="00000000-0000-0000-0000-000000000001",
        customer_id="customer",
        raw_address="100 Main St",
        source_file="area.kmz",
    )
    polygon = Address(
        id=22,
        job_id="00000000-0000-0000-0000-000000000001",
        raw_address="Polygon",
        source_file="area.kmz",
    )
    discovery = {
        "seed_address_id": 11,
        "polygon_id": 22,
        "address": "102 Main St, Orlando, FL 32808, USA",
        "latitude": 28.6,
        "longitude": -81.4,
        "confidence": 95,
        "provider": "google_reverse_geocode",
        "neighborhood": "Orlando",
        "city": "Orlando",
        "state": "FL",
        "zip_code": "32808",
    }

    row = _create_address_from_discovery(
        FakeSession(),
        job_id="00000000-0000-0000-0000-000000000001",
        discovery=discovery,
        seed_row=seed,
        polygon_row=polygon,
    )

    final = row.raw_metadata["final_resolution"]
    assert row.id == 999
    assert row.source_layer == "Agent 7 Neighborhood Discovery"
    assert row.raw_metadata["map_layer_only"] is False
    assert row.raw_metadata["agent7_discovered"] is True
    assert final["address"] == discovery["address"]
    assert final["latitude"] == 28.6
    assert final["longitude"] == -81.4
    assert final["confidence"] == 95
    assert final["source_agent"] == "agent7_neighborhood_discovery"
    assert row.raw_metadata["final_structure_type"] == "UNKNOWN"
    assert row.raw_metadata["final_structure_confidence"] == 0
