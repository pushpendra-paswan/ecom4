# gRPC clients for the agent: the ONLY way the agent reaches other services (tools never touch a database).
# Every entry point returns plain Python dicts or lists, never proto objects. Errors are not caught here:
# a grpc.RpcError propagates to the Celery task, which retries.
import time

import grpc
import jwt

from app.config import settings
from app.generated import knowledge_pb2, knowledge_pb2_grpc, ticket_pb2, ticket_pb2_grpc

# Deadlines (seconds), the M5 convention: 5 s by default, 15 s for search (it makes an embedding call)
DEFAULT_TIMEOUT_SECONDS = 5
SEARCH_TIMEOUT_SECONDS = 15
SERVICE_TOKEN_LIFETIME_SECONDS = 60

# One channel per target per process, created on first use and then reused (channels are thread-safe).
# Not created at import time: Celery imports this module BEFORE it forks its worker processes,
# and a gRPC channel must not be shared across a fork.
ticket_stub = None
knowledge_stub = None


# A fresh service token for every call: HS256, signed with the shared secret, sub=agent, valid for 60 seconds
def service_token_metadata() -> list:
    issued_at = int(time.time())
    claims = {"sub": "agent", "iat": issued_at, "exp": issued_at + SERVICE_TOKEN_LIFETIME_SECONDS}
    token = jwt.encode(claims, settings.service_token_secret, algorithm="HS256")
    return [("authorization", f"Bearer {token}")]


# The ticket with its messages, oldest first
def get_ticket(ticket_id: str) -> dict:
    global ticket_stub

    # Step 1: open the channel once per process
    if ticket_stub is None:
        ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(settings.ticket_grpc_address))

    # Step 2: call the Ticket service
    ticket = ticket_stub.GetTicket(
        ticket_pb2.GetTicketRequest(ticket_id=ticket_id),
        timeout=DEFAULT_TIMEOUT_SECONDS,
        metadata=service_token_metadata(),
    )

    # Step 3: convert to plain Python
    messages = []
    for message in ticket.messages:
        messages.append({
            "id": message.id,
            "sender_type": message.sender_type,
            "content": message.content,
            "created_at": message.created_at,
        })
    return {
        "id": ticket.id,
        "customer_id": ticket.customer_id,
        "subject": ticket.subject,
        "status": ticket.status,
        "messages": messages,
    }


# Posts a message as "ai" or "system" and returns it
def post_message(ticket_id: str, sender_type: str, content: str) -> dict:
    global ticket_stub

    # Step 1: open the channel once per process
    if ticket_stub is None:
        ticket_stub = ticket_pb2_grpc.TicketServiceStub(grpc.insecure_channel(settings.ticket_grpc_address))

    # Step 2: call the Ticket service
    message = ticket_stub.PostMessage(
        ticket_pb2.PostMessageRequest(ticket_id=ticket_id, sender_type=sender_type, content=content),
        timeout=DEFAULT_TIMEOUT_SECONDS,
        metadata=service_token_metadata(),
    )

    # Step 3: convert to plain Python
    return {
        "id": message.id,
        "sender_type": message.sender_type,
        "content": message.content,
        "created_at": message.created_at,
    }


# Semantic search over the policy documents, best match first
def search_knowledge(query: str, top_k: int) -> list[dict]:
    global knowledge_stub

    # Step 1: open the channel once per process
    if knowledge_stub is None:
        knowledge_stub = knowledge_pb2_grpc.KnowledgeServiceStub(
            grpc.insecure_channel(settings.knowledge_grpc_address)
        )

    # Step 2: call the Knowledge service
    response = knowledge_stub.Search(
        knowledge_pb2.SearchRequest(query=query, top_k=top_k),
        timeout=SEARCH_TIMEOUT_SECONDS,
        metadata=service_token_metadata(),
    )

    # Step 3: convert to plain Python
    results = []
    for result in response.results:
        results.append({
            "title": result.title,
            "section": result.section,
            "content": result.content,
            "score": result.score,
        })
    return results
