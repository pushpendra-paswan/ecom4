from celery import Celery

from app.config import settings

# Step 1: RabbitMQ carries the task messages, Redis stores the task results
celery_app = Celery(
    "knowledge",
    broker=settings.rabbitmq_url,
    backend=settings.redis_url,
    include=["app.tasks"],  # the worker imports this module to find the tasks
)

# Step 2: JSON only (task arguments are plain strings), and log times in UTC
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
