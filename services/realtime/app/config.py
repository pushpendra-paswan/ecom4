import socket

from pydantic_settings import BaseSettings


# Every setting is read from the environment variable with the same name (case-insensitive)
class Settings(BaseSettings):
    service_name: str
    root_path: str = ""
    redis_url: str
    rabbitmq_url: str
    jwt_public_key_path: str
    # Identifies this instance in auth_ok and in the logs. Docker sets the hostname to the container id,
    # so every replica (docker compose up --scale realtime=2) gets its own value.
    instance_id: str = socket.gethostname()


settings = Settings()
