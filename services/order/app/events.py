import json
import logging
import uuid
from datetime import datetime, timezone

import pika

from app.config import settings

logger = logging.getLogger(__name__)

# One topic exchange for every domain event in the platform; the routing key is the event type
EXCHANGE_NAME = "support.events"
# Short timeouts so a broker outage never hangs the caller
CONNECT_TIMEOUT_SECONDS = 3


# Publishes one event to RabbitMQ. Called by the services right AFTER their commit (copied from the Ticket service,
# M9: the order worker publishes refund.completed).
# A new short-lived connection is opened for every publish, because pika's BlockingConnection is not
# thread-safe and Celery may run tasks on several threads or processes.
# A failure is logged and swallowed: the change is already saved, and the caller must not fail (no outbox).
def publish_event(event_type: str, data: dict) -> None:
    # Step 1: build the envelope (UUIDs in data are turned into strings by json.dumps(default=str))
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    body = json.dumps(envelope, default=str)

    try:
        # Step 2: connect with short timeouts and a single attempt
        parameters = pika.URLParameters(settings.rabbitmq_url)
        parameters.connection_attempts = 1
        parameters.socket_timeout = CONNECT_TIMEOUT_SECONDS
        parameters.stack_timeout = CONNECT_TIMEOUT_SECONDS
        parameters.blocked_connection_timeout = CONNECT_TIMEOUT_SECONDS
        connection = pika.BlockingConnection(parameters)

        try:
            # Step 3: declare the durable topic exchange (declaring is idempotent)
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)

            # Step 4: publish a persistent JSON message; the routing key is the event type
            channel.basic_publish(
                exchange=EXCHANGE_NAME,
                routing_key=event_type,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=pika.DeliveryMode.Persistent,
                    message_id=envelope["event_id"],
                ),
            )
        finally:
            # Step 5: always close the connection
            connection.close()

        logger.info("Published event %s (%s)", event_type, envelope["event_id"])
    except Exception as error:
        # Step 6: never fail the caller; the event is lost (accepted limitation, no outbox)
        logger.error("Could not publish event %s (%s): %r", event_type, envelope["event_id"], error)
