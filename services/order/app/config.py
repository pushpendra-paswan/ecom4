from pydantic_settings import BaseSettings


# Every setting is read from the environment variable with the same name (case-insensitive)
class Settings(BaseSettings):
    service_name: str
    root_path: str = ""
    database_url: str
    redis_url: str
    rabbitmq_url: str
    jwt_public_key_path: str
    # The gRPC server runs in the same process as the REST API, on this port
    grpc_port: int
    # Service tokens on gRPC calls: the shared HS256 secret, and the comma-separated caller names allowed in
    service_token_secret: str
    grpc_allowed_callers: str


settings = Settings()
