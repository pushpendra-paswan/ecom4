import asyncio
import json
import logging

import aio_pika
import redis.asyncio as redis

from app import connections
from app.config import settings

logger = logging.getLogger(__name__)

# RabbitMQ: the platform's topic exchange, and ONE durable queue shared by every Realtime instance
EXCHANGE_NAME = "support.events"
QUEUE_NAME = "realtime.events"
# Redis pub/sub channel that fans each event out to ALL Realtime instances
REDIS_CHANNEL = "realtime:events"
# Pause before retrying after a connection problem
RETRY_SECONDS = 2


# Background task 1: competing consumer on the shared RabbitMQ queue.
# RabbitMQ gives each event to exactly one instance; that instance forwards it to the Redis channel.
async def consume_rabbitmq_events() -> None:
    # Step 1: connect to RabbitMQ and Redis. The robust connection reconnects by itself after a drop
    # (and restores the channel, queue and consumer); only the very first connect needs this retry loop.
    redis_client = redis.from_url(settings.redis_url)
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            break
        except Exception as error:
            logger.warning("[%s] RabbitMQ not reachable (%r); retrying in %s s", settings.instance_id, error, RETRY_SECONDS)
            await asyncio.sleep(RETRY_SECONDS)

    try:
        # Step 2: one unacknowledged message at a time, so RabbitMQ hands events out in turn to the instances
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        # Step 3: declare the exchange, the shared durable queue and a binding for every routing key (all idempotent)
        exchange = await channel.declare_exchange(EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True)
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        await queue.bind(exchange, routing_key="#")

        # Step 4: handle each message: forward it to Redis, then acknowledge it
        async def handle_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            # Step 4a: a message that is not a JSON event can never be delivered; log it and drop it
            try:
                envelope = json.loads(message.body)
                event_type = envelope["event_type"]
            except Exception:
                logger.warning("[%s] dropping a message that is not a valid event: %r", settings.instance_id, message.body[:200])
                await message.ack()
                return

            # Step 4b: publish to Redis; only acknowledge once that worked, otherwise RabbitMQ delivers it again
            try:
                await redis_client.publish(REDIS_CHANNEL, message.body)
                await message.ack()
                logger.info("[%s] forwarded %s (%s) to Redis", settings.instance_id, event_type, envelope.get("event_id"))
            except Exception as error:
                logger.error("[%s] could not forward %s to Redis (%r); requeueing", settings.instance_id, event_type, error)
                await asyncio.sleep(RETRY_SECONDS)
                await message.nack(requeue=True)

        await queue.consume(handle_message)
        logger.info("[%s] consuming RabbitMQ queue %s", settings.instance_id, QUEUE_NAME)

        # Step 5: keep running until the app shuts down (the task is cancelled then)
        await asyncio.Future()
    finally:
        # Step 6: clean up on shutdown
        await connection.close()
        await redis_client.aclose()


# Background task 2: EVERY instance subscribes to the Redis channel and delivers each event
# to its own local WebSocket connections, using the routing rules below.
async def subscribe_redis_events() -> None:
    while True:
        redis_client = redis.from_url(settings.redis_url)
        try:
            # Step 1: subscribe to the channel
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)
            logger.info("[%s] subscribed to Redis channel %s", settings.instance_id, REDIS_CHANNEL)

            async for redis_message in pubsub.listen():
                # Step 2: skip the subscribe confirmation; only real messages carry an event
                if redis_message["type"] != "message":
                    continue
                envelope = json.loads(redis_message["data"])
                event_type = envelope["event_type"]

                # Step 3: routing rules. Staff get every event; the ticket's customer also gets ticket.* events.
                # approval.* and any other event type go to staff only.
                rooms = ["staff"]
                customer_id = envelope.get("data", {}).get("customer_id")
                if event_type.startswith("ticket.") and customer_id:
                    rooms.append(f"user:{customer_id}")

                # Step 4: deliver to this instance's connections in those rooms
                delivered = await connections.deliver_to_rooms(rooms, {"type": "event", "event": envelope})
                logger.info(
                    "[%s] delivered %s (%s) to %s local connection(s)",
                    settings.instance_id,
                    event_type,
                    envelope["event_id"],
                    delivered,
                )
        except asyncio.CancelledError:
            # Step 5: the app is shutting down
            await redis_client.aclose()
            raise
        except Exception as error:
            # Step 6: the connection dropped (or a bad message arrived): wait a moment and subscribe again
            logger.error("[%s] Redis subscriber stopped (%r); reconnecting in %s s", settings.instance_id, error, RETRY_SECONDS)

        await redis_client.aclose()
        await asyncio.sleep(RETRY_SECONDS)
