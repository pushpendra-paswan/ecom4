from pydantic_settings import BaseSettings


# Every setting is read from the environment variable with the same name (case-insensitive)
class Settings(BaseSettings):
    service_name: str
    root_path: str = ""
    database_url: str
    redis_url: str
    rabbitmq_url: str
    jwt_private_key_path: str
    jwt_public_key_path: str
    access_token_expire_minutes: int = 60


settings = Settings()
