from celery import Celery

from app.config import settings

# Step 1: RabbitMQ carries the task messages. No result backend: the one task (execute_approved_refund) returns
# nothing and ignores its result.
celery_app = Celery(
    "order",
    broker=settings.rabbitmq_url,
    include=["app.tasks"],  # the worker imports this module to find the tasks
)

# Step 2: JSON only, UTC, and our OWN queue on the shared RabbitMQ (knowledge and agent have theirs).
# The worker is started with `-Q order`.
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="order",
)
