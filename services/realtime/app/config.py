from pydantic_settings import BaseSettings


# Every setting is read from the environment variable with the same name (case-insensitive)
class Settings(BaseSettings):
    service_name: str
    root_path: str = ""
    redis_url: str
    rabbitmq_url: str


settings = Settings()
