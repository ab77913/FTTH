"""
Agent-to-Agent (A2A) Bus — high-level façade over RabbitMQClient.

Architecture layer: E — Agentic Intelligence (Supervisor agent + A2A Bus).

This module provides the ``AgentBus`` class, the single integration point
between the pipeline runner and the RabbitMQ messaging layer.

Design principles:
  • Zero coupling — if RabbitMQ is unreachable the bus is a silent no-op;
    the pipeline continues to run normally without messaging.
  • Three message patterns (mirrors the architecture diagram):
      - P2P       : supervisor → specific agent  (Direct exchange)
      - Broadcast : supervisor → all agents      (Fanout exchange)
      - Events    : all agents → topic exchange  (routing_key = ftth.job.*)
  • Context-manager friendly for clean acquire/release in pipelines.

Typical pipeline usage::

    with AgentBus() as bus:
        bus.notify_pipeline_start(job_id, total)
        for each stage:
            bus.notify_agent_start(job_id, agent_name, stage_count)
            # ... run agent ...
            bus.notify_agent_complete(job_id, agent_name, result)
            bus.broadcast_progress(job_id, agent_name, pct)
        bus.notify_pipeline_complete(job_id, summary)
"""
from __future__ import annotations

import logging
from typing import Any

from data_ingestion.messaging.rabbitmq_client import RabbitMQClient

logger = logging.getLogger(__name__)


class AgentBus:
    """High-level A2A bus used by the pipeline supervisor.

    Parameters
    ----------
    rabbitmq_url:
        Override the RabbitMQ URL from settings.  If omitted the value from
        ``RABBITMQ_URL`` in ``.env`` / environment is used.
    """

    def __init__(self, rabbitmq_url: str | None = None) -> None:
        url = rabbitmq_url or _resolve_url()
        self._client = RabbitMQClient(url)
        self._connected = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> "AgentBus":
        """Connect to the broker and set up the exchange/queue topology."""
        self._connected = self._client.connect()
        if self._connected:
            self._client.setup_topology()
            logger.info("AgentBus: connected and topology ready")
        else:
            logger.info("AgentBus: broker unavailable — running without A2A messaging")
        return self

    def disconnect(self) -> None:
        """Close the AMQP connection."""
        self._client.disconnect()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Pipeline lifecycle events (Topic exchange) ────────────────────────────

    def notify_pipeline_start(self, job_id: str, total_records: int) -> None:
        """Broadcast pipeline-start to all agents and publish a topic event."""
        if not self._connected:
            return
        self._client.publish_fanout({
            "type": "pipeline_start",
            "job_id": job_id,
            "total_records": total_records,
        })
        self._client.publish_topic("ftth.job.pipeline.started", {
            "job_id": job_id,
            "total_records": total_records,
        })

    def notify_pipeline_complete(self, job_id: str, summary: dict[str, Any]) -> None:
        """Broadcast pipeline-complete and publish a topic event with the summary."""
        if not self._connected:
            return
        self._client.publish_fanout({
            "type": "pipeline_complete",
            "job_id": job_id,
            "summary": summary,
        })
        self._client.publish_topic("ftth.job.pipeline.completed", {
            "job_id": job_id,
            "summary": summary,
        })

    # ── Per-agent events (Direct + Topic exchanges) ───────────────────────────

    def notify_agent_start(self, job_id: str, agent_name: str, total_records: int) -> None:
        """P2P: tell the agent it is about to process records; publish topic event."""
        if not self._connected:
            return
        self._client.publish_direct(agent_name, {
            "type": "agent_start",
            "job_id": job_id,
            "agent_name": agent_name,
            "total_records": total_records,
        })
        self._client.publish_topic(f"ftth.job.{agent_name}.started", {
            "job_id": job_id,
            "agent": agent_name,
        })

    def notify_agent_complete(
        self,
        job_id: str,
        agent_name: str,
        result: dict[str, Any],
    ) -> None:
        """P2P: report agent completion back to the supervisor; publish topic event."""
        if not self._connected:
            return
        self._client.publish_direct("supervisor", {
            "type": "agent_complete",
            "job_id": job_id,
            "agent_name": agent_name,
            "result": result,
        })
        self._client.publish_topic(f"ftth.job.{agent_name}.completed", {
            "job_id": job_id,
            "agent": agent_name,
        })

    # ── Progress broadcast (Fanout exchange) ──────────────────────────────────

    def broadcast_progress(self, job_id: str, stage: str, progress_pct: int) -> None:
        """Fanout-broadcast the current overall progress percentage to all agents."""
        if not self._connected:
            return
        self._client.publish_fanout({
            "type": "progress",
            "job_id": job_id,
            "stage": stage,
            "progress": min(max(progress_pct, 0), 100),
        })

    # ── Low-level pass-throughs (for advanced use) ────────────────────────────

    def send_to_agent(self, agent_name: str, message: dict[str, Any]) -> bool:
        """Raw P2P send — prefer the higher-level notify_* methods."""
        if not self._connected:
            return False
        return self._client.publish_direct(agent_name, message)

    def broadcast(self, message: dict[str, Any]) -> bool:
        """Raw fanout broadcast — prefer the higher-level notify_* methods."""
        if not self._connected:
            return False
        return self._client.publish_fanout(message)

    def poll_agent(self, agent_name: str) -> dict[str, Any] | None:
        """Non-blocking poll: retrieve one pending message for *agent_name* or None."""
        if not self._connected:
            return None
        return self._client.basic_get(agent_name)

    def queue_depth(self, agent_name: str) -> int:
        """Return the number of unread messages in *agent_name*'s queue."""
        return self._client.queue_depth(agent_name)

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "AgentBus":
        return self.connect()

    def __exit__(self, *_args: Any) -> None:
        self.disconnect()


# ── Module helpers ────────────────────────────────────────────────────────────

def _resolve_url() -> str:
    """Read RABBITMQ_URL from settings (lazy import to avoid circular deps)."""
    try:
        from data_ingestion.config.settings import get_settings
        return get_settings().rabbitmq_url
    except Exception:
        return "amqp://ftth:ftth@127.0.0.1:5672/ftth"


def get_agent_bus(rabbitmq_url: str | None = None) -> AgentBus:
    """Return a connected AgentBus, or an inert one if the broker is unreachable.

    Never raises — always safe to call even without a running broker.
    """
    bus = AgentBus(rabbitmq_url)
    bus.connect()
    return bus
