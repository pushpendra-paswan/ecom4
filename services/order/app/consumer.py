# Event consumer process for the order service: run as its own container with `python -m app.consumer`.
# It reads approval.decided events and queues one Celery task per APPROVED REFUND approval (auto-approved or
# approved by a human). Rejected approvals and other action types need nothing from the order service.
# It never refunds anything itself: the task verifies the approval with the Ticket service first.
import json
import logging
import time

import pika

from app.celery_app import celery_app
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.consumer")

EXCHANGE_NAME = "support.events"
QUEUE_NAME = "order.approvals"
ROUTING_KEY = "approval.decided"
PREFETCH_COUNT = 10
RECONNECT_SECONDS = 5
# Sent by name, like the agent's consumer, so this process does not import the task module
TASK_NAME = "app.tasks.execute_approved_refund"


# Called by pika for every event on the queue
def handle_event(channel, method, properties, body: bytes) -> None:
    # Step 1: parse the event; a message that is not a valid event can never succeed, so drop it
    try:
        event = json.loads(body)
        data = event["data"]
        approval_id = data["approval_id"]
        status = data["status"]
        action_type = data["action_type"]
    except (ValueError, KeyError, TypeError) as error:
        logger.error("Dropping an invalid event: %r", error)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 2: only approved refunds lead to a refund
    if status != "approved" or action_type != "refund":
        logger.info("Ignored approval %s (%s, %s)", approval_id, action_type, status)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 3: queue the refund task; if that fails, put the event back on the queue (after a short pause)
    try:
        celery_app.send_task(TASK_NAME, args=[approval_id])
    except Exception as error:
        logger.error("Could not queue the refund for approval %s, requeueing the event: %r", approval_id, error)
        time.sleep(2)
        channel.basic_reject(delivery_tag=method.delivery_tag, requeue=True)
        return

    # Step 4: done
    channel.basic_ack(delivery_tag=method.delivery_tag)
    logger.info("Queued refund for approved approval %s", approval_id)


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
