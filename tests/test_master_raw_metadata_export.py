from __future__ import annotations

import json
from pathlib import Path

from api_server import _write_master_raw_metadata_file


def test_write_master_raw_metadata_file_creates_expected_json(tmp_path: Path) -> None:
    payload = {
        "job_id": "8f2f9c1e-5e0e-4f2f-a86a-3d97d2f2a3c5",
        "upload_batch_id": "upload_123",
        "record_count": 1,
        "records": [
            {
                "address_id": 1,
                "master_json": {
                    "old": {"line1": "123 Main St"},
                    "new": {"provider": "regrid"},
                    "final_resolution": {"chosen_provider": "smarty"},
                },
                "agent_outputs": {
                    "agent1_address_validator": {"confidence_score": 0.91},
                },
            }
        ],
    }

    output = _write_master_raw_metadata_file(tmp_path, payload)

    assert output == tmp_path / "master_raw_metadata.json"
    assert output.exists()

    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["upload_batch_id"] == "upload_123"
    assert parsed["record_count"] == 1
    assert "records" in parsed and len(parsed["records"]) == 1

    record = parsed["records"][0]
    assert "master_json" in record
    assert "old" in record["master_json"]
    assert "new" in record["master_json"]
    assert "final_resolution" in record["master_json"]
