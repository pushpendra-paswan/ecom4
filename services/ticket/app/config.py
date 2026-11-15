from decimal import Decimal

from pydantic_settings import BaseSettings


# Every setting is read from the environment variable with the same name (case-insensitive)
class Settings(BaseSettings):
    service_name: str
    root_path: str = ""
    database_url: str
    redis_url: str
    rabbitmq_url: str
    jwt_public_key_path: str
    # Refund approvals at or below this amount (mock INR) are approved automatically by policy
    refund_auto_approve_limit: Decimal = Decimal("5000")


settings = Settings()
