from __future__ import annotations

import json
import logging
from typing import Any

from data_ingestion.database.models import Address
from data_ingestion.database.repositories import DispatchRepository

logger = logging.getLogger(__name__)


class SequentialDispatcher:
    """Simple sequential dispatcher for downstream verification agents.

    In production this can be replaced by Kafka, RabbitMQ, SQS, or a workflow engine.
    For this POC, the dispatcher pulls pending records and calls a local handler.
    """

    def __init__(self, repository: DispatchRepository):
        self.repository = repository

    def dispatch_pending(self, *, limit: int = 100) -> int:
        items = self.repository.get_pending_items(limit=limit)
        processed = 0

        for item in items:
            self.repository.mark_processing(item)
            address = self.repository.get_address(item.address_id)
            if address is None:
                self.repository.mark_failed(item, f"Address {item.address_id} not found")
                continue

            try:
                payload = self._build_payload(address)
                self._send_to_next_agent(payload)
                self.repository.mark_completed(item)
                processed += 1
            except Exception as exc:  # noqa: BLE001 - dispatcher should record any downstream failure
                logger.exception("Failed to dispatch address_id=%s", item.address_id)
                self.repository.mark_failed(item, str(exc))

        return processed

    def _build_payload(self, address: Address) -> dict[str, Any]:
        return {
            "record_id": str(address.record_uuid),
            "database_id": address.id,
            "job_id": str(address.job_id),
            "record_type": "ADDRESS",
            "raw_address": address.raw_address,
            "city": address.city,
            "state": address.state,
            "zip_code": address.zip_code,
            "latitude": address.latitude,
            "longitude": address.longitude,
            "network_node": address.network_node,
            "terminal_id": address.terminal_id,
            "address_id": address.address_id,
            "source_file": address.source_file,
            "source_sheet": address.source_sheet,
            "source_layer": address.source_layer,
            "raw_metadata": address.raw_metadata or {},
        }

    def _send_to_next_agent(self, payload: dict[str, Any]) -> None:
        """Stub: replace with actual agent/API/queue call.

        The downstream chain can be:
        Address Validation Agent → Parcel Matching Agent → Structure Classification Agent → etc.
        """
        logger.info("Dispatching record to downstream agents: %s", json.dumps(payload, default=str))
