"""
Agent messaging layer — RabbitMQ-backed A2A Bus.

Architecture layer: E — Agentic Intelligence.

Provides peer-to-peer (Direct exchange) and broadcast (Fanout exchange)
messaging between agents in the FTTH pipeline, matching the RabbitMQ
topology shown in the solution architecture.

Quick usage::

    from data_ingestion.messaging.agent_bus import AgentBus

    with AgentBus() as bus:
        bus.notify_pipeline_start(job_id, total_records)
        bus.notify_agent_start(job_id, "agent1_address_validator", total_records)
"""
from data_ingestion.messaging.rabbitmq_client import RabbitMQClient
from data_ingestion.messaging.agent_bus import AgentBus

__all__ = ["RabbitMQClient", "AgentBus"]
