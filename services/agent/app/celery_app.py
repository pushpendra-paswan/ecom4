from celery import Celery

from app.config import settings

# Step 1: RabbitMQ carries the task messages, Redis is the result backend (our task ignores results)
celery_app = Celery(
    "agent",
    broker=settings.rabbitmq_url,
    backend=settings.redis_url,
    include=["app.tasks"],  # the worker imports this module to find the tasks
)

# Step 2: JSON only, UTC, and our OWN queue. The knowledge service uses the same RabbitMQ as its broker;
# with the shared default queue ("celery") each worker would receive the other service's tasks.
# The worker is started with `-Q agent`. One message at a time per worker process, because runs are long.
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="agent",
    worker_prefetch_multiplier=1,
)
