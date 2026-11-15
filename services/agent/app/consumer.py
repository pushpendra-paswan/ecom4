# Event consumer process for the agent service: run as its own container with `python -m app.consumer`.
# It reads two event types from one queue and queues Celery tasks; it never runs the agent itself:
# - ticket.message_created: one run_agent_for_message task per CUSTOMER message. ai, agent and system messages are
#   ignored (so the AI never answers its own replies).
# - approval.decided (M9): one resume_after_approval task per HUMAN decision (auto_approved false). Auto-approvals
#   are ignored: the run that requested the refund already told the customer it was approved.
import json
import logging
import time

import pika

from app.celery_app import celery_app
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.consumer")

EXCHANGE_NAME = "support.events"
# The queue keeps its M7 name; from M9 it has a second binding
QUEUE_NAME = "agent.ticket_messages"
ROUTING_KEYS = ["ticket.message_created", "approval.decided"]
PREFETCH_COUNT = 10
RECONNECT_SECONDS = 5
# Sent by name, so this process does not import app.tasks (and with it LangGraph and the gRPC clients)
MESSAGE_TASK_NAME = "app.tasks.run_agent_for_message"
APPROVAL_TASK_NAME = "app.tasks.resume_after_approval"


# Called by pika for every event on the queue
def handle_event(channel, method, properties, body: bytes) -> None:
    # Step 1: parse the event; a message that is not a valid event can never succeed, so drop it
    try:
        event = json.loads(body)
        event_type = event["event_type"]
        data = event["data"]
        ticket_id = data["ticket_id"]
        if event_type == "approval.decided":
            approval_id = data["approval_id"]
            auto_approved = data["auto_approved"]
        else:
            message_id = data["message_id"]
            sender_type = data["sender_type"]
    except (ValueError, KeyError, TypeError) as error:
        logger.error("Dropping an invalid event: %r", error)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Step 2: pick the task. Only customer messages get an AI answer; only human decisions resume a paused run.
    if event_type == "approval.decided":
        if auto_approved:
            logger.info("Ignored auto-approved approval %s on ticket %s", approval_id, ticket_id)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        task_name = APPROVAL_TASK_NAME
        task_args = [ticket_id, approval_id]
    else:
        if sender_type != "customer":
            logger.info("Ignored %s message %s on ticket %s", sender_type, message_id, ticket_id)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        task_name = MESSAGE_TASK_NAME
        task_args = [ticket_id, message_id]

    # Step 3: queue the task; if that fails, put the event back on the queue (after a short pause)
    try:
        celery_app.send_task(task_name, args=task_args)
    except Exception as error:
        logger.error("Could not queue %s %s, requeueing the event: %r", task_name, task_args, error)
        time.sleep(2)
        channel.basic_reject(delivery_tag=method.delivery_tag, requeue=True)
        return

    # Step 4: done
    channel.basic_ack(delivery_tag=method.delivery_tag)
    if event_type == "approval.decided":
        logger.info("Queued resume for decided approval %s on ticket %s", approval_id, ticket_id)
    else:
        logger.info("Queued agent run for customer message %s on ticket %s", message_id, ticket_id)


def main() -> None:
    # Reconnect loop: if RabbitMQ restarts or the connection drops, wait and connect again
    while True:
        try:
            # Step 1: connect
            connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            channel = connection.channel()

            # Step 2: declare the exchange and our own durable queue, bound to both event types (idempotent)
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            for routing_key in ROUTING_KEYS:
                channel.queue_bind(queue=QUEUE_NAME, exchange=EXCHANGE_NAME, routing_key=routing_key)

            # Step 3: manual acknowledgement, at most 10 unacknowledged events at a time
            channel.basic_qos(prefetch_count=PREFETCH_COUNT)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=handle_event)

            # Step 4: consume until the connection breaks
            logger.info("Consumer ready: queue %s bound to %s with %s", QUEUE_NAME, EXCHANGE_NAME, ", ".join(ROUTING_KEYS))
            channel.start_consuming()
        except Exception as error:
            logger.error("Consumer connection lost (%r), reconnecting in %d s", error, RECONNECT_SECONDS)
            time.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    main()
