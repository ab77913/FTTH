"""
RabbitMQ client for agent-to-agent (A2A) messaging.

Architecture layer: E — Agentic Intelligence (Peer-Peer + Broadcast).

Exchange topology
-----------------
  ftth.agent.direct   DIRECT   P2P messages      routing_key = target agent name
  ftth.agent.fanout   FANOUT   Broadcast          routing_key = "" (ignored)
  ftth.job.topic      TOPIC    Job-level events   routing_key = ftth.job.<event>

Queue naming: ``ftth.queue.{agent_name}``

Each agent queue is bound to:
  - ``ftth.agent.direct``  with routing_key = agent_name   (receives P2P)
  - ``ftth.agent.fanout``  (receives all broadcasts)

The supervisor queue is also bound to ``ftth.job.topic`` with ``ftth.job.#``
to receive all job events.

All connections use ``pika.BlockingConnection``.  If pika is not installed or
the broker is unreachable the client degrades gracefully — all publish calls
become no-ops and ``connected`` returns False.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Topology constants ────────────────────────────────────────────────────────

_EXCHANGE_DIRECT = "ftth.agent.direct"
_EXCHANGE_FANOUT = "ftth.agent.fanout"
_EXCHANGE_TOPIC  = "ftth.job.topic"
_QUEUE_PREFIX    = "ftth.queue."

_KNOWN_AGENTS: list[str] = [
    "supervisor",
    "reverse_geocoder",
    "agent1_address_validator",
    "agent2_geocoding",
    "agent3_parcel",
    "agent4_building",
    "agent5_0_offline_ocr",
    "agent5_streetview",
    "agent6_final",
]


class RabbitMQClient:
    """Low-level AMQP client wrapping pika.BlockingConnection.

    Parameters
    ----------
    rabbitmq_url:
        Full AMQP URL, e.g. ``amqp://user:pass@host:5672/vhost``.
    heartbeat:
        Heartbeat interval in seconds (default 60).  Keeps long-running
        pipeline connections alive without triggering the broker's idle timeout.
    """

    # Public exchange / queue-prefix names (accessible for testing)
    EXCHANGE_DIRECT: str = _EXCHANGE_DIRECT
    EXCHANGE_FANOUT: str = _EXCHANGE_FANOUT
    EXCHANGE_TOPIC:  str = _EXCHANGE_TOPIC
    QUEUE_PREFIX:    str = _QUEUE_PREFIX
    KNOWN_AGENTS:    list[str] = _KNOWN_AGENTS

    def __init__(self, rabbitmq_url: str, heartbeat: int = 60) -> None:
        self._url = rabbitmq_url
        self._heartbeat = heartbeat
        self._connection = None
        self._channel = None
        self._connected = False

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Open the AMQP connection and channel.  Returns True on success."""
        try:
            import pika  # noqa: PLC0415
            params = pika.URLParameters(self._url)
            params.heartbeat = self._heartbeat
            params.blocked_connection_timeout = 30
            self._connection = pika.BlockingConnection(params)
            self._channel = self._connection.channel()
            self._connected = True
            logger.info("RabbitMQClient: connected to %s", self._url)
            return True
        except ImportError:
            logger.warning("RabbitMQClient: pika not installed — install it with: pip install pika")
            self._connected = False
            return False
        except Exception as exc:
            logger.warning("RabbitMQClient: broker unavailable (%s); A2A messaging disabled", exc)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Close the channel and connection gracefully."""
        try:
            if self._channel and self._channel.is_open:
                self._channel.close()
        except Exception as exc:
            logger.debug("RabbitMQClient: error closing channel: %s", exc)
        try:
            if self._connection and not self._connection.is_closed:
                self._connection.close()
        except Exception as exc:
            logger.debug("RabbitMQClient: error closing connection: %s", exc)
        finally:
            self._connected = False
            self._channel = None
            self._connection = None
            logger.info("RabbitMQClient: disconnected")

    def setup_topology(self) -> None:
        """Declare exchanges and per-agent queues with correct bindings.

        This is idempotent — safe to call on every startup.
        Exchanges and queues are durable so they survive broker restarts.
        """
        if not self._connected or not self._channel:
            return
        try:
            ch = self._channel
            # ── Exchanges ────────────────────────────────────────────────────
            ch.exchange_declare(_EXCHANGE_DIRECT, exchange_type="direct", durable=True)
            ch.exchange_declare(_EXCHANGE_FANOUT, exchange_type="fanout", durable=True)
            ch.exchange_declare(_EXCHANGE_TOPIC,  exchange_type="topic",  durable=True)

            # ── Per-agent queues ─────────────────────────────────────────────
            for agent in _KNOWN_AGENTS:
                q = f"{_QUEUE_PREFIX}{agent}"
                ch.queue_declare(q, durable=True)
                # Bind to direct exchange for P2P (routing_key = agent name)
                ch.queue_bind(q, _EXCHANGE_DIRECT, routing_key=agent)
                # Bind to fanout exchange to receive broadcasts
                ch.queue_bind(q, _EXCHANGE_FANOUT, routing_key="")

            # ── Job-event queue (supervisor collects all job events) ──────────
            events_q = f"{_QUEUE_PREFIX}job.events"
            ch.queue_declare(events_q, durable=True)
            ch.queue_bind(events_q, _EXCHANGE_TOPIC, routing_key="ftth.job.#")

            logger.info("RabbitMQClient: topology declared (%d agent queues)", len(_KNOWN_AGENTS))
        except Exception as exc:
            logger.warning("RabbitMQClient: topology setup failed: %s", exc)

    # ── Publishing ────────────────────────────────────────────────────────────

    def publish_direct(self, target_agent: str, message: dict[str, Any]) -> bool:
        """Peer-to-Peer: send *message* to the queue of *target_agent* only."""
        return self._publish(_EXCHANGE_DIRECT, target_agent, message)

    def publish_fanout(self, message: dict[str, Any]) -> bool:
        """Broadcast: deliver *message* to every agent queue simultaneously."""
        return self._publish(_EXCHANGE_FANOUT, "", message)

    def publish_topic(self, routing_key: str, message: dict[str, Any]) -> bool:
        """Topic event: route *message* using *routing_key* (e.g. ``ftth.job.started``)."""
        return self._publish(_EXCHANGE_TOPIC, routing_key, message)

    def _publish(self, exchange: str, routing_key: str, message: dict[str, Any]) -> bool:
        """Internal publish helper shared by all three exchange types."""
        if not self._connected or not self._channel:
            return False
        try:
            import pika  # noqa: PLC0415
            self._channel.basic_publish(
                exchange=exchange,
                routing_key=routing_key,
                body=json.dumps(message, default=str),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,    # persistent — survives broker restart
                ),
            )
            logger.debug("RabbitMQClient: published to %s/%s", exchange, routing_key)
            return True
        except Exception as exc:
            logger.warning(
                "RabbitMQClient: publish failed (exchange=%s routing_key=%s): %s",
                exchange, routing_key, exc,
            )
            self._connected = False     # mark stale so callers can re-check
            return False

    # ── Consuming ─────────────────────────────────────────────────────────────

    def basic_get(self, agent_name: str) -> dict[str, Any] | None:
        """Non-blocking poll: return one message from *agent_name*'s queue or None.

        The message is auto-acknowledged on retrieval.
        """
        if not self._connected or not self._channel:
            return None
        try:
            queue = f"{_QUEUE_PREFIX}{agent_name}"
            method, _props, body = self._channel.basic_get(queue, auto_ack=True)
            if method is None:
                return None
            return json.loads(body)
        except Exception as exc:
            logger.warning("RabbitMQClient: basic_get failed for %s: %s", agent_name, exc)
            return None

    def subscribe(
        self,
        agent_name: str,
        callback: Callable[[dict[str, Any]], None],
        auto_ack: bool = True,
    ) -> None:
        """Blocking consumer: call *callback* for each message in *agent_name*'s queue.

        This call blocks until the channel is cancelled or the connection closes.
        Run it in a dedicated thread for background agents.
        """
        if not self._connected or not self._channel:
            logger.warning("RabbitMQClient: cannot subscribe — not connected")
            return

        queue = f"{_QUEUE_PREFIX}{agent_name}"

        def _on_message(ch, method, _props, body: bytes) -> None:
            try:
                msg = json.loads(body)
                callback(msg)
                if not auto_ack:
                    ch.basic_ack(method.delivery_tag)
            except Exception as exc:
                logger.warning("RabbitMQClient: message handler error: %s", exc)
                if not auto_ack:
                    ch.basic_nack(method.delivery_tag, requeue=False)

        self._channel.basic_consume(queue, on_message_callback=_on_message, auto_ack=auto_ack)
        logger.info("RabbitMQClient: started consuming from %s", queue)
        try:
            self._channel.start_consuming()
        except KeyboardInterrupt:
            self._channel.stop_consuming()

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def queue_depth(self, agent_name: str) -> int:
        """Return the number of messages waiting in *agent_name*'s queue."""
        if not self._connected or not self._channel:
            return -1
        try:
            queue = f"{_QUEUE_PREFIX}{agent_name}"
            result = self._channel.queue_declare(queue, durable=True, passive=True)
            return result.method.message_count
        except Exception:
            return -1

    @property
    def connected(self) -> bool:
        """True if the AMQP connection is open and healthy."""
        return self._connected

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "RabbitMQClient":
        self.connect()
        self.setup_topology()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.disconnect()
