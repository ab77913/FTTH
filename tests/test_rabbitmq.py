"""
Comprehensive tests for RabbitMQClient and AgentBus.

All tests use unittest.mock to avoid requiring a live RabbitMQ broker.
The RabbitMQClient graceful-degradation path (no pika / broker unreachable)
is also fully tested.

Run:
    pytest tests/test_rabbitmq.py -v
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


from data_ingestion.messaging.rabbitmq_client import RabbitMQClient
from data_ingestion.messaging.agent_bus import AgentBus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_connection():
    """Return a pre-wired mock pika.BlockingConnection + channel."""
    mock_channel = MagicMock()
    mock_conn = MagicMock()
    mock_conn.channel.return_value = mock_channel
    mock_conn.is_closed = False
    mock_channel.is_open = True
    return mock_conn, mock_channel


def _connected_client(mock_conn, mock_channel) -> RabbitMQClient:
    """Return a RabbitMQClient that is already 'connected' via mocks."""
    client = RabbitMQClient("amqp://ftth:ftth@localhost:5672/ftth")
    client._connection = mock_conn
    client._channel = mock_channel
    client._connected = True
    return client


# ══════════════════════════════════════════════════════════════════════════════
# 1. Connection management
# ══════════════════════════════════════════════════════════════════════════════

class TestConnection:
    def test_connect_success(self):
        mock_conn, mock_channel = _make_mock_connection()
        with patch("pika.BlockingConnection", return_value=mock_conn):
            client = RabbitMQClient("amqp://ftth:ftth@localhost:5672/ftth")
            result = client.connect()

        assert result is True
        assert client.connected is True

    def test_connect_broker_unreachable(self):
        with patch("pika.BlockingConnection", side_effect=Exception("Connection refused")):
            client = RabbitMQClient("amqp://nonexistent:5672/")
            result = client.connect()

        assert result is False
        assert client.connected is False

    def test_connect_pika_not_installed(self):
        with patch.dict("sys.modules", {"pika": None}):
            client = RabbitMQClient("amqp://localhost/")
            result = client.connect()

        assert result is False
        assert client.connected is False

    def test_disconnect_closes_connection(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        client.disconnect()

        mock_conn.close.assert_called_once()
        assert client.connected is False

    def test_disconnect_when_already_closed(self):
        mock_conn, mock_channel = _make_mock_connection()
        mock_conn.is_closed = True
        client = _connected_client(mock_conn, mock_channel)

        client.disconnect()  # must not raise

        assert client.connected is False

    def test_context_manager_connect_and_disconnect(self):
        mock_conn, mock_channel = _make_mock_connection()
        with patch("pika.BlockingConnection", return_value=mock_conn):
            with RabbitMQClient("amqp://localhost/") as client:
                assert client.connected is True
        mock_conn.close.assert_called_once()
        assert client.connected is False


# ══════════════════════════════════════════════════════════════════════════════
# 2. Topology declaration
# ══════════════════════════════════════════════════════════════════════════════

class TestTopology:
    def test_setup_declares_three_exchanges(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)
        client.setup_topology()

        exchange_calls = [c[1]["exchange_type"] for c in mock_channel.exchange_declare.call_args_list]
        assert "direct" in exchange_calls
        assert "fanout" in exchange_calls
        assert "topic"  in exchange_calls
        assert mock_channel.exchange_declare.call_count == 3

    def test_setup_declares_agent_queues(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)
        client.setup_topology()

        declared_queues = [c[0][0] for c in mock_channel.queue_declare.call_args_list]
        for agent in RabbitMQClient.KNOWN_AGENTS:
            assert f"ftth.queue.{agent}" in declared_queues

    def test_setup_binds_each_agent_to_direct_and_fanout(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)
        client.setup_topology()

        # queue_bind is called as queue_bind(queue, exchange, routing_key=...),
        # so exchange is the second positional arg: call_args[0][1].
        exchanges_bound = {c[0][1] for c in mock_channel.queue_bind.call_args_list}
        assert RabbitMQClient.EXCHANGE_DIRECT in exchanges_bound
        assert RabbitMQClient.EXCHANGE_FANOUT in exchanges_bound

    def test_setup_declares_job_events_queue(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)
        client.setup_topology()

        declared_queues = [c[0][0] for c in mock_channel.queue_declare.call_args_list]
        assert "ftth.queue.job.events" in declared_queues

    def test_setup_noop_when_disconnected(self):
        client = RabbitMQClient("amqp://localhost/")
        client.setup_topology()  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# 3. Peer-to-Peer publishing (Direct exchange)
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishDirect:
    def test_publish_direct_uses_direct_exchange(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        result = client.publish_direct("agent1_address_validator", {"type": "start", "job_id": "j1"})

        assert result is True
        mock_channel.basic_publish.assert_called_once()
        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["exchange"] == RabbitMQClient.EXCHANGE_DIRECT

    def test_publish_direct_sets_routing_key_to_agent_name(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        client.publish_direct("agent3_parcel", {"job_id": "j1"})

        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["routing_key"] == "agent3_parcel"

    def test_publish_direct_serialises_body_as_json(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        msg = {"type": "agent_start", "job_id": "job123", "count": 42}
        client.publish_direct("supervisor", msg)

        kwargs = mock_channel.basic_publish.call_args[1]
        body = json.loads(kwargs["body"])
        assert body == msg

    def test_publish_direct_returns_false_when_disconnected(self):
        client = RabbitMQClient("amqp://localhost/")
        result = client.publish_direct("agent1_address_validator", {"test": True})
        assert result is False

    def test_publish_direct_marks_messages_persistent(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        client.publish_direct("supervisor", {"msg": "hello"})

        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["properties"].delivery_mode == 2


# ══════════════════════════════════════════════════════════════════════════════
# 4. Broadcast publishing (Fanout exchange)
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishFanout:
    def test_publish_fanout_uses_fanout_exchange(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        result = client.publish_fanout({"type": "progress", "pct": 50})

        assert result is True
        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["exchange"] == RabbitMQClient.EXCHANGE_FANOUT

    def test_publish_fanout_uses_empty_routing_key(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        client.publish_fanout({"broadcast": True})

        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["routing_key"] == ""

    def test_publish_fanout_returns_false_when_disconnected(self):
        client = RabbitMQClient("amqp://localhost/")
        result = client.publish_fanout({"broadcast": True})
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# 5. Topic publishing
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishTopic:
    def test_publish_topic_uses_topic_exchange(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        result = client.publish_topic("ftth.job.pipeline.started", {"job_id": "j1"})

        assert result is True
        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["exchange"] == RabbitMQClient.EXCHANGE_TOPIC

    def test_publish_topic_passes_routing_key(self):
        mock_conn, mock_channel = _make_mock_connection()
        client = _connected_client(mock_conn, mock_channel)

        client.publish_topic("ftth.job.agent1.completed", {"job_id": "j2"})

        kwargs = mock_channel.basic_publish.call_args[1]
        assert kwargs["routing_key"] == "ftth.job.agent1.completed"

    def test_publish_topic_returns_false_when_disconnected(self):
        client = RabbitMQClient("amqp://localhost/")
        result = client.publish_topic("ftth.job.x", {})
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# 6. Consuming — basic_get (non-blocking poll)
# ══════════════════════════════════════════════════════════════════════════════

class TestBasicGet:
    def test_basic_get_returns_parsed_message(self):
        mock_conn, mock_channel = _make_mock_connection()
        payload = {"type": "agent_complete", "job_id": "j1"}
        mock_method = MagicMock()
        mock_channel.basic_get.return_value = (
            mock_method,
            MagicMock(),
            json.dumps(payload).encode(),
        )
        client = _connected_client(mock_conn, mock_channel)

        result = client.basic_get("supervisor")

        assert result == payload
        mock_channel.basic_get.assert_called_once_with("ftth.queue.supervisor", auto_ack=True)

    def test_basic_get_returns_none_when_queue_empty(self):
        mock_conn, mock_channel = _make_mock_connection()
        mock_channel.basic_get.return_value = (None, None, None)
        client = _connected_client(mock_conn, mock_channel)

        result = client.basic_get("supervisor")

        assert result is None

    def test_basic_get_returns_none_when_disconnected(self):
        client = RabbitMQClient("amqp://localhost/")
        result = client.basic_get("supervisor")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# 7. Error resilience
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorResilience:
    def test_publish_returns_false_on_channel_error(self):
        mock_conn, mock_channel = _make_mock_connection()
        mock_channel.basic_publish.side_effect = Exception("Channel closed")
        client = _connected_client(mock_conn, mock_channel)

        result = client.publish_direct("agent1_address_validator", {"test": True})

        assert result is False
        assert client.connected is False  # stale connection marked

    def test_setup_topology_survives_exchange_declare_failure(self):
        mock_conn, mock_channel = _make_mock_connection()
        mock_channel.exchange_declare.side_effect = Exception("Access refused")
        client = _connected_client(mock_conn, mock_channel)

        client.setup_topology()  # must not raise

    def test_disconnect_survives_close_error(self):
        mock_conn, mock_channel = _make_mock_connection()
        mock_conn.close.side_effect = Exception("Already closed")
        client = _connected_client(mock_conn, mock_channel)

        client.disconnect()  # must not raise
        assert client.connected is False


# ══════════════════════════════════════════════════════════════════════════════
# 8. AgentBus — high-level façade
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentBus:
    """Test AgentBus using a mocked RabbitMQClient."""

    def _make_bus(self, connected: bool = True) -> tuple[AgentBus, MagicMock]:
        mock_client = MagicMock()
        mock_client.connect.return_value = connected
        with patch("data_ingestion.messaging.agent_bus.RabbitMQClient", return_value=mock_client):
            bus = AgentBus("amqp://localhost/")
        bus._client = mock_client
        bus._connected = connected
        return bus, mock_client

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def test_connect_calls_client_connect_and_setup(self):
        mock_client = MagicMock()
        mock_client.connect.return_value = True
        with patch("data_ingestion.messaging.agent_bus.RabbitMQClient", return_value=mock_client):
            bus = AgentBus("amqp://localhost/")
            bus.connect()

        mock_client.connect.assert_called_once()
        mock_client.setup_topology.assert_called_once()
        assert bus.connected is True

    def test_connect_skips_topology_when_broker_unreachable(self):
        mock_client = MagicMock()
        mock_client.connect.return_value = False
        with patch("data_ingestion.messaging.agent_bus.RabbitMQClient", return_value=mock_client):
            bus = AgentBus("amqp://localhost/")
            bus.connect()

        mock_client.setup_topology.assert_not_called()
        assert bus.connected is False

    def test_disconnect_calls_client_disconnect(self):
        bus, mock_client = self._make_bus()
        bus.disconnect()
        mock_client.disconnect.assert_called_once()
        assert bus.connected is False

    def test_context_manager(self):
        mock_client = MagicMock()
        mock_client.connect.return_value = True
        with patch("data_ingestion.messaging.agent_bus.RabbitMQClient", return_value=mock_client):
            with AgentBus("amqp://localhost/") as bus:
                assert bus.connected is True
        mock_client.disconnect.assert_called_once()

    # ── Pipeline events ───────────────────────────────────────────────────────

    def test_notify_pipeline_start_fanout_and_topic(self):
        bus, mock_client = self._make_bus()
        bus.notify_pipeline_start("job1", 500)

        mock_client.publish_fanout.assert_called_once()
        fanout_msg = mock_client.publish_fanout.call_args[0][0]
        assert fanout_msg["type"] == "pipeline_start"
        assert fanout_msg["job_id"] == "job1"
        assert fanout_msg["total_records"] == 500

        mock_client.publish_topic.assert_called_once_with(
            "ftth.job.pipeline.started",
            {"job_id": "job1", "total_records": 500},
        )

    def test_notify_pipeline_complete_fanout_and_topic(self):
        bus, mock_client = self._make_bus()
        summary = {"agent6_final": {"processed": 500}}
        bus.notify_pipeline_complete("job1", summary)

        fanout_msg = mock_client.publish_fanout.call_args[0][0]
        assert fanout_msg["type"] == "pipeline_complete"
        assert fanout_msg["summary"] == summary

    # ── Agent events ──────────────────────────────────────────────────────────

    def test_notify_agent_start_direct_to_agent_and_topic(self):
        bus, mock_client = self._make_bus()
        bus.notify_agent_start("job1", "agent2_geocoding", 300)

        direct_calls = mock_client.publish_direct.call_args_list
        assert len(direct_calls) == 1
        target_agent, msg = direct_calls[0][0]
        assert target_agent == "agent2_geocoding"
        assert msg["type"] == "agent_start"
        assert msg["total_records"] == 300

        mock_client.publish_topic.assert_called_with(
            "ftth.job.agent2_geocoding.started",
            {"job_id": "job1", "agent": "agent2_geocoding"},
        )

    def test_notify_agent_complete_reports_to_supervisor(self):
        bus, mock_client = self._make_bus()
        bus.notify_agent_complete("job1", "agent3_parcel", {"processed": 100})

        direct_calls = mock_client.publish_direct.call_args_list
        assert len(direct_calls) == 1
        target_agent, msg = direct_calls[0][0]
        assert target_agent == "supervisor", "completion must go to supervisor"
        assert msg["type"] == "agent_complete"
        assert msg["agent_name"] == "agent3_parcel"
        assert msg["result"] == {"processed": 100}

    def test_broadcast_progress_sends_fanout(self):
        bus, mock_client = self._make_bus()
        bus.broadcast_progress("job1", "agent4_building", 65)

        msg = mock_client.publish_fanout.call_args[0][0]
        assert msg["type"] == "progress"
        assert msg["stage"] == "agent4_building"
        assert msg["progress"] == 65

    def test_broadcast_progress_clamps_to_0_100(self):
        bus, mock_client = self._make_bus()
        bus.broadcast_progress("job1", "classify", 150)  # over 100

        msg = mock_client.publish_fanout.call_args[0][0]
        assert msg["progress"] == 100

        mock_client.reset_mock()
        bus.broadcast_progress("job1", "classify", -10)  # under 0

        msg = mock_client.publish_fanout.call_args[0][0]
        assert msg["progress"] == 0

    # ── No-ops when disconnected ──────────────────────────────────────────────

    def test_all_notify_calls_are_noop_when_disconnected(self):
        bus, mock_client = self._make_bus(connected=False)

        bus.notify_pipeline_start("j1", 10)
        bus.notify_pipeline_complete("j1", {})
        bus.notify_agent_start("j1", "agent1_address_validator", 10)
        bus.notify_agent_complete("j1", "agent1_address_validator", {})
        bus.broadcast_progress("j1", "agent1_address_validator", 50)
        bus.send_to_agent("agent2_geocoding", {"msg": "test"})
        bus.broadcast({"global": True})

        mock_client.publish_direct.assert_not_called()
        mock_client.publish_fanout.assert_not_called()
        mock_client.publish_topic.assert_not_called()

    # ── Full pipeline lifecycle ───────────────────────────────────────────────

    def test_full_pipeline_lifecycle(self):
        """Simulate a complete pipeline run and verify all messages are sent."""
        bus, mock_client = self._make_bus()

        agents = [
            "reverse_geocoder",
            "agent1_address_validator",
            "agent2_geocoding",
            "agent3_parcel",
            "agent4_building",
            "agent5_streetview",
            "agent6_final",
        ]

        bus.notify_pipeline_start("job_xyz", 1000)

        for agent in agents:
            bus.notify_agent_start("job_xyz", agent, 1000)
            bus.broadcast_progress("job_xyz", agent, 50)
            bus.notify_agent_complete("job_xyz", agent, {"processed": 1000})

        bus.notify_pipeline_complete("job_xyz", {"ok": True})

        # pipeline_start fanout + per-agent progress + pipeline_complete fanout
        expected_fanout = 1 + len(agents) + 1
        assert mock_client.publish_fanout.call_count == expected_fanout

        # agent_start direct (→ agent) + agent_complete direct (→ supervisor)
        expected_direct = len(agents) * 2
        assert mock_client.publish_direct.call_count == expected_direct


# ══════════════════════════════════════════════════════════════════════════════
# 9. Queue depth diagnostic
# ══════════════════════════════════════════════════════════════════════════════

class TestQueueDepth:
    def test_queue_depth_returns_message_count(self):
        mock_conn, mock_channel = _make_mock_connection()
        mock_result = MagicMock()
        mock_result.method.message_count = 7
        mock_channel.queue_declare.return_value = mock_result
        client = _connected_client(mock_conn, mock_channel)

        depth = client.queue_depth("supervisor")

        assert depth == 7

    def test_queue_depth_returns_minus_one_when_disconnected(self):
        client = RabbitMQClient("amqp://localhost/")
        assert client.queue_depth("supervisor") == -1
