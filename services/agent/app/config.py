from pydantic_settings import BaseSettings


# Every setting is read from the environment variable with the same name (case-insensitive)
class Settings(BaseSettings):
    service_name: str
    root_path: str = ""
    database_url: str
    redis_url: str
    rabbitmq_url: str
    jwt_public_key_path: str
    # The chat model and its key (never hardcoded)
    openai_api_key: str
    chat_model: str
    # Shared HS256 secret for the service tokens this service sends on its gRPC calls
    service_token_secret: str
    # gRPC servers this service calls (the order address is unused until M8)
    knowledge_grpc_address: str = "knowledge:50052"
    ticket_grpc_address: str = "ticket:50053"
    order_grpc_address: str = "order:50051"
    # Hard step limit for one graph run, and how many ticket messages the agent reads
    agent_recursion_limit: int = 10
    agent_history_limit: int = 20


settings = Settings()
