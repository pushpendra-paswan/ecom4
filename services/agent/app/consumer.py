# Event consumer process for the agent service: run as its own container with `python -m app.consumer`.
# It reads ticket.message_created events and queues one Celery task per CUSTOMER message. It never runs the agent
# itself, and it ignores ai, agent and system messages (so the AI never answers its own replies).
import json
import logging
import time

import pika

from app.celery_app import celery_app
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.consumer")

EXCHANGE_NAME = "support.events"
QUEUE_NAME = "agent.ticket_messages"
ROUTING_KEY = "ticket.message_created"
PREFETCH_COUNT = 10
RECONNECT_SECONDS = 5
# Sent by name, so this process does not import app.tasks (and with it LangGraph and the gRPC clients)
TASK_NAME = "app.tasks.run_agent_for_message"


# Called by pika for every event on the queue
def handle_event(channel, method, properties, body: bytes) -> None:
    # Step 1: parse the event; a message that is not a valid event can never succeed, so drop it
    try:
        event = json.loads(body)
        data = event["data"]
        ticket_id = data["ticket_id"]
        message_id = data["message_id"]
        sender_type = data["sender_type"]
    except (ValueError, KeyError, TypeError) as error:
        logger.error("Dropping an invalid event: %r", error)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 2: only customer messages get an AI answer
    if sender_type != "customer":
        logger.info("Ignored %s message %s on ticket %s", sender_type, message_id, ticket_id)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 3: queue the agent run; if that fails, put the event back on the queue (after a short pause)
    try:
        celery_app.send_task(TASK_NAME, args=[ticket_id, message_id])
    except Exception as error:
        logger.error("Could not queue the agent run for message %s, requeueing the event: %r", message_id, error)
        time.sleep(2)
        channel.basic_reject(delivery_tag=method.delivery_tag, requeue=True)
        return

    # Step 4: done
    channel.basic_ack(delivery_tag=method.delivery_tag)
    logger.info("Queued agent run for customer message %s on ticket %s", message_id, ticket_id)


def main() -> None:
    # Reconnect loop: if RabbitMQ restarts or the connection drops, wait and connect again
    while True:
        try:
            # Step 1: connect
            connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            channel = connection.channel()

            # Step 2: declare the exchange and our own durable queue, bound to one event type (idempotent)
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.queue_bind(queue=QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=ROUTING_KEY)

            # Step 3: manual acknowledgement, at most 10 unacknowledged events at a time
            channel.basic_qos(prefetch_count=PREFETCH_COUNT)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=handle_event)

            # Step 4: consume until the connection breaks
            logger.info("Consumer ready: queue %s bound to %s with %s", QUEUE_NAME, EXCHANGE_NAME, ROUTING_KEY)
            channel.start_consuming()
        except Exception as error:
            logger.error("Consumer connection lost (%r), reconnecting in %d s", error, RECONNECT_SECONDS)
            time.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    main()
